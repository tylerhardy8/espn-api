"""League history intelligence for draft-day strategy.

Mines past seasons for patterns that matter in an auction room:
- Per-manager draft habits: positional spending, top-heaviness, price
  discipline (dollars per point actually delivered), signature overpays
- Trade/waiver activity (who you can trade out of a mistake with)
- Results: win rates, finishes, titles — and what the champions' draft
  profiles looked like

Everything is keyed by manager (owner identity) so patterns survive
team-name changes. Auction leagues get dollar-based analysis; snake
seasons fall back to round-based position priorities.
"""

from collections import defaultdict

from .historical import get_manager_key, build_draft_stats_map, analyze_luck

CORE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def _manager_of(team):
    return get_manager_key(team)[0]


def build_league_intel(leagues_by_year):
    """Build the league intelligence profile from multi-year data.

    Returns {"years": [...], "league": {...}, "managers": {name: {...}}}.
    """
    years = sorted(leagues_by_year.keys())
    managers = defaultdict(lambda: {
        "seasons": 0, "wins": 0, "games": 0, "finishes": [], "titles": 0,
        "trades": 0, "acquisitions": 0,
        "draft_years": [],  # per-year draft style dicts
    })
    league_pos_spend = defaultdict(float)
    league_total_spend = 0.0
    champion_profiles = []
    top_sales = []
    # Price history: per season, the sale prices at each position ranked
    # high→low, and dollars/points per position — the league's own price curve
    pos_price_years = defaultdict(list)   # pos -> [[prices desc] per season]
    pos_dollars = defaultdict(float)
    pos_points = defaultdict(float)
    team_spend_years = []                 # avg dollars spent per team, per season
    player_prices = defaultdict(dict)     # playerId -> {year: {"bid", "manager", "team"}}
    budget_years = []                     # auction budget per season (for scaling)

    for year, league in leagues_by_year.items():
        # Results + activity
        champion = None
        for team in league.teams:
            m = managers[_manager_of(team)]
            m["seasons"] += 1
            m["wins"] += team.wins
            m["games"] += team.wins + team.losses + team.ties
            finish = team.final_standing or team.standing
            m["finishes"].append(finish)
            if finish == 1:
                m["titles"] += 1
                champion = team
            m["trades"] += team.trades
            m["acquisitions"] += team.acquisitions

        # Draft styles
        if not league.draft:
            continue
        stats_map = build_draft_stats_map(league)
        is_auction = any(p.bid_amount for p in league.draft)
        year_styles = _analyze_draft_year(league, stats_map, is_auction)

        for manager, style in year_styles.items():
            style["year"] = year
            managers[manager]["draft_years"].append(style)
            if is_auction:
                for pos, amt in style["pos_spend"].items():
                    league_pos_spend[pos] += amt
                league_total_spend += style["total_spent"]

        if is_auction:
            bids = sorted((p.bid_amount for p in league.draft), reverse=True)
            if bids:
                top_sales.append(bids[0])
            season_prices = defaultdict(list)
            for pick in league.draft:
                total_points, _avg, position = stats_map.get(pick.playerId, (0, 0, ""))
                if position and pick.bid_amount:
                    season_prices[position].append(pick.bid_amount)
                    pos_dollars[position] += pick.bid_amount
                    pos_points[position] += max(0.0, total_points or 0.0)
                if pick.bid_amount and getattr(pick, "team", None):
                    player_prices[pick.playerId][year] = {
                        "bid": pick.bid_amount,
                        "manager": _manager_of(pick.team),
                        "team": pick.team.team_name,
                        "keeper": bool(getattr(pick, "keeper_status", False)),
                    }
            for pos, prices in season_prices.items():
                pos_price_years[pos].append(sorted(prices, reverse=True))
            if league.teams:
                team_spend_years.append(sum(bids) / len(league.teams))
            past_budget = getattr(getattr(league, "settings", None), "auction_budget", 0) or 0
            if past_budget:
                budget_years.append(past_budget)

        if champion is not None:
            champ_style = year_styles.get(_manager_of(champion))
            if champ_style:
                champion_profiles.append({
                    "year": year,
                    "manager": _manager_of(champion),
                    **{k: champ_style[k] for k in ("top3_share", "pos_spend", "total_spent")},
                })

    # Aggregate per-manager
    for name, m in managers.items():
        m["win_pct"] = round(m["wins"] / m["games"], 3) if m["games"] else 0
        m["avg_finish"] = round(sum(m["finishes"]) / len(m["finishes"]), 1) if m["finishes"] else 0
        s = m["seasons"] or 1
        m["trades_per_season"] = round(m["trades"] / s, 1)
        m["acquisitions_per_season"] = round(m["acquisitions"] / s, 1)
        m["draft_style"] = _aggregate_draft_style(m["draft_years"])

    luck = analyze_luck(leagues_by_year, group_by="manager")
    for name, r in luck.items():
        if name in managers:
            managers[name]["luck_delta"] = r["luck_delta"]

    pos_price_curve = {}
    for pos, seasons in pos_price_years.items():
        depth = max(len(x) for x in seasons)
        curve = []
        for i in range(depth):
            vals = [x[i] for x in seasons if len(x) > i]
            curve.append(round(sum(vals) / len(vals), 1))
        pos_price_curve[pos] = curve

    league_summary = {
        "pos_spend_share": {
            pos: round(amt / league_total_spend, 3)
            for pos, amt in league_pos_spend.items()
        } if league_total_spend else {},
        "avg_top_sale": round(sum(top_sales) / len(top_sales), 1) if top_sales else None,
        "champion_profiles": champion_profiles,
        # Avg sale price of the Nth most expensive buy at each position
        "pos_price_curve": pos_price_curve,
        # Dollars paid per fantasy point delivered, by position
        "pos_price_per_point": {
            pos: round(pos_dollars[pos] / pos_points[pos], 3)
            for pos in pos_dollars if pos_points[pos] > 0
        },
        "avg_team_spend": (round(sum(team_spend_years) / len(team_spend_years), 1)
                           if team_spend_years else None),
        "avg_budget": (round(sum(budget_years) / len(budget_years), 1)
                       if budget_years else None),
        # What each player sold for in past drafts of this league
        "player_prices": {pid: prices for pid, prices in player_prices.items()},
    }
    top3 = [m["draft_style"]["avg_top3_share"] for m in managers.values()
            if m.get("draft_style") and m["draft_style"].get("is_auction")]
    league_summary["avg_top3_share"] = round(sum(top3) / len(top3), 3) if top3 else None
    # Per-manager positional share of their own budget (vs the league's share)
    for name, m in managers.items():
        style = m.get("draft_style")
        if style and style.get("is_auction") and style.get("avg_total_spent"):
            style["pos_share"] = {
                pos: round(amt / style["avg_total_spent"], 3)
                for pos, amt in style["avg_pos_spend"].items()
            }

    return {"years": years, "league": league_summary, "managers": dict(managers)}


