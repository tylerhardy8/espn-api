"""Trade engine: propose, target, shop, and evaluate trades the way an
active league actually makes them.

Value is week-by-week lineup points: every roster is scored as the sum of
its best legal lineup for each remaining week (byes removed, playoff weeks
weighted up), plus a small insurance credit for bench depth. A trade is
worth what it changes in that number for each side — which is why the same
player is worth different amounts to different rosters, and why trades
happen at all.

Acceptance models the other owner: their own lineup gain, fairness in the
currency they judge with (consensus market value, tilted by the positions
their history shows they overvalue), how often they trade, and roster
crunch from lopsided player counts.
"""

from collections import defaultdict
from itertools import combinations

from .lineup import optimal_lineup, BENCH_WEIGHTS
from .ros import ros_projection, playoff_weeks, SEASON_WEEKS

CORE = ("QB", "RB", "WR", "TE")
PLAYOFF_WEEK_WEIGHT = 1.5      # how much a playoff week counts vs a regular week
ROSTER_CRUNCH_PENALTY = 3.0    # lineup points an owner "charges" per extra body received
MAX_CANDIDATES = 9             # players per side considered for packages
MAX_PAIRS = 14
MIN_MY_GAIN = 3.0
MIN_SCORE = 2.0


# ---------------------------------------------------------------------------
# Player weekly values
# ---------------------------------------------------------------------------

def remaining_weeks(league):
    current = int(getattr(league, "current_week", 0) or 0)
    return list(range(max(1, current), SEASON_WEEKS + 1))


def week_weights(league):
    po = set(playoff_weeks(league))
    return {w: (PLAYOFF_WEEK_WEIGHT if w in po else 1.0) for w in remaining_weeks(league)}


def player_card(player, league, pool=None):
    """{"player_id","name","position","team","ros","bye","weekly":{w: pts},
    "market","crowd","availability"} for a rostered ESPN player."""
    entry = (pool or {}).get(player.playerId, {})
    ros = ros_projection(player, league)
    weeks = remaining_weeks(league)
    bye = entry.get("bye")
    if bye is None:
        schedule = getattr(player, "schedule", None) or {}
        if schedule:
            bye = next((w for w in range(1, 15) if str(w) not in schedule and w not in schedule), None)
    playing = [w for w in weeks if w != bye]
    per_week = ros / len(playing) if playing else 0.0
    published = {}
    for w, row in (getattr(player, "stats", None) or {}).items():
        if isinstance(w, int) and w > 0 and isinstance(row, dict) and row.get("projected_points") is not None:
            published[w] = float(row["projected_points"])
    weekly = {w: (0.0 if w == bye else published.get(w, per_week)) for w in weeks}
    market = float(entry.get("market_value") or entry.get("value") or 0) or max(1.0, ros / 10.0)
    return {
        "player_id": player.playerId,
        "name": player.name,
        "position": getattr(player, "position", ""),
        "team": getattr(player, "proTeam", ""),
        "ros": round(ros, 1),
        "value": round(ros, 1),          # compatibility with older callers
        "bye": bye,
        "weekly": weekly,
        "market": round(market, 1),
        "crowd": entry.get("crowd_value") or entry.get("espn_value"),
        "availability": entry.get("availability", 1.0),
        "injury": entry.get("injury_status") or getattr(player, "injuryStatus", "") or "",
        "age": entry.get("age"),
    }


def roster_cards(team, league, pool=None):
    return [player_card(p, league, pool) for p in team.roster]


# ---------------------------------------------------------------------------
# Roster value
# ---------------------------------------------------------------------------

def weekly_lineup_value(cards, profile, weights):
    """Σ_w weight_w × best lineup points in week w (bye players unavailable)."""
    total = 0.0
    for w, weight in weights.items():
        avail = [{"position": c["position"], "value": c["weekly"].get(w, 0.0), "id": c["player_id"]}
                 for c in cards if c["weekly"].get(w, 0.0) > 0]
        starters, _ = optimal_lineup(avail, profile)
        total += weight * sum(p["value"] for p in starters)
    return total


