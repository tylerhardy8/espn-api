"""Waiver wire pickup recommendations for fantasy football.

Provides:
- Best available free agents ranked by value
- Matchup-aware recommendations for the upcoming week
- Trending players (ownership % changes)
- Bye-week fill-ins
- Streamer recommendations for QB, D/ST, K
"""

from collections import defaultdict


STREAMING_POSITIONS = {"QB", "D/ST", "K", "TE"}


def get_top_free_agents(league, week=None, size=50, position=None):
    """Fetch and rank available free agents.

    Returns a list of player dicts sorted by projected points.
    """
    try:
        players = league.free_agents(week=week, size=size, position=position)
    except Exception:
        return []

    ranked = []
    for player in players:
        ranked.append({
            "name": player.name,
            "player_id": player.playerId,
            "position": player.position,
            "team": player.proTeam,
            "projected_points": round(player.projected_points, 2),
            "points": round(player.points, 2),
            "total_points": round(player.total_points, 2),
            "avg_points": round(player.avg_points, 2),
            "percent_owned": player.percent_owned,
            "percent_started": player.percent_started,
            "pro_opponent": getattr(player, "pro_opponent", ""),
            "pro_pos_rank": getattr(player, "pro_pos_rank", 0),
            "on_bye": player.on_bye_week,
            "injury_status": player.injuryStatus or "Active",
        })

    ranked.sort(key=lambda x: x["projected_points"], reverse=True)
    return ranked


def find_bye_week_fillers(league, team, week=None):
    """Find free agents to fill in for players on bye weeks.

    Returns a dict of position -> list of replacement options.
    """
    if week is None:
        week = league.current_week

    bye_players = []
    for player in team.roster:
        # Check if player has bye week info from box scores
        if player.lineupSlot not in ("BE", "IR"):
            bye_players.append(player)

    fillers = {}
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        agents = get_top_free_agents(league, week=week, size=20, position=pos)
        available = [a for a in agents if not a["on_bye"]]
        if available:
            fillers[pos] = available[:5]

    return fillers


def find_streamers(league, week=None):
    """Find streaming options for volatile positions (QB, TE, D/ST, K).

    Prioritizes favorable matchups (low pro_pos_rank = better matchup).
    """
    streamers = {}
    for pos in STREAMING_POSITIONS:
        agents = get_top_free_agents(league, week=week, size=30, position=pos)
        available = [a for a in agents if not a["on_bye"]]

        # Sort by matchup favorability (lower rank = easier opponent)
        for a in available:
            matchup_bonus = max(0, 16 - a["pro_pos_rank"]) * 0.5 if a["pro_pos_rank"] > 0 else 0
            a["streamer_score"] = round(a["projected_points"] + matchup_bonus, 2)

        available.sort(key=lambda x: x["streamer_score"], reverse=True)
        streamers[pos] = available[:5]

    return streamers


def get_waiver_recommendations(league, my_team_name=None, week=None):
    """Generate personalized waiver wire recommendations.

    Considers team needs, matchups, and player value.
    """
    if week is None:
        week = league.current_week

    all_agents = get_top_free_agents(league, week=week, size=100)

    my_team = None
    if my_team_name:
        for team in league.teams:
            if team.team_name.lower() == my_team_name.lower():
                my_team = team
                break

    recommendations = []
    if my_team:
        # Assess roster by position
        roster_by_pos = defaultdict(list)
        for player in my_team.roster:
            roster_by_pos[player.position].append(player)

        weakest_at_pos = {}
        for pos, players in roster_by_pos.items():
            if players:
                weakest = min(players, key=lambda p: p.total_points)
                weakest_at_pos[pos] = {
                    "name": weakest.name,
                    "total_points": weakest.total_points,
                    "avg_points": weakest.avg_points,
                }

        for agent in all_agents:
            if agent["on_bye"]:
                continue

            pos = agent["position"]
            current_weakest = weakest_at_pos.get(pos)
            if not current_weakest:
                continue

            upgrade = round(agent["avg_points"] - current_weakest["avg_points"], 2)
            if upgrade > 0:
                recommendations.append({
                    **agent,
                    "replaces": current_weakest["name"],
                    "replaces_avg": round(current_weakest["avg_points"], 2),
                    "upgrade_per_week": upgrade,
                })

        recommendations.sort(key=lambda x: x["upgrade_per_week"], reverse=True)
    else:
        recommendations = all_agents

    return recommendations


def format_waiver_report(league, my_team_name=None, week=None):
    """Generate a formatted waiver wire report."""
    lines = []
    lines.append("=" * 70)
    lines.append("WAIVER WIRE RECOMMENDATIONS")
    lines.append("=" * 70)

    if week is None:
        week = league.current_week
    lines.append(f"Week {week}")

    # Top Free Agents
    top_agents = get_top_free_agents(league, week=week, size=30)
    if top_agents:
        lines.append("\n--- TOP AVAILABLE FREE AGENTS ---")
        lines.append(
            f"{'Rank':>5} {'Player':<25} {'Pos':<5} {'Team':<5} {'Proj':>6} "
            f"{'Avg':>6} {'Own%':>6} {'Opp':>5} {'Rank':>5}"
        )
        lines.append("-" * 73)
        for i, agent in enumerate(top_agents[:20], 1):
            lines.append(
                f"{i:>5} {agent['name']:<25} {agent['position']:<5} {agent['team']:<5} "
                f"{agent['projected_points']:>6.1f} {agent['avg_points']:>6.1f} "
                f"{agent['percent_owned']:>5.1f}% {agent['pro_opponent']:>5} "
                f"{agent['pro_pos_rank']:>5}"
            )

    # Streaming Recommendations
    streamers = find_streamers(league, week=week)
    if any(streamers.values()):
        lines.append("\n--- STREAMING RECOMMENDATIONS ---")
        for pos in ["QB", "TE", "D/ST", "K"]:
            if pos in streamers and streamers[pos]:
                lines.append(f"\n  Best {pos} Streamers:")
                for s in streamers[pos][:3]:
                    lines.append(
                        f"    {s['name']:<25} vs {s['pro_opponent']:<5} "
                        f"Proj: {s['projected_points']:.1f}  Score: {s['streamer_score']:.1f}"
                    )

    # Personalized Recommendations
    if my_team_name:
        recs = get_waiver_recommendations(league, my_team_name, week=week)
        if recs:
            lines.append(f"\n--- RECOMMENDED PICKUPS FOR YOUR TEAM ---")
            lines.append(
                f"{'Player':<25} {'Pos':<5} {'Proj':>6} {'Avg':>6} "
                f"{'Replaces':<20} {'Upgrade':>8}"
            )
            lines.append("-" * 73)
            for rec in recs[:10]:
                lines.append(
                    f"{rec['name']:<25} {rec['position']:<5} {rec['projected_points']:>6.1f} "
                    f"{rec['avg_points']:>6.1f} {rec.get('replaces', 'N/A'):<20} "
                    f"+{rec.get('upgrade_per_week', 0):>7.1f}"
                )

    return "\n".join(lines)
