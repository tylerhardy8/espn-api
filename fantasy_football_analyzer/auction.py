"""Auction draft valuation engine.

Builds the full draftable player pool (free agents + rostered players),
derives replacement-level baselines from the league's real lineup slots,
converts value-over-replacement into auction dollars, blends the external
dollar signals on the league's own budget scale, prices availability, and
groups players into tiers by projection gaps.

Pipeline (see build_valued_pool):
  calculate_auction_values  projections -> vbd / vbd_depth -> model_value, crowd_value
  sources.enrich_pool       attaches Sleeper + FantasyPros fields and expert_value
  finalize_values           explicit blend -> availability -> caps -> cash-sum normalize
"""

from collections import defaultdict
from statistics import pstdev

from .draft import ROSTER_TARGETS, VBD_BASELINES
from .lineup import (
    CORE_POSITIONS, BENCH_SHARE, FLEX_SHARE, slot_profile, profile_from_targets,
)

DEFAULT_BUDGET = 200
DEFAULT_ROSTER_SIZE = 16

# --- tunable constants (every valuation change has a switch here) ----------
# Share of discretionary dollars reserved for depth between the starter
# baseline and the roster baseline (the rest goes to value over a starter).
DEPTH_DOLLAR_SHARE = 0.10
# Positions priced at the floor regardless of projection: {pos: (top_n, top_price)}
CAPPED_POSITIONS = {"K": (2, 2.0), "D/ST": (2, 2.0)}
# ESPN crowd values and FantasyPros auction values assume this budget
EXTERNAL_BUDGET_BASIS = 200
# Blend weights (renormalized over the signals a player actually has)
W_MODEL, W_CROWD, W_EXPERT = 0.45, 0.20, 0.35
# Dollars reserved per non-core rostered slot (IDP, P, HC) when normalizing
NONCORE_SLOT_PRICE = 1.0
# Pre-season availability multipliers by injury / roster status (upper-cased);
# the minimum over every status a player carries is applied
AVAILABILITY = {
    "INJURY_RESERVE": 0.35, "INJURED RESERVE": 0.35, "IR": 0.35,
    "PUP": 0.65, "PHYSICALLY UNABLE TO PERFORM": 0.65, "NFI": 0.65,
    "SUSPENSION": 0.80, "SUS": 0.80, "SUSPENDED": 0.80,
    "COV": 0.95,
    "OUT": 0.85, "DOUBTFUL": 0.92, "QUESTIONABLE": 0.97,
    "PRACTICE SQUAD": 0.50,
    "INACTIVE": 0.20, "RETIRED": 0.20,
}


def _pool_entry(player, bye_map=None):
    projected = getattr(player, "projected_total_points", 0) or 0
    espn_value = getattr(player, "auction_value_avg", -1)
    team = getattr(player, "proTeam", "") or ""
    entry = {
        "player_id": player.playerId,
        "name": player.name,
        "position": getattr(player, "position", ""),
        "team": team,
        "projected_points": round(projected, 2),
        "total_points": round(getattr(player, "total_points", 0) or 0, 2),
        "espn_value": round(espn_value, 1) if espn_value and espn_value > 0 else None,
        "adp": getattr(player, "avg_draft_position", -1),
        "injury_status": getattr(player, "injuryStatus", "") or "",
    }
    if bye_map:
        entry["bye"] = bye_map.get(team)
    return entry


def bye_weeks_by_pro_team(league, last_bye_week=14):
    """{pro team abbr: bye week} from ESPN's pro schedule (a bye is a week
    with no game). Cached on the league object; empty on any failure."""
    cached = getattr(league, "_ffa_bye_map", None)
    if cached is not None:
        return cached
    byes = {}
    try:
        from espn_api.football.constant import PRO_TEAM_MAP
        schedule = league._get_all_pro_schedule()
        for team_id, games in schedule.items():
            abbr = PRO_TEAM_MAP.get(team_id)
            if not abbr:
                continue
            weeks = {int(w) for w, g in games.items() if g}
            bye = next((w for w in range(1, last_bye_week + 1) if w not in weeks), None)
            if bye:
                byes[abbr] = bye
    except Exception:
        byes = {}
    try:
        league._ffa_bye_map = byes
    except Exception:
        pass
    return byes


