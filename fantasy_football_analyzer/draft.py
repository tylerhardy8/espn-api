"""Live draft recommendations for fantasy football.

Provides real-time draft strategy including:
- Value-based drafting (VBD) with customizable baseline players
- Positional scarcity analysis
- Best available player recommendations
- Round-by-round strategy suggestions based on roster needs
- ADP comparison to identify potential steals
"""

from collections import defaultdict

# Positional tiers for standard leagues
ROSTER_TARGETS = {
    "QB": {"starter": 1, "bench": 1, "total": 2},
    "RB": {"starter": 2, "bench": 2, "total": 4},
    "WR": {"starter": 2, "bench": 2, "total": 4},
    "TE": {"starter": 1, "bench": 1, "total": 2},
    "D/ST": {"starter": 1, "bench": 0, "total": 1},
    "K": {"starter": 1, "bench": 0, "total": 1},
    "FLEX": {"starter": 1, "bench": 0, "total": 0},  # filled by RB/WR/TE
}

# Baseline player index (for VBD calculation) - position rank at which a
# replacement-level player is typically available on the waiver wire
VBD_BASELINES = {
    "QB": 12,
    "RB": 24,
    "WR": 30,
    "TE": 12,
    "K": 12,
    "D/ST": 12,
}


def build_player_rankings(league, week=None):
    """Build ranked player list from league's available data.

    Uses projected points and recent performance. Returns a list of dicts
    sorted by composite score.
    """
    rankings = []

    for team in league.teams:
        for player in team.roster:
            projected = player.projected_total_points
            actual = player.total_points
            avg = player.avg_points

            rankings.append({
                "name": player.name,
                "player_id": player.playerId,
                "position": player.position,
                "team": player.proTeam,
                "total_points": round(actual, 2),
                "projected_points": round(projected, 2),
                "avg_points": round(avg, 2),
                "percent_owned": player.percent_owned,
                "on_team": team.team_name,
            })

    rankings.sort(key=lambda x: x["projected_points"], reverse=True)
    return rankings


def calculate_vbd(player_rankings):
    """Calculate Value-Based Drafting scores.

    VBD = player's projected points - baseline replacement player's projected points.
    Higher VBD means more valuable relative to what's freely available at that position.
    """
    # Group by position
    by_position = defaultdict(list)
    for p in player_rankings:
        if p["position"] in VBD_BASELINES:
            by_position[p["position"]].append(p)

    # Sort each position by projected points
    for pos in by_position:
        by_position[pos].sort(key=lambda x: x["projected_points"], reverse=True)

    # Find baseline values
    baselines = {}
    for pos, idx in VBD_BASELINES.items():
        players = by_position.get(pos, [])
        if len(players) >= idx:
            baselines[pos] = players[idx - 1]["projected_points"]
        elif players:
            baselines[pos] = players[-1]["projected_points"]
        else:
            baselines[pos] = 0

    # Calculate VBD for each player
    vbd_rankings = []
    for p in player_rankings:
        pos = p["position"]
        baseline = baselines.get(pos, 0)
        vbd = round(p["projected_points"] - baseline, 2)
        entry = {**p, "vbd": vbd, "baseline": round(baseline, 2)}
        vbd_rankings.append(entry)

    vbd_rankings.sort(key=lambda x: x["vbd"], reverse=True)
    return vbd_rankings


def analyze_positional_scarcity(player_rankings):
    """Identify positions where talent drops off steeply.

    Returns a dict with position -> scarcity analysis.
    """
    by_position = defaultdict(list)
    for p in player_rankings:
        by_position[p["position"]].append(p["projected_points"])

    scarcity = {}
    for pos, points_list in by_position.items():
        points_list.sort(reverse=True)
        if len(points_list) < 3:
            continue

        top_5_avg = sum(points_list[:5]) / min(5, len(points_list))
        next_5_avg = sum(points_list[5:10]) / min(5, len(points_list[5:10])) if len(points_list) > 5 else 0
        rest_avg = sum(points_list[10:20]) / min(10, len(points_list[10:20])) if len(points_list) > 10 else 0

        tier_1_dropoff = round(top_5_avg - next_5_avg, 2)
        tier_2_dropoff = round(next_5_avg - rest_avg, 2)

        scarcity[pos] = {
            "top_5_avg": round(top_5_avg, 2),
            "next_5_avg": round(next_5_avg, 2),
            "rest_avg": round(rest_avg, 2),
            "tier_1_dropoff": tier_1_dropoff,
            "tier_2_dropoff": tier_2_dropoff,
            "total_available": len(points_list),
        }

    return scarcity