def bench_insurance(cards, profile, weights):
    """Small credit for the best bench skill players (season-scale)."""
    season = [{"position": c["position"], "value": c["ros"], "id": c["player_id"]} for c in cards]
    _, bench = optimal_lineup(season, profile)
    skill = sorted((p["value"] for p in bench if p["position"] in CORE), reverse=True)
    mean_w = (sum(weights.values()) / len(weights)) if weights else 1.0
    return sum(wt * v for wt, v in zip(BENCH_WEIGHTS, skill)) * mean_w


def roster_value(cards, profile, weights, _cache=None):
    key = frozenset(c["player_id"] for c in cards)
    if _cache is not None and key in _cache:
        return _cache[key]
    val = weekly_lineup_value(cards, profile, weights) + bench_insurance(cards, profile, weights)
    if _cache is not None:
        _cache[key] = val
    return val


def week_starters(cards, profile, week):
    avail = [{"position": c["position"], "value": c["weekly"].get(week, 0.0), "name": c["name"]}
             for c in cards if c["weekly"].get(week, 0.0) > 0]
    starters, _ = optimal_lineup(avail, profile)
    return starters


def empty_starting_slots(cards, profile, week):
    slots = sum(profile["fixed"].get(p, 0) for p in CORE) + sum(n for _, n in profile["flex"])
    return max(0, slots - len([s for s in week_starters(cards, profile, week) if s["position"] in CORE]))


# ---------------------------------------------------------------------------
# Owner model
# ---------------------------------------------------------------------------

def owner_profile(team, league, intel=None):
    """Tendencies that shape what an owner accepts."""
    prof = {"manager": None, "trades_per_season": None, "pos_pref": {}, "activity": 1.0,
            "faab_remaining": None, "faab_share": None}
    try:
        budget = int(getattr(league.settings, "acquisition_budget", 0) or 0)
        if budget and getattr(league.settings, "faab", False):
            spent = int(getattr(team, "acquisition_budget_spent", 0) or 0)
            prof["faab_remaining"] = max(0, budget - spent)
            prof["faab_share"] = prof["faab_remaining"] / budget
    except Exception:
        pass
    try:
        from .historical import get_manager_key
        prof["manager"] = get_manager_key(team)[0]
    except Exception:
        return prof
    m = ((intel or {}).get("managers") or {}).get(prof["manager"])
    if not m:
        return prof
    tps = m.get("trades_per_season")
    prof["trades_per_season"] = tps
    if tps is not None:
        prof["activity"] = max(0.8, min(1.25, 0.8 + 0.15 * float(tps)))
    style = m.get("draft_style") or {}
    league_share = ((intel or {}).get("league") or {}).get("pos_spend_share") or {}
    for pos, share in (style.get("pos_share") or {}).items():
        if league_share.get(pos):
            prof["pos_pref"][pos] = max(0.85, min(1.2, share / league_share[pos]))
    return prof


def perceived_market(cards, owner):
    """Market value of a package as this owner sees it (positional tilt)."""
    return sum(c["market"] * owner["pos_pref"].get(c["position"], 1.0) for c in cards)


def acceptance(their_gain, ratio, owner, net_bodies):
    """Probability-like score that the other owner says yes."""
    if their_gain < -2:
        return 0.0
    gain_factor = min(1.0, 0.35 + max(0.0, their_gain) / 25.0)
    if ratio >= 0.95:
        market_factor = 1.0
    elif ratio >= 0.7:
        market_factor = (ratio - 0.7) / 0.25
    else:
        market_factor = 0.0
    crunch = max(0.0, 1.0 - 0.15 * max(0, net_bodies))
    # Short on FAAB and sending more bodies than they get: depth is hard to replace
    faab_share = owner.get("faab_share")
    depth_pen = 0.85 if (faab_share is not None and faab_share < 0.25 and net_bodies < 0) else 1.0
    return min(1.0, gain_factor * market_factor * owner.get("activity", 1.0) * crunch * depth_pen)


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

