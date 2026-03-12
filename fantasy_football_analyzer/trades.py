"""Trade analysis and recommendations for fantasy football.

Provides:
- Trade fairness evaluation based on player value
- Trade recommendations targeting positional weaknesses
- Rest-of-season outlook comparisons
- Trade history analysis for your league
"""

from collections import defaultdict


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
        })

    # Sort by total points within each position
    for pos in by_position:
        by_position[pos].sort(key=lambda x: x["total_points"], reverse=True)

    strengths = {}
    starter_counts = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1}

    for pos, players in by_position.items():
        num_starters = starter_counts.get(pos, 1)
        starters = players[:num_starters]
        bench = players[num_starters:]

        total = sum(p["total_points"] for p in players)
        starter_total = sum(p["total_points"] for p in starters)

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
                give_value += player.total_points
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
                receive_value += player.total_points
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


def find_trade_targets(my_team, league, max_suggestions=10):
    """Find beneficial trade opportunities.

    Identifies positions of need for your team and surplus, then finds
    other teams with the inverse needs to suggest mutually beneficial trades.
    """
    my_needs = identify_team_needs(my_team, league)
    my_strength = evaluate_roster_strength(my_team)

    # Find positions where we have surplus (bench depth with good players)
    surplus_positions = []
    for pos, data in my_strength.items():
        if data["depth"] >= 3 and data["bench"]:
            surplus_positions.append((pos, data["bench"][0]))  # best bench player

    # Find positions of need (positive deficit)
    need_positions = [n for n in my_needs if n["deficit"] > 0]

    suggestions = []
    for other_team in league.teams:
        if other_team.team_id == my_team.team_id:
            continue

        other_needs = identify_team_needs(other_team, league)
        other_strength = evaluate_roster_strength(other_team)

        # Look for complementary needs
        for my_need in need_positions[:3]:
            need_pos = my_need["position"]
            other_has = other_strength.get(need_pos, {})

            if not other_has.get("bench"):
                continue

            # Check if other team needs what we have surplus of
            for surplus_pos, surplus_player in surplus_positions:
                other_need_for_surplus = next(
                    (n for n in other_needs if n["position"] == surplus_pos and n["deficit"] > 0),
                    None,
                )
                if not other_need_for_surplus:
                    continue

                target_player = other_has["bench"][0]
                suggestions.append({
                    "trade_partner": other_team.team_name,
                    "give_player": surplus_player["name"],
                    "give_position": surplus_pos,
                    "give_points": surplus_player["total_points"],
                    "receive_player": target_player["name"],
                    "receive_position": need_pos,
                    "receive_points": target_player["total_points"],
                    "reason": f"You need {need_pos}, they need {surplus_pos}",
                })

    suggestions.sort(key=lambda x: x["receive_points"], reverse=True)
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