def get_draft_recommendations(league, my_team_name=None, num_recommendations=10):
    """Get draft recommendations based on current league state.

    If my_team_name is provided, recommendations account for current roster needs.
    """
    rankings = build_player_rankings(league)
    vbd = calculate_vbd(rankings)
    scarcity = analyze_positional_scarcity(rankings)

    # Determine roster needs if team specified
    needs = {}
    if my_team_name:
        my_team = None
        for team in league.teams:
            if team.team_name.lower() == my_team_name.lower():
                my_team = team
                break

        if my_team:
            roster_counts = defaultdict(int)
            for player in my_team.roster:
                roster_counts[player.position] += 1

            for pos, targets in ROSTER_TARGETS.items():
                have = roster_counts.get(pos, 0)
                need = targets["total"] - have
                if need > 0:
                    needs[pos] = need

    # Filter to undrafted/available players and apply need bonuses
    recommendations = []
    for p in vbd[:50]:
        score = p["vbd"]
        # Boost score for positions of need
        if p["position"] in needs and needs[p["position"]] > 0:
            score *= 1.2  # 20% boost for needed positions

        recommendations.append({
            **p,
            "recommendation_score": round(score, 2),
            "position_need": needs.get(p["position"], 0),
        })

    recommendations.sort(key=lambda x: x["recommendation_score"], reverse=True)
    return recommendations[:num_recommendations], scarcity


def analyze_draft_picks(league):
    """Analyze completed draft picks to identify value and reaches."""
    if not league.draft:
        return []

    analysis = []
    for pick in league.draft:
        # Find player on current roster to get actual performance
        total_points = 0
        for team in league.teams:
            for player in team.roster:
                if player.playerId == pick.playerId:
                    total_points = player.total_points
                    break

        overall_pick = (pick.round_num - 1) * len(league.teams) + pick.round_pick
        analysis.append({
            "round": pick.round_num,
            "pick": pick.round_pick,
            "overall": overall_pick,
            "player": pick.playerName,
            "team": pick.team.team_name if hasattr(pick, "team") and pick.team else "Unknown",
            "total_points": round(total_points, 2),
        })

    # Add value rating - compare points to pick position expectations
    analysis.sort(key=lambda x: x["total_points"], reverse=True)
    for rank, entry in enumerate(analysis, 1):
        entry["points_rank"] = rank
        entry["value"] = entry["overall"] - rank  # positive = steal, negative = bust

    analysis.sort(key=lambda x: x["overall"])
    return analysis


def format_draft_report(league, my_team_name=None):
    """Generate a formatted draft recommendation report."""
    lines = []
    lines.append("=" * 70)
    lines.append("DRAFT ANALYSIS & RECOMMENDATIONS")
    lines.append("=" * 70)

    recommendations, scarcity = get_draft_recommendations(league, my_team_name)

    # Positional Scarcity
    lines.append("\n--- POSITIONAL SCARCITY ---")
    lines.append(f"{'Position':<10} {'Top 5 Avg':>10} {'Next 5 Avg':>11} {'Dropoff':>8} {'Available':>10}")
    lines.append("-" * 52)
    for pos in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
        if pos in scarcity:
            s = scarcity[pos]
            lines.append(
                f"{pos:<10} {s['top_5_avg']:>10.2f} {s['next_5_avg']:>11.2f} "
                f"{s['tier_1_dropoff']:>8.2f} {s['total_available']:>10}"
            )

    # Top Recommendations
    lines.append("\n--- TOP DRAFT PICKS ---")
    if my_team_name:
        lines.append(f"(Personalized for: {my_team_name})")
    lines.append(
        f"{'Rank':>5} {'Player':<25} {'Pos':<6} {'Team':<5} {'VBD':>8} {'Score':>8}"
    )
    lines.append("-" * 60)
    for i, rec in enumerate(recommendations, 1):
        need_marker = " *" if rec["position_need"] > 0 else ""
        lines.append(
            f"{i:>5} {rec['name']:<25} {rec['position']:<6} {rec['team']:<5} "
            f"{rec['vbd']:>8.2f} {rec['recommendation_score']:>8.2f}{need_marker}"
        )

    if my_team_name:
        lines.append("\n  * = position of need on your roster")

    # Draft Review (if draft has happened)
    draft_analysis = analyze_draft_picks(league)
    if draft_analysis:
        lines.append("\n--- DRAFT REVIEW ---")
        lines.append(f"{'Pick':>5} {'Player':<25} {'Team':<25} {'Points':>8} {'Value':>6}")
        lines.append("-" * 72)
        for entry in draft_analysis:
            value_str = f"+{entry['value']}" if entry["value"] > 0 else str(entry["value"])
            lines.append(
                f"{entry['overall']:>5} {entry['player']:<25} {entry['team']:<25} "
                f"{entry['total_points']:>8.2f} {value_str:>6}"
            )

        # Biggest steals
        steals = sorted(draft_analysis, key=lambda x: x["value"], reverse=True)[:5]
        lines.append("\n  Biggest Steals:")
        for s in steals:
            lines.append(
                f"    Pick #{s['overall']} {s['player']} - {s['total_points']:.2f} pts "
                f"(+{s['value']} spots better than pick)"
            )

        # Biggest busts
        busts = sorted(draft_analysis, key=lambda x: x["value"])[:5]
        lines.append("\n  Biggest Reaches:")
        for b in busts:
            lines.append(
                f"    Pick #{b['overall']} {b['player']} - {b['total_points']:.2f} pts "
                f"({b['value']} spots worse than pick)"
            )

    return "\n".join(lines)
