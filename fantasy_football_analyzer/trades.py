"""Trade analysis and recommendations for fantasy football.

Provides:
- Trade fairness evaluation based on player value
- Trade recommendations targeting positional weaknesses
- Rest-of-season outlook comparisons
- Trade history analysis for your league
"""

from collections import defaultdict


def _player_value(player):
    """A player's trade value: actual points once games are played,
    season projection before that (post-draft, totals are all zero)."""
    total = getattr(player, "total_points", 0) or 0
    if total > 0:
        return total
    return getattr(player, "projected_total_points", 0) or 0


def evaluate_roster_strength(team):
    """Evaluate a team's roster by position, returning strength scores.

    Returns a dict with position -> {"starters": [...], "bench": [...], "total_points": float}
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
            "value": round(_player_value(player), 1),
        })

    # Sort by trade value within each position
    for pos in by_position:
        by_position[pos].sort(key=lambda x: x["value"], reverse=True)

    strengths = {}
    starter_counts = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1}

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
    team_strength = evaluate_roster_strength(team)

    # Get league averages by position
    league_avgs = defaultdict(list)
    for t in league.teams:
        strengths = evaluate_roster_strength(t)
        for pos, data in strengths.items():
            league_avgs[pos].append(data["starter_points"])

    avg_by_pos = {}
    for pos, points_list in league_avgs.items():
        avg_by_pos[pos] = sum(points_list) / len(points_list) if points_list else 0

    needs = []
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
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


def evaluate_trade(team_a, players_give, team_b, players_receive):
    """Evaluate trade fairness between two teams.

    players_give: list of player names from team_a
    players_receive: list of player names from team_b
    Returns analysis dict with fairness assessment.
    """
    give_value = 0
    give_details = []
    for name in players_give:
        for player in team_a.roster:
            if player.name.lower() == name.lower():
                give_value += _player_value(player)
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
                receive_value += _player_value(player)
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
MIN_MY_GAIN = 5.0      # a trade must improve my starting lineup by this much
MIN_THEIR_GAIN = 8.0   # ...and meaningfully improve theirs, or they'd never accept
UNTOUCHABLE_COUNT = 2  # never offer my top-N VORP players — nobody trades those
STRONG_SURPLUS = 15.0  # deficit below -this = position of strength: don't buy there
WEAK_DEFICIT = 15.0    # deficit above this = position of weakness: don't sell from it


def _team_players(team):
    return [
        {"name": p.name, "player_id": p.playerId, "position": p.position,
         "value": round(_player_value(p), 1)}
        for p in team.roster
    ]


def lineup_value(players):
    """Total value of the optimal starting lineup (fixed slots + flex)."""
    by_pos = defaultdict(list)
    for p in players:
        by_pos[p["position"]].append(p["value"])
    total = 0.0
    flex_pool = []
    for pos, values in by_pos.items():
        values.sort(reverse=True)
        n = LINEUP_SLOTS.get(pos, 0)
        total += sum(values[:n])
        if pos in FLEX_POSITIONS:
            flex_pool.extend(values[n:])
    flex_pool.sort(reverse=True)
    total += sum(flex_pool[:FLEX_COUNT])
    return total


def _swap_net(players, base_value, give_ids, get_players):
    """Change in optimal-lineup value after sending give_ids for get_players."""
    after = [p for p in players if p["player_id"] not in give_ids] + list(get_players)
    return lineup_value(after) - base_value


def _bench_locked(players):
    """Players contributing nothing to the optimal lineup (droppable depth)."""
    base = lineup_value(players)
    locked = []
    for p in players:
        if p["position"] not in TRADE_CORE_POSITIONS:
            continue
        without = [q for q in players if q["player_id"] != p["player_id"]]
        if lineup_value(without) >= base - 0.01:  # removing them costs nothing
            locked.append(p)
    return sorted(locked, key=lambda p: p["value"], reverse=True)


def _replacement_levels(league):
    """Per-position replacement value: what the Nth-ranked league-wide player
    scores, where N = starting slots (+ flex share) across all teams. Trade
    value is points over THIS, not raw points — a 287-pt QB is worth far less
    than a 281-pt RB when waiver QBs score 250 and waiver RBs score 150."""
    pools = defaultdict(list)
    for t in league.teams:
        for p in t.roster:
            if p.position in TRADE_CORE_POSITIONS:
                pools[p.position].append(_player_value(p))
    flex_share = {"RB": 0.5, "WR": 0.4, "TE": 0.1}
    n = len(league.teams)
    repl = {}
    for pos, vals in pools.items():
        vals.sort(reverse=True)
        idx = max(1, int(n * (LINEUP_SLOTS.get(pos, 1) + flex_share.get(pos, 0))))
        repl[pos] = vals[idx - 1] if idx <= len(vals) else vals[-1]
    return repl


def find_trade_matches(my_team, league, max_partners=6, max_proposals_per_partner=3):
    """Scan every opposing roster for trades BOTH sides would actually accept.

    A proposal survives only if each team's optimal starting lineup improves
    (net of what leaves it): I convert depth into a starter upgrade, and so
    do they. Includes 2-for-1 consolidations — sending two depth pieces for
    one starter — which is how a depth-rich team realistically upgrades.
    My top players by value-over-replacement are never offered.
    """
    replacement = _replacement_levels(league)
    my_deficits = {n["position"]: n["deficit"] for n in identify_team_needs(my_team, league)}

    mine = _team_players(my_team)
    my_base = lineup_value(mine)
    my_core = sorted(
        (p for p in mine if p["position"] in TRADE_CORE_POSITIONS),
        key=lambda p: p["value"], reverse=True,
    )[:8]
    untouchable_ids = {
        p["player_id"]
        for p in sorted(
            my_core,
            key=lambda p: p["value"] - replacement.get(p["position"], 0),
            reverse=True,
        )[:UNTOUCHABLE_COUNT]
    }
    # Package pieces: bench-locked depth, but never from a weak position —
    # draining a thin spot's insurance for an upgrade elsewhere is how you
    # lose in November.
    my_depth = [
        p for p in _bench_locked(mine)
        if my_deficits.get(p["position"], 0) <= WEAK_DEFICIT
    ][:4]

    partners = []
    for other in league.teams:
        if other.team_id == my_team.team_id:
            continue
        theirs = _team_players(other)
        their_base = lineup_value(theirs)
        their_core = sorted(
            (p for p in theirs if p["position"] in TRADE_CORE_POSITIONS),
            key=lambda p: p["value"], reverse=True,
        )[:8]

        proposals = []

        def consider(give_list, get):
            # Strategy gates: don't buy where I'm already clearly strong —
            # by league-average deficit OR by my starter already carrying
            # healthy value over replacement (a good QB stays a good QB even
            # in a league where two teams hoard elite ones).
            get_pos = get["position"]
            if my_deficits.get(get_pos, 0) < -STRONG_SURPLUS:
                return
            my_best_at = max(
                (p["value"] for p in mine if p["position"] == get_pos), default=0,
            )
            if my_best_at - replacement.get(get_pos, 0) >= 20:
                return
            give_ids = {g["player_id"] for g in give_list}
            my_net = _swap_net(mine, my_base, give_ids, [get])
            # Efficiency floor: moving bigger assets must return bigger gains —
            # nobody trades a 250-pt player to improve their lineup by 6.
            required = max(MIN_MY_GAIN, 0.10 * sum(g["value"] for g in give_list))
            if my_net < required:
                return
            their_net = _swap_net(theirs, their_base, {get["player_id"]}, give_list)
            if their_net < MIN_THEIR_GAIN:
                return
            proposals.append({
                "give_players": [g["name"] for g in give_list],
                "give_positions": [g["position"] for g in give_list],
                "give_points": round(sum(g["value"] for g in give_list), 1),
                "receive_player": get["name"],
                "receive_position": get["position"],
                "receive_points": get["value"],
                "my_net": round(my_net, 1),
                "their_net": round(their_net, 1),
                "give_total_value": round(sum(g["value"] for g in give_list), 1),
                "reason": (
                    f"Both starting lineups improve: you {my_net:+.0f}, "
                    f"they {their_net:+.0f}"
                ),
            })

        # 1-for-1: my non-untouchable core pieces for any of theirs
        for give in my_core:
            if give["player_id"] in untouchable_ids:
                continue
            if my_deficits.get(give["position"], 0) > WEAK_DEFICIT:
                continue  # don't sell from a position of weakness
            for get in their_core:
                if get["position"] == give["position"]:
                    continue  # lateral same-position swaps rarely help both
                consider([give], get)

        # 2-for-1: two of my bench-locked depth pieces for one of their starters
        for i in range(len(my_depth)):
            for j in range(i + 1, len(my_depth)):
                pair = [my_depth[i], my_depth[j]]
                for get in their_core:
                    consider(pair, get)

        if not proposals:
            continue
        # Prefer the biggest gain for me achieved with the least outgoing value
        proposals.sort(key=lambda p: (-p["my_net"], p["give_total_value"]))
        deduped, seen = [], set()
        for p in proposals:
            key = (tuple(p["give_players"]), p["receive_player"])
            if key not in seen:
                seen.add(key)
                deduped.append(p)

        their_needs = [n["position"] for n in identify_team_needs(other, league)
                       if n["deficit"] > 0 and n["position"] in TRADE_CORE_POSITIONS]
        partners.append({
            "partner": other.team_name,
            "record": f"{other.wins}-{other.losses}",
            "fit_score": round(max(p["my_net"] + p["their_net"] for p in deduped), 1),
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
            })
    suggestions.sort(key=lambda x: -x["my_net"])
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
