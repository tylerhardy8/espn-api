"""Tests for the AI advisor module.

Tests cover context building and logic; actual API calls are mocked.
"""

import unittest
from unittest.mock import MagicMock, patch
from fantasy_football_analyzer.ai_advisor import (
    build_draft_context,
    _lookup_player_position,
    LiveDraftAdvisor,
)
from fantasy_football_analyzer.draft_tracker import DraftState


def make_mock_player(name, player_id, position):
    player = MagicMock()
    player.name = name
    player.playerId = player_id
    player.position = position
    return player


def make_mock_league(num_teams=4):
    league = MagicMock()
    league.teams = []
    for i in range(num_teams):
        team = MagicMock()
        team.team_name = f"Team {i + 1}"
        team.team_id = i + 1
        team.roster = [
            make_mock_player(f"Player {i}A", i * 10, "QB"),
            make_mock_player(f"Player {i}B", i * 10 + 1, "RB"),
        ]
        league.teams.append(team)

    league.player_map = {
        100: "Star QB",
        101: "Star RB",
        102: "Star WR",
        "Star QB": 100,
        "Star RB": 101,
        "Star WR": 102,
    }
    league.draft = []
    league.settings = MagicMock()
    league.settings.name = "Test League"
    league.settings.scoring_format = [
        {"label": "Each Reception", "abbr": "REC", "points": 1}
    ]
    league.settings.position_slot_counts = {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D/ST": 1, "K": 1, "BE": 6,
    }
    return league


class TestBuildDraftContext(unittest.TestCase):
    def test_produces_context_string(self):
        league = make_mock_league()
        state = DraftState(league)

        context = build_draft_context(state, "Team 1", league)

        self.assertIn("DRAFT STATE", context)
        self.assertIn("Team 1", context)
        self.assertIn("Test League", context)

    def test_context_includes_settings(self):
        league = make_mock_league()
        state = DraftState(league)

        context = build_draft_context(state, "Team 1", league)

        self.assertIn("Full PPR", context)
        self.assertIn("Roster slots", context)

    def test_context_includes_recent_picks(self):
        league = make_mock_league()
        state = DraftState(league)

        pick = MagicMock()
        pick.playerId = 100
        pick.playerName = "Star QB"
        pick.round_num = 1
        pick.round_pick = 1
        pick.bid_amount = 0
        pick.keeper_status = False
        pick.team = league.teams[0]

        state.apply_picks([pick])
        context = build_draft_context(state, "Team 1", league)

        self.assertIn("RECENT PICKS", context)
        self.assertIn("Star QB", context)

    def test_context_with_no_picks(self):
        league = make_mock_league()
        state = DraftState(league)

        context = build_draft_context(state, "Team 2", league)

        self.assertIn("No picks yet", context)


class TestLookupPlayerPosition(unittest.TestCase):
    def test_finds_position(self):
        league = make_mock_league()
        # Player 0A has id=0 and position="QB"
        pos = _lookup_player_position(0, league)
        self.assertEqual(pos, "QB")

    def test_returns_none_for_unknown(self):
        league = make_mock_league()
        pos = _lookup_player_position(9999, league)
        self.assertIsNone(pos)


class TestLiveDraftAdvisor(unittest.TestCase):
    def test_initialization(self):
        league = make_mock_league()
        advisor = LiveDraftAdvisor(league, "Team 1", poll_interval=5)

        self.assertEqual(advisor.my_team_name, "Team 1")
        self.assertEqual(advisor.my_team_id, 1)
        self.assertEqual(advisor.poll_interval, 5)
        self.assertTrue(advisor.auto_advise)

    def test_finds_team_id(self):
        league = make_mock_league()
        advisor = LiveDraftAdvisor(league, "Team 3")
        self.assertEqual(advisor.my_team_id, 3)

    def test_unknown_team(self):
        league = make_mock_league()
        advisor = LiveDraftAdvisor(league, "Nonexistent Team")
        self.assertIsNone(advisor.my_team_id)

    @patch("fantasy_football_analyzer.ai_advisor.get_ai_recommendation")
    def test_get_recommendation(self, mock_ai):
        mock_ai.return_value = "RECOMMENDATION: Star QB (QB, KC)\nREASONING: Best available."

        league = make_mock_league()
        advisor = LiveDraftAdvisor(league, "Team 1")
        advisor.get_recommendation()

        mock_ai.assert_called_once()
        call_args = mock_ai.call_args
        self.assertEqual(call_args[0][1], "Team 1")


if __name__ == "__main__":
    unittest.main()
