"""Auction draft valuation engine.

Builds the full draftable player pool (free agents + rostered players),
derives replacement-level baselines from the league's actual roster
settings, converts value-over-replacement into auction dollar values,
and groups players into tiers by projection gaps.
"""

from collections import defaultdict
from statistics import pstdev

from .draft import ROSTER_TARGETS, VBD_BASELINES

CORE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")

# How flex and bench slots are typically spent, by position
FLEX_SHARE = {"RB": 0.40, "WR": 0.45, "TE": 0.15}
BENCH_SHARE = {"QB": 0.10, "RB": 0.40, "WR": 0.35, "TE": 0.10, "K": 0.0, "D/ST": 0.05}

DEFAULT_BUDGET = 200
DEFAULT_ROSTER_SIZE = 16


def _pool_entry(player):
    projected = getattr(player, "projected_total_points", 0) or 0
    espn_value = getattr(player, "auction_value_avg", -1)
    return {
        "player_id": player.playerId,
        "name": player.name,
        "position": getattr(player, "position", ""),
        "team": getattr(player, "proTeam", ""),
        "projected_points": round(projected, 2),
        "total_points": round(getattr(player, "total_points", 0) or 0, 2),
        "espn_value": round(espn_value, 1) if espn_value and espn_value > 0 else None,
        "adp": getattr(player, "avg_draft_position", -1),
        "injury_status": getattr(player, "injuryStatus", "") or "",
    }


def build_draft_pool(league, size=400):
    """Build the draftable player pool as {playerId: entry}.

    Combines free agents (pre-draft, that's everyone) with rostered players
    (mid-draft, sold players sit on rosters), so the pool stays complete at
    any point in the draft.
    """
    pool = {}

    for team in league.teams:
        for player in team.roster:
            pool[player.playerId] = _pool_entry(player)

    try:
        agents = league.free_agents(size=size)
    except Exception:
        agents = []
    for player in agents:
        pool.setdefault(player.playerId, _pool_entry(player))

    return pool


def derive_roster_targets(league):
    """Derive per-position rosterable counts and roster size from league settings.

    Returns (targets, roster_size) where targets[pos] is how many players of
    that position one team typically rosters (starters + flex share + bench
    share), and roster_size is total draftable slots per team.
    """
    slots = getattr(league.settings, "position_slot_counts", None) or {}
    if not slots:
        targets = {pos: t["total"] for pos, t in ROSTER_TARGETS.items() if pos != "FLEX"}
        return targets, DEFAULT_ROSTER_SIZE

    bench = slots.get("BE", 0)
    flex = slots.get("RB/WR/TE", 0) + slots.get("FLEX", 0)
    roster_size = sum(
        count for pos, count in slots.items()
        if count > 0 and pos not in ("IR",)
    )

    targets = {}
    for pos in CORE_POSITIONS:
        direct = slots.get(pos, 0)
        share = flex * FLEX_SHARE.get(pos, 0) + bench * BENCH_SHARE.get(pos, 0)
        targets[pos] = direct + share

    return targets, roster_size or DEFAULT_ROSTER_SIZE


