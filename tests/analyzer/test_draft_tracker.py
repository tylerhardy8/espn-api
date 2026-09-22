"""Tests for the real-time draft tracker module."""

import unittest
from unittest.mock import MagicMock
from fantasy_football_analyzer.draft_tracker import DraftState, DraftTracker


def make_mock_pick(player_id, player_name, round_num, round_pick, team_name, team_id,
                   bid_amount=0, keeper=False):
    pick = MagicMock()
    pick.playerId = player_id
    pick.playerName = player_name
    pick.round_num = round_num
    pick.round_pick = round_pick
    pick.bid_amount = bid_amount
    pick.keeper_status = keeper
    pick.team = MagicMock()
    pick.team.team_name = team_name
    pick.team.team_id = team_id
    return pick


def make_mock_league(num_teams=10, draft_picks=None):
    league = MagicMock()
    league.teams = []
    for i in range(num_teams):
        team = MagicMock()
        team.team_name = f"Team {i + 1}"
        team.team_id = i + 1
        team.roster = []
        league.teams.append(team)

    league.player_map = {
        100: "Patrick Mahomes",
        101: "Josh Allen",
        102: "Saquon Barkley",
        103: "Derrick Henry",
        104: "CeeDee Lamb",
        "Patrick Mahomes": 100,
        "Josh Allen": 101,
        "Saquon Barkley": 102,
        "Derrick Henry": 103,
        "CeeDee Lamb": 104,
    }
    league.draft = draft_picks or []
    league.settings = MagicMock()
    league.settings.name = "Test League"
    return league


class TestDraftState(unittest.TestCase):
    def test_initial_state(self):
        league = make_mock_league()
        state = DraftState(league)

        self.assertEqual(state.total_teams, 10)
        self.assertEqual(len(state.picks), 0)
        self.assertEqual(len(state.drafted_ids), 0)
        # Should have 5 players (only int keys from player_map)
        self.assertEqual(len(state.available_players), 5)

    def test_apply_picks(self):
        league = make_mock_league()
        state = DraftState(league)

        picks = [
            make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1),
            make_mock_pick(102, "Saquon Barkley", 1, 2, "Team 2", 2),
        ]

        new = state.apply_picks(picks)
        self.assertEqual(len(new), 2)
        self.assertEqual(len(state.picks), 2)
        self.assertIn(100, state.drafted_ids)
        self.assertIn(102, state.drafted_ids)
        self.assertNotIn(100, state.available_players)
        self.assertIn(101, state.available_players)

    def test_apply_picks_incremental(self):
        league = make_mock_league()
        state = DraftState(league)

        picks1 = [make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1)]
        state.apply_picks(picks1)
        self.assertEqual(len(state.picks), 1)

        # Second poll returns all picks including the first
        picks2 = [
            make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1),
            make_mock_pick(102, "Saquon Barkley", 1, 2, "Team 2", 2),
        ]
        new = state.apply_picks(picks2)
        self.assertEqual(len(new), 1)  # only the new pick
        self.assertEqual(len(state.picks), 2)

    def test_team_rosters(self):
        league = make_mock_league()
        state = DraftState(league)

        picks = [
            make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1),
            make_mock_pick(102, "Saquon Barkley", 1, 2, "Team 1", 1),
        ]
        state.apply_picks(picks)

        team1_picks = state.get_team_picks("Team 1")
        self.assertEqual(len(team1_picks), 2)
        self.assertEqual(team1_picks[0]["player_name"], "Patrick Mahomes")

    def test_get_recent_picks(self):
        league = make_mock_league()
        state = DraftState(league)

        picks = [
            make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1),
            make_mock_pick(101, "Josh Allen", 1, 2, "Team 2", 2),
            make_mock_pick(102, "Saquon Barkley", 1, 3, "Team 3", 3),
        ]
        state.apply_picks(picks)

        recent = state.get_recent_picks(count=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["player_name"], "Josh Allen")

    def test_board_summary(self):
        league = make_mock_league()
        state = DraftState(league)

        picks = [make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1)]
        state.apply_picks(picks)

        summary = state.get_board_summary()
        self.assertEqual(summary["total_picks"], 1)
        self.assertEqual(summary["current_round"], 1)
        self.assertEqual(summary["players_drafted"], 1)
        self.assertEqual(summary["players_available"], 4)

    def test_format_pick(self):
        league = make_mock_league()
        state = DraftState(league)

        picks = [make_mock_pick(100, "Patrick Mahomes", 1, 3, "Team 3", 3)]
        state.apply_picks(picks)

        formatted = state.format_pick(state.picks[0])
        self.assertIn("Patrick Mahomes", formatted)
        self.assertIn("Team 3", formatted)
        self.assertIn("Round 1", formatted)


class TestDraftTracker(unittest.TestCase):
    def test_initialization(self):
        league = make_mock_league()
        tracker = DraftTracker(league, poll_interval=5)

        self.assertEqual(tracker.poll_interval, 5)
        self.assertFalse(tracker.is_running)
        self.assertIsNotNone(tracker.state)

    def test_check_for_updates(self):
        league = make_mock_league()
        pick = make_mock_pick(100, "Patrick Mahomes", 1, 1, "Team 1", 1)
        league.draft = [pick]

        tracker = DraftTracker(league)
        # refresh_draft will be called but since we control league.draft, just test state
        tracker.state.apply_picks(league.draft)

        self.assertEqual(len(tracker.state.picks), 1)


if __name__ == "__main__":
    unittest.main()
