"""Tests for the draft recommendations module."""

import unittest
from unittest.mock import MagicMock
from fantasy_football_analyzer.draft import (
    build_player_rankings,
    calculate_vbd,
    analyze_positional_scarcity,
    get_draft_recommendations,
    analyze_draft_picks,
)


def make_mock_player(name, player_id, position, pro_team, total_pts, projected_pts,
                     avg_pts=0, pct_owned=50, lineup_slot=""):
    player = MagicMock()
    player.name = name
    player.playerId = player_id
    player.position = position
    player.proTeam = pro_team
    player.total_points = total_pts
    player.projected_total_points = projected_pts
    player.avg_points = avg_pts
    player.percent_owned = pct_owned
    player.lineupSlot = lineup_slot
    return player


def make_mock_team(name, team_id, roster=None, wins=5, losses=5):
    team = MagicMock()
    team.team_name = name
    team.team_id = team_id
    team.roster = roster or []
    team.wins = wins
    team.losses = losses
    team.points_for = sum(p.total_points for p in team.roster) if team.roster else 0
    return team


class TestBuildPlayerRankings(unittest.TestCase):
    def test_returns_sorted_by_projected(self):
        p1 = make_mock_player("Player A", 1, "QB", "KC", 200, 250, 15)
        p2 = make_mock_player("Player B", 2, "RB", "SF", 180, 220, 13)
        team = make_mock_team("Team 1", 1, [p1, p2])

        league = MagicMock()
        league.teams = [team]

        rankings = build_player_rankings(league)
        self.assertEqual(len(rankings), 2)
        self.assertEqual(rankings[0]["name"], "Player A")
        self.assertGreater(rankings[0]["projected_points"], rankings[1]["projected_points"])


class TestCalculateVBD(unittest.TestCase):
    def test_vbd_positive_for_top_players(self):
        rankings = [
            {"name": f"QB{i}", "position": "QB", "projected_points": 300 - i * 10,
             "total_points": 0, "avg_points": 0, "percent_owned": 90, "team": "KC",
             "player_id": i, "on_team": "Team"}
            for i in range(15)
        ]

        vbd = calculate_vbd(rankings)
        # Top QB should have positive VBD
        top = next(p for p in vbd if p["name"] == "QB0")
        self.assertGreater(top["vbd"], 0)


class TestAnalyzePositionalScarcity(unittest.TestCase):
    def test_scarcity_analysis(self):
        rankings = []
        for i in range(20):
            rankings.append({
                "name": f"RB{i}", "position": "RB",
                "projected_points": 250 - i * 10,
            })

        scarcity = analyze_positional_scarcity(rankings)
        self.assertIn("RB", scarcity)
        self.assertGreater(scarcity["RB"]["top_5_avg"], scarcity["RB"]["next_5_avg"])


class TestAnalyzeDraftPicks(unittest.TestCase):
    def test_draft_analysis(self):
        p1 = make_mock_player("Mahomes", 100, "QB", "KC", 350, 300)
        pick = MagicMock()
        pick.playerName = "Mahomes"
        pick.playerId = 100
        pick.round_num = 1
        pick.round_pick = 1
        pick.team = make_mock_team("Team 1", 1, [p1])

        team = make_mock_team("Team 1", 1, [p1])
        league = MagicMock()
        league.teams = [team]
        league.draft = [pick]

        analysis = analyze_draft_picks(league)
        self.assertEqual(len(analysis), 1)
        self.assertEqual(analysis[0]["player"], "Mahomes")
        self.assertEqual(analysis[0]["total_points"], 350)


if __name__ == "__main__":
    unittest.main()
