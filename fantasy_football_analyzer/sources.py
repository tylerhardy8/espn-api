"""External data sources that enrich the draft pool beyond ESPN.

- Sleeper (free, no auth): injury status, practice participation, depth
  chart slot, and trending adds. Mapped to ESPN players via Sleeper's
  `espn_id` field.
- FantasyPros (API key): Expert Consensus Rankings (ECR) and tiers, matched
  by normalized name + position. ECR is converted to a dollar signal by
  rank-to-value mapping and blended into the auction values.

Every fetch is cached and fails soft: if a source is unreachable or not
configured, the pool is simply not enriched by it.
"""

import os
import re
import time

import requests

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
SLEEPER_TRENDING_URL = "https://api.sleeper.app/v1/players/nfl/trending/add"
FANTASYPROS_RANKINGS_URL = "https://api.fantasypros.com/public/v2/json/nfl/{year}/consensus-rankings"

_cache = {}
SLEEPER_TTL = 6 * 3600
TRENDING_TTL = 3600
FP_TTL = 6 * 3600
REQUEST_TIMEOUT = 25

# What enrich_pool last applied — surfaced in the UI/AI context
LAST_STATUS = {"sleeper": False, "fantasypros": False, "fp_matched": 0, "trending": 0}


def _cached(key, ttl, loader):
    now = time.time()
    if key in _cache:
        value, ts = _cache[key]
        if now - ts < ttl:
            return value
    try:
        value = loader()
    except Exception:
        value = None
    if value is not None:
        _cache[key] = (value, now)
    return value


def clear_sources_cache():
    _cache.clear()


def get_sources_status():
    return dict(LAST_STATUS, fantasypros_key_set=bool(os.environ.get("FANTASYPROS_API_KEY")))


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name):
    """Lowercase, strip punctuation and generational suffixes."""
    if not name:
        return ""
    cleaned = re.sub(r"[^a-z0-9 ]", "", name.lower().replace(".", ""))
    parts = [p for p in cleaned.split() if p not in _SUFFIXES]
    return " ".join(parts)


def _dst_key(name):
    """'49ers D/ST' and 'San Francisco 49ers' both -> '49ers'."""
    words = normalize_name(name.replace("D/ST", "").replace("DST", "")).split()
    return words[-1] if words else ""


# ---------------------------------------------------------------------------
# Sleeper
# ---------------------------------------------------------------------------

