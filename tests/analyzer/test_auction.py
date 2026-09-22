"""Tests for the auction valuation pipeline (invariants on a fixture league)."""

import unittest
from types import SimpleNamespace

from fantasy_football_analyzer import auction
from fantasy_football_analyzer.auction import (
    calculate_auction_values, finalize_values, normalize_values, availability_multiplier,
    blend_value, bye_weeks_by_pro_team, build_valued_pool, CAPPED_POSITIONS,
)
from fantasy_football_analyzer.lineup import slot_profile
from fantasy_football_analyzer.draft_tracker import DraftState

PAPA_TRUMP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "LB": 1, "DL": 1, "DB": 1, "K": 1,
                    "P": 1, "HC": 1, "BE": 6, "IR": 1, "RB/WR/TE": 2}
NUM_TEAMS, BUDGET = 12, 320


def fake_player(pid, name, pos, proj, team="DET", espn_value=None, injury=""):
    return SimpleNamespace(
        playerId=pid, name=name, position=pos, proTeam=team,
        projected_total_points=proj, total_points=0.0,
        auction_value_avg=espn_value if espn_value is not None else -1,
        avg_draft_position=-1, injuryStatus=injury,
    )


def fake_players():
    players, pid = [], 1000
    curves = {"QB": (40, 400, 8), "RB": (90, 330, 5), "WR": (110, 320, 4), "TE": (40, 240, 7),
              "K": (32, 150, 2), "D/ST": (32, 130, 2.5)}
    for pos, (n, top, step) in curves.items():
        for i in range(n):
            pid += 1
            players.append(fake_player(pid, f"{pos}{i + 1}", pos, top - step * i))
    for pos in ("LB", "DE", "DB"):
        for i in range(4):
            pid += 1
            players.append(fake_player(pid, f"{pos}{i + 1}", pos, 120 - 5 * i))
    return players


def fake_league(slots=PAPA_TRUMP_SLOTS, budget=BUDGET, players=None):
    players = players or fake_players()
    settings = SimpleNamespace(position_slot_counts=slots, auction_budget=budget,
                               draft_type="AUCTION", draft_pick_order=[])
    teams = [SimpleNamespace(team_id=i + 1, team_name=f"Team {i + 1}", roster=[])
             for i in range(NUM_TEAMS)]
    league = SimpleNamespace(settings=settings, teams=teams, league_id=1, year=2026,
                             player_map={})
    league.free_agents = lambda size=400, **kw: players[:size]
    league._get_all_pro_schedule = lambda: {}
    return league


def valued_pool(**kw):
    league = fake_league(**kw)
    return build_valued_pool(league, enrich=False), league


