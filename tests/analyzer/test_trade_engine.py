"""Tests for the week-by-week trade engine."""

import unittest
from types import SimpleNamespace

from fantasy_football_analyzer.trade_engine import TradeEngine, player_card, empty_starting_slots
from fantasy_football_analyzer.lineup import slot_profile

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "RB/WR/TE": 2, "BE": 6, "LB": 1, "DL": 1, "DB": 1, "P": 1, "HC": 1}


def player(pid, name, pos, proj, bye, total=0.0):
    return SimpleNamespace(playerId=pid, name=name, position=pos, proTeam="X", projected_total_points=proj,
                           total_points=total, injuryStatus="", stats={}, schedule={},
                           avg_points=0.0, lineupSlot="BE")


def league_with(rosters, current_week=1):
    settings = SimpleNamespace(reg_season_count=14, matchup_periods={str(i): [i] for i in range(1, 18)},
                               position_slot_counts=SLOTS, auction_budget=320, draft_type="AUCTION", draft_pick_order=[])
    teams = []
    for i, roster in enumerate(rosters):
        teams.append(SimpleNamespace(team_id=i + 1, team_name=f"Team {i + 1}", roster=roster, wins=0, losses=0,
                                     owners=[{"displayName": f"owner{i + 1}", "id": f"o{i + 1}"}]))
    return SimpleNamespace(settings=settings, teams=teams, current_week=current_week, league_id=1, year=2026)


def pool_from(league):
    pool = {}
    for t in league.teams:
        for p in t.roster:
            pool[p.playerId] = {"player_id": p.playerId, "name": p.name, "position": p.position,
                                "value": p.projected_total_points / 10, "bye": p._bye}
    return pool


def mk(pid, name, pos, proj, bye):
    p = player(pid, name, pos, proj, bye)
    p._bye = bye
    return p


class EngineTests(unittest.TestCase):
    def setUp(self):
        # Me: stars-and-scrubs with a week-13 pile-up and empty flex spots
        mine = [mk(1, "QB A", "QB", 400, 7), mk(2, "RB A", "RB", 330, 13), mk(3, "RB B", "RB", 280, 13),
                mk(4, "WR A", "WR", 300, 13), mk(5, "WR B", "WR", 260, 14), mk(6, "TE A", "TE", 280, 13),
                mk(7, "K A", "K", 150, 9)]
        # Them: deep at WR with different byes, weak TE
        theirs = [mk(11, "QB C", "QB", 350, 5), mk(12, "RB C", "RB", 260, 6), mk(13, "RB D", "RB", 230, 8),
                  mk(14, "WR C", "WR", 250, 6), mk(15, "WR D", "WR", 240, 9), mk(16, "WR E", "WR", 230, 10),
                  mk(17, "WR F", "WR", 220, 11), mk(18, "TE C", "TE", 120, 7), mk(19, "K C", "K", 140, 9),
                  mk(20, "WR G", "WR", 200, 12), mk(21, "RB E", "RB", 190, 5), mk(22, "WR H", "WR", 180, 6)]
        self.league = league_with([mine, theirs])
        self.pool = pool_from(self.league)
        self.engine = TradeEngine(self.league, pool=self.pool)
        self.me, self.them = self.league.teams

    def test_cards_carry_bye_and_weekly(self):
        card = player_card(self.me.roster[1], self.league, self.pool)
        self.assertEqual(card["bye"], 13)
        self.assertEqual(card["weekly"][13], 0.0)
        self.assertGreater(card["weekly"][12], 0)
        self.assertAlmostEqual(sum(card["weekly"].values()), card["ros"], delta=1.0)

    def test_needs_and_holes(self):
        needs = self.engine.needs(self.me)
        self.assertNotIn("QB", needs)          # one bye week is not a need
        self.assertIn("FLEX", needs)           # two empty flex spots all season
        holes13 = empty_starting_slots(self.engine.cards(self.me), self.engine.profile, 13)
        self.assertGreaterEqual(holes13, 4)   # RB A, RB B, WR A, TE A all sit
        holes12 = empty_starting_slots(self.engine.cards(self.me), self.engine.profile, 12)
        self.assertEqual(holes12, 2)          # two empty flex spots all season

    def test_matches_find_a_two_for_one_that_fills_flex(self):
        partners = self.engine.matches(self.me)
        self.assertTrue(partners)
        props = partners[0]["proposals"]
        self.assertTrue(props)
        best = props[0]
        self.assertGreater(best["my_net"], 0)
        self.assertGreaterEqual(best["acceptance"], 0.0)
        self.assertIn("Your lineup", best["reason"])
        # Something fills my empty flex: every proposal returns at least as many bodies as it sends
        self.assertTrue(any(len(p["receive_players"]) >= len(p["give_players"]) for p in props))

    def test_target_and_shop(self):
        t = self.engine.target(self.me, "WR C")
        self.assertEqual(t["partner"], "Team 2")
        self.assertTrue(t["packages"])
        s = self.engine.shop(self.me, "TE A")
        self.assertEqual(s["player"]["name"], "TE A")
        self.assertTrue(s["offers"])
        self.assertTrue(all(o["partner"] == "Team 2" for o in s["offers"]))

    def test_evaluate_with_counter(self):
        # Lopsided against me: my TE A for their TE C
        r = self.engine.evaluate(self.me, self.them, ["TE A"], ["TE C"])
        self.assertEqual(r["verdict"], "DECLINE")
        # Reasonable: their WR C for my K? not core — use RB B for WR C + WR D
        r = self.engine.evaluate(self.me, self.them, ["RB B"], ["WR C", "WR D"])
        self.assertIn(r["verdict"], ("ACCEPT", "FAIR", "COUNTER"))
        self.assertIn("offer", r)

    def test_playoff_weeks_weighted(self):
        weights = self.engine.weights
        self.assertEqual(weights[16], 1.5)
        self.assertEqual(weights[10], 1.0)


if __name__ == "__main__":
    unittest.main()