def build_draft_pool(league, size=400):
    """Build the draftable player pool as {playerId: entry}.

    Combines free agents (pre-draft, that's everyone) with rostered players
    (mid-draft, sold players sit on rosters), so the pool stays complete at
    any point in the draft.
    """
    pool = {}
    bye_map = bye_weeks_by_pro_team(league)

    for team in league.teams:
        for player in team.roster:
            pool[player.playerId] = _pool_entry(player, bye_map)

    try:
        agents = league.free_agents(size=size)
    except Exception:
        agents = []
    for player in agents:
        pool.setdefault(player.playerId, _pool_entry(player, bye_map))

    return pool


def derive_roster_targets(league):
    """Per-position rosterable counts and roster size from league settings.

    Returns (targets, roster_size): targets[pos] is how many players of that
    position one team ends up rostering (starters + flex share + bench
    share; positions without a slot are absent), roster_size is the number
    of draftable slots per team. See lineup.slot_profile for the full picture.
    """
    slots = getattr(league.settings, "position_slot_counts", None) or {}
    if not slots:
        targets = {pos: t["total"] for pos, t in ROSTER_TARGETS.items() if pos != "FLEX"}
        return targets, DEFAULT_ROSTER_SIZE
    profile = slot_profile(slots)
    return profile["roster_targets"], profile["roster_size"] or DEFAULT_ROSTER_SIZE


def _replacement(entries, idx):
    if not entries:
        return 0.0
    idx = max(1, int(round(idx)))
    return entries[min(idx, len(entries)) - 1]["projected_points"]


def calculate_auction_values(pool, budget, num_teams, targets, profile=None):
    """Assign auction dollar values to every pool entry (mutates entries).

    Two-segment value over replacement: points above the last *starter*
    (fixed slots + flex share) earn the bulk of the league's discretionary
    dollars; points between the starter line and the last *rostered* player
    earn a small depth allowance (DEPTH_DOLLAR_SHARE), so bench-calibre
    players price at a few dollars instead of a cliff. Positions with no
    slot and capped positions (K, D/ST) never absorb discretionary dollars.

    Sets on each entry: pos_rank, vbd, vbd_depth, model_value, crowd_value,
    value (provisional; finalize_values produces the final blend).
    """
    if profile is None:
        profile = profile_from_targets(targets)
    starter_targets = profile["starter_targets"]
    roster_targets = profile["roster_targets"]
    roster_size = profile["roster_size"] or max(1, round(sum(roster_targets.values())))

    by_position = defaultdict(list)
    for entry in pool.values():
        if entry["position"] in CORE_POSITIONS:
            by_position[entry["position"]].append(entry)

    total_vbd = 0.0
    total_depth = 0.0
    for pos, entries in by_position.items():
        entries.sort(key=lambda e: e["projected_points"], reverse=True)
        for rank, entry in enumerate(entries, 1):
            entry["pos_rank"] = rank
            entry["vbd"] = 0.0
            entry["vbd_depth"] = 0.0
        if pos in CAPPED_POSITIONS or pos not in starter_targets:
            continue  # floor-priced: no share of discretionary dollars
        starter_idx = starter_targets[pos] * num_teams
        roster_idx = roster_targets.get(pos, starter_targets[pos]) * num_teams
        if starter_idx < 1:
            starter_idx = VBD_BASELINES.get(pos, 12)
        repl_starter = _replacement(entries, starter_idx)
        repl_roster = _replacement(entries, max(roster_idx, starter_idx))
        for entry in entries:
            proj = entry["projected_points"]
            entry["vbd"] = round(max(0.0, proj - repl_starter), 2)
            entry["vbd_depth"] = round(max(0.0, min(proj, repl_starter) - repl_roster), 2)
            total_vbd += entry["vbd"]
            total_depth += entry["vbd_depth"]

    discretionary = max(0.0, num_teams * budget - num_teams * roster_size)
    star_pot = discretionary * (1 - DEPTH_DOLLAR_SHARE if total_depth else 1.0)
    depth_pot = discretionary - star_pot
    scale = budget / EXTERNAL_BUDGET_BASIS if budget else 1.0

    for entries in by_position.values():
        for entry in entries:
            model = 1.0
            if total_vbd:
                model += entry["vbd"] / total_vbd * star_pot
            if total_depth:
                model += entry["vbd_depth"] / total_depth * depth_pot
            entry["model_value"] = round(model, 1)
            entry["crowd_value"] = (round(entry["espn_value"] * scale, 1)
                                    if entry.get("espn_value") else None)

    # Entries outside core positions get $1 placeholders
    for entry in pool.values():
        entry.setdefault("vbd", 0.0)
        entry.setdefault("vbd_depth", 0.0)
        entry.setdefault("model_value", 1.0)
        entry.setdefault("crowd_value", None)
        entry.setdefault("pos_rank", 0)

    finalize_values(pool, budget, num_teams, profile)
    return pool


