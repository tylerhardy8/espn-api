"""FAAB (free-agent acquisition budget) modeling.

Every team gets one bank account for the season; waiver claims are blind
bids. The questions that matter: what is this pickup worth to *my* lineup,
how much would the rivals who also need him bid, and what does that imply
I should bid given how much season is left. Priors are used until the
league produces its own winning-bid history, which is then blended in.
"""

from collections import defaultdict

SEASON_WEEKS = 17
# Share of a team's REMAINING budget a pickup of each tier typically draws.
# Tiers are points over the replacement-level free agent at the position.
TIER_SHARES = [
    # (min_points_over_replacement, tier name, low share, high share)
    (60.0, "league-winner", 0.35, 0.60),
    (35.0, "starter", 0.15, 0.30),
    (18.0, "upgrade", 0.05, 0.12),
    (0.0, "depth", 0.00, 0.03),
]
MIN_BID = 1
REPLACEMENT_RANK = 5   # the Nth-best free agent at a position is "replacement"


def faab_state(league):
    """Budget and per-team remaining FAAB (sorted, most cash first)."""
    budget = int(getattr(league.settings, "acquisition_budget", 0) or 0)
    using = bool(getattr(league.settings, "faab", False))
    teams = []
    for t in league.teams:
        spent = int(getattr(t, "acquisition_budget_spent", 0) or 0)
        teams.append({"team": t.team_name, "team_id": t.team_id, "spent": spent,
                      "remaining": max(0, budget - spent)})
    teams.sort(key=lambda x: -x["remaining"])
    return {"enabled": using, "budget": budget, "teams": teams}


def bid_history(league, size=200):
    """Winning waiver bids this season: [{"player", "team", "bid"}]."""
    out = []
    try:
        acts = league.recent_activity(size=size)
    except Exception:
        return out
    for a in acts:
        for team, action, player, bid in getattr(a, "actions", []):
            if action == "WAIVER ADDED" and bid:
                out.append({"player": getattr(player, "name", str(player)),
                            "team": getattr(team, "team_name", str(team)), "bid": int(bid)})
    return out


def _tier(points_over_repl):
    for floor, name, lo, hi in TIER_SHARES:
        if points_over_repl >= floor:
            return name, lo, hi
    return "depth", 0.0, 0.03


def replacement_levels(free_agents):
    """{pos: ROS points of the Nth-best free agent} from a list of
    {"position", "ros"} dicts."""
    by_pos = defaultdict(list)
    for fa in free_agents:
        by_pos[fa["position"]].append(fa["ros"])
    out = {}
    for pos, vals in by_pos.items():
        vals.sort(reverse=True)
        out[pos] = vals[min(REPLACEMENT_RANK, len(vals)) - 1]
    return out


def league_aggressiveness(history, budget, default=1.0):
    """How this league bids relative to the priors: median winning bid as a
    share of budget for meaningful claims (>= $5), scaled against the
    'upgrade' tier midpoint. 1.0 = as the priors expect."""
    meaningful = sorted(b["bid"] for b in history if b["bid"] >= 5)
    if len(meaningful) < 4 or not budget:
        return default
    median = meaningful[len(meaningful) // 2] / budget
    expected = 0.085  # upgrade-tier midpoint share of a full budget
    return max(0.5, min(2.0, median / expected))


def suggest_bid(player, my_team_name, league, free_agents, my_marginal=None,
                rival_needs=None, history=None, current_week=None):
    """Suggested FAAB bid for a free agent.

    player:       {"name", "position", "ros"} (rest-of-season points)
    free_agents:  the current free-agent list as {"position", "ros"} dicts
    my_marginal:  lineup points the player adds to my roster (None = unknown)
    rival_needs:  {team_name: set(positions with lineup holes)} (optional)
    history:      bid_history() output (optional)
    Returns {"bid", "low", "high", "tier", "expected_rival", "reason"}.
    """
    state = faab_state(league)
    budget = state["budget"] or 100
    mine = next((t for t in state["teams"] if t["team"].lower() == my_team_name.lower()), None)
    my_remaining = mine["remaining"] if mine else budget
    repl = replacement_levels(free_agents)
    over = max(0.0, player["ros"] - repl.get(player["position"], 0.0))
    tier, lo, hi = _tier(over)
    aggr = league_aggressiveness(history or [], budget)

    # Season clock: money left late in the year is worth less, so shares rise
    week = int(current_week or getattr(league, "current_week", 1) or 1)
    weeks_left = max(1, SEASON_WEEKS - week + 1)
    clock = 1.0 + 0.6 * (1 - weeks_left / SEASON_WEEKS)

    # Rivals: the ones with a hole at the position and the cash to fill it
    rivals = []
    for t in state["teams"]:
        if t["team"].lower() == my_team_name.lower():
            continue
        needs = (rival_needs or {}).get(t["team"], set())
        needy = player["position"] in needs or "FLEX" in needs
        share = hi if needy else lo
        rivals.append((t["team"], int(round(t["remaining"] * share * aggr * clock))))
    rivals.sort(key=lambda r: -r[1])
    expected_rival = rivals[0][1] if rivals else 0

    worth_it = my_marginal is None or my_marginal > 0
    if not worth_it or tier == "depth" and (my_marginal is not None and my_marginal < 5):
        bid = MIN_BID if worth_it else 0
        reason = "depth add; don't chase" if worth_it else "no lineup gain for you"
    else:
        low = max(MIN_BID, int(round(my_remaining * lo * aggr * clock)))
        high = max(low, int(round(my_remaining * hi * aggr * clock)))
        # Beat the likeliest rival by a dollar, inside my tier band
        bid = max(low, min(high, expected_rival + 1))
        bid = min(bid, my_remaining)
        reason = (f"{tier}: +{over:.0f} pts over the replacement {player['position']}; "
                  f"top rival ({rivals[0][0] if rivals else '-'}) likely ~${expected_rival}")
        if my_marginal is not None:
            reason += f"; adds {my_marginal:+.0f} to your lineup"
    return {
        "bid": int(bid), "low": int(max(MIN_BID, my_remaining * lo * aggr * clock)) if worth_it else 0,
        "high": int(max(MIN_BID, my_remaining * hi * aggr * clock)) if worth_it else 0,
        "tier": tier, "points_over_replacement": round(over, 1),
        "expected_rival": expected_rival, "rivals": rivals[:3],
        "my_remaining": my_remaining, "aggressiveness": round(aggr, 2), "reason": reason,
    }
