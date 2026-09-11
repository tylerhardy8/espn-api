import unittest
from types import SimpleNamespace

from fantasy_football_analyzer.faab import (
    faab_state, suggest_bid, replacement_levels, league_aggressiveness,
)


def league(spent=(0, 40, 100), budget=150, week=3):
    settings = SimpleNamespace(faab=True, acquisition_budget=budget)
    teams = [SimpleNamespace(team_id=i + 1, team_name=f"Team {i + 1}", acquisition_budget_spent=s)
             for i, s in enumerate(spent)]
    return SimpleNamespace(settings=settings, teams=teams, current_week=week)


FAS = [{"position": "RB", "ros": v} for v in (200, 150, 120, 110, 100, 90, 80)] + \
      [{"position": "WR", "ros": v} for v in (160, 140, 130, 125, 120, 110)]


class FaabTests(unittest.TestCase):
    def test_state(self):
        s = faab_state(league())
        self.assertEqual(s["budget"], 150)
        self.assertEqual(s["teams"][0]["remaining"], 150)
        self.assertEqual(s["teams"][-1]["remaining"], 50)

    def test_replacement(self):
        r = replacement_levels(FAS)
        self.assertEqual(r["RB"], 100)   # 5th best
        self.assertEqual(r["WR"], 120)

    def test_league_winner_bid_scales_with_rivals(self):
        lg = league()
        player = {"name": "X", "position": "RB", "ros": 200}   # +100 over replacement -> league-winner
        quiet = suggest_bid(player, "Team 1", lg, FAS, my_marginal=80)
        needy = suggest_bid(player, "Team 1", lg, FAS, my_marginal=80,
                            rival_needs={"Team 2": {"RB"}})
        self.assertEqual(quiet["tier"], "league-winner")
        self.assertGreater(needy["expected_rival"], quiet["expected_rival"])
        self.assertGreaterEqual(needy["bid"], quiet["bid"])
        self.assertLessEqual(needy["bid"], 150)

    def test_depth_is_a_dollar_and_useless_is_zero(self):
        lg = league()
        depth = {"name": "D", "position": "WR", "ros": 121}
        self.assertEqual(suggest_bid(depth, "Team 1", lg, FAS, my_marginal=2)["bid"], 1)
        self.assertEqual(suggest_bid(depth, "Team 1", lg, FAS, my_marginal=0)["bid"], 0)

    def test_late_season_spends_more_of_what_is_left(self):
        player = {"name": "X", "position": "RB", "ros": 140}   # starter tier
        early = suggest_bid(player, "Team 1", league(week=2), FAS, my_marginal=30)
        late = suggest_bid(player, "Team 1", league(week=14), FAS, my_marginal=30)
        self.assertGreater(late["high"], early["high"])

    def test_aggressiveness_from_history(self):
        self.assertEqual(league_aggressiveness([], 150), 1.0)
        hot = [{"bid": b} for b in (30, 40, 25, 45, 35)]
        self.assertGreater(league_aggressiveness(hot, 150), 1.5)
        cold = [{"bid": b} for b in (5, 6, 5, 7, 5)]
        self.assertLess(league_aggressiveness(cold, 150), 0.7)


if __name__ == "__main__":
    unittest.main()
