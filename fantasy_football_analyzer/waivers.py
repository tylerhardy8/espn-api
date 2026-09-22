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
    try:
        from .ros import ros_projection
    except Exception:
        ros_projection = None
    for player in players:
        try:
            season_total = ros_projection(player, league) if ros_projection else None
        except Exception:
            season_total = None
        ranked.append({
            "name": player.name,
            "player_id": player.playerId,
            "position": player.position,
            "team": player.proTeam,
            "projected_points": round(player.projected_points, 2),
            "projected_total": round(season_total if season_total is not None
                                     else (getattr(player, "projected_total_points", 0) or 0), 2),
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
    from .waiver_model import profile_for
    for pos in profile_for(league)["starter_targets"]:
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
    from .waiver_model import profile_for
    eligible = profile_for(league)["starter_targets"]
    for pos in sorted(STREAMING_POSITIONS & set(eligible)):
        agents = get_top_free_agents(league, week=week, size=30, position=pos)
        available = [a for a in agents if not a["on_bye"]]

        # Sort by matchup favorability (lower rank = easier opponent)
        for a in available:
            matchup_bonus = max(0, 16 - a["pro_pos_rank"]) * 0.5 if a["pro_pos_rank"] > 0 else 0
            a["streamer_score"] = round(a["projected_points"] + matchup_bonus, 2)

        available.sort(key=lambda x: x["streamer_score"], reverse=True)
        streamers[pos] = available[:5]

    return streamers


def get_waiver_recommendations(league, my_team_name=None, week=None, team_id=None, config=None):
    """Evaluate the whole roster, including depth and future bye coverage."""
    from .waiver_model import resolve_team
    from .waiver_service import ranked_candidates
    team = resolve_team(league, team_id, my_team_name, config)
    return ranked_candidates(league, team, week or league.current_week)


def format_waiver_report(league, my_team_name=None, week=None, team_id=None, config=None):
    """CLI report uses the same verified, roster-aware model as the web app."""
    from .waiver_service import build_waiver_payload
    payload = build_waiver_payload(league, config, team_id=team_id, team_name=my_team_name, week=week)
    lines = [payload['advisory'], f"Week {payload['week']} · {payload['team']} (ID {payload['team_id']})",
             f"FAAB: ${payload['faab']['mine']['remaining']} of ${payload['faab']['budget']}",
             f"Processing: {payload['processing_time']['value']} ({payload['processing_time']['source']})"]
    for rec in payload['recommendations'][:12]:
        b = rec['faab']
        lines.append(f"{rec['name']} ({rec['position']}): {rec['ros_per_game']:.1f} ROS pts/game; "
                     f"bid estimate ${b['bid']} (${b['low']}-${b['high']}). {rec['reason']}")
    lines += [payload['franchise_rule'], 'VERIFY IN ESPN: ' + ', '.join(payload['verify_in_espn'])]
    lines += payload['assumptions']
    return '\n'.join(lines)
