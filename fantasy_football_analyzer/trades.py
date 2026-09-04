"""Trade analysis and recommendations for fantasy football.

Provides:
- Trade fairness evaluation based on player value
- Trade recommendations targeting positional weaknesses
- Rest-of-season outlook comparisons
- Trade history analysis for your league
"""

from collections import defaultdict


def _player_value(player, league=None):
    """A player's trade value: rest-of-season points.

    Pre-season this is the season projection. In-season it prorates the
    projection over the weeks left, blends in actual pace, weights the
    league's playoff weeks, and scales by availability (see ros.py) — so a
    player who banked points and is now hurt is worth what he'll score, not
    what he scored.
    """
    if league is not None:
        try:
            from .ros import ros_projection
            return ros_projection(player, league)
        except Exception:
            pass
    total = getattr(player, "total_points", 0) or 0
    if total > 0:
        return total
    return getattr(player, "projected_total_points", 0) or 0


def _profile(league):
    """Lineup slot profile for a league (legacy LINEUP_SLOTS when unknown)."""
    try:
        from .auction import league_profile
        prof = league_profile(league) if league is not None else None
    except Exception:
        prof = None
    if prof:
        return prof
    from .lineup import profile_from_targets
    prof = profile_from_targets(dict(LINEUP_SLOTS))
    prof["flex"] = [(FLEX_POSITIONS, FLEX_COUNT)]
    return prof


def evaluate_roster_strength(team, league=None):
    """Evaluate a team's roster by position, returning strength scores.

    Returns a dict with position -> {"starters": [...], "bench": [...], "total_points": float}
    Starter counts come from the league's real lineup slots when a league is given.
    """
    by_position = defaultdict(list)
    for player in team.roster:
        by_position[player.position].append({
            "name": player.name,
            "player_id": player.playerId,
            "total_points": player.total_points,
            "projected_points": player.projected_total_points,
            "avg_points": player.avg_points,
            "slot": player.lineupSlot,
            "value": round(_player_value(player, league), 1),
        })

    # Sort by trade value within each position
    for pos in by_position:
        by_position[pos].sort(key=lambda x: x["value"], reverse=True)

    strengths = {}
    starter_counts = dict(_profile(league)["fixed"]) if league is not None else \
        {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1}

    for pos, players in by_position.items():
        num_starters = starter_counts.get(pos, 1)
        starters = players[:num_starters]
        bench = players[num_starters:]

        total = sum(p["value"] for p in players)
        starter_total = sum(p["value"] for p in starters)

        strengths[pos] = {
            "starters": starters,
            "bench": bench,
            "total_points": round(total, 2),
            "starter_points": round(starter_total, 2),
            "depth": len(players),
        }

    return strengths


def identify_team_needs(team, league):
    """Identify positions where a team is weakest relative to the league.

    Returns positions sorted by need (most needed first).
    """
    team_strength = evaluate_roster_strength(team, league)

    # Get league averages by position
    league_avgs = defaultdict(list)
    for t in league.teams:
        strengths = evaluate_roster_strength(t, league)
        for pos, data in strengths.items():
            league_avgs[pos].append(data["starter_points"])

    avg_by_pos = {}
    for pos, points_list in league_avgs.items():
        avg_by_pos[pos] = sum(points_list) / len(points_list) if points_list else 0

    needs = []
    positions = [p for p in ("QB", "RB", "WR", "TE", "D/ST", "K") if p in _profile(league)["fixed"]] \
        or ["QB", "RB", "WR", "TE", "D/ST", "K"]
    for pos in positions:
        team_pts = team_strength.get(pos, {}).get("starter_points", 0)
        league_avg = avg_by_pos.get(pos, 0)
        deficit = round(league_avg - team_pts, 2)

        needs.append({
            "position": pos,
            "team_points": round(team_pts, 2),
            "league_avg": round(league_avg, 2),
            "deficit": deficit,
            "depth": team_strength.get(pos, {}).get("depth", 0),
        })

    needs.sort(key=lambda x: x["deficit"], reverse=True)
    return needs


