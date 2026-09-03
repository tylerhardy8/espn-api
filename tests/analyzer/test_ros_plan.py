"""Tests for rest-of-season values and the budget plan."""

import unittest
from types import SimpleNamespace

from fantasy_football_analyzer.ros import ros_projection, playoff_weeks
from fantasy_football_analyzer.plan import build_budget_plan, format_plan_for_ai
from fantasy_football_analyzer.draft_tracker import DraftState


def league(current_week=0, reg=14, total=17):
    settings = SimpleNamespace(reg_season_count=reg, matchup_periods={str(i): [i] for i in range(1, total + 1)},
                               draft_type="AUCTION", auction_budget=320, draft_pick_order=[],
                               position_slot_counts={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1,
                                                     "RB/WR/TE": 2, "BE": 6, "LB": 1, "DL": 1, "DB": 1, "P": 1, "HC": 1})
    teams = [SimpleNamespace(team_id=i + 1, team_name=f"Team {i + 1}", roster=[]) for i in range(12)]
    return SimpleNamespace(settings=settings, current_week=current_week, teams=teams, player_map={})


def player(proj, total=0.0, injury="", stats=None):
    return SimpleNamespace(projected_total_points=proj, total_points=total, injuryStatus=injury,
                           stats=stats or {}, schedule={})


class RosTests(unittest.TestCase):
    def test_playoff_weeks(self):
        self.assertEqual(playoff_weeks(league()), [15, 16, 17])

    def test_preseason_equals_projection(self):
        self.assertAlmostEqual(ros_projection(player(340), league(0)), 340, delta=0.5)

    def test_prorates_and_blends_pace(self):
        # week 9: 8 games played at 25/g (200 pts) vs a 17-game projection of 340 (20/g)
        v = ros_projection(player(340, total=200), league(9))
        per_week = 0.7 * 20 + 0.3 * 25
        self.assertAlmostEqual(v, per_week * 9, delta=0.5)

    def test_availability_scales(self):
        healthy = ros_projection(player(340), league(0))
        hurt = ros_projection(player(340, injury="INJURY_RESERVE"), league(0))
        self.assertAlmostEqual(hurt, healthy * 0.35, delta=0.5)

    def test_published_weekly_projection_used(self):
        stats = {16: {"projected_points": 40.0}}
        base = ros_projection(player(170), league(16))
        boosted = ros_projection(player(170, stats=stats), league(16))
        self.assertGreater(boosted, base)


class PlanTests(unittest.TestCase):
    def test_plan_shape(self):
        lg = league(0)
        pool = {}
        pid = 1
        for pos, n, top in (("QB", 20, 380), ("RB", 60, 330), ("WR", 60, 320), ("TE", 20, 240), ("K", 12, 150)):
            for i in range(n):
                pool[pid] = {"player_id": pid, "name": f"{pos}{i + 1}", "position": pos, "team": "X",
                             "value": max(1.0, top / 4 - i * 2), "projected_points": top - i * 4,
                             "tier": 1 + i // 5, "espn_value": None}
                pid += 1
        targets = {"QB": 1.6, "RB": 5.2, "WR": 5.0, "TE": 1.9, "K": 1.0}
        state = DraftState(lg, pool=pool, budget=320, targets=targets, roster_size=20)
        plan = build_budget_plan(state, "Team 1")
        self.assertEqual(plan["remaining"], 320)
        self.assertEqual(plan["slots_left"], 20)
        self.assertEqual(plan["stars_left"], 3)
        self.assertTrue(plan["targets"])
        total = sum(t["target"] for t in plan["targets"]) + plan["fillers"]
        self.assertLessEqual(total, 320 + len(plan["targets"]))   # rounding slack
        self.assertGreaterEqual(plan["targets"][0]["target"], plan["targets"][-1]["target"])
        self.assertIn("BUDGET PLAN", format_plan_for_ai(plan))
        self.assertEqual(plan["pace"]["read"], "on pace")


if __name__ == "__main__":
    unittest.main()