def _analyze_draft_year(league, stats_map, is_auction):
    """Per-manager draft style for one season."""
    by_manager = defaultdict(list)
    for pick in league.draft:
        if not (hasattr(pick, "team") and pick.team):
            continue
        total_points, _avg, position = stats_map.get(pick.playerId, (0, 0, ""))
        by_manager[_manager_of(pick.team)].append({
            "player": pick.playerName,
            "position": position,
            "bid": pick.bid_amount or 0,
            "round": pick.round_num,
            "points": total_points,
            "keeper": pick.keeper_status,
        })

    styles = {}
    for manager, picks in by_manager.items():
        style = {"is_auction": is_auction, "num_picks": len(picks)}
        if is_auction:
            total = sum(p["bid"] for p in picks)
            bids = sorted((p["bid"] for p in picks), reverse=True)
            points = sum(p["points"] for p in picks)
            pos_spend = defaultdict(float)
            for p in picks:
                if p["position"]:
                    pos_spend[p["position"]] += p["bid"]
            biggest = max(picks, key=lambda p: p["bid"], default=None)
            style.update({
                "total_spent": total,
                "top3_share": round(sum(bids[:3]) / total, 3) if total else 0,
                "pos_spend": dict(pos_spend),
                "price_per_point": round(total / points, 2) if points else None,
                "biggest_buy": (
                    f"{biggest['player']} ${biggest['bid']}" if biggest and biggest["bid"] else None
                ),
            })
        else:
            # Snake fallback: when each position was first taken
            first_round = {}
            for p in sorted(picks, key=lambda x: x["round"]):
                if p["position"] and p["position"] not in first_round:
                    first_round[p["position"]] = p["round"]
            style.update({"total_spent": 0, "top3_share": 0, "pos_spend": {},
                          "price_per_point": None, "biggest_buy": None,
                          "first_round_by_pos": first_round})
        styles[manager] = style
    return styles


