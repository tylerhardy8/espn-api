"""Historical trends analysis for fantasy football leagues.

Analyzes multi-year league data to surface patterns like:
- Team performance trends (win rates, scoring averages, consistency)
- Managerial tendencies (draft style, trade frequency, waiver activity)
- Head-to-head rivalries and dominance
- Draft pick value analysis (hits, busts, steals)
- Positional scoring trends across seasons
"""

from collections import defaultdict


def analyze_team_history(leagues_by_year):
    """Analyze each team's performance across multiple seasons.

    Returns a dict keyed by team name with yearly stats and trends.
    """
    team_data = defaultdict(lambda: {"seasons": []})

    for year, league in sorted(leagues_by_year.items()):
        standings = league.standings()
        for rank, team in enumerate(standings, 1):
            record = {
                "year": year,
                "rank": rank,
                "wins": team.wins,
                "losses": team.losses,
                "ties": team.ties,
                "points_for": round(team.points_for, 2),
                "points_against": round(team.points_against, 2),
                "acquisitions": team.acquisitions,
                "trades": team.trades,
                "drops": team.drops,
            }
            team_data[team.team_name]["seasons"].append(record)

    # Compute aggregate stats
    for name, data in team_data.items():
        seasons = data["seasons"]
        total_games = sum(s["wins"] + s["losses"] + s["ties"] for s in seasons)
        total_wins = sum(s["wins"] for s in seasons)
        data["all_time_win_pct"] = round(total_wins / total_games, 3) if total_games else 0
        data["avg_finish"] = round(sum(s["rank"] for s in seasons) / len(seasons), 1)
        data["avg_points_for"] = round(sum(s["points_for"] for s in seasons) / len(seasons), 1)
        data["championships"] = sum(1 for s in seasons if s["rank"] == 1)
        data["num_seasons"] = len(seasons)

    return dict(team_data)


def analyze_head_to_head(leagues_by_year):
    """Build head-to-head records between all team pairs across seasons.

    Returns a nested dict: h2h[team_a][team_b] = {"wins": W, "losses": L, "ties": T}
    """
    h2h = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0}))

    for year, league in leagues_by_year.items():
        for team in league.teams:
            for week_idx, opponent in enumerate(team.schedule):
                if not hasattr(opponent, "team_name"):
                    continue
                outcome = team.outcomes[week_idx] if week_idx < len(team.outcomes) else None
                if outcome == "W":
                    h2h[team.team_name][opponent.team_name]["wins"] += 1
                elif outcome == "L":
                    h2h[team.team_name][opponent.team_name]["losses"] += 1
                elif outcome == "T":
                    h2h[team.team_name][opponent.team_name]["ties"] += 1

    return {k: dict(v) for k, v in h2h.items()}


def analyze_draft_history(leagues_by_year):
    """Analyze draft pick effectiveness across seasons.

    Returns a list of draft pick analyses sorted by value-over-pick.
    """
    picks = []

    for year, league in leagues_by_year.items():
        if not league.draft:
            continue

        for pick in league.draft:
            player_name = pick.playerName
            round_num = pick.round_num
            round_pick = pick.round_pick

            # Find the player on a team to get season stats
            total_points = 0
            for team in league.teams:
                for player in team.roster:
                    if player.playerId == pick.playerId:
                        total_points = player.total_points
                        break

            picks.append({
                "year": year,
                "round": round_num,
                "pick": round_pick,
                "overall_pick": (round_num - 1) * len(league.teams) + round_pick,
                "player": player_name,
                "team": pick.team.team_name if hasattr(pick, "team") and pick.team else "Unknown",
                "total_points": round(total_points, 2),
            })

    # Sort by total points descending to identify steals vs busts
    picks.sort(key=lambda x: x["total_points"], reverse=True)
    return picks


def analyze_scoring_trends(leagues_by_year):
    """Analyze league-wide scoring trends across seasons.

    Returns per-year scoring summaries.
    """
    trends = []
    for year, league in sorted(leagues_by_year.items()):
        all_scores = []
        for team in league.teams:
            all_scores.extend([s for s in team.scores if s > 0])

        if not all_scores:
            continue

        trends.append({
            "year": year,
            "avg_score": round(sum(all_scores) / len(all_scores), 2),
            "max_score": round(max(all_scores), 2),
            "min_score": round(min(all_scores), 2),
            "total_teams": len(league.teams),
            "weeks_played": len(league.teams[0].scores) if league.teams else 0,
        })

    return trends


