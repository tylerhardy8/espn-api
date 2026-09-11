"""Tests for the waiver wire recommendations module."""

import unittest
from unittest.mock import MagicMock, patch
from fantasy_football_analyzer.waivers import (
    get_top_free_agents,
    find_streamers,
    get_waiver_recommendations,
)


def make_mock_free_agent(name, position, projected, points, total, avg,
                         pct_owned=20, pct_started=5, opp="NYG",
                         pos_rank=10, on_bye=False, injury=None):
    player = MagicMock()
    player.name = name
    player.playerId = hash(name)
    player.position = position
    player.proTeam = "KC"
    player.projected_points = projected
    player.points = points
    player.total_points = total
    player.avg_points = avg
    player.percent_owned = pct_owned
    player.percent_started = pct_started
    player.pro_opponent = opp
    player.pro_pos_rank = pos_rank
    player.on_bye_week = on_bye
    player.injuryStatus = injury
    return player


class TestGetTopFreeAgents(unittest.TestCase):
    def test_returns_sorted_agents(self):
        fa1 = make_mock_free_agent("FA1", "RB", 15.0, 12.0, 120.0, 10.0)
        fa2 = make_mock_free_agent("FA2", "WR", 18.0, 14.0, 140.0, 12.0)

        league = MagicMock()
        league.free_agents.return_value = [fa1, fa2]

        result = get_top_free_agents(league, week=5, size=10)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "FA2")  # higher projected
        self.assertGreater(result[0]["projected_points"], result[1]["projected_points"])

    def test_handles_exception(self):
        league = MagicMock()
        league.free_agents.side_effect = Exception("API Error")

        result = get_top_free_agents(league, week=5)
        self.assertEqual(result, [])


class TestGetWaiverRecommendations(unittest.TestCase):
    def test_personalized_recommendations(self):
        # Create a team with a weak RB
        weak_rb = MagicMock()
        weak_rb.name = "Weak RB"
        weak_rb.position = "RB"
        weak_rb.total_points = 30
        weak_rb.avg_points = 3.0
        weak_rb.lineupSlot = "RB"

        team = MagicMock()
        team.team_name = "My Team"
        team.team_id = 1
        team.roster = [weak_rb]

        league = MagicMock()
        league.teams = [team]
        league.current_week = 5

        # Mock free agents with a better RB
        better_rb = make_mock_free_agent("Better RB", "RB", 12.0, 10.0, 100.0, 8.0)
        league.free_agents.return_value = [better_rb]

        result = get_waiver_recommendations(league, "My Team", week=5)
        self.assertTrue(len(result) > 0)
        self.assertEqual(result[0]["name"], "Better RB")
        self.assertGreater(result[0]["upgrade_per_week"], 0)


if __name__ == "__main__":
    unittest.main()