def _candidates(cards, profile, weights, cache, max_singles=MAX_CANDIDATES, max_pairs=MAX_PAIRS):
    core = sorted((c for c in cards if c["position"] in CORE), key=lambda c: -c["ros"])[:max_singles]
    base = roster_value(cards, profile, weights, cache)
    marginal = {}
    for c in core:
        rest = [x for x in cards if x["player_id"] != c["player_id"]]
        marginal[c["player_id"]] = base - roster_value(rest, profile, weights, cache)
    low = sorted(core, key=lambda c: marginal[c["player_id"]])[:6]
    pairs = [list(p) for p in combinations(low, 2)][:max_pairs]
    return [[c] for c in core] + pairs, marginal


def _after(cards, give_ids, get_cards):
    return [c for c in cards if c["player_id"] not in give_ids] + list(get_cards)


def _explain(my_cards, their_cards, give, get, profile, league, my_gain, their_gain, ratio):
    give_ids = {g["player_id"] for g in give}
    get_ids = {g["player_id"] for g in get}
    mine_after = _after(my_cards, give_ids, get)
    notes = []
    # Which week improves most for me (bye relief shows up here)
    weights = week_weights(league)
    best_w, best_delta = None, 0.0
    for w in weights:
        before = sum(s["value"] for s in week_starters(my_cards, profile, w))
        after = sum(s["value"] for s in week_starters(mine_after, profile, w))
        if after - before > best_delta:
            best_w, best_delta = w, after - before
    if best_w and best_delta >= 3:
        notes.append(f"biggest lift week {best_w} ({best_delta:+.0f})")
    holes_before = max(empty_starting_slots(my_cards, profile, w) for w in weights)
    holes_after = max(empty_starting_slots(mine_after, profile, w) for w in weights)
    if holes_after < holes_before:
        notes.append(f"worst-week empty starters {holes_before} → {holes_after}")
    theirs_after = _after(their_cards, get_ids, give)
    their_before_w = {w: sum(s["value"] for s in week_starters(their_cards, profile, w)) for w in weights}
    their_after_w = {w: sum(s["value"] for s in week_starters(theirs_after, profile, w)) for w in weights}
    up_weeks = sum(1 for w in weights if their_after_w[w] > their_before_w[w] + 0.5)
    them = f"their lineup {their_gain:+.0f} ({up_weeks} of {len(weights)} weeks better)"
    return (f"Your lineup {my_gain:+.0f}; {them}; they receive {ratio:.0%} of the market value "
            f"they send" + ("; " + "; ".join(notes) if notes else ""))


def _proposal(my_cards, their_cards, give, get, profile, league, weights, cache, owner):
    give_ids = {g["player_id"] for g in give}
    get_ids = {g["player_id"] for g in get}
    my_base = roster_value(my_cards, profile, weights, cache)
    their_base = roster_value(their_cards, profile, weights, cache)
    my_gain = roster_value(_after(my_cards, give_ids, get), profile, weights, cache) - my_base
    their_gain = roster_value(_after(their_cards, get_ids, give), profile, weights, cache) - their_base
    ratio = perceived_market(give, owner) / max(perceived_market(get, owner), 1e-6)
    net_bodies = len(give) - len(get)          # bodies THEY receive minus send
    accept = acceptance(their_gain, ratio, owner, net_bodies)
    return {
        "give_players": [g["name"] for g in give],
        "give_positions": [g["position"] for g in give],
        "give_ids": [g["player_id"] for g in give],
        "receive_players": [g["name"] for g in get],
        "receive_positions": [g["position"] for g in get],
        "receive_ids": [g["player_id"] for g in get],
        "give_points": round(sum(g["ros"] for g in give), 1),
        "receive_points": round(sum(g["ros"] for g in get), 1),
        "give_market": round(sum(g["market"] for g in give), 1),
        "receive_market": round(sum(g["market"] for g in get), 1),
        "my_net": round(my_gain, 1),
        "their_net": round(their_gain, 1),
        "market_ratio": round(ratio, 2),
        "acceptance": round(accept, 2),
        "score": round(my_gain * accept, 1),
        "reason": _explain(my_cards, their_cards, give, get, profile, league, my_gain, their_gain, ratio),
    }


def _dedupe(proposals):
    out, seen = [], set()
    for p in sorted(proposals, key=lambda p: -p["score"]):
        key = (tuple(p["give_ids"]), tuple(p["receive_ids"]))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

