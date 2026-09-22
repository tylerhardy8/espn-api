"""Lineup slots as the single source of roster truth.

Everything that needs to know "how many of each position start, what the
flex slots accept, how deep the bench is" derives it from the league's real
`position_slot_counts` through `slot_profile`. The auction baseline, roster
needs, and the on-block lineup math all read the same profile, so a league
with two flex slots, a superflex, IDP slots, or no D/ST slot is priced as
it is actually played.
"""

CORE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")

# Which positions a flex-type slot accepts, and how the slot's starts are
# typically split between them (shares sum to 1 per slot type).
FLEX_ELIGIBILITY = {
    "RB/WR/TE": ("RB", "WR", "TE"),
    "FLEX": ("RB", "WR", "TE"),
    "RB/WR": ("RB", "WR"),
    "WR/TE": ("WR", "TE"),
    "OP": ("QB", "RB", "WR", "TE"),  # superflex: almost always a QB
}
FLEX_SHARE = {
    "RB/WR/TE": {"RB": 0.40, "WR": 0.45, "TE": 0.15},
    "FLEX": {"RB": 0.40, "WR": 0.45, "TE": 0.15},
    "RB/WR": {"RB": 0.50, "WR": 0.50},
    "WR/TE": {"WR": 0.80, "TE": 0.20},
    "OP": {"QB": 0.85, "RB": 0.06, "WR": 0.07, "TE": 0.02},
}
# How bench slots are typically spent, by position
BENCH_SHARE = {"QB": 0.10, "RB": 0.40, "WR": 0.35, "TE": 0.10, "K": 0.0, "D/ST": 0.05}

# Rostered slots the model does not price (IDP, punter, head coach, ...)
NONCORE_SLOTS = ("LB", "DL", "DB", "DT", "DE", "CB", "S", "DP", "P", "HC", "TQB", "ER")
IGNORED_SLOTS = ("IR", "BE", "", "Rookie")

BENCH_WEIGHTS = (0.18, 0.10, 0.05, 0.02)  # insurance value of bench players 1..4


def slot_profile(slots):
    """Describe a league's roster from `position_slot_counts`.

    Returns a dict:
      fixed            {pos: n}  starting slots per core position (only positions with a slot)
      flex             [(eligible_positions, n)]  flex-type slots, in settings order
      bench            n
      roster_size      all rostered slots except IR (what a team drafts)
      noncore_slots    rostered slots the model prices at the floor (IDP, P, HC, ...)
      starter_targets  {pos: starters + flex share}  — the replacement baseline
      roster_targets   {pos: starter target + bench share}  — what a team ends up rostering
    Positions with no fixed slot and no flex eligibility are absent from both
    target dicts (e.g. D/ST in a league without a D/ST slot).
    """
    slots = {k: int(v) for k, v in (slots or {}).items() if v and int(v) > 0}
    fixed = {pos: n for pos, n in slots.items() if pos in CORE_POSITIONS}
    flex = [(FLEX_ELIGIBILITY[name], n) for name, n in slots.items() if name in FLEX_ELIGIBILITY]
    bench = slots.get("BE", 0)
    roster_size = sum(n for pos, n in slots.items() if pos != "IR")
    noncore = sum(n for pos, n in slots.items() if pos in NONCORE_SLOTS)

    starter = {}
    for pos, n in fixed.items():
        starter[pos] = float(n)
    for name, n in slots.items():
        if name in FLEX_SHARE:
            for pos, share in FLEX_SHARE[name].items():
                starter[pos] = starter.get(pos, 0.0) + n * share

    roster = {}
    for pos, target in starter.items():
        roster[pos] = target + bench * BENCH_SHARE.get(pos, 0.0)

    return {
        "fixed": fixed,
        "flex": flex,
        "bench": bench,
        "roster_size": roster_size,
        "noncore_slots": noncore,
        "core_size": roster_size - noncore,
        "starter_targets": starter,
        "roster_targets": roster,
    }


def profile_for(league):
    """`slot_profile` from a league object, or None without slot settings."""
    slots = getattr(getattr(league, "settings", None), "position_slot_counts", None)
    return slot_profile(slots) if slots else None


def profile_from_targets(targets, roster_size=None):
    """A profile for callers that only have legacy per-position targets."""
    targets = {pos: float(t) for pos, t in (targets or {}).items() if t}
    size = roster_size or max(1, round(sum(targets.values())))
    return {
        "fixed": {pos: int(round(t)) for pos, t in targets.items()},
        "flex": [],
        "bench": 0,
        "roster_size": size,
        "noncore_slots": 0,
        "core_size": size,
        "starter_targets": dict(targets),
        "roster_targets": dict(targets),
    }


def optimal_lineup(players, profile):
    """Best starting lineup for a list of {"position", "value"} players.

    Fixed slots first (top values per position), then flex-type slots, the
    narrowest eligibility first, from what is left. Returns
    (starters, bench) as lists of player dicts. Players are tracked by
    identity, so two players with identical dicts are still two players.
    """
    remaining = sorted(players, key=lambda p: p.get("value", 0.0), reverse=True)
    starters = []

    def take(pred, n):
        nonlocal remaining
        chosen, rest = [], []
        for p in remaining:
            if len(chosen) < n and pred(p):
                chosen.append(p)
            else:
                rest.append(p)
        remaining = rest
        return chosen

    for pos, n in profile["fixed"].items():
        starters += take(lambda p, pos=pos: p.get("position") == pos, n)
    for eligible, n in sorted(profile["flex"], key=lambda f: len(f[0])):
        starters += take(lambda p, el=eligible: p.get("position") in el, n)
    return starters, remaining


def optimal_lineup_value(players, profile):
    starters, _ = optimal_lineup(players, profile)
    return sum(p.get("value", 0.0) for p in starters)


def team_context_value(players, profile, bench_weights=BENCH_WEIGHTS):
    """Lineup points plus a small insurance credit for the best bench players.

    Bench insurance only counts positions that can actually start (core
    skill positions), weighted down the bench so depth matters but never
    outweighs a starter.
    """
    starters, bench = optimal_lineup(players, profile)
    total = sum(p.get("value", 0.0) for p in starters)
    skill = [p for p in bench if p.get("position") in ("QB", "RB", "WR", "TE")]
    skill.sort(key=lambda p: p.get("value", 0.0), reverse=True)
    for weight, p in zip(bench_weights, skill):
        total += weight * p.get("value", 0.0)
    return total


def marginal_value(players, candidate, profile):
    """Points the candidate adds to a roster's lineup-plus-bench value."""
    return team_context_value(players + [candidate], profile) - team_context_value(players, profile)