def calculate_auction_values(pool, budget, num_teams, targets):
    """Assign auction dollar values to every pool entry (mutates entries).

    Classic VBD-to-dollars: every rosterable slot costs at least $1; the
    league's remaining (discretionary) dollars are distributed proportionally
    to each player's value over replacement. Where ESPN publishes a crowd
    auction value, the model blends 50/50 with it.

    Sets on each entry: vbd, model_value, value (blended), pos_rank.
    """
    by_position = defaultdict(list)
    for entry in pool.values():
        if entry["position"] in CORE_POSITIONS:
            by_position[entry["position"]].append(entry)

    total_vbd = 0.0
    for pos, entries in by_position.items():
        entries.sort(key=lambda e: e["projected_points"], reverse=True)
        rostered_count = round(targets.get(pos, 0) * num_teams)
        baseline_idx = rostered_count if rostered_count >= 1 else VBD_BASELINES.get(pos, 12)
        if baseline_idx <= len(entries):
            replacement = entries[baseline_idx - 1]["projected_points"]
        elif entries:
            replacement = entries[-1]["projected_points"]
        else:
            replacement = 0

        for rank, entry in enumerate(entries, 1):
            entry["pos_rank"] = rank
            entry["vbd"] = round(max(0.0, entry["projected_points"] - replacement), 2)
            total_vbd += entry["vbd"]

    roster_size = max(1, round(sum(targets.values())))
    discretionary = max(0, num_teams * budget - num_teams * roster_size)

    for entries in by_position.values():
        for entry in entries:
            model = 1 + (entry["vbd"] / total_vbd * discretionary if total_vbd else 0)
            entry["model_value"] = round(model, 1)
            if entry["espn_value"]:
                entry["value"] = round((model + entry["espn_value"]) / 2, 1)
            else:
                entry["value"] = entry["model_value"]

    # Entries outside core positions get $1 placeholders
    for entry in pool.values():
        entry.setdefault("vbd", 0.0)
        entry.setdefault("model_value", 1.0)
        entry.setdefault("value", 1.0)
        entry.setdefault("pos_rank", 0)

    normalize_values(pool, budget, num_teams, roster_size)
    return pool


def normalize_values(pool, budget, num_teams, roster_size):
    """Rescale `value` so the rosterable pool's total equals total league cash.

    Blending in external dollar scales (ESPN crowd values, expert values)
    breaks the cash-sum invariant. Rescaling keeps the $1 floor and makes
    inflation start at ~1.0 and stay meaningful. Mutates entries in place.
    """
    top_n = num_teams * roster_size
    ranked = sorted(pool.values(), key=lambda e: e.get("value", 1.0), reverse=True)
    current = sum(e.get("value", 1.0) for e in ranked[:top_n])
    target = num_teams * budget
    if current > top_n:
        scale = (target - top_n) / (current - top_n)
        for entry in pool.values():
            entry["value"] = round(1 + max(0.0, entry.get("value", 1.0) - 1) * scale, 1)
    return pool


def detect_tiers(pool, max_per_position=40):
    """Group players into per-position tiers by projection gaps (mutates entries).

    A new tier starts where the projection drop to the next player exceeds
    0.75 standard deviations of that position's adjacent gaps.
    """
    by_position = defaultdict(list)
    for entry in pool.values():
        if entry["position"] in CORE_POSITIONS:
            by_position[entry["position"]].append(entry)

    for pos, entries in by_position.items():
        entries.sort(key=lambda e: e["projected_points"], reverse=True)
        top = entries[:max_per_position]
        gaps = [
            top[i]["projected_points"] - top[i + 1]["projected_points"]
            for i in range(len(top) - 1)
        ]
        threshold = 0.75 * pstdev(gaps) if len(gaps) > 1 else float("inf")

        tier = 1
        for i, entry in enumerate(top):
            entry["tier"] = tier
            if i < len(gaps) and gaps[i] > threshold and gaps[i] > 0:
                tier += 1
        for entry in entries[max_per_position:]:
            entry["tier"] = tier + 1

    for entry in pool.values():
        entry.setdefault("tier", 0)

    return pool


def build_valued_pool(league, budget=None, size=400, enrich=True):
    """One-call helper: pool + values + external enrichment + tiers.

    Returns (pool, budget, targets, roster_size).
    """
    targets, roster_size = derive_roster_targets(league)
    budget = budget or getattr(league.settings, "auction_budget", 0) or DEFAULT_BUDGET
    pool = build_draft_pool(league, size=size)
    calculate_auction_values(pool, budget, len(league.teams), targets)
    if enrich:
        try:
            from .sources import enrich_pool
            enrich_pool(pool, league, budget, len(league.teams), roster_size)
        except Exception:
            pass  # external sources are best-effort
    detect_tiers(pool)
    return pool, budget, targets, roster_size