def analyze_manager_tendencies(leagues_by_year):
    """Analyze managerial behavior patterns (trade frequency, waiver usage, etc.)."""
    managers = defaultdict(lambda: {
        "total_trades": 0,
        "total_acquisitions": 0,
        "total_drops": 0,
        "seasons": 0,
    })

    for year, league in leagues_by_year.items():
        for team in league.teams:
            m = managers[team.team_name]
            m["total_trades"] += team.trades
            m["total_acquisitions"] += team.acquisitions
            m["total_drops"] += team.drops
            m["seasons"] += 1

    for name, m in managers.items():
        s = m["seasons"]
        m["avg_trades_per_season"] = round(m["total_trades"] / s, 1) if s else 0
        m["avg_acquisitions_per_season"] = round(m["total_acquisitions"] / s, 1) if s else 0
        m["avg_drops_per_season"] = round(m["total_drops"] / s, 1) if s else 0

    return dict(managers)


def format_historical_report(leagues_by_year):
    """Generate a full historical analysis report as a formatted string."""
    lines = []
    lines.append("=" * 70)
    lines.append("HISTORICAL LEAGUE ANALYSIS")
    lines.append("=" * 70)

    # Team History
    team_history = analyze_team_history(leagues_by_year)
    lines.append("\n--- ALL-TIME TEAM RANKINGS ---")
    sorted_teams = sorted(team_history.items(), key=lambda x: x[1]["all_time_win_pct"], reverse=True)
    lines.append(f"{'Team':<30} {'Win%':>6} {'Avg Finish':>11} {'Titles':>7} {'Avg PF':>8}")
    lines.append("-" * 65)
    for name, data in sorted_teams:
        lines.append(
            f"{name:<30} {data['all_time_win_pct']:>6.3f} {data['avg_finish']:>11.1f} "
            f"{data['championships']:>7} {data['avg_points_for']:>8.1f}"
        )

    # Scoring Trends
    trends = analyze_scoring_trends(leagues_by_year)
    if trends:
        lines.append("\n--- LEAGUE SCORING TRENDS ---")
        lines.append(f"{'Year':>6} {'Avg Score':>10} {'High Score':>11} {'Low Score':>10}")
        lines.append("-" * 40)
        for t in trends:
            lines.append(f"{t['year']:>6} {t['avg_score']:>10.2f} {t['max_score']:>11.2f} {t['min_score']:>10.2f}")

    # Manager Tendencies
    managers = analyze_manager_tendencies(leagues_by_year)
    lines.append("\n--- MANAGER TENDENCIES ---")
    sorted_mgrs = sorted(managers.items(), key=lambda x: x[1]["avg_acquisitions_per_season"], reverse=True)
    lines.append(f"{'Team':<30} {'Trades/Yr':>10} {'Pickups/Yr':>11} {'Drops/Yr':>9}")
    lines.append("-" * 63)
    for name, m in sorted_mgrs:
        lines.append(
            f"{name:<30} {m['avg_trades_per_season']:>10.1f} "
            f"{m['avg_acquisitions_per_season']:>11.1f} {m['avg_drops_per_season']:>9.1f}"
        )

    # Head-to-Head Dominance
    h2h = analyze_head_to_head(leagues_by_year)
    lines.append("\n--- HEAD-TO-HEAD RIVALRIES (top matchups by total games) ---")
    rivalries = []
    seen = set()
    for team_a, opponents in h2h.items():
        for team_b, record in opponents.items():
            pair = tuple(sorted([team_a, team_b]))
            if pair in seen:
                continue
            seen.add(pair)
            total = record["wins"] + record["losses"] + record["ties"]
            rivalries.append((team_a, team_b, record, total))

    rivalries.sort(key=lambda x: x[3], reverse=True)
    for team_a, team_b, record, total in rivalries[:10]:
        lines.append(
            f"  {team_a} vs {team_b}: {record['wins']}-{record['losses']}-{record['ties']} "
            f"({total} games)"
        )

    # Draft Analysis
    draft_data = analyze_draft_history(leagues_by_year)
    if draft_data:
        lines.append("\n--- BEST DRAFT PICKS (by total points) ---")
        lines.append(f"{'Year':>6} {'Pick':>5} {'Player':<25} {'Team':<25} {'Points':>8}")
        lines.append("-" * 72)
        for pick in draft_data[:15]:
            lines.append(
                f"{pick['year']:>6} {pick['overall_pick']:>5} {pick['player']:<25} "
                f"{pick['team']:<25} {pick['total_points']:>8.2f}"
            )

        lines.append("\n--- BIGGEST DRAFT BUSTS (early picks, low points) ---")
        early_picks = [p for p in draft_data if p["overall_pick"] <= 30]
        busts = sorted(early_picks, key=lambda x: x["total_points"])
        for pick in busts[:10]:
            lines.append(
                f"  Pick #{pick['overall_pick']} ({pick['year']}): {pick['player']} - "
                f"{pick['total_points']:.2f} pts"
            )

    return "\n".join(lines)
