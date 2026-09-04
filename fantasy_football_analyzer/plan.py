"""Budget plan for an auction: what should the rest of my roster cost?

Given remaining cash, remaining needs, current inflation and the price
ladder at each position, allocate a target spend per open slot. The shape
prior (how top-heavy a winning roster is) comes from the league's own
champions when history is available.
"""

DEFAULT_TOP3_SHARE = 0.55   # share of budget on the top three buys (stars-and-scrubs-ish)
MAX_TOP3_SHARE = 0.68       # champions' shape prior is capped so mid-tier slots keep real money
FILLER_PRICE = 1.0          # dollars reserved per slot that will be a $1 flyer


def _ladder(state, position, limit=40):
    """Available players at a position, best first, with market-ish prices."""
    ranked = state.get_available_ranked(limit=200, position=position)
    out = []
    for e in ranked[:limit]:
        price = e.get("market_price") or e.get("adjusted_value") or e.get("value", 1.0)
        out.append({"player_id": e["player_id"], "name": e["name"], "tier": e.get("tier"),
                    "price": float(price)})
    return out


def build_budget_plan(state, team_name, top3_share=None, intel=None):
    """Target spend per open slot for `team_name`.

    Returns {"remaining": $, "slots_left": n, "targets": [{position, tier, target,
    example}], "pace": {...}} or None when the state isn't an auction.
    """
    if not getattr(state, "is_auction", False) or not state.pool:
        return None
    budgets = state.get_budgets()
    mine = next((b for b in budgets if b["team"].lower() == team_name.lower()), None)
    if not mine:
        return None
    remaining, slots_left = mine["remaining"], mine["slots_left"]
    if slots_left <= 0:
        return {"remaining": remaining, "slots_left": 0, "targets": [], "pace": None}

    if top3_share is None:
        top3_share = DEFAULT_TOP3_SHARE
        try:
            champs = (intel or {}).get("league", {}).get("champion_profiles") or []
            shares = [c["top3_share"] for c in champs if c.get("top3_share")]
            if shares:
                top3_share = min(MAX_TOP3_SHARE, sum(shares) / len(shares))
        except Exception:
            pass

    needs = state.get_team_needs(team_name)  # {pos: count still wanted}
    open_slots = []
    for pos, n in needs.items():
        if pos in ("K", "D/ST"):
            continue
        open_slots += [pos] * int(n)
    fillers = max(0, slots_left - len(open_slots))  # K, IDP, extra bench: $1 each
    spendable = max(0.0, remaining - fillers * FILLER_PRICE)

    # How many "big" buys I still owe myself: what I've spent vs the shape prior
    my_picks = state.team_rosters.get(team_name, [])
    spent_prices = sorted((p.get("bid_amount", 0) for p in my_picks), reverse=True)
    stars_bought = sum(1 for b in spent_prices[:3] if b >= 0.12 * state.budget)
    stars_left = max(0, 3 - stars_bought)
    star_pool = max(0.0, top3_share * state.budget - sum(spent_prices[:3]))
    star_pool = min(star_pool, spendable)

    # Star slots rotate across the positions with the priciest boards (an
    # RB1 and a WR1 before a second RB); the rest are filled in order of
    # the best available price at that position.
    ladders = {pos: _ladder(state, pos) for pos in set(open_slots)}
    top_price = lambda pos: ladders[pos][0]["price"] if ladders[pos] else 0.0
    by_pos_remaining = {pos: open_slots.count(pos) for pos in set(open_slots)}
    star_slots = []
    pos_cycle = sorted(by_pos_remaining, key=lambda pos: -top_price(pos))
    while len(star_slots) < min(stars_left, len(open_slots)) and pos_cycle:
        for pos in list(pos_cycle):
            if len(star_slots) >= stars_left:
                break
            if by_pos_remaining[pos] > 0 and top_price(pos) >= 0.12 * state.budget:
                star_slots.append(pos)
                by_pos_remaining[pos] -= 1
            else:
                pos_cycle.remove(pos)
    rest_slots_list = []
    for pos in sorted(by_pos_remaining, key=lambda pos: -top_price(pos)):
        rest_slots_list += [pos] * by_pos_remaining[pos]
    slot_order = star_slots + rest_slots_list
    stars_n = len(star_slots)
    targets = []
    next_idx = {}
    star_each = star_pool / stars_n if stars_n else 0.0
    # A star slot can't be worth more than the best player left at that position
    star_targets = [min(star_each, top_price(pos)) for pos in star_slots]
    rest_pool = max(0.0, spendable - sum(star_targets))
    rest_each = rest_pool / max(1, len(rest_slots_list))
    for i, pos in enumerate(slot_order):
        target = star_targets[i] if i < stars_n else rest_each
        target = max(FILLER_PRICE, min(target, spendable))
        ladder = ladders.get(pos) or []
        start = next_idx.get(pos, 0)
        # The first not-yet-used player at or below the target is the example
        found = next((k for k in range(start, len(ladder)) if ladder[k]["price"] <= target * 1.1), None)
        if found is None and start < len(ladder):
            found = start
        example = ladder[found] if found is not None else None
        if found is not None:
            next_idx[pos] = found + 1
        targets.append({
            "position": pos,
            "target": int(round(target)),
            "tier": example["tier"] if example else None,
            "example": example["name"] if example else None,
            "example_price": int(round(example["price"])) if example else None,
        })
    targets.sort(key=lambda t: -t["target"])

    spent = state.budget - remaining
    picks_made = len(my_picks)
    plan_spent_share = spent / state.budget if state.budget else 0
    slots_share = picks_made / max(1, state.roster_size)
    pace = {
        "spent": spent, "picks": picks_made,
        "spent_share": round(plan_spent_share, 2), "slots_share": round(slots_share, 2),
        "read": ("ahead of pace — you've spent faster than you've filled slots; bargains needed"
                 if plan_spent_share > slots_share + 0.15 else
                 "behind pace — cash is piling up; don't get stuck with $ and no players"
                 if slots_share > plan_spent_share + 0.15 else "on pace"),
    }
    return {
        "remaining": remaining, "slots_left": slots_left, "fillers": fillers,
        "top3_share": round(top3_share, 2), "stars_left": stars_left,
        "targets": targets, "pace": pace,
    }


def format_plan_for_ai(plan):
    if not plan or not plan.get("targets"):
        return ""
    lines = [f"BUDGET PLAN: ${plan['remaining']} left for {plan['slots_left']} slots "
             f"({plan.get('fillers', 0)} will be $1 fillers); pace: {plan['pace']['read']}."]
    lines.append("Target spend per remaining slot: " + ", ".join(
        f"{t['position']} ~${t['target']}" + (f" (e.g. {t['example']} ${t['example_price']})" if t.get("example") else "")
        for t in plan["targets"][:8]))
    return "\n".join(lines)