def _aggregate_draft_style(draft_years):
    """Average a manager's per-year draft styles into one profile."""
    auction_years = [d for d in draft_years if d["is_auction"]]
    if not auction_years:
        snake = [d for d in draft_years if d.get("first_round_by_pos")]
        if not snake:
            return None
        pos_rounds = defaultdict(list)
        for d in snake:
            for pos, rnd in d["first_round_by_pos"].items():
                pos_rounds[pos].append(rnd)
        return {
            "is_auction": False,
            "avg_first_round_by_pos": {
                pos: round(sum(r) / len(r), 1) for pos, r in pos_rounds.items()
            },
        }

    n = len(auction_years)
    pos_spend = defaultdict(float)
    for d in auction_years:
        for pos, amt in d["pos_spend"].items():
            pos_spend[pos] += amt / n
    ppp = [d["price_per_point"] for d in auction_years if d["price_per_point"]]
    return {
        "is_auction": True,
        "avg_total_spent": round(sum(d["total_spent"] for d in auction_years) / n, 1),
        "avg_top3_share": round(sum(d["top3_share"] for d in auction_years) / n, 3),
        "avg_pos_spend": {pos: round(amt, 1) for pos, amt in pos_spend.items()},
        "avg_price_per_point": round(sum(ppp) / len(ppp), 2) if ppp else None,
        "signature_buys": [d["biggest_buy"] for d in auction_years if d["biggest_buy"]][-3:],
    }


def format_intel_for_ai(intel, my_manager=None):
    """Render the intel profile as a compact text block for the AI context."""
    if not intel or not intel.get("managers"):
        return ""

    years = intel["years"]
    lines = [f"=== LEAGUE HISTORY INTEL ({years[0]}-{years[-1]}, {len(years)} seasons) ==="]

    lg = intel["league"]
    if lg.get("pos_spend_share"):
        share = " / ".join(
            f"{pos} {lg['pos_spend_share'].get(pos, 0) * 100:.0f}%"
            for pos in CORE_POSITIONS if pos in lg["pos_spend_share"]
        )
        lines.append(f"League draft dollars historically go: {share}")
    if lg.get("avg_top_sale"):
        lines.append(f"Avg top sale across seasons: ${lg['avg_top_sale']:.0f}")
    if lg.get("champion_profiles"):
        shares = [c["top3_share"] for c in lg["champion_profiles"]]
        avg_share = sum(shares) / len(shares)
        champs = ", ".join(f"{c['year']}: {c['manager']}" for c in lg["champion_profiles"])
        lines.append(
            f"Champions averaged {avg_share * 100:.0f}% of budget on their top-3 players ({champs})"
        )

    lines.append("\n--- MANAGER PROFILES ---")
    ranked = sorted(
        intel["managers"].items(),
        key=lambda kv: kv[1]["avg_finish"] if kv[1]["avg_finish"] else 99,
    )
    for name, m in ranked:
        tag = " <== ME" if my_manager and name.lower() == my_manager.lower() else ""
        bits = [
            f"finish {m['avg_finish']}",
            f"win% {m['win_pct']:.3f}",
        ]
        if m["titles"]:
            bits.append(f"{m['titles']} title(s)")
        bits.append(f"{m['trades_per_season']} trades/yr")
        if m.get("luck_delta") is not None:
            bits.append(f"luck {m['luck_delta']:+.1f}")

        style = m.get("draft_style")
        style_txt = ""
        if style and style.get("is_auction"):
            top_pos = sorted(
                style["avg_pos_spend"].items(), key=lambda kv: kv[1], reverse=True
            )[:2]
            spend_txt = ", ".join(f"{pos} ${amt:.0f}" for pos, amt in top_pos)
            style_txt = (
                f" Auction style: {style['avg_top3_share'] * 100:.0f}% on top-3, "
                f"spends most on {spend_txt}"
            )
            if style.get("avg_price_per_point"):
                style_txt += f", ${style['avg_price_per_point']:.2f}/pt delivered"
            if style.get("signature_buys"):
                style_txt += f". Big buys: {'; '.join(style['signature_buys'])}"
        elif style and style.get("avg_first_round_by_pos"):
            prio = sorted(style["avg_first_round_by_pos"].items(), key=lambda kv: kv[1])[:3]
            style_txt = " First buys: " + ", ".join(f"{pos} (R{r:.0f})" for pos, r in prio)

        lines.append(f"{name}{tag}: {', '.join(bits)}.{style_txt}")

    lines.append(
        "\nUse these profiles to predict bidding behavior: price-enforce against "
        "known overpayers, let bargains come from disciplined spenders' positions "
        "of surplus, and expect runs at positions this league historically overspends on."
    )
    return "\n".join(lines)


