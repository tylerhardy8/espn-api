"""Historical trends analysis for fantasy football leagues.

Analyzes multi-year league data to surface patterns like:
- Team performance trends (win rates, scoring averages, consistency)
- Managerial tendencies (draft style, trade frequency, waiver activity)
- Head-to-head rivalries and dominance
- Draft pick value analysis (hits, busts, steals)
- Positional scoring trends across seasons
"""

import statistics
from collections import defaultdict


def get_manager_key(team):
    """Get a stable manager identity from a team object.

    Uses the first owner's displayName if available, falling back to team name.
    Returns (display_name, owner_id) tuple for grouping and display.
    """
    owners = getattr(team, "owners", [])
    if owners and isinstance(owners, list) and len(owners) > 0:
        owner = owners[0]
        display = owner.get("displayName") or owner.get("firstName", "")
        if owner.get("lastName"):
            display = display or ""
            if owner.get("firstName"):
                display = f"{owner['firstName']} {owner['lastName']}"
        owner_id = owner.get("id", "")
        if display:
            return display, owner_id
    return team.team_name, ""


def _get_identity(team, group_by):
    """Return the grouping key for a team based on group_by mode."""
    if group_by == "manager":
        name, _ = get_manager_key(team)
        return name
    return team.team_name


def analyze_team_history(leagues_by_year, group_by="team"):
    """Analyze each team's performance across multiple seasons.

    Args:
        leagues_by_year: dict of {year: League}
        group_by: "team" (default) groups by team name,
                  "manager" groups by owner/manager identity

    Returns a dict keyed by team/manager name with yearly stats and trends.
    """
    team_data = defaultdict(lambda: {"seasons": [], "team_names": set()})

    for year, league in sorted(leagues_by_year.items()):
        standings = league.standings()
        for rank, team in enumerate(standings, 1):
            key = _get_identity(team, group_by)
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
                "team_name": team.team_name,
            }
            team_data[key]["seasons"].append(record)
            team_data[key]["team_names"].add(team.team_name)

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
        # Convert set to sorted list for display
        data["team_names"] = sorted(data["team_names"])

    return dict(team_data)


def analyze_head_to_head(leagues_by_year, group_by="team"):
    """Build head-to-head records between all team pairs across seasons.

    Returns a nested dict: h2h[team_a][team_b] = {"wins": W, "losses": L, "ties": T}
    """
    h2h = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0}))

    for year, league in leagues_by_year.items():
        for team in league.teams:
            team_key = _get_identity(team, group_by)
            for week_idx, opponent in enumerate(team.schedule):
                if not hasattr(opponent, "team_name"):
                    continue
                opp_key = _get_identity(opponent, group_by)
                outcome = team.outcomes[week_idx] if week_idx < len(team.outcomes) else None
                if outcome == "W":
                    h2h[team_key][opp_key]["wins"] += 1
                elif outcome == "L":
                    h2h[team_key][opp_key]["losses"] += 1
                elif outcome == "T":
                    h2h[team_key][opp_key]["ties"] += 1

    return {k: dict(v) for k, v in h2h.items()}


def _build_player_stats_map(league):
    """Map playerId -> (total_points, avg_points, position) from all rosters."""
    stats = {}
    for team in league.teams:
        for player in team.roster:
            stats[player.playerId] = (
                player.total_points,
                getattr(player, "avg_points", 0),
                getattr(player, "position", ""),
            )
    return stats


def analyze_draft_history(leagues_by_year):
    """Analyze draft pick effectiveness across seasons.

    Returns a list of draft pick analyses sorted by total points.
    """
    picks = []

    for year, league in leagues_by_year.items():
        if not league.draft:
            continue

        player_stats = _build_player_stats_map(league)

        # Players dropped mid-season aren't on any end-of-season roster, so a
        # roster-only lookup scores them 0 and floods the busts list. Fetch
        # their season stats directly instead.
        missing = list({p.playerId for p in league.draft} - set(player_stats))
        for i in range(0, len(missing), 50):
            try:
                fetched = league.player_info(playerId=missing[i:i + 50]) or []
            except Exception:
                continue
            if not isinstance(fetched, list):
                fetched = [fetched]
            for player in fetched:
                player_stats[player.playerId] = (
                    player.total_points,
                    getattr(player, "avg_points", 0),
                    getattr(player, "position", ""),
                )

        year_picks = []
        for pick in league.draft:
            total_points, avg_points, position = player_stats.get(pick.playerId, (0, 0, ""))
            year_picks.append({
                "year": year,
                "round": pick.round_num,
                "pick": pick.round_pick,
                "overall_pick": (pick.round_num - 1) * len(league.teams) + pick.round_pick,
                "player": pick.playerName,
                "position": position,
                "team": pick.team.team_name if hasattr(pick, "team") and pick.team else "Unknown",
                "total_points": round(total_points, 2),
                "avg_points": round(avg_points, 2),
            })

        # Rank each pick against same-position picks from the same draft
        by_position = defaultdict(list)
        for p in year_picks:
            by_position[p["position"]].append(p)
        for pos_picks in by_position.values():
            pos_picks.sort(key=lambda x: x["total_points"], reverse=True)
            for rank, p in enumerate(pos_picks, 1):
                p["pos_rank"] = rank

        picks.extend(year_picks)

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


