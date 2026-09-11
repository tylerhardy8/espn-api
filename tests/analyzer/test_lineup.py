"""Tests for lineup slot profiles and lineup value."""

import unittest

from fantasy_football_analyzer.lineup import (
    slot_profile, optimal_lineup, team_context_value, marginal_value, profile_from_targets,
)

PAPA_TRUMP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "LB": 1, "DL": 1, "DB": 1, "K": 1,
                    "P": 1, "HC": 1, "BE": 6, "IR": 1, "RB/WR/TE": 2}


class SlotProfileTests(unittest.TestCase):
    def test_papa_trump_profile(self):
        prof = slot_profile(PAPA_TRUMP_SLOTS)
        self.assertEqual(prof["roster_size"], 20)
        self.assertEqual(prof["noncore_slots"], 5)
        self.assertEqual(prof["core_size"], 15)
        self.assertEqual(prof["bench"], 6)
        st = prof["starter_targets"]
        self.assertAlmostEqual(st["QB"], 1.0)
        self.assertAlmostEqual(st["RB"], 2.8)
        self.assertAlmostEqual(st["WR"], 2.9)
        self.assertAlmostEqual(st["TE"], 1.3)
        self.assertAlmostEqual(st["K"], 1.0)
        self.assertNotIn("D/ST", st)          # no D/ST slot in this league
        self.assertNotIn("D/ST", prof["roster_targets"])
        self.assertAlmostEqual(prof["roster_targets"]["RB"], 2.8 + 6 * 0.40)

    def test_superflex_and_other_flex_labels(self):
        prof = slot_profile({"QB": 1, "OP": 1, "RB": 2, "WR": 2, "RB/WR": 1, "WR/TE": 1, "BE": 5})
        st = prof["starter_targets"]
        self.assertGreater(st["QB"], 1.5)     # superflex is mostly a QB
        self.assertAlmostEqual(st["RB"], 2 + 0.5 + 0.06)
        self.assertAlmostEqual(st["WR"], 2 + 0.5 + 0.8 + 0.07)
        self.assertAlmostEqual(st["TE"], 0.2 + 0.02)

    def test_unknown_and_zero_slots_ignored(self):
        prof = slot_profile({"QB": 1, "RB": 2, "WR": 0, "XX": 3, "BE": 0, "IR": 2})
        self.assertEqual(prof["starter_targets"], {"QB": 1.0, "RB": 2.0})
        self.assertEqual(prof["roster_size"], 6)  # QB1 + RB2 + XX3, IR excluded

    def test_profile_from_targets(self):
        prof = profile_from_targets({"QB": 2, "RB": 4}, roster_size=16)
        self.assertEqual(prof["roster_size"], 16)
        self.assertEqual(prof["starter_targets"], {"QB": 2.0, "RB": 4.0})


class LineupValueTests(unittest.TestCase):
    def setUp(self):
        self.prof = slot_profile(PAPA_TRUMP_SLOTS)

    def test_two_flex_filled_from_best_remaining(self):
        players = [
            {"position": "QB", "value": 300},
            {"position": "RB", "value": 250}, {"position": "RB", "value": 200},
            {"position": "RB", "value": 180},
            {"position": "WR", "value": 220}, {"position": "WR", "value": 210},
            {"position": "WR", "value": 150},
            {"position": "TE", "value": 120}, {"position": "TE", "value": 100},
            {"position": "K", "value": 90},
        ]
        starters, bench = optimal_lineup(players, self.prof)
        values = sorted(p["value"] for p in starters)
        # fixed: QB300 RB250 RB200 WR220 WR210 TE120 K90; flex x2: RB180, WR150
        self.assertEqual(values, sorted([300, 250, 200, 220, 210, 120, 90, 180, 150]))
        self.assertEqual([p["value"] for p in bench], [100])

    def test_marginal_value_fills_open_slot_fully(self):
        mine = [{"position": "RB", "value": 250}]
        gain = marginal_value(mine, {"position": "QB", "value": 300}, self.prof)
        self.assertAlmostEqual(gain, 300)

    def test_marginal_value_displacing_a_starter(self):
        mine = [{"position": "RB", "value": 300}, {"position": "RB", "value": 250},
                {"position": "WR", "value": 200}, {"position": "WR", "value": 200},
                {"position": "WR", "value": 200}, {"position": "WR", "value": 200},
                {"position": "TE", "value": 100}]
        # both flex slots hold 200-pt WRs; a 220 RB bumps one into bench insurance
        gain = marginal_value(mine, {"position": "RB", "value": 220}, self.prof)
        self.assertAlmostEqual(gain, 220 - 200 + 0.18 * 200)
        self.assertLess(gain, 220)
        self.assertGreater(gain, 0)

    def test_bench_insurance_weights(self):
        mine = [{"position": "QB", "value": 300}]
        # second QB only earns bench insurance
        gain = marginal_value(mine, {"position": "QB", "value": 280}, self.prof)
        self.assertAlmostEqual(gain, 0.18 * 280)
        self.assertLess(team_context_value(mine, self.prof), 301)


if __name__ == "__main__":
    unittest.main()