class BaselineTests(unittest.TestCase):
    def setUp(self):
        (self.pool, self.budget, self.targets, self.roster_size), self.league = valued_pool()
        self.by_pos = {}
        for e in self.pool.values():
            self.by_pos.setdefault(e["position"], []).append(e)
        for lst in self.by_pos.values():
            lst.sort(key=lambda e: e["pos_rank"])

    def test_roster_size_and_targets(self):
        self.assertEqual(self.roster_size, 20)
        self.assertNotIn("D/ST", self.targets)
        self.assertAlmostEqual(self.targets["RB"], 2.8 + 2.4)

    def test_starter_baselines(self):
        # starter targets QB1.0 RB2.8 WR2.9 TE1.3 x 12 -> QB12 RB34 WR35 TE16
        for pos, idx in (("QB", 12), ("RB", 34), ("WR", 35), ("TE", 16)):
            ranked = self.by_pos[pos]
            self.assertEqual(ranked[idx - 1]["vbd"], 0.0, pos)
            self.assertGreater(ranked[idx - 2]["vbd"], 0.0, pos)

    def test_depth_band_prices(self):
        rbs = self.by_pos["RB"]
        for e in rbs[34:61]:   # RB35..RB61: depth band, a few dollars, no cliff
            self.assertGreater(e["value"], 1.0, e["name"])
            self.assertLessEqual(e["value"], 8.0, e["name"])
        self.assertGreater(rbs[34]["value"], rbs[50]["value"])

    def test_no_slot_position_is_floor(self):
        for e in self.by_pos["D/ST"]:
            self.assertEqual(e["value"], 1.0)
            self.assertEqual(e["vbd"], 0.0)
        self.assertGreater(self.by_pos["D/ST"][0]["pos_rank"], 0)

    def test_kicker_cap(self):
        ks = self.by_pos["K"]
        self.assertEqual([e["value"] for e in ks[:2]], [2.0, 2.0])
        self.assertTrue(all(e["value"] == 1.0 for e in ks[2:]))

    def test_noncore_positions_floor(self):
        for pos in ("LB", "DE", "DB"):
            self.assertTrue(all(e["value"] == 1.0 for e in self.by_pos[pos]))

    def test_floor_and_cash_sum(self):
        self.assertTrue(all(e["value"] >= 1.0 for e in self.pool.values()))
        core = sorted((e["value"] for e in self.pool.values()
                       if e["position"] in ("QB", "RB", "WR", "TE", "K", "D/ST")), reverse=True)
        self.assertAlmostEqual(sum(core[:NUM_TEAMS * 15]), NUM_TEAMS * BUDGET - 60, delta=90)

    def test_inflation_starts_near_one(self):
        state = DraftState(self.league, pool=self.pool, budget=self.budget,
                           targets=self.targets, roster_size=self.roster_size)
        self.assertAlmostEqual(state.get_inflation(), 1.0, delta=0.03)

    def test_top_end_concentration(self):
        rbs = self.by_pos["RB"]
        self.assertGreater(rbs[0]["value"], 0.25 * BUDGET)   # an elite RB is a quarter of a budget
        self.assertGreater(rbs[0]["value"], rbs[9]["value"] * 1.2)
        qb_dollars = sum(e["value"] for e in self.by_pos["QB"])
        total = sum(e["value"] for e in self.pool.values())
        self.assertLess(qb_dollars / total, 0.15)   # 1-QB league: QB share stays modest

    def test_position_shares_in_range(self):
        total = sum(e["value"] for e in self.pool.values())
        for pos, lo, hi in (("RB", 0.30, 0.50), ("WR", 0.30, 0.50), ("TE", 0.04, 0.15)):
            share = sum(e["value"] for e in self.by_pos[pos]) / total
            self.assertTrue(lo <= share <= hi, f"{pos} share {share:.3f}")


class BlendTests(unittest.TestCase):
    def test_crowd_value_scaled_to_budget(self):
        players = fake_players()
        next(p for p in players if p.name == "RB1").auction_value_avg = 50
        (pool, *_), _ = valued_pool(players=players)
        rb1 = next(e for e in pool.values() if e["name"] == "RB1")
        self.assertEqual(rb1["espn_value"], 50)
        self.assertEqual(rb1["crowd_value"], 80.0)

    def test_blend_renormalizes_over_present_signals(self):
        self.assertAlmostEqual(blend_value({"model_value": 40, "crowd_value": None,
                                            "expert_value": None}), 40)
        blended = blend_value({"model_value": 40, "crowd_value": 60, "expert_value": None})
        self.assertAlmostEqual(blended, (0.45 * 40 + 0.20 * 60) / 0.65)
        blended = blend_value({"model_value": 40, "crowd_value": 60, "expert_value": 50})
        self.assertAlmostEqual(blended, 0.45 * 40 + 0.20 * 60 + 0.35 * 50)

    def test_cap_applies_after_blend(self):
        players = fake_players()
        k1 = next(p for p in players if p.name == "K1")
        k1.auction_value_avg = 9
        (pool, *_), _ = valued_pool(players=players)
        self.assertEqual(next(e for e in pool.values() if e["name"] == "K1")["value"], 2.0)