def evaluate_trade(team_a, players_give, team_b, players_receive, league=None):
    """Evaluate trade fairness between two teams.

    players_give: list of player names from team_a
    players_receive: list of player names from team_b
    With a league, each player counts as value over positional replacement
    (so a 2-for-1 of bench pieces doesn't beat one star). Returns analysis
    dict with fairness assessment.
    """
    repl = {}
    if league is not None:
        try:
            repl = _replacement_levels(league)
        except Exception:
            repl = {}

    def worth(player):
        return max(0.0, _player_value(player, league) - repl.get(player.position, 0.0))

    give_value = 0
    give_details = []
    for name in players_give:
        for player in team_a.roster:
            if player.name.lower() == name.lower():
                give_value += worth(player)
                give_details.append({
                    "name": player.name,
                    "position": player.position,
                    "total_points": round(player.total_points, 2),
                    "projected_points": round(player.projected_total_points, 2),
                    "avg_points": round(player.avg_points, 2),
                })
                break

    receive_value = 0
    receive_details = []
    for name in players_receive:
        for player in team_b.roster:
            if player.name.lower() == name.lower():
                receive_value += worth(player)
                receive_details.append({
                    "name": player.name,
                    "position": player.position,
                    "total_points": round(player.total_points, 2),
                    "projected_points": round(player.projected_total_points, 2),
                    "avg_points": round(player.avg_points, 2),
                })
                break

    diff = round(receive_value - give_value, 2)
    pct_diff = round((diff / give_value) * 100, 1) if give_value else 0

    if abs(pct_diff) < 10:
        verdict = "FAIR"
    elif pct_diff > 10:
        verdict = f"FAVORS {team_a.team_name}" if diff > 0 else f"FAVORS {team_b.team_name}"
    else:
        verdict = f"FAVORS {team_b.team_name}" if diff < 0 else f"FAVORS {team_a.team_name}"

    return {
        "giving": give_details,
        "receiving": receive_details,
        "give_value": round(give_value, 2),
        "receive_value": round(receive_value, 2),
        "difference": diff,
        "pct_difference": pct_diff,
        "verdict": verdict,
    }


TRADE_CORE_POSITIONS = ("QB", "RB", "WR", "TE")
LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1}
FLEX_POSITIONS = ("RB", "WR", "TE")
FLEX_COUNT = 1

# Bench pieces carry insurance value (injuries, byes) with diminishing
# returns by depth: your first backup at a position matters, your fourth is
# roster clay. This is what makes "worthless" depth not actually free.
BENCH_WEIGHTS = (0.18, 0.10, 0.05, 0.02)


def _team_players(team, league=None):
    return [
        {"name": p.name, "player_id": p.playerId, "position": p.position,
         "value": round(_player_value(p, league), 1)}
        for p in team.roster
    ]


def lineup_value(players, profile=None):
    """Total value of the optimal starting lineup (fixed slots + flex)."""
    from .lineup import optimal_lineup_value
    return optimal_lineup_value(players, profile or _profile(None))


def team_context_value(players, profile=None):
    """What this roster is actually worth to its owner: the optimal starting
    lineup, plus depth weighted for insurance value (diminishing by depth).

    This is the engine's core quantity. A player's value *to a specific team*
    is the change in this number — which differs between rosters, and that
    difference is exactly why trades happen at all. Slots come from the
    league's real lineup (lineup.slot_profile) when a profile is given.
    """
    from .lineup import team_context_value as _ctx
    return _ctx(players, profile or _profile(None), BENCH_WEIGHTS)


def _context_after(players, give_ids, get_players, profile=None):
    after = [p for p in players if p["player_id"] not in give_ids] + list(get_players)
    return team_context_value(after, profile)


def _replacement_levels(league):
    """Per-position replacement value: what the Nth-ranked league-wide player
    scores, where N = starting slots (+ flex share) across all teams. Trade
    value is points over THIS, not raw points — a 287-pt QB is worth far less
    than a 281-pt RB when waiver QBs score 250 and waiver RBs score 150."""
    pools = defaultdict(list)
    for t in league.teams:
        for p in t.roster:
            if p.position in TRADE_CORE_POSITIONS:
                pools[p.position].append(_player_value(p, league))
    starter_targets = _profile(league)["starter_targets"]
    n = len(league.teams)
    repl = {}
    for pos, vals in pools.items():
        vals.sort(reverse=True)
        idx = max(1, int(round(n * starter_targets.get(pos, 1.0))))
        repl[pos] = vals[idx - 1] if idx <= len(vals) else vals[-1]
    return repl