KEEPER_VALUE_RATIO = 2.5   # market value / draft price above which a player is a keeper-tag candidate
KEEPER_MIN_ROS = 150       # a $5-or-less player projecting this many points is a tag candidate outright


def draft_prices(league):
    """{playerId: auction price} from the league's draft feed."""
    out = {}
    for p in getattr(league, "draft", None) or []:
        if getattr(p, "bid_amount", 0):
            out[p.playerId] = int(p.bid_amount)
    return out


def keeper_flags(cards, prices):
    """Players whose draft price is far below their market value — the ones
    worth a 2027 franchise tag. Trading them forfeits that (league rule:
    only drafted, never-traded players are tag-eligible)."""
    flags = []
    for c in cards:
        price = prices.get(c["player_id"])
        if not price:
            continue
        cheap_starter = price <= 5 and c["ros"] >= KEEPER_MIN_ROS
        if cheap_starter or (c["market"] >= KEEPER_VALUE_RATIO * price and c["market"] >= 15):
            flags.append(f"{c['name']} (drafted ${price}, ROS {c['ros']:.0f} pts)")
    return flags


class TradeEngine:
    def __init__(self, league, pool=None, intel=None, profile=None):
        self.league = league
        self.pool = pool or {}
        self.intel = intel
        self.prices = draft_prices(league)
        self.keeper_rule = bool(self.prices)   # 2027 tag: drafted & never traded
        if profile is None:
            from .auction import league_profile
            profile = league_profile(league)
        self.profile = profile
        self.weights = week_weights(league)
        self.cache = {}
        self._cards = {}
        self._owners = {}

    def team(self, name_or_id):
        for t in self.league.teams:
            if t.team_id == name_or_id or t.team_name.lower() == str(name_or_id).lower():
                return t
        return None

    def cards(self, team):
        if team.team_id not in self._cards:
            cards = roster_cards(team, self.league, self.pool)
            if not cards and self.pool:
                cards = self._cards_from_board(team)
            self._cards[team.team_id] = cards
        return self._cards[team.team_id]

    def _cards_from_board(self, team):
        """Until ESPN publishes post-draft rosters, build them from the
        draft board (marks: player -> team, price)."""
        try:
            from .marks import store
            marks = store.get(self.league.league_id)
        except Exception:
            return []
        from types import SimpleNamespace
        out = []
        for pid, info in marks.items():
            if info.get("team_id") != team.team_id or pid not in self.pool:
                continue
            e = self.pool[pid]
            fake = SimpleNamespace(playerId=pid, name=e["name"], position=e["position"],
                                   proTeam=e.get("team", ""), projected_total_points=e.get("projected_points", 0),
                                   total_points=0.0, injuryStatus=e.get("injury_status", ""), stats={}, schedule={})
            out.append(player_card(fake, self.league, self.pool))
        return out

    def owner(self, team):
        if team.team_id not in self._owners:
            self._owners[team.team_id] = owner_profile(team, self.league, self.intel)
        return self._owners[team.team_id]

    def needs(self, team):
        """Positions with empty starting slots in more than one week (a
        single bye week alone is not a need)."""
        cards = self.cards(team)
        holes = defaultdict(int)
        for w in self.weights:
            starters = week_starters(cards, self.profile, w)
            counts = defaultdict(int)
            for s in starters:
                counts[s["position"]] += 1
            flex_taken = sum(1 for s in starters) - sum(min(counts[p], n) for p, n in self.profile["fixed"].items())
            for pos, n in self.profile["fixed"].items():
                if pos in CORE and counts[pos] < n:
                    holes[pos] += 1
            flex_slots = sum(n for _, n in self.profile["flex"])
            if flex_taken < flex_slots:
                holes["FLEX"] += 1
        return {pos: n for pos, n in holes.items() if n > 1}

    def surplus(self, team):
        """Bench-bound core players (low marginal value to this roster)."""
        cards = self.cards(team)
        _, marginal = _candidates(cards, self.profile, self.weights, self.cache)
        core = [c for c in cards if c["player_id"] in marginal]
        core.sort(key=lambda c: marginal[c["player_id"]])
        return [c["name"] for c in core if marginal[c["player_id"]] < 0.25 * c["ros"]][:4]

    def _flag(self, p, give, get):
        """Keeper-tag consequences on both sides (drafted, never traded)."""
        if not self.keeper_rule:
            return p
        lose = keeper_flags(give, self.prices)
        theirs = keeper_flags(get, self.prices)
        p["keeper_forfeit"] = lose
        p["keeper_forfeit_theirs"] = theirs
        notes = []
        if lose:
            notes.append("forfeits your 2027 tag on " + ", ".join(lose))
        if theirs:
            notes.append("they forfeit their tag on " + ", ".join(theirs))
        if notes:
            p["reason"] += "; " + "; ".join(notes)
        return p

    def partner_proposals(self, my_team, other, allow_two_for_two=True, max_proposals=4):
        mine, theirs = self.cards(my_team), self.cards(other)
        my_pk, _ = _candidates(mine, self.profile, self.weights, self.cache)
        their_pk, _ = _candidates(theirs, self.profile, self.weights, self.cache)
        owner = self.owner(other)
        out = []
        for give in my_pk:
            for get in their_pk:
                if len(give) > 1 and len(get) > 1 and not allow_two_for_two:
                    continue
                p = self._flag(_proposal(mine, theirs, give, get, self.profile, self.league, self.weights, self.cache, owner), give, get)
                if p["my_net"] >= MIN_MY_GAIN and p["score"] >= MIN_SCORE:
                    out.append(p)
        return _dedupe(out)[:max_proposals]

    def matches(self, my_team, max_partners=6, max_proposals_per_partner=3):
        partners = []
        for other in self.league.teams:
            if other.team_id == my_team.team_id:
                continue
            props = self.partner_proposals(my_team, other, max_proposals=max_proposals_per_partner)
            if not props:
                continue
            owner = self.owner(other)
            partners.append({
                "partner": other.team_name,
                "record": f"{other.wins}-{other.losses}",
                "fit_score": props[0]["score"],
                "their_needs": sorted(self.needs(other), key=lambda p: -self.needs(other)[p]),
                "their_surplus": self.surplus(other),
                "trades_per_season": owner.get("trades_per_season"),
                "pos_pref": {k: v for k, v in owner.get("pos_pref", {}).items() if abs(v - 1) >= 0.1},
                "proposals": props,
            })
        partners.sort(key=lambda p: -p["fit_score"])
        return partners[:max_partners]

    def target(self, my_team, player_name, max_packages=5):
        """What would it take to get a specific player from whoever has him."""
        for other in self.league.teams:
            if other.team_id == my_team.team_id:
                continue
            card = next((c for c in self.cards(other) if c["name"].lower() == player_name.lower()), None)
            if not card:
                continue
            mine, theirs = self.cards(my_team), self.cards(other)
            my_pk, _ = _candidates(mine, self.profile, self.weights, self.cache, max_singles=12, max_pairs=30)
            owner = self.owner(other)
            props = [self._flag(_proposal(mine, theirs, give, [card], self.profile, self.league, self.weights, self.cache, owner), give, [card])
                     for give in my_pk]
            # Also try pairing the target with one of their bench pieces (2-for-2 style asks)
            props = [p for p in props if p["acceptance"] > 0]
            props.sort(key=lambda p: (-p["acceptance"], -p["my_net"]))
            return {"partner": other.team_name, "player": card, "packages": _dedupe(props)[:max_packages],
                    "owner": owner}
        return None

    def shop(self, my_team, player_name, max_offers=6):
        """What could I get for one of my players, across the league."""
        mine = self.cards(my_team)
        card = next((c for c in mine if c["name"].lower() == player_name.lower()), None)
        if not card:
            return None
        offers = []
        for other in self.league.teams:
            if other.team_id == my_team.team_id:
                continue
            theirs = self.cards(other)
            their_pk, _ = _candidates(theirs, self.profile, self.weights, self.cache, max_singles=12, max_pairs=30)
            owner = self.owner(other)
            for get in their_pk:
                p = self._flag(_proposal(mine, theirs, [card], get, self.profile, self.league, self.weights, self.cache, owner), [card], get)
                if p["acceptance"] > 0 and p["my_net"] > 0:
                    p["partner"] = other.team_name
                    offers.append(p)
        offers.sort(key=lambda p: -p["score"])
        return {"player": card, "offers": _dedupe(offers)[:max_offers]}

    def evaluate(self, my_team, other, give_names, get_names):
        """Judge a specific offer and suggest the smallest counter that fixes it."""
        mine, theirs = self.cards(my_team), self.cards(other)
        give = [c for c in mine if c["name"].lower() in {n.lower() for n in give_names}]
        get = [c for c in theirs if c["name"].lower() in {n.lower() for n in get_names}]
        if not give or not get:
            return {"error": "players not found on those rosters"}
        owner = self.owner(other)
        p = self._flag(_proposal(mine, theirs, give, get, self.profile, self.league, self.weights, self.cache, owner), give, get)
        if p["my_net"] >= 3 and p["acceptance"] >= 0.5:
            verdict = "ACCEPT" if p["my_net"] >= 3 else "FAIR"
        elif p["my_net"] < 0:
            verdict = "DECLINE"
        else:
            verdict = "COUNTER"
        counters = []
        give_ids = {g["player_id"] for g in give}
        get_ids = {g["player_id"] for g in get}
        if p["my_net"] < 3:
            # Ask for one more piece from their bench that closes my gap
            for extra in sorted((c for c in theirs if c["player_id"] not in get_ids and c["position"] in CORE),
                                key=lambda c: c["market"]):
                q = self._flag(_proposal(mine, theirs, give, get + [extra], self.profile, self.league, self.weights, self.cache, owner), give, get + [extra])
                if q["my_net"] >= 3 and q["acceptance"] > 0.3:
                    counters.append({"type": "ask_add", "player": extra["name"], **q})
                    if len(counters) >= 2:
                        break
        if p["acceptance"] < 0.5:
            # Sweeten with my lowest-marginal piece that doesn't hurt me
            for extra in sorted((c for c in mine if c["player_id"] not in give_ids and c["position"] in CORE),
                                key=lambda c: c["market"]):
                q = self._flag(_proposal(mine, theirs, give + [extra], get, self.profile, self.league, self.weights, self.cache, owner), give + [extra], get)
                if q["my_net"] >= 0 and q["acceptance"] >= 0.5:
                    counters.append({"type": "sweeten", "player": extra["name"], **q})
                    if len(counters) >= 4:
                        break
        return {"verdict": verdict, "offer": p, "counters": counters[:3], "owner": owner}