def fetch_sleeper_players():
    """{espn_id: {...}} plus a sleeper_id -> espn_id map under key '_by_sleeper'."""
    def load():
        resp = requests.get(SLEEPER_PLAYERS_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        by_espn, by_sleeper = {}, {}
        for sleeper_id, p in data.items():
            espn_id = p.get("espn_id")
            if not espn_id:
                continue
            try:
                espn_id = int(espn_id)
            except (TypeError, ValueError):
                continue
            by_espn[espn_id] = {
                "sleeper_id": sleeper_id,
                "injury_status": p.get("injury_status") or "",
                "injury_body_part": p.get("injury_body_part") or "",
                "practice_participation": p.get("practice_participation") or "",
                "depth_chart_position": p.get("depth_chart_position") or "",
                "depth_chart_order": p.get("depth_chart_order"),
                "status": p.get("status") or "",
                "age": p.get("age"),
                "years_exp": p.get("years_exp"),
            }
            by_sleeper[sleeper_id] = espn_id
        by_espn["_by_sleeper"] = by_sleeper
        return by_espn

    return _cached("sleeper_players", SLEEPER_TTL, load) or {}


def fetch_sleeper_trending(lookback_hours=24, limit=50):
    """{espn_id: add_count} for the most-added players on Sleeper."""
    players = fetch_sleeper_players()
    by_sleeper = players.get("_by_sleeper", {})

    def load():
        resp = requests.get(
            SLEEPER_TRENDING_URL,
            params={"lookback_hours": lookback_hours, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        out = {}
        for item in resp.json():
            espn_id = by_sleeper.get(str(item.get("player_id")))
            if espn_id:
                out[espn_id] = item.get("count", 0)
        return out

    if not by_sleeper:
        return {}
    return _cached("sleeper_trending", TRENDING_TTL, load) or {}


# ---------------------------------------------------------------------------
# FantasyPros
# ---------------------------------------------------------------------------

# ESPN player position ids used as keys in scoringItems[].pointsOverrides
_POS_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
_RECEPTION_STAT = 53


def reception_scoring(league):
    """Points per reception: {"base": float, "by_pos": {"RB": 1.0, "TE": 1.5, ...}}.

    ESPN can express PPR either as a flat `points` value or entirely through
    per-position `pointsOverrides` (base 0) — the espn_api settings parser only
    reads the D/ST override, so we go to the raw scoring items.
    """
    raw = getattr(league.settings, "_raw_scoring_settings", None) or {}
    for item in raw.get("scoringItems", []) or []:
        if item.get("statId") != _RECEPTION_STAT:
            continue
        base = float(item.get("points") or 0)
        by_pos = {}
        for pid, pts in (item.get("pointsOverrides") or {}).items():
            pos = _POS_IDS.get(int(pid)) if str(pid).isdigit() else None
            if pos:
                by_pos[pos] = float(pts or 0)
        return {"base": base, "by_pos": by_pos}
    # Fallback: parsed scoring_format (flat points only)
    for item in getattr(league.settings, "scoring_format", None) or []:
        if (item.get("label") or "").lower() == "each reception" or (item.get("abbr") or "").upper() == "REC":
            return {"base": float(item.get("points") or 0), "by_pos": {}}
    return {"base": 0.0, "by_pos": {}}


def _core_reception_points(league):
    """Reception points that matter for rankings (RB/WR, else the base)."""
    rec = reception_scoring(league)
    core = [rec["by_pos"][p] for p in ("RB", "WR") if p in rec["by_pos"]]
    return max(core) if core else rec["base"]


def detect_scoring(league):
    """'PPR' | 'HALF' | 'STD' from the league's scoring rules."""
    pts = _core_reception_points(league)
    if pts >= 1:
        return "PPR"
    if pts > 0:
        return "HALF"
    return "STD"


def describe_scoring(league):
    """Human label, e.g. 'Full PPR (TE premium: 1.5/rec)' or 'Standard (non-PPR)'."""
    rec = reception_scoring(league)
    pts = _core_reception_points(league)
    if pts >= 1:
        label = "Full PPR" if pts == 1 else f"{pts:g} PPR"
    elif pts > 0:
        label = "Half PPR" if pts == 0.5 else f"{pts:g} PPR"
    else:
        label = "Standard (non-PPR)"
    extras = [f"{pos} {v:g}/rec" for pos, v in rec["by_pos"].items()
              if pos in ("TE", "QB") and v and v != pts]
    if extras:
        label += " (" + ", ".join(extras) + " premium)" if any(v > pts for pos, v in rec["by_pos"].items() if pos in ("TE", "QB")) else " (" + ", ".join(extras) + ")"
    return label


FANTASYPROS_PROJECTIONS_URL = "https://api.fantasypros.com/public/v2/json/nfl/{year}/projections"
PROJECTION_POSITIONS = ("QB", "RB", "WR", "TE", "K")
# FantasyPros stat line field -> ESPN statId (deterministic items only; per-game
# bonuses like 100-yard games are not projectable from season totals and ESPN's
# own projections leave them out too, so both sources stay comparable)
_FP_STAT_IDS = {
    "pass_yds": 3, "pass_tds": 4, "pass_ints": 20,
    "rush_yds": 24, "rush_tds": 25,
    "rec_rec": 53, "rec_yds": 42, "rec_tds": 43,
}
FUMBLE_LOST_SHARE = 0.5   # FantasyPros projects total fumbles; roughly half are lost
CONSENSUS_ESPN_WEIGHT = 0.5   # ESPN vs FantasyPros consensus in the blended projection


def fetch_fantasypros_projections(year, api_key=None):
    """{(normalized_name, position): stats dict} of season projections."""
    api_key = api_key or os.environ.get("FANTASYPROS_API_KEY")
    if not api_key or not year:
        return {}

    def load():
        out = {}
        for pos in PROJECTION_POSITIONS:
            resp = requests.get(
                FANTASYPROS_PROJECTIONS_URL.format(year=year),
                params={"position": pos, "week": 0, "scoring": "PPR"},
                headers={"x-api-key": api_key},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            for pl in resp.json().get("players", []):
                name = pl.get("name") or ""
                position = (pl.get("position_id") or pos).upper()
                stats = pl.get("stats") or {}
                if name and stats:
                    out[(normalize_name(name), position)] = stats
        return out

    return _cached(f"fp_proj_{year}", FP_TTL, load) or {}


def league_stat_points(league):
    """{statId: {"base": pts, "by_pos": {pos: pts}}} for the deterministic items."""
    raw = getattr(getattr(league, "settings", None), "_raw_scoring_settings", None) or {}
    table = {}
    for item in raw.get("scoringItems", []) or []:
        sid = item.get("statId")
        by_pos = {}
        for pid, pts in (item.get("pointsOverrides") or {}).items():
            pos = _POS_IDS.get(int(pid)) if str(pid).isdigit() else None
            if pos:
                by_pos[pos] = float(pts or 0)
        table[sid] = {"base": float(item.get("points") or 0), "by_pos": by_pos}
    # Sensible defaults when a league has no raw settings (tests, CLI)
    table.setdefault(3, {"base": 0.04, "by_pos": {}})
    table.setdefault(4, {"base": 4.0, "by_pos": {}})
    table.setdefault(20, {"base": -2.0, "by_pos": {}})
    table.setdefault(24, {"base": 0.1, "by_pos": {}})
    table.setdefault(25, {"base": 6.0, "by_pos": {}})
    table.setdefault(42, {"base": 0.1, "by_pos": {}})
    table.setdefault(43, {"base": 6.0, "by_pos": {}})
    table.setdefault(53, {"base": 1.0, "by_pos": {}})
    table.setdefault(72, {"base": -2.0, "by_pos": {}})
    return table


def score_stat_line(stats, position, league):
    """Fantasy points for a FantasyPros season stat line under this league's rules."""
    if position == "K":
        # Decimal-per-yard kicking can't be scored without distances; FP's own
        # standard points are close enough for a $1-2 position
        return float(stats.get("points") or 0)
    table = league_stat_points(league)

    def pts(sid):
        row = table.get(sid, {"base": 0.0, "by_pos": {}})
        return row["by_pos"].get(position, row["base"])

    total = 0.0
    for field, sid in _FP_STAT_IDS.items():
        total += float(stats.get(field) or 0) * pts(sid)
    total += float(stats.get("fumbles") or 0) * FUMBLE_LOST_SHARE * pts(72)
    for field, sid in (("2pt_tds", 44),):
        total += float(stats.get(field) or 0) * pts(sid)
    return round(total, 2)


def consensus_projection_map(league):
    """{(normalized_name, position): FantasyPros season points under this
    league's scoring}, cached per league-year. Empty without a key."""
    year = getattr(league, "year", None)
    key = f"fp_scored_{getattr(league, 'league_id', 0)}_{year}"

    def load():
        raw = fetch_fantasypros_projections(year)
        return {k: score_stat_line(stats, k[1], league) for k, stats in raw.items()}

    return _cached(key, FP_TTL, load) or {}


def blended_projection(name, position, espn_points, league, fp_map=None):
    """ESPN and FantasyPros consensus blended (CONSENSUS_ESPN_WEIGHT); falls back
    to whichever exists. Returns (blended, fp_points_or_None)."""
    fp_map = fp_map if fp_map is not None else consensus_projection_map(league)
    fp = fp_map.get((normalize_name(name or ""), position))
    espn = float(espn_points or 0)
    if fp is None:
        return espn, None
    if espn <= 0:
        return float(fp), float(fp)
    w = CONSENSUS_ESPN_WEIGHT
    return round(w * espn + (1 - w) * float(fp), 2), float(fp)


def fetch_fantasypros_rankings(year, scoring="PPR", api_key=None):
    """{(normalized_name, position): {ecr, tier, pos_rank, best, worst}} or {}."""
    api_key = api_key or os.environ.get("FANTASYPROS_API_KEY")
    if not api_key:
        return {}

    def load():
        resp = requests.get(
            FANTASYPROS_RANKINGS_URL.format(year=year),
            # week=0 is required: without it the API serves a truncated
            # free-tier response even for premium keys
            params={"position": "ALL", "type": "draft", "scoring": scoring, "week": 0},
            headers={"x-api-key": api_key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        out = {}
        for p in resp.json().get("players", []):
            name = p.get("player_name") or ""
            position = (p.get("player_position_id") or p.get("position") or "").upper()
            if position == "DST":
                position = "D/ST"
                key = (_dst_key(name), position)
            else:
                key = (normalize_name(name), position)
            ecr = p.get("rank_ecr")
            if not name or ecr is None:
                continue
            out[key] = {
                "ecr": int(ecr),
                "tier": p.get("tier"),
                "pos_rank": p.get("pos_rank"),
                "best": p.get("rank_min"),
                "worst": p.get("rank_max"),
                "auction_value": p.get("player_auction_value") or p.get("auction_value"),
            }
        return out

    return _cached(f"fp_{year}_{scoring}", FP_TTL, load) or {}


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_pool(pool, league, budget, num_teams, roster_size):
    """Attach Sleeper + FantasyPros data to pool entries (mutates in place).

    Attaches injury / practice / depth / age / trending from Sleeper and
    ECR, tier, best/worst rank, and a dollar `expert_value` from FantasyPros.
    Blending into `value` happens afterwards in auction.finalize_values.
    Returns a status dict.
    """
    status = {"sleeper": False, "fantasypros": False, "fp_matched": 0, "trending": 0}

    # --- Sleeper: injuries, practice, depth chart, trending ----------------
    sleeper = fetch_sleeper_players()
    if sleeper:
        status["sleeper"] = True
        trending = fetch_sleeper_trending()
        status["trending"] = len(trending)
        for pid, entry in pool.items():
            s = sleeper.get(pid)
            if not s:
                continue
            entry["sleeper_injury"] = s["injury_status"]
            entry["sleeper_status"] = s.get("status") or ""
            entry["injury_body_part"] = s["injury_body_part"]
            entry["practice"] = s["practice_participation"]
            if s["depth_chart_position"] and s["depth_chart_order"]:
                entry["depth_chart"] = f"{s['depth_chart_position']}{s['depth_chart_order']}"
            entry["age"] = s["age"]
            entry["years_exp"] = s.get("years_exp")
            entry["trending_adds"] = trending.get(pid, 0)
            # Prefer a concrete Sleeper designation when ESPN says nothing
            if (not entry.get("injury_status") or entry["injury_status"].upper() == "ACTIVE") \
                    and s["injury_status"]:
                entry["injury_status"] = s["injury_status"]

    # --- FantasyPros ECR: attach + derive a dollar signal -------------------
    year = getattr(league, "year", None)
    fp = fetch_fantasypros_rankings(year, detect_scoring(league)) if year else {}
    if fp:
        status["fantasypros"] = True
        from .auction import EXTERNAL_BUDGET_BASIS
        scale = (budget / EXTERNAL_BUDGET_BASIS) if budget else 1.0
        # The model's own dollar ladder, indexed by consensus rank: turns a
        # rank into "what the model pays the player ranked there"
        ranked = sorted(pool.values(), key=lambda e: e.get("value", 1.0), reverse=True)
        value_by_rank = [e.get("value", 1.0) for e in ranked]

        def dollars_at(rank):
            idx = int(rank) - 1
            return value_by_rank[idx] if 0 <= idx < len(value_by_rank) else 1.0

        matched = 0
        for entry in pool.values():
            pos = entry.get("position", "")
            key = (_dst_key(entry["name"]), pos) if pos == "D/ST" else (normalize_name(entry["name"]), pos)
            f = fp.get(key)
            if not f:
                continue
            matched += 1
            entry["fp_ecr"] = f["ecr"]
            entry["fp_tier"] = f["tier"]
            entry["fp_pos_rank"] = f["pos_rank"]
            entry["fp_best"] = f.get("best")
            entry["fp_worst"] = f.get("worst")
            expert_value = dollars_at(f["ecr"])
            if f.get("auction_value"):
                try:
                    expert_value = (expert_value + float(f["auction_value"]) * scale) / 2
                except (TypeError, ValueError):
                    pass
            entry["expert_value"] = round(expert_value, 1)
            # Ceiling / floor: the ladder at the experts' best and worst ranks
            if f.get("best"):
                entry["ceiling_value"] = round(dollars_at(f["best"]), 1)
            if f.get("worst"):
                entry["floor_value"] = round(dollars_at(f["worst"]), 1)
        status["fp_matched"] = matched

    LAST_STATUS.update(status)
    return status