def _market_value(entry, pool_entry, replacement):
    """What the league consensus thinks a player is worth — the currency
    owners judge fairness in. Uses the valuation pool's blended dollar value
    (ESPN crowd + expert consensus + projections) when available; falls back
    to value over positional replacement."""
    if pool_entry and pool_entry.get("value"):
        return max(1.0, float(pool_entry["value"]))
    return max(1.0, entry["value"] - replacement.get(entry["position"], 0))


def _acceptance(their_gain, market_ratio):
    """How plausibly the other owner says yes. Two soft factors:
    - their roster must not get worse (contextual gain), scaled up as it grows
    - the deal must look fair-to-winning in consensus terms (owners anchor on
      market value, not on your projections; they especially like receiving
      slightly more market value than they send)."""
    if their_gain < -2:
        return 0.0
    gain_factor = min(1.0, 0.35 + max(0.0, their_gain) / 25.0)
    if market_ratio >= 0.95:
        market_factor = 1.0
    elif market_ratio >= 0.7:
        market_factor = (market_ratio - 0.7) / 0.25
    else:
        market_factor = 0.0
    return gain_factor * market_factor


def find_trade_matches(my_team, league, pool=None, max_partners=6,
                       max_proposals_per_partner=3):
    """Propose trades the way leagues actually make them.

    A trade exists when the same players are worth different amounts to
    different rosters (team_context_value: optimal lineup + depth insurance).
    A proposal is scored by MY contextual gain, weighted by how plausibly the
    other owner accepts — their own contextual gain, and fairness in MARKET
    terms (consensus value), because owners judge offers against consensus,
    not against my projections.

    Nothing is hard-coded: stars stay put because their contextual value is
    too high for fair returns to beat (unless someone genuinely overpays —
    which will then surface, as it should); thin positions don't get drained
    because their depth carries insurance value; lopsided asks die in the
    acceptance term.
    """
    replacement = _replacement_levels(league)
    pool = pool or {}

    def market(p):
        return _market_value(p, pool.get(p["player_id"]), replacement)

    profile = _profile(league)
    mine = _team_players(my_team, league)
    my_base = team_context_value(mine, profile)

    def candidates(players):
        core = sorted(
            (p for p in players if p["position"] in TRADE_CORE_POSITIONS),
            key=lambda p: p["value"], reverse=True,
        )[:10]
        # Package pairs from the lower-marginal half — the pieces an owner
        # would actually double up to move
        base = team_context_value(players, profile)
        marginals = sorted(
            core,
            key=lambda p: base - team_context_value(
                [q for q in players if q["player_id"] != p["player_id"]]
            , profile),
        )[:6]
        pairs = []
        for i in range(len(marginals)):
            for j in range(i + 1, len(marginals)):
                pairs.append([marginals[i], marginals[j]])
        singles = [[p] for p in core]
        return singles + pairs[:12]

    my_packages = candidates(mine)

    partners = []
    for other in league.teams:
        if other.team_id == my_team.team_id:
            continue
        theirs = _team_players(other, league)
        their_base = team_context_value(theirs, profile)
        their_packages = candidates(theirs)

        proposals = []
        for give_list in my_packages:
            give_ids = {g["player_id"] for g in give_list}
            give_market = sum(market(g) for g in give_list)
            for get_list in their_packages:
                if len(give_list) > 1 and len(get_list) > 1:
                    continue  # keep proposals readable: no 2-for-2
                get_ids = {g["player_id"] for g in get_list}
                get_market = sum(market(g) for g in get_list)

                my_gain = _context_after(mine, give_ids, get_list, profile) - my_base
                if my_gain < 3:
                    continue
                their_gain = _context_after(theirs, get_ids, give_list, profile) - their_base
                # Ratio of market value THEY receive vs give
                ratio = give_market / max(get_market, 1e-6)
                accept = _acceptance(their_gain, ratio)
                score = my_gain * accept
                if score < 3:
                    continue
                proposals.append({
                    "give_players": [g["name"] for g in give_list],
                    "give_positions": [g["position"] for g in give_list],
                    "receive_players": [g["name"] for g in get_list],
                    "receive_positions": [g["position"] for g in get_list],
                    "give_points": round(sum(g["value"] for g in give_list), 1),
                    "receive_points": round(sum(g["value"] for g in get_list), 1),
                    "my_net": round(my_gain, 1),
                    "their_net": round(their_gain, 1),
                    "market_ratio": round(ratio, 2),
                    "score": round(score, 1),
                    "reason": (
                        f"Your roster {my_gain:+.0f}, theirs {their_gain:+.0f}; "
                        f"they receive {ratio:.0%} of the consensus value they send"
                    ),
                })

        if not proposals:
            continue
        proposals.sort(key=lambda p: -p["score"])
        deduped, seen = [], set()
        for p in proposals:
            key = (tuple(p["give_players"]), tuple(p["receive_players"]))
            if key not in seen:
                seen.add(key)
                deduped.append(p)

        their_needs = [n["position"] for n in identify_team_needs(other, league)
                       if n["deficit"] > 0 and n["position"] in TRADE_CORE_POSITIONS]
        partners.append({
            "partner": other.team_name,
            "record": f"{other.wins}-{other.losses}",
            "fit_score": deduped[0]["score"],
            "their_needs": their_needs,
            "their_surplus": [],
            "proposals": deduped[:max_proposals_per_partner],
        })

    partners.sort(key=lambda p: p["fit_score"], reverse=True)
    return partners[:max_partners]


