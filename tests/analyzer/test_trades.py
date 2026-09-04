"""Tests for the trade analysis module."""

import unittest
from unittest.mock import MagicMock
from fantasy_football_analyzer.trades import (
    evaluate_roster_strength,
    identify_team_needs,
    evaluate_trade,
    find_trade_targets,
)


def make_mock_player(name, position, total_pts, projected_pts=0, avg_pts=0, slot=""):
    player = MagicMock()
    player.name = name
    player.position = position
    player.total_points = total_pts
    player.projected_total_points = projected_pts
    player.avg_points = avg_pts
    player.lineupSlot = slot
    player.playerId = hash(name)
    return player


def make_mock_team(name, team_id, roster, wins=5, losses=5, pf=1000):
    team = MagicMock()
    team.team_name = name
    team.team_id = team_id
    team.roster = roster
    team.wins = wins
    team.losses = losses
    team.points_for = pf
    return team


class TestEvaluateRosterStrength(unittest.TestCase):
    def test_basic_strength(self):
        roster = [
            make_mock_player("QB1", "QB", 200),
            make_mock_player("RB1", "RB", 180),
            make_mock_player("RB2", "RB", 150),
            make_mock_player("RB3", "RB", 80),
        ]
        team = make_mock_team("Team A", 1, roster)

        strengths = evaluate_roster_strength(team)
        self.assertIn("QB", strengths)
        self.assertIn("RB", strengths)
        self.assertEqual(strengths["QB"]["depth"], 1)
        self.assertEqual(strengths["RB"]["depth"], 3)
        self.assertEqual(len(strengths["RB"]["bench"]), 1)


class TestIdentifyTeamNeeds(unittest.TestCase):
    def test_identifies_weak_position(self):
        # Team A has weak WR
        roster_a = [
            make_mock_player("QB1", "QB", 200),
            make_mock_player("RB1", "RB", 180),
            make_mock_player("RB2", "RB", 150),
            make_mock_player("WR1", "WR", 50),
            make_mock_player("WR2", "WR", 40),
        ]
        team_a = make_mock_team("Team A", 1, roster_a)

        # Team B has strong WR
        roster_b = [
            make_mock_player("QB2", "QB", 180),
            make_mock_player("RB3", "RB", 100),
            make_mock_player("RB4", "RB", 90),
            make_mock_player("WR3", "WR", 200),
            make_mock_player("WR4", "WR", 180),
        ]
        team_b = make_mock_team("Team B", 2, roster_b)

        league = MagicMock()
        league.teams = [team_a, team_b]

        needs = identify_team_needs(team_a, league)
        wr_need = next((n for n in needs if n["position"] == "WR"), None)
        self.assertIsNotNone(wr_need)
        self.assertGreater(wr_need["deficit"], 0)


class TestEvaluateTrade(unittest.TestCase):
    def test_fair_trade(self):
        roster_a = [make_mock_player("PlayerA", "RB", 150, 160, 10)]
        roster_b = [make_mock_player("PlayerB", "WR", 145, 155, 9.5)]
        team_a = make_mock_team("Team A", 1, roster_a)
        team_b = make_mock_team("Team B", 2, roster_b)

        result = evaluate_trade(team_a, ["PlayerA"], team_b, ["PlayerB"])
        self.assertEqual(result["verdict"], "FAIR")

    def test_unfair_trade(self):
        roster_a = [make_mock_player("Star", "RB", 300, 280, 20)]
        roster_b = [make_mock_player("Scrub", "RB", 50, 60, 3)]
        team_a = make_mock_team("Team A", 1, roster_a)
        team_b = make_mock_team("Team B", 2, roster_b)

        result = evaluate_trade(team_a, ["Star"], team_b, ["Scrub"])
        self.assertNotEqual(result["verdict"], "FAIR")
        self.assertLess(result["difference"], 0)


if __name__ == "__main__":
    unittest.main()
