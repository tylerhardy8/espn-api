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
FAIRNESS_BAND = 0.30  # proposals within +/-30% of value are plausibly acceptable


def _tradeable_players(strength, pos):
    """Players a team could realistically move at a position: bench pieces,
    plus the second starter when they're two-deep at a two-starter spot."""
    data = strength.get(pos, {})
    candidates = list(data.get("bench", []))
    starters = data.get("starters", [])
    if len(starters) >= 2 and data.get("depth", 0) >= 3:
        candidates.append(starters[-1])
    return sorted(candidates, key=lambda p: p["value"], reverse=True)


def find_trade_matches(my_team, league, max_partners=6, max_proposals_per_partner=3):
    """Scan every opposing roster for mutually beneficial trades.

    For each opponent, finds complementary need/surplus pairs (they're weak
    where I'm deep, and strong where I'm weak), proposes value-balanced
    player swaps, and estimates how much each swap upgrades my starting
    lineup. Returns partners sorted by fit.
    """
    my_needs = {n["position"]: n for n in identify_team_needs(my_team, league)}
    my_strength = evaluate_roster_strength(my_team)

    partners = []
    for other in league.teams:
        if other.team_id == my_team.team_id:
            continue
        other_needs = {n["position"]: n for n in identify_team_needs(other, league)}
        other_strength = evaluate_roster_strength(other)

        proposals = []
        fit_score = 0.0
        for get_pos in TRADE_CORE_POSITIONS:      # position I want back
            if my_needs.get(get_pos, {}).get("deficit", 0) <= 0:
                continue
            for give_pos in TRADE_CORE_POSITIONS:  # position I'd send away
                if give_pos == get_pos:
                    continue
                if other_needs.get(give_pos, {}).get("deficit", 0) <= 0:
                    continue  # they don't need what I'd send

                gives = _tradeable_players(my_strength, give_pos)
                gets = _tradeable_players(other_strength, get_pos)
                if not gives or not gets:
                    continue

                fit_score += (my_needs[get_pos]["deficit"]
                              + other_needs[give_pos]["deficit"])

                # Best value-balanced pairing within the fairness band
                for give in gives[:2]:
                    for get in gets[:2]:
                        if give["value"] <= 0 or get["value"] <= 0:
                            continue
                        imbalance = (get["value"] - give["value"]) / max(give["value"], 1)
                        if abs(imbalance) > FAIRNESS_BAND:
                            continue
                        # Upgrade: does the incoming player beat my worst starter there?
                        my_starters = my_strength.get(get_pos, {}).get("starters", [])
                        floor = my_starters[-1]["value"] if my_starters else 0
                        upgrade = round(get["value"] - floor, 1)
                        proposals.append({
                            "give_player": give["name"],
                            "give_position": give_pos,
                            "give_points": give["value"],
                            "receive_player": get["name"],
                            "receive_position": get_pos,
                            "receive_points": get["value"],
                            "value_delta": round(get["value"] - give["value"], 1),
                            "lineup_upgrade": upgrade,
                            "reason": (
                                f"They're thin at {give_pos} "
                                f"(-{other_needs[give_pos]['deficit']:.0f} vs league avg); "
                                f"you're thin at {get_pos} "
                                f"(-{my_needs[get_pos]['deficit']:.0f})"
                            ),
                        })

        if not proposals:
            continue
        # Best proposals: biggest lineup upgrade, then fairest
        proposals.sort(key=lambda p: (-p["lineup_upgrade"], abs(p["value_delta"])))
        deduped, seen = [], set()
        for p in proposals:
            key = (p["give_player"], p["receive_player"])
            if key not in seen:
                seen.add(key)
                deduped.append(p)
        partners.append({
            "partner": other.team_name,
            "record": f"{other.wins}-{other.losses}",
            "fit_score": round(fit_score, 1),
            "their_needs": [pos for pos in TRADE_CORE_POSITIONS
                            if other_needs.get(pos, {}).get("deficit", 0) > 0],
            "their_surplus": [pos for pos in TRADE_CORE_POSITIONS
                              if _tradeable_players(other_strength, pos)],
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
            suggestions.append({**p, "trade_partner": partner["partner"]})
    suggestions.sort(key=lambda x: -x["lineup_upgrade"])
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