def rival_profile(intel, manager, position):
    """How a manager bids at a position vs the league: {"tag", "pos_ratio", ...}.

    pos_ratio > 1 means they put a bigger share of their budget into that
    position than the league does; ppp_ratio > 1 means they pay more per
    point delivered than the league average (an overpayer in general).
    """
    if not intel or not manager:
        return None
    m = (intel.get("managers") or {}).get(manager)
    style = (m or {}).get("draft_style") or {}
    if not style.get("is_auction"):
        return None
    league = intel.get("league") or {}
    league_share = (league.get("pos_spend_share") or {}).get(position)
    my_share = (style.get("pos_share") or {}).get(position)
    pos_ratio = round(my_share / league_share, 2) if my_share and league_share else None

    ppp_all = [x["draft_style"]["avg_price_per_point"] for x in intel["managers"].values()
               if x.get("draft_style") and x["draft_style"].get("avg_price_per_point")]
    ppp_ratio = None
    if ppp_all and style.get("avg_price_per_point"):
        ppp_ratio = round(style["avg_price_per_point"] / (sum(ppp_all) / len(ppp_all)), 2)

    tags = []
    if pos_ratio and pos_ratio >= 1.15:
        tags.append(f"overspends at {position} ({(pos_ratio - 1) * 100:+.0f}% vs league)")
    elif pos_ratio and pos_ratio <= 0.85:
        tags.append(f"light at {position} ({(pos_ratio - 1) * 100:+.0f}% vs league)")
    if ppp_ratio and ppp_ratio >= 1.15:
        tags.append("pays above value in general")
    elif ppp_ratio and ppp_ratio <= 0.85:
        tags.append("bargain hunter")
    norm = league.get("avg_top3_share")
    share = style.get("avg_top3_share")
    if share and norm:
        if share >= norm + 0.08:
            tags.append(f"stars-and-scrubs ({share * 100:.0f}% on top 3 vs league {norm * 100:.0f}%)")
        elif share <= norm - 0.08:
            tags.append(f"spreads the budget ({share * 100:.0f}% on top 3 vs league {norm * 100:.0f}%)")
    return {
        "manager": manager,
        "pos_ratio": pos_ratio,
        "ppp_ratio": ppp_ratio,
        "top3_share": style.get("avg_top3_share"),
        "signature_buys": style.get("signature_buys") or [],
        "tags": tags,
        "runs_hot": bool((pos_ratio and pos_ratio >= 1.15) or (ppp_ratio and ppp_ratio >= 1.15)),
    }


def league_price(intel, position, pos_rank, budget):
    """What this league has historically paid for the Nth-ranked buy at a
    position, scaled to the current budget. None without auction history."""
    if not intel or not position or not pos_rank:
        return None
    league = intel.get("league") or {}
    curve = (league.get("pos_price_curve") or {}).get(position)
    if not curve:
        return None
    idx = min(len(curve), max(1, int(pos_rank))) - 1
    price = curve[idx]
    # Scale by the budget those seasons were played under (not by dollars
    # actually spent — leftover cash would otherwise inflate the history)
    hist_budget = league.get("avg_budget") or league.get("avg_team_spend")
    if hist_budget and budget:
        price = price * (budget / hist_budget)
    return max(1, int(round(price)))