def format_trades_for_ai(engine, my_team, partners):
    """Compact text block for Claude: partner reads plus proposals with weekly reasoning."""
    lines = []
    owner_me = engine.owner(my_team)
    weights = engine.weights
    mine = engine.cards(my_team)
    holes = {w: empty_starting_slots(mine, engine.profile, w) for w in weights}
    bad = [w for w, h in holes.items() if h]
    lines.append(f"MY LINEUP: {len(weights)} weeks left; weeks with empty starting slots: "
                 + (", ".join(f"wk{w} ({holes[w]})" for w in bad) if bad else "none"))
    lines.append(f"MY BYES: " + ", ".join(f"{c['name']} {c['bye']}" for c in sorted(mine, key=lambda c: -c['ros'])[:8]))
    for part in partners:
        pref = ", ".join(f"{k} x{v:.2f}" for k, v in part.get("pos_pref", {}).items())
        lines.append(f"\n{part['partner']} ({part['record']}): needs {', '.join(part['their_needs']) or '-'}; "
                     f"surplus {', '.join(part['their_surplus']) or '-'}; trades/yr {part.get('trades_per_season')}"
                     + (f"; values {pref}" if pref else ""))
        for p in part["proposals"]:
            lines.append(f"  {' + '.join(p['give_players'])} for {' + '.join(p['receive_players'])} — "
                         f"me {p['my_net']:+.0f}, them {p['their_net']:+.0f}, accept {p['acceptance']:.0%}: {p['reason']}")
    return "\n".join(lines)
