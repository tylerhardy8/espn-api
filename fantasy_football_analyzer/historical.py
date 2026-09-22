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
from .history_data import identity_labels, season_complete, final_rank, completed_games, coverage_for, activity_count


def get_manager_key(team):
    """Display name and stable owner-ID group; neither team names nor display names are IDs."""
    owners = getattr(team, 'owners', [])
    owners = owners if isinstance(owners, list) else []
    names, ids = [], []
    for owner in sorted((o for o in owners if isinstance(o, dict)), key=lambda o: str(o.get('id', ''))):
        name = ' '.join(str(owner.get(k) or '') for k in ('firstName','lastName')).strip()
        names.append(name or owner.get('displayName') or team.team_name)
        if owner.get('id'): ids.append(str(owner['id']).casefold())
    return (' / '.join(names) or team.team_name, '|'.join(sorted(ids)))


def analyze_team_history(leagues_by_year, group_by="team"):
    """Analyze each team's performance across multiple seasons.

    Args:
        leagues_by_year: dict of {year: League}
        group_by: "team" (default) groups by franchise ID,
                  "manager" groups by owner-ID set (latest names are labels)

    Returns a dict keyed by team/manager name with yearly stats and trends.
    """
    team_data = defaultdict(lambda: {"seasons": [], "team_names": set()})
    labels = identity_labels(leagues_by_year, group_by)

    for year, league in sorted(leagues_by_year.items()):
        standings = league.standings()
        for rank, team in enumerate(standings, 1):
            key = labels[(year, str(team.team_id))]
            record = {
                "year": year,
                "rank": final_rank(team, league) or rank,
                "final_rank": final_rank(team, league),
                "completed": season_complete(league),
                "team_id": team.team_id,
                "wins": team.wins,
                "losses": team.losses,
                "ties": team.ties,
                "points_for": round(team.points_for, 2),
                "points_against": round(team.points_against, 2),
                "acquisitions": activity_count(team, "acquisitions"),
                "trades": activity_count(team, "trades"),
                "drops": activity_count(team, "drops"),
                "team_name": team.team_name,
            }
            team_data[key]["seasons"].append(record)
            team_data[key]["team_names"].add(team.team_name)

    # Compute aggregate stats
    for name, data in team_data.items():
        seasons = data["seasons"]
        total_games = sum(s["wins"] + s["losses"] + s["ties"] for s in seasons)
        total_wins = sum(s["wins"] + .5*s["ties"] for s in seasons)
        data["all_time_win_pct"] = round(total_wins / total_games, 3) if total_games else 0
        final_ranks = [s["final_rank"] for s in seasons if s["final_rank"] is not None]
        data["avg_finish"] = round(sum(final_ranks) / len(final_ranks), 1) if final_ranks else None
        data["completed_seasons"] = len(final_ranks)
        data["avg_points_for"] = round(sum(s["points_for"] for s in seasons) / len(seasons), 1)
        data["championships"] = sum(1 for s in seasons if s["final_rank"] == 1)
        data["num_seasons"] = len(seasons)
        # Convert set to sorted list for display
        data["team_names"] = sorted(data["team_names"])

    return dict(team_data)


def analyze_head_to_head(leagues_by_year, group_by="team"):
    """Decided matchups only, using stable identity and excluding playoff byes."""
    labels = identity_labels(leagues_by_year, group_by)
    h2h = defaultdict(lambda: defaultdict(lambda: {'wins':0, 'losses':0, 'ties':0}))
    for year, league in leagues_by_year.items():
        for game in completed_games(league):
            a = labels[(year, str(game['team'].team_id))]
            b = labels[(year, str(game['opponent'].team_id))]
            if a != b:
                h2h[a][b][{'W':'wins','L':'losses','T':'ties'}[game['outcome']]] += 1
    return {k:dict(v) for k,v in h2h.items()}


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


def build_draft_stats_map(league):
    """Stats map covering every drafted player.

    Players dropped mid-season aren't on any end-of-season roster, so a
    roster-only lookup scores them 0 (and hides their position). Batch-fetch
    the missing ones directly.
    """
    player_stats = _build_player_stats_map(league)
    if not league.draft:
        return player_stats

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
    return player_stats


def analyze_draft_history(leagues_by_year):
    """Analyze draft pick effectiveness across seasons.

    Returns a list of draft pick analyses sorted by total points.
    """
    picks = []

    for year, league in leagues_by_year.items():
        if not league.draft:
            continue

        player_stats = build_draft_stats_map(league)

        year_picks = []
        for pick in league.draft:
            total_points, avg_points, position = player_stats.get(pick.playerId, (None, None, ""))
            year_picks.append({
                "year": year,
                "round": pick.round_num,
                "pick": pick.round_pick,
                "overall_pick": (pick.round_num - 1) * len(league.teams) + pick.round_pick,
                "player": pick.playerName,
                "position": position,
                "team": pick.team.team_name if hasattr(pick, "team") and pick.team else "Unknown",
                "total_points": round(total_points, 2) if total_points is not None else None,
                "avg_points": round(avg_points, 2) if avg_points is not None else None,
                "stats_available": total_points is not None,
                "completed": season_complete(league),
                "draft_type": getattr(league.settings, "draft_type", ""),
                "bid_amount": getattr(pick, "bid_amount", None),
            })

        # Rank each pick against same-position picks from the same draft
        by_position = defaultdict(list)
        for p in year_picks:
            p["pos_rank"] = None
            if p["stats_available"]:
                by_position[p["position"]].append(p)
        for pos_picks in by_position.values():
            pos_picks.sort(key=lambda x: x["total_points"] if x["total_points"] is not None else float("-inf"), reverse=True)
            for rank, p in enumerate(pos_picks, 1):
                p["pos_rank"] = rank

        picks.extend(year_picks)

    # Sort by total points descending to identify steals vs busts
    picks.sort(key=lambda x: x["total_points"] if x["total_points"] is not None else float("-inf"), reverse=True)
    return picks