def player_sale_history(intel, player_id, seasons=2):
    """The player's most recent sale prices in this league, newest first:
    [{"year", "bid", "manager", "team", "keeper"}]."""
    if not intel:
        return []
    prices = ((intel.get("league") or {}).get("player_prices") or {}).get(player_id) or {}
    out = []
    for year in sorted(prices, reverse=True)[:seasons]:
        out.append({"year": year, **prices[year]})
    return out


PREMIUM_MIN, PREMIUM_MAX = 0.6, 1.6


def positional_premiums(intel, pool, num_teams, roster_size):
    """League price premium per position: share of dollars this league has
    historically put on a position divided by the share the value model
    allocates to it. > 1 means the room pays above model there.

    Both shares are computed over the same positions (those present in both
    the history and the rosterable pool) so IDP/other slots don't skew it.
    """
    if not intel or not pool:
        return {}
    league_share = ((intel.get("league") or {}).get("pos_spend_share")) or {}
    if not league_share:
        return {}
    top_n = max(1, int(num_teams) * int(roster_size))
    ranked = sorted(pool.values(), key=lambda e: e.get("value", 1.0), reverse=True)[:top_n]
    model_dollars = {}
    for e in ranked:
        pos = e.get("position")
        if pos:
            model_dollars[pos] = model_dollars.get(pos, 0.0) + max(1.0, e.get("value", 1.0))
    grand = sum(model_dollars.values()) or 1.0
    # Only positions the model actually prices (IDP/K-sized slivers would
    # produce meaningless multipliers)
    common = [pos for pos in model_dollars
              if league_share.get(pos) and pos in CORE_POSITIONS
              and model_dollars[pos] / grand >= 0.02]
    if not common:
        return {}
    model_total = sum(model_dollars[pos] for pos in common) or 1.0
    league_total = sum(league_share[pos] for pos in common) or 1.0
    premiums = {}
    for pos in common:
        model_pct = model_dollars[pos] / model_total
        league_pct = league_share[pos] / league_total
        if model_pct > 0:
            premiums[pos] = round(min(PREMIUM_MAX, max(PREMIUM_MIN, league_pct / model_pct)), 3)
    return premiums


CURVE_RANKS = 12  # ranks per position where the sale-price curve is direct evidence


def apply_market_values(pool, premiums, num_teams, budget, roster_size, intel=None):
    """Set entry["market_value"]: what a player is likely to cost in this room.

    Base: value x positional premium (aggregate spend share). At the top of
    each position, blend toward the league's own price curve for that rank
    (what the Nth-priciest RB actually sold for, budget-scaled) — a flat
    premium understates stars and overstates depth. Rescaled so the top
    rosterable market values still sum to the league's cash. The value
    column is untouched — it stays the bargain yardstick.
    """
    if not pool:
        return
    if not premiums and not intel:
        for e in pool.values():
            e.pop("market_value", None)
        return
    for e in pool.values():
        prem = premiums.get(e.get("position"), 1.0) if premiums else 1.0
        market = max(1.0, e.get("value", 1.0) * prem)
        rank = e.get("pos_rank") or 0
        if intel and 0 < rank <= CURVE_RANKS:
            curve_price = league_price(intel, e.get("position"), rank, budget)
            if curve_price:
                weight = 0.7 if rank <= 6 else 0.5
                market = weight * curve_price + (1 - weight) * market
        e["market_value"] = market
    top_n = max(1, int(num_teams) * int(roster_size))
    ranked = sorted(pool.values(), key=lambda e: e["market_value"], reverse=True)[:top_n]
    total = sum(e["market_value"] for e in ranked)
    target = float(num_teams) * float(budget)
    if total > 0 and target > 0:
        scale = target / total
        for e in pool.values():
            e["market_value"] = round(max(1.0, e["market_value"] * scale), 1)