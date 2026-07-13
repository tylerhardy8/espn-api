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

    league_summary = {
        "pos_spend_share": {
            pos: round(amt / league_total_spend, 3)
            for pos, amt in league_pos_spend.items()
        } if league_total_spend else {},
        "avg_top_sale": round(sum(top_sales) / len(top_sales), 1) if top_sales else None,
        "champion_profiles": champion_profiles,
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