def analyze_manager_tendencies(leagues_by_year, group_by="team"):
    """Analyze managerial behavior patterns (trade frequency, waiver usage, etc.)."""
    managers = defaultdict(lambda: {
        "total_trades": 0,
        "total_acquisitions": 0,
        "total_drops": 0,
        "seasons": 0,
    })

    for year, league in leagues_by_year.items():
        for team in league.teams:
            key = _get_identity(team, group_by)
            m = managers[key]
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


def analyze_luck(leagues_by_year, group_by="team"):
    """Estimate schedule luck and scoring consistency from weekly results.

    Expected wins use the "all-play" method: each week, a team is credited
    with the fraction of opponents it would have beaten. The gap between
    actual and expected wins is schedule luck.
    """
    results = defaultdict(lambda: {
        "actual_wins": 0,
        "expected_wins": 0.0,
        "games": 0,
        "close_wins": 0,
        "close_losses": 0,
        "weekly_scores": [],
        "seasons": set(),
    })

    for year, league in leagues_by_year.items():
        # Collect decided weekly scores for the whole league, aligned by week
        scores_by_week = defaultdict(list)
        played = {}  # team -> list of (week_idx, score, outcome, mov)
        for team in league.teams:
            games = []
            for w, score in enumerate(team.scores):
                outcome = team.outcomes[w] if w < len(team.outcomes) else None
                if outcome not in ("W", "L", "T") or score <= 0:
                    continue
                mov = team.mov[w] if w < len(team.mov) else 0
                games.append((w, score, outcome, mov))
                scores_by_week[w].append(score)
            played[team] = games

        for team, games in played.items():
            key = _get_identity(team, group_by)
            r = results[key]
            r["seasons"].add(year)
            for w, score, outcome, mov in games:
                week_scores = scores_by_week[w]
                others = len(week_scores) - 1
                if others > 0:
                    beaten = sum(1 for s in week_scores if s < score)
                    tied = sum(1 for s in week_scores if s == score) - 1
                    r["expected_wins"] += (beaten + tied * 0.5) / others
                r["games"] += 1
                r["weekly_scores"].append(score)
                if outcome == "W":
                    r["actual_wins"] += 1
                if abs(mov) <= 5:
                    if outcome == "W":
                        r["close_wins"] += 1
                    elif outcome == "L":
                        r["close_losses"] += 1

    for name, r in results.items():
        scores = r.pop("weekly_scores")
        r["seasons"] = len(r["seasons"])
        r["expected_wins"] = round(r["expected_wins"], 1)
        r["luck_delta"] = round(r["actual_wins"] - r["expected_wins"], 1)
        r["score_stdev"] = round(statistics.pstdev(scores), 1) if len(scores) > 1 else 0
        r["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0

    return dict(results)


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

    # Luck & Consistency
    luck = analyze_luck(leagues_by_year)
    if luck:
        lines.append("\n--- LUCK & CONSISTENCY (all-play expected wins) ---")
        lines.append(f"{'Team':<30} {'Wins':>5} {'xWins':>6} {'Luck':>6} {'StDev':>7}")
        lines.append("-" * 58)
        sorted_luck = sorted(luck.items(), key=lambda x: x[1]["luck_delta"], reverse=True)
        for name, r in sorted_luck:
            lines.append(
                f"{name:<30} {r['actual_wins']:>5} {r['expected_wins']:>6.1f} "
                f"{r['luck_delta']:>+6.1f} {r['score_stdev']:>7.1f}"
            )

    # Draft Analysis
    draft_data = analyze_draft_history(leagues_by_year)
    if draft_data:
        lines.append("\n--- BEST DRAFT PICKS (by total points) ---")
        lines.append(f"{'Year':>6} {'Pick':>5} {'Pos':<4} {'Player':<25} {'Team':<25} {'Points':>8}")
        lines.append("-" * 77)
        for pick in draft_data[:15]:
            lines.append(
                f"{pick['year']:>6} {pick['overall_pick']:>5} {pick['position']:<4} "
                f"{pick['player']:<25} {pick['team']:<25} {pick['total_points']:>8.2f}"
            )

        lines.append("\n--- BIGGEST DRAFT BUSTS (early picks, low points) ---")
        early_picks = [p for p in draft_data if p["overall_pick"] <= 30]
        busts = sorted(early_picks, key=lambda x: x["total_points"])
        for pick in busts[:10]:
            lines.append(
                f"  Pick #{pick['overall_pick']} ({pick['year']}): {pick['player']} "
                f"({pick['position'] or '?'}) - {pick['total_points']:.2f} pts"
            )

    return "\n".join(lines)
