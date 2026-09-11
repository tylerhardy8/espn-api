"""Backtest the auction model against a league's real past sale prices.

Rebuilds the player pool for a past season from ESPN's projections for that
season (still served after the fact), values it with the league's own
settings and budget, and compares to what the room actually paid — and to
what the players actually scored. Run from the repo root:

    uv run --no-project --with flask --with feedparser --with requests \
        python3 tools/backtest_2025.py --league-id 202314 --year 2025

Variants toggle the model's constants so before/after tables come from one
run. "old" loads the committed auction.py from git (HEAD~N or a ref).
"""

import argparse
import importlib.util
import os
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.getcwd())

from fantasy_football_analyzer import auction as new_auction  # noqa: E402
from fantasy_football_analyzer import sources  # noqa: E402
from fantasy_football_analyzer.config import load_config  # noqa: E402
from fantasy_football_analyzer.league_connector import connect_league  # noqa: E402


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def load_old_auction(ref):
    src = subprocess.check_output(["git", "show", f"{ref}:fantasy_football_analyzer/auction.py"]).decode()
    path = os.path.join(tempfile.mkdtemp(), "old_auction.py")
    with open(path, "w") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("fantasy_football_analyzer.old_auction", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "fantasy_football_analyzer"
    spec.loader.exec_module(mod)
    return mod


def fetch_pool(league, extra_free_agents=400):
    """Pool entries for every drafted player (+ free-agent depth) with that
    season's projections and actual points."""
    ids = [p.playerId for p in league.draft]
    players = {}
    for i in range(0, len(ids), 50):
        try:
            got = league.player_info(playerId=ids[i:i + 50]) or []
        except Exception as e:
            print("player_info failed:", e)
            continue
        for p in (got if isinstance(got, list) else [got]):
            players[p.playerId] = p
    try:
        for p in league.free_agents(size=extra_free_agents):
            players.setdefault(p.playerId, p)
    except Exception:
        pass
    return players


def value_variant(players, league, module, constants, use_fp):
    pool = {pid: module._pool_entry(p) for pid, p in players.items()}
    saved = {k: getattr(module, k) for k in constants if hasattr(module, k)}
    for k, v in constants.items():
        if hasattr(module, k):
            setattr(module, k, v)
    try:
        budget = league.settings.auction_budget
        n = len(league.teams)
        if hasattr(module, "league_profile"):
            profile = module.league_profile(league)
            module.calculate_auction_values(pool, budget, n, profile["roster_targets"], profile=profile)
            if use_fp:
                sources.fetch_sleeper_players = lambda: {}
                try:
                    sources.enrich_pool(pool, league, budget, n, profile["roster_size"])
                except Exception as e:
                    print("  (FantasyPros unavailable for this season:", e, ")")
            module.finalize_values(pool, budget, n, profile)
        else:
            targets, roster_size = module.derive_roster_targets(league)
            module.calculate_auction_values(pool, budget, n, targets)
            if use_fp:
                sources.fetch_sleeper_players = lambda: {}
                try:
                    sources.enrich_pool(pool, league, budget, n, roster_size)
                    module.normalize_values(pool, budget, n, roster_size)
                except Exception as e:
                    print("  (FantasyPros unavailable for this season:", e, ")")
    finally:
        for k, v in saved.items():
            setattr(module, k, v)
    return pool


def report(name, pool, league, actual_share):
    bids = {p.playerId: p.bid_amount for p in league.draft if p.bid_amount}
    rows = [(pool[pid], bid) for pid, bid in bids.items() if pid in pool]
    values = [e["value"] for e, _ in rows]
    prices = [bid for _, bid in rows]
    actual = [e.get("total_points", 0) for e, _ in rows]
    rho_price = spearman(values, prices)
    rho_actual = spearman(values, actual)
    mae = statistics.mean(abs(v - b) for v, b in zip(values, prices))
    top = sorted(rows, key=lambda r: -r[1])[:30]
    mae_top = statistics.mean(abs(e["value"] - b) for e, b in top)
    total_value = sum(e["value"] for e, _ in rows) or 1
    shares = {}
    for e, _ in rows:
        shares[e["position"]] = shares.get(e["position"], 0) + e["value"] / total_value
    share_txt = " ".join(f"{pos} {shares.get(pos, 0) * 100:.0f}/{actual_share.get(pos, 0) * 100:.0f}"
                         for pos in ("WR", "RB", "QB", "TE", "K", "D/ST"))
    print(f"{name:<34} rho(price) {rho_price:.3f}  rho(actual pts) {rho_actual:.3f}  "
          f"MAE ${mae:5.1f}  top30 MAE ${mae_top:5.1f}  share model/actual: {share_txt}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=202314)
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--old-ref", default="HEAD")
    ap.add_argument("--fp", action="store_true", help="also blend FantasyPros for that season")
    ap.add_argument("--config", default=os.environ.get("FFA_CONFIG"),
                    help="analyzer config JSON with espn_s2/swid (default: the app's)")
    args = ap.parse_args()

    cfg = load_config(args.config) if args.config else load_config()
    league = connect_league(args.league_id, args.year, cfg.get("espn_s2"), cfg.get("swid"))
    assert any(p.bid_amount for p in league.draft), "no auction prices in this draft"
    budget = league.settings.auction_budget
    print(f"{league.settings.name} {args.year}: {len(league.teams)} teams, ${budget} budget, "
          f"{sum(1 for p in league.draft if p.bid_amount)} priced picks")
    print("caveat: the season projection row may include in-season updates; applies to all variants equally")

    players = fetch_pool(league)
    print(f"pool: {len(players)} players with {args.year} projections; sample:",
          [(p.name, p.projected_total_points) for p in list(players.values())[:3]])

    spent = {}
    total_spent = 0
    for p in league.draft:
        if p.bid_amount:
            pos = getattr(players.get(p.playerId), "position", "?")
            spent[pos] = spent.get(pos, 0) + p.bid_amount
            total_spent += p.bid_amount
    actual_share = {pos: amt / total_spent for pos, amt in spent.items()}

    old = load_old_auction(args.old_ref)
    variants = [
        ("OLD (committed)", old, {}, False),
        ("NEW defaults", new_auction, {}, False),
        ("NEW depth 0.05", new_auction, {"DEPTH_DOLLAR_SHARE": 0.05}, False),
        ("NEW depth 0.15", new_auction, {"DEPTH_DOLLAR_SHARE": 0.15}, False),
        ("NEW no-cap K/DST", new_auction, {"CAPPED_POSITIONS": {}}, False),
    ]
    if args.fp:
        variants += [
            ("OLD + FP", old, {}, True),
            ("NEW + FP 45/20/35", new_auction, {}, True),
            ("NEW + FP 35/20/45", new_auction, {"W_MODEL": 0.35, "W_EXPERT": 0.45}, True),
        ]
    rows_by = {}
    for name, module, constants, use_fp in variants:
        pool = value_variant(players, league, module, constants, use_fp)
        rows_by[name] = report(name, pool, league, actual_share)

    # Signals vs actual points (how good is each predictor of what happened)
    rows = rows_by["NEW defaults"]
    bids = [b for _, b in rows]
    actual = [e.get("total_points", 0) for e, _ in rows]
    proj = [e["projected_points"] for e, _ in rows]
    print(f"\nrho vs {args.year} actual points: room's own prices {spearman(bids, actual):.3f} | "
          f"ESPN projection {spearman(proj, actual):.3f} | model value {spearman([e['value'] for e, _ in rows], actual):.3f}")

    print("\nTop 15 by NEW value (value / price / actual pts):")
    for e, bid in sorted(rows, key=lambda r: -r[0]["value"])[:15]:
        print(f"  {e['name']:<24} {e['position']:<3} ${e['value']:<6} ${bid:<5} {e.get('total_points', 0):.0f}")
    print("\nBiggest misses (|value - price|):")
    for e, bid in sorted(rows, key=lambda r: -abs(r[0]['value'] - r[1]))[:10]:
        print(f"  {e['name']:<24} {e['position']:<3} model ${e['value']:<6} paid ${bid:<5} actual {e.get('total_points', 0):.0f}")


if __name__ == "__main__":
    main()