class AvailabilityTests(unittest.TestCase):
    def test_table(self):
        self.assertEqual(availability_multiplier({}), 1.0)
        self.assertEqual(availability_multiplier({"injury_status": "ACTIVE"}), 1.0)
        self.assertEqual(availability_multiplier({"injury_status": "QUESTIONABLE"}), 0.97)
        self.assertEqual(availability_multiplier({"injury_status": "OUT"}), 0.85)
        self.assertEqual(availability_multiplier({"injury_status": "INJURY_RESERVE"}), 0.35)
        self.assertEqual(availability_multiplier({"sleeper_injury": "IR"}), 0.35)
        self.assertEqual(availability_multiplier({"sleeper_injury": "PUP"}), 0.65)
        self.assertEqual(availability_multiplier({"sleeper_status": "Inactive"}), 0.20)
        # minimum over statuses
        self.assertEqual(availability_multiplier({"injury_status": "QUESTIONABLE",
                                                  "sleeper_injury": "Out"}), 0.85)

    def test_availability_prices_value(self):
        players = fake_players()
        rb2 = next(p for p in players if p.name == "RB2")
        rb2.injuryStatus = "INJURY_RESERVE"
        (pool, *_), _ = valued_pool(players=players)
        rb1 = next(e for e in pool.values() if e["name"] == "RB1")
        rb2e = next(e for e in pool.values() if e["name"] == "RB2")
        rb3 = next(e for e in pool.values() if e["name"] == "RB3")
        self.assertEqual(rb2e["availability"], 0.35)
        self.assertLess(rb2e["value"], rb3["value"])
        self.assertLess(rb2e["value"], 0.45 * rb1["value"])


class NormalizeTests(unittest.TestCase):
    def test_normalize_over_core_slots(self):
        pool = {i: {"position": "RB", "value": 50.0} for i in range(30)}
        pool.update({100 + i: {"position": "LB", "value": 1.0} for i in range(10)})
        normalize_values(pool, budget=100, num_teams=2, roster_size=12, noncore_slots=2)
        core = sorted((e["value"] for e in pool.values() if e["position"] == "RB"), reverse=True)
        self.assertAlmostEqual(sum(core[:20]), 2 * 100 - 2 * 2 * 1.0, delta=2)
        self.assertTrue(all(e["value"] == 1.0 for e in pool.values() if e["position"] == "LB"))


class ByeTests(unittest.TestCase):
    def test_bye_map_from_schedule(self):
        from espn_api.football.constant import PRO_TEAM_MAP
        det = next(k for k, v in PRO_TEAM_MAP.items() if v == "DET")
        kc = next(k for k, v in PRO_TEAM_MAP.items() if v == "KC")
        league = SimpleNamespace()
        league._get_all_pro_schedule = lambda: {
            det: {str(w): [{"id": 1}] for w in range(1, 19) if w != 6},   # DET bye 6
            kc: {str(w): [{"id": 1}] for w in range(1, 19) if w != 11},   # KC bye 11
        }
        byes = bye_weeks_by_pro_team(league)
        self.assertEqual(byes.get("DET"), 6)
        self.assertEqual(byes.get("KC"), 11)
        self.assertIs(bye_weeks_by_pro_team(league), byes)  # cached

    def test_bye_map_failure_is_empty(self):
        league = SimpleNamespace()
        def boom(): raise RuntimeError("no network")
        league._get_all_pro_schedule = boom
        self.assertEqual(bye_weeks_by_pro_team(league), {})


class LegacyPathTests(unittest.TestCase):
    def test_no_settings_fallback(self):
        players = fake_players()
        league = fake_league(players=players)
        league.settings.position_slot_counts = {}
        pool, budget, targets, roster_size = build_valued_pool(league, enrich=False)
        self.assertEqual(roster_size, 16)
        self.assertTrue(all(e["value"] >= 1.0 for e in pool.values()))
        self.assertIn("D/ST", targets)


if __name__ == "__main__":
    unittest.main()