def analyze_scoring_trends(leagues_by_year):
    """Decided matchup totals; include legitimate zero/negative scores."""
    trends = []
    for year, league in sorted(leagues_by_year.items()):
        games = completed_games(league)
        scores = [g['score'] for g in games]
        if scores:
            trends.append({'year':year, 'avg_score':round(statistics.mean(scores),2),
                'max_score':max(scores), 'min_score':min(scores), 'total_teams':len(league.teams),
                'weeks_played':len({g['period'] for g in games}),
                'periods_played':len({g['period'] for g in games}), 'units':'matchup-period total'})
    return trends


def analyze_manager_tendencies(leagues_by_year, group_by="team"):
    """Analyze managerial behavior patterns (trade frequency, waiver usage, etc.)."""
    labels = identity_labels(leagues_by_year, group_by)
    managers = defaultdict(lambda: {
        "total_trades": 0,
        "total_acquisitions": 0,
        "total_drops": 0,
        "seasons": 0,
    })

    for year, league in leagues_by_year.items():
        for team in league.teams:
            key = labels[(year, str(team.team_id))]
            m = managers[key]
            m['seasons'] += 1
            for counter in ('trades','acquisitions','drops'):
                value = activity_count(team, counter)
                reported = counter + '_reported_seasons'
                m.setdefault(reported, 0)
                if value is not None:
                    m['total_' + counter] += value
                    m[reported] += 1
    for m in managers.values():
        for counter in ('trades','acquisitions','drops'):
            n = m[counter + '_reported_seasons']
            m['avg_' + counter + '_per_season'] = round(m['total_' + counter]/n, 1) if n else None


    return dict(managers)


def analyze_luck(leagues_by_year, group_by="team", identity_context=None):
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

    labels = identity_labels(identity_context or leagues_by_year, group_by)
    for year, league in leagues_by_year.items():
        games = completed_games(league)
        by_period = defaultdict(list)
        for game in games:
            by_period[game['period']].append(game['score'])
        for game in games:
            key = labels[(year, str(game['team'].team_id))]
            r = results[key]
            r['seasons'].add(year)
            score, outcome = game['score'], game['outcome']
            scores = by_period[game['period']]
            others = len(scores)-1
            if others:
                r['expected_wins'] += (sum(s < score for s in scores) + .5*(sum(s == score for s in scores)-1))/others
            r['games'] += 1
            r['weekly_scores'].append(score)
            r['actual_wins'] += 1 if outcome == 'W' else .5 if outcome == 'T' else 0
            if type(game['mov']) in (int,float) and abs(game['mov']) <= 5:
                if outcome == 'W': r['close_wins'] += 1
                elif outcome == 'L': r['close_losses'] += 1

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

    coverage = getattr(leagues_by_year, 'coverage', {})
    lines.append('Loaded seasons: ' + ', '.join(map(str, sorted(leagues_by_year))))
    for year, error in coverage.get('failed_years', {}).items():
        lines.append(f'MISSING {year}: {error}')
    lines.append('Titles and average finishes require final season results. Activity totals are not a full transaction ledger.')
    # Team History
    team_history = analyze_team_history(leagues_by_year)
    lines.append("\n--- ALL-TIME TEAM RANKINGS ---")
    sorted_teams = sorted(team_history.items(), key=lambda x: x[1]["all_time_win_pct"], reverse=True)
    lines.append(f"{'Team':<30} {'Win%':>6} {'Avg Finish':>11} {'Titles':>7} {'Avg PF':>8}")
    lines.append("-" * 65)
    for name, data in sorted_teams:
        lines.append(
            f"{name:<30} {data['all_time_win_pct']:>6.3f} {str(data['avg_finish']) if data['avg_finish'] is not None else 'pending':>11} "
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
    sorted_mgrs = sorted(managers.items(), key=lambda x: x[1]["avg_acquisitions_per_season"] if x[1]["avg_acquisitions_per_season"] is not None else -1, reverse=True)
    lines.append(f"{'Team':<30} {'Trades/Yr':>10} {'Pickups/Yr':>11} {'Drops/Yr':>9}")
    lines.append("-" * 63)
    for name, m in sorted_mgrs:
        lines.append(
            f"{name:<30} {str(m['avg_trades_per_season']) if m['avg_trades_per_season'] is not None else 'unknown':>10} "
            f"{str(m['avg_acquisitions_per_season']) if m['avg_acquisitions_per_season'] is not None else 'unknown':>11} {str(m['avg_drops_per_season']) if m['avg_drops_per_season'] is not None else 'unknown':>9}"
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
        for pick in [p for p in draft_data if p["stats_available"]][:15]:
            lines.append(
                f"{pick['year']:>6} {pick['overall_pick']:>5} {pick['position']:<4} "
                f"{pick['player']:<25} {pick['team']:<25} {pick['total_points']:>8.2f}"
            )

        lines.append("\n--- BIGGEST DRAFT BUSTS (early picks, low points) ---")
        early_picks = [p for p in draft_data if p["overall_pick"] <= 30 and p["stats_available"] and p["completed"] and p["draft_type"] != "AUCTION"]
        busts = sorted(early_picks, key=lambda x: x["total_points"])
        for pick in busts[:10]:
            lines.append(
                f"  Pick #{pick['overall_pick']} ({pick['year']}): {pick['player']} "
                f"({pick['position'] or '?'}) - {pick['total_points']:.2f} pts"
            )

    return "\n".join(lines)