def find_trade_targets(my_team, league, max_suggestions=10):
    """Flat list of the best trade proposals across all partners.

    Kept for CLI/report compatibility; the web UI uses find_trade_matches.
    """
    suggestions = []
    for partner in find_trade_matches(my_team, league):
        for p in partner["proposals"]:
            suggestions.append({
                **p,
                "trade_partner": partner["partner"],
                "give_player": " + ".join(p["give_players"]),
                "give_position": "/".join(dict.fromkeys(p["give_positions"])),
                "receive_player": " + ".join(p["receive_players"]),
                "receive_position": "/".join(dict.fromkeys(p["receive_positions"])),
            })
    suggestions.sort(key=lambda x: -x["score"])
    return suggestions[:max_suggestions]


def format_trade_report(league, my_team_name=None):
    """Generate a formatted trade analysis report."""
    lines = []
    lines.append("=" * 70)
    lines.append("TRADE ANALYSIS & RECOMMENDATIONS")
    lines.append("=" * 70)

    # Roster Strength by Team
    lines.append("\n--- ROSTER STRENGTH BY TEAM ---")
    for team in sorted(league.teams, key=lambda t: t.points_for, reverse=True):
        strengths = evaluate_roster_strength(team)
        marker = " <-- YOUR TEAM" if my_team_name and team.team_name.lower() == my_team_name.lower() else ""
        lines.append(f"\n  {team.team_name} ({team.wins}-{team.losses}){marker}")
        for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
            if pos in strengths:
                s = strengths[pos]
                starter_names = ", ".join(p["name"] for p in s["starters"])
                lines.append(f"    {pos:<5} Starter: {s['starter_points']:>7.1f} pts  Depth: {s['depth']}  ({starter_names})")

    # Team-specific analysis
    if my_team_name:
        my_team = None
        for team in league.teams:
            if team.team_name.lower() == my_team_name.lower():
                my_team = team
                break

        if my_team:
            # Positional Needs
            needs = identify_team_needs(my_team, league)
            lines.append(f"\n--- YOUR POSITIONAL NEEDS ({my_team.team_name}) ---")
            lines.append(f"{'Position':<10} {'Your Pts':>9} {'League Avg':>11} {'Deficit':>8} {'Depth':>6}")
            lines.append("-" * 47)
            for need in needs:
                deficit_str = f"+{need['deficit']:.1f}" if need["deficit"] > 0 else f"{need['deficit']:.1f}"
                lines.append(
                    f"{need['position']:<10} {need['team_points']:>9.1f} {need['league_avg']:>11.1f} "
                    f"{deficit_str:>8} {need['depth']:>6}"
                )

            # Trade Suggestions
            suggestions = find_trade_targets(my_team, league)
            if suggestions:
                lines.append(f"\n--- TRADE SUGGESTIONS FOR {my_team.team_name} ---")
                for i, s in enumerate(suggestions, 1):
                    lines.append(f"\n  Trade #{i}:")
                    lines.append(f"    Partner: {s['trade_partner']}")
                    lines.append(f"    Give:    {s['give_player']} ({s['give_position']}, {s['give_points']:.1f} pts)")
                    lines.append(f"    Receive: {s['receive_player']} ({s['receive_position']}, {s['receive_points']:.1f} pts)")
                    lines.append(f"    Reason:  {s['reason']}")

    return "\n".join(lines)
