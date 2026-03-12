"""Tests for the historical trends analyzer module."""

import unittest
from unittest.mock import MagicMock
from fantasy_football_analyzer.historical import (
    analyze_team_history,
    analyze_head_to_head,
    analyze_scoring_trends,
    analyze_manager_tendencies,
    analyze_draft_history,
    format_historical_report,
)


def make_mock_team(name, team_id, wins, losses, ties, pf, pa,
                   acquisitions=5, trades=1, drops=3, standing=1,
                   final_standing=0, schedule=None, scores=None,
                   outcomes=None, roster=None):
    """Create a mock Team object."""
    team = MagicMock()
    team.team_name = name
    team.team_id = team_id
    team.wins = wins
    team.losses = losses
    team.ties = ties
    team.points_for = pf
    team.points_against = pa
    team.acquisitions = acquisitions
    team.trades = trades
    team.drops = drops
    team.standing = standing
    team.final_standing = final_standing
    team.schedule = schedule or []
    team.scores = scores or []
    team.outcomes = outcomes or []
    team.roster = roster or []
    return team


def make_mock_league(teams, draft=None):
    """Create a mock League."""
    league = MagicMock()
    league.teams = teams
    league.draft = draft or []

    def standings():
        return sorted(teams, key=lambda t: t.final_standing if t.final_standing else t.standing)

    league.standings = standings
    return league


class TestAnalyzeTeamHistory(unittest.TestCase):
    def test_single_season(self):
        t1 = make_mock_team("Team A", 1, 10, 4, 0, 1500.0, 1200.0, standing=1, final_standing=1)
        t2 = make_mock_team("Team B", 2, 6, 8, 0, 1200.0, 1400.0, standing=2, final_standing=2)
        league = make_mock_league([t1, t2])

        result = analyze_team_history({2023: league})

        self.assertIn("Team A", result)
        self.assertIn("Team B", result)
        self.assertEqual(result["Team A"]["championships"], 1)
        self.assertEqual(result["Team B"]["championships"], 0)
        self.assertAlmostEqual(result["Team A"]["all_time_win_pct"], 10/14, places=3)

    def test_multi_season(self):
        t1_2022 = make_mock_team("Team A", 1, 8, 6, 0, 1400.0, 1300.0, standing=2, final_standing=2)
        t2_2022 = make_mock_team("Team B", 2, 10, 4, 0, 1500.0, 1200.0, standing=1, final_standing=1)
        t1_2023 = make_mock_team("Team A", 1, 10, 4, 0, 1500.0, 1200.0, standing=1, final_standing=1)
        t2_2023 = make_mock_team("Team B", 2, 6, 8, 0, 1100.0, 1300.0, standing=2, final_standing=2)
        league_2022 = make_mock_league([t1_2022, t2_2022])
        league_2023 = make_mock_league([t1_2023, t2_2023])

        result = analyze_team_history({2022: league_2022, 2023: league_2023})

        self.assertEqual(result["Team A"]["num_seasons"], 2)
        self.assertEqual(result["Team A"]["championships"], 1)
        self.assertAlmostEqual(result["Team A"]["avg_finish"], 1.5, places=1)


class TestAnalyzeHeadToHead(unittest.TestCase):
    def test_basic_h2h(self):
        t1 = make_mock_team("Team A", 1, 2, 0, 0, 200, 150)
        t2 = make_mock_team("Team B", 2, 0, 2, 0, 150, 200)
        t1.schedule = [t2, t2]
        t1.outcomes = ["W", "W"]
        t2.schedule = [t1, t1]
        t2.outcomes = ["L", "L"]
        league = make_mock_league([t1, t2])

        result = analyze_head_to_head({2023: league})

        self.assertEqual(result["Team A"]["Team B"]["wins"], 2)
        self.assertEqual(result["Team A"]["Team B"]["losses"], 0)


class TestAnalyzeScoringTrends(unittest.TestCase):
    def test_scoring_summary(self):
        t1 = make_mock_team("Team A", 1, 2, 0, 0, 200, 150, scores=[100, 120])
        t2 = make_mock_team("Team B", 2, 0, 2, 0, 150, 200, scores=[80, 90])
        league = make_mock_league([t1, t2])

        result = analyze_scoring_trends({2023: league})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["year"], 2023)
        self.assertEqual(result[0]["max_score"], 120)
        self.assertEqual(result[0]["min_score"], 80)


class TestAnalyzeManagerTendencies(unittest.TestCase):
    def test_tendencies(self):
        t1 = make_mock_team("Team A", 1, 10, 4, 0, 1500, 1200,
                            acquisitions=20, trades=3, drops=15)
        league = make_mock_league([t1])

        result = analyze_manager_tendencies({2023: league})

        self.assertEqual(result["Team A"]["total_acquisitions"], 20)
        self.assertEqual(result["Team A"]["avg_trades_per_season"], 3.0)


class TestFormatHistoricalReport(unittest.TestCase):
    def test_produces_report(self):
        t1 = make_mock_team("Team A", 1, 10, 4, 0, 1500.0, 1200.0,
                            standing=1, final_standing=1, scores=[100, 110],
                            schedule=[], outcomes=["W", "W"])
        league = make_mock_league([t1])

        report = format_historical_report({2023: league})

        self.assertIn("HISTORICAL LEAGUE ANALYSIS", report)
        self.assertIn("Team A", report)


if __name__ == "__main__":
    unittest.main()