def availability_multiplier(entry):
    """Share of a season a player is expected to be available, from every
    injury / roster status attached to the entry (minimum wins)."""
    statuses = [
        entry.get("injury_status"), entry.get("sleeper_injury"), entry.get("sleeper_status"),
    ]
    mult = 1.0
    for status in statuses:
        if not status:
            continue
        key = str(status).strip().upper().replace("_", " ")
        found = AVAILABILITY.get(key)
        if found is None:
            found = AVAILABILITY.get(key.replace(" ", "_"))
        if found is not None:
            mult = min(mult, found)
    return mult


def blend_value(entry):
    """Explicit weighted blend of the dollar signals a player carries."""
    signals = [
        (W_MODEL, entry.get("model_value")),
        (W_CROWD, entry.get("crowd_value")),
        (W_EXPERT, entry.get("expert_value")),
    ]
    present = [(w, v) for w, v in signals if v is not None and w > 0]
    if not present:
        return 1.0
    total_w = sum(w for w, _ in present)
    return sum(w * v for w, v in present) / total_w


def finalize_values(pool, budget, num_teams, profile):
    """Blend -> availability -> caps -> cash-sum normalization (mutates).

    Sets `value`, `availability`. Positions without a slot in this league
    stay at $1; capped positions (K, D/ST) get top_price for their top_n and
    $1 otherwise, applied after the blend so crowd values can't reinflate them.
    """
    starter_targets = profile["starter_targets"]
    for entry in pool.values():
        pos = entry.get("position", "")
        if pos not in CORE_POSITIONS or pos not in starter_targets:
            entry["value"] = 1.0
            entry["availability"] = 1.0
            continue
        avail = availability_multiplier(entry)
        entry["availability"] = avail
        if pos in CAPPED_POSITIONS:
            top_n, top_price = CAPPED_POSITIONS[pos]
            entry["value"] = top_price if 0 < entry.get("pos_rank", 0) <= top_n else 1.0
            continue
        blended = blend_value(entry)
        entry["value"] = round(1 + max(0.0, blended - 1) * avail, 1)

    normalize_values(
        pool, budget, num_teams, profile["roster_size"],
        noncore_slots=profile.get("noncore_slots", 0),
    )
    return pool


def normalize_values(pool, budget, num_teams, roster_size, noncore_slots=0,
                     noncore_price=NONCORE_SLOT_PRICE):
    """Rescale `value` so the rosterable core pool sums to the league's cash.

    Blending in external dollar scales breaks the cash-sum invariant;
    rescaling keeps the $1 floor and makes inflation start at ~1.0. Only
    core slots are ranked (IDP / P / HC slots are reserved at noncore_price
    each). Mutates entries in place.
    """
    core_slots = max(1, roster_size - noncore_slots)
    top_n = num_teams * core_slots
    ranked = sorted(
        (e for e in pool.values() if e.get("position") in CORE_POSITIONS),
        key=lambda e: e.get("value", 1.0), reverse=True,
    )
    current = sum(e.get("value", 1.0) for e in ranked[:top_n])
    target = num_teams * budget - num_teams * noncore_slots * noncore_price
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


def league_profile(league):
    """The slot profile for a league (legacy targets when settings are absent)."""
    slots = getattr(getattr(league, "settings", None), "position_slot_counts", None) or {}
    if slots:
        return slot_profile(slots)
    targets, roster_size = derive_roster_targets(league)
    return profile_from_targets(targets, roster_size)


def build_valued_pool(league, budget=None, size=400, enrich=True):
    """One-call helper: pool + values + external enrichment + final blend + tiers.

    Returns (pool, budget, targets, roster_size).
    """
    profile = league_profile(league)
    targets, roster_size = profile["roster_targets"], profile["roster_size"]
    budget = budget or getattr(league.settings, "auction_budget", 0) or DEFAULT_BUDGET
    pool = build_draft_pool(league, size=size)
    calculate_auction_values(pool, budget, len(league.teams), targets, profile=profile)
    if enrich:
        try:
            from .sources import enrich_pool
            enrich_pool(pool, league, budget, len(league.teams), roster_size)
        except Exception:
            pass  # external sources are best-effort
        finalize_values(pool, budget, len(league.teams), profile)
    detect_tiers(pool)
    return pool, budget, targets, roster_size
