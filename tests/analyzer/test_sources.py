"""Tests for external-source scoring and projection blending."""

import unittest
from types import SimpleNamespace

from fantasy_football_analyzer import sources
from fantasy_football_analyzer.sources import score_stat_line, blended_projection, league_stat_points


def league_with_te_premium():
    raw = {"scoringItems": [
        {"statId": 3, "points": 0.04}, {"statId": 4, "points": 6.0}, {"statId": 20, "points": -2.0},
        {"statId": 24, "points": 0.1}, {"statId": 25, "points": 6.0},
        {"statId": 42, "points": 0.0, "pointsOverrides": {"1": 0.1, "2": 0.1, "3": 0.1, "4": 0.1}},
        {"statId": 43, "points": 0.0, "pointsOverrides": {"1": 6.0, "2": 6.0, "3": 6.0, "4": 6.0}},
        {"statId": 53, "points": 0.0, "pointsOverrides": {"1": 1.0, "2": 1.0, "3": 1.0, "4": 1.5}},
        {"statId": 72, "points": -2.0},
    ]}
    return SimpleNamespace(settings=SimpleNamespace(_raw_scoring_settings=raw), year=2026, league_id=1)


class ScoringTests(unittest.TestCase):
    def test_rb_line(self):
        lg = league_with_te_premium()
        stats = {"rush_yds": 1000, "rush_tds": 10, "rec_rec": 50, "rec_yds": 400, "rec_tds": 2, "fumbles": 2}
        # 100 + 60 + 50 + 40 + 12 - 2 (half of 2 fumbles lost x -2) = 260
        self.assertAlmostEqual(score_stat_line(stats, "RB", lg), 260.0)

    def test_te_premium(self):
        lg = league_with_te_premium()
        stats = {"rec_rec": 100, "rec_yds": 1000, "rec_tds": 5}
        te = score_stat_line(stats, "TE", lg)
        wr = score_stat_line(stats, "WR", lg)
        self.assertAlmostEqual(te - wr, 50.0)   # 0.5 extra per catch

    def test_qb_line(self):
        lg = league_with_te_premium()
        stats = {"pass_yds": 4000, "pass_tds": 30, "pass_ints": 10, "rush_yds": 300, "rush_tds": 3}
        self.assertAlmostEqual(score_stat_line(stats, "QB", lg), 160 + 180 - 20 + 30 + 18)

    def test_kicker_uses_fp_points(self):
        self.assertEqual(score_stat_line({"points": 151.4, "fg": 34}, "K", league_with_te_premium()), 151.4)

    def test_defaults_without_settings(self):
        table = league_stat_points(SimpleNamespace(settings=None))
        self.assertEqual(table[53]["base"], 1.0)


class BlendTests(unittest.TestCase):
    def test_blend_and_fallbacks(self):
        lg = league_with_te_premium()
        fp_map = {("breece hall", "RB"): 250.0}
        self.assertEqual(blended_projection("Breece Hall", "RB", 290.0, lg, fp_map), (270.0, 250.0))
        self.assertEqual(blended_projection("Breece Hall", "RB", 0, lg, fp_map), (250.0, 250.0))
        self.assertEqual(blended_projection("Nobody", "RB", 100.0, lg, fp_map), (100.0, None))


if __name__ == "__main__":
    unittest.main()
