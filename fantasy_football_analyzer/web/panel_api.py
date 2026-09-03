"""JSON endpoints for the Chrome side panel.

The panel is the whole app docked beside ESPN's draft room: league switcher,
auction on-block state with a suggested max bid, trade and waiver analysis.
Registered on the main blueprint (imported by the app factory after routes).
"""

import time

from flask import request, jsonify

from ..config import load_config, save_config, set_active_league
from ..marks import store as mark_store
from ..trades import identify_team_needs, find_trade_matches
from ..waivers import get_waiver_recommendations, find_streamers, get_top_free_agents
from ..rss_news import fetch_news, match_news_to_players
from .helpers import (
    get_league_or_redirect, get_valued_pool, get_ai_key, ai_available,
    get_league_intel_cached, warm_league_intel,
)
from ..league_intel import rival_profile, league_price, player_sale_history
from ..auction import league_profile
from ..lineup import marginal_value
from ..historical import get_manager_key
from .routes import bp, _build_draft_state, trade_ai_advice, waiver_ai_advice


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------

@bp.route("/api/leagues")
def api_leagues():
    config = load_config()
    return jsonify({
        "active": config.get("active"),
        "leagues": [
            {"name": l.get("name"), "league_id": l.get("league_id"),
             "year": l.get("year"), "team_name": l.get("team_name") or ""}
            for l in (config.get("leagues") or [])
        ],
    })


@bp.route("/api/league/switch", methods=["POST", "OPTIONS"])
def api_league_switch():
    """Activate a league profile (CORS-safe twin of /league/switch for the panel)."""
    if request.method == "OPTIONS":
        return ("", 204)
    from .helpers import clear_league_cache
    payload = request.get_json(silent=True) or {}
    updated = set_active_league(load_config(), payload.get("name") or "")
    if not updated:
        return jsonify({"error": "Unknown league profile"}), 404
    save_config(updated)
    clear_league_cache()
    _state_cache.clear()
    return jsonify({"active": updated["active"], "league_id": updated.get("league_id"),
                    "team_name": updated.get("team_name") or ""})


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

@bp.route("/api/marks/clear", methods=["POST", "OPTIONS"])
def api_marks_clear():
    """Wipe the active league's marks (before a draft, or after a mock)."""
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    mark_store.clear(league.league_id)
    _auction_live.pop(league.league_id, None)
    _state_cache.pop(league.league_id, None)
    return jsonify({"ok": True, "marked": 0})


# ---------------------------------------------------------------------------
# Auction: on-block state + suggested max bid
# ---------------------------------------------------------------------------

_auction_live = {}   # league_id -> {"player_id", "high_bid", "high_bidder", "updated", "events"}
_state_cache = {}    # league_id -> (state, built_at, mark_count)
_STATE_TTL = 4       # seconds; the on-block card polls faster than the board


def _draft_state(league, config):
    """A DraftState reused across quick polls (rebuilt when marks change)."""
    marks = (len(mark_store.get(league.league_id)), mark_store.is_mock(league.league_id))
    cached = _state_cache.get(league.league_id)
    if cached and time.time() - cached[1] < _STATE_TTL and cached[2] == marks:
        return cached[0]
    state = _build_draft_state(league, config)
    _state_cache[league.league_id] = (state, time.time(), marks)
    return state


def _find_pid_by_name(pool, name):
    from ..sources import normalize_name
    wanted = normalize_name(name)
    return next((p for p, e in pool.items() if normalize_name(e["name"]) == wanted), None)


def mock_allowed(league_id):
    return mark_store.is_mock(league_id)


@bp.route("/api/mock-mode", methods=["GET", "POST", "OPTIONS"])
def api_mock_mode():
    """Rehearsal switch: let a mock draft room drive a *separate* mock board
    for the active league. Mock marks never mix with the real board, and the
    mode survives restarts; switching back shows the real board untouched.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        mark_store.set_mock(league.league_id, bool(payload.get("enabled")))
        _auction_live.pop(league.league_id, None)
        _state_cache.pop(league.league_id, None)
    return jsonify({"enabled": mock_allowed(league.league_id), "league_id": league.league_id})


def _foreign_room(payload, league):
    """True when a payload comes from a room that must not touch this board."""
    if mock_allowed(league.league_id):
        return False
    page_league = payload.get("league_id")
    return bool(payload.get("mock")) or (
        isinstance(page_league, int) and page_league != league.league_id
    )


def _decode_event(event):
    """Read a room event relayed by the extension (shapes captured from a
    real ESPN auction room; see chrome-extension/ws-hook.js)."""
    kind = event.get("kind")
    if kind:
        return kind, event
    # Raw token fallback: {verb, ints}
    verb = str(event.get("verb", "")).upper()
    i = [x for x in (event.get("ints") or []) if isinstance(x, int)] + [None] * 6
    if verb == "NOMINATION":
        return "nominating", {"teamId": i[0], "clockMs": i[1]}
    if verb == "BID":
        return "bid", {"teamId": i[0], "playerId": i[1], "amount": i[2], "clockMs": i[4]}
    if verb == "CLOCK" and i[0] == 2:
        return "clock", {"clockMs": i[1], "teamId": i[2], "playerId": i[3], "amount": i[4]}
    if verb == "SOLD":
        return "sold", {"teamId": i[0], "playerId": i[1], "pick": i[2], "amount": i[3]}
    return None, {}


def _empty_live():
    return {"player_id": None, "high_bid": 0, "high_bidder": None, "nominating": None,
            "clock_ms": None, "phase": None, "updated": 0, "events": [], "my_team_id": None}


@bp.route("/api/auction-live", methods=["GET", "POST", "OPTIONS"])
def api_auction_live():
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    try:
        pool, *_ = get_valued_pool(league, config)
    except Exception:
        pool = {}
    live = _auction_live.setdefault(league.league_id, _empty_live())

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if _foreign_room(payload, league):
            return jsonify({"error": "different league than the active profile"}), 409

        if payload.get("clear"):
            live.update({"player_id": None, "high_bid": 0, "high_bidder": None, "clock_ms": None})
        elif payload.get("meta"):
            tid = payload["meta"].get("teamId")
            if isinstance(tid, int):
                live["my_team_id"] = tid
        elif payload.get("event"):
            event = payload["event"]
            kind, ev = _decode_event(event)
            if kind != "clock":
                print(f"AUCTION-EVENT >>> {str(event.get('raw', ''))[:300]}", flush=True)
                live["events"] = (live["events"] + [str(event.get("raw", ""))[:120]])[-20:]
            if kind == "nominating":
                live.update({"player_id": None, "high_bid": 0, "high_bidder": None,
                             "nominating": ev.get("teamId"), "clock_ms": ev.get("clockMs"),
                             "phase": "nominating"})
            elif kind in ("bid", "clock"):
                pid, amount = ev.get("playerId"), ev.get("amount")
                if isinstance(pid, int) and isinstance(amount, int):
                    if pid != live["player_id"] or amount >= live["high_bid"]:
                        live.update({"player_id": pid, "high_bid": amount,
                                     "high_bidder": ev.get("teamId")})
                    live.update({"clock_ms": ev.get("clockMs"), "phase": "bidding"})
            elif kind == "between":
                live.update({"phase": "between", "clock_ms": ev.get("clockMs")})
            elif kind == "sold":
                pid = ev.get("playerId")
                if isinstance(pid, int) and pid in pool:
                    mark_store.set(league.league_id, pid, team_id=ev.get("teamId"),
                                   bid=ev.get("amount") or live["high_bid"] or None)
                live.update({"player_id": None, "high_bid": 0, "high_bidder": None,
                             "clock_ms": None, "phase": "sold"})
        else:
            # Manual on-block from the panel: {player_id|name, high_bid, high_bidder}
            pid = payload.get("player_id")
            if not isinstance(pid, int) and payload.get("name"):
                pid = _find_pid_by_name(pool, payload["name"])
            if isinstance(pid, int):
                live["player_id"] = pid
            hb = payload.get("high_bid")
            if isinstance(hb, (int, float)):
                live["high_bid"] = int(hb)
            bidder = payload.get("high_bidder")
            if isinstance(bidder, int):
                live["high_bidder"] = bidder
            elif isinstance(bidder, str):
                match = next((t.team_id for t in league.teams
                              if t.team_name.lower() == bidder.strip().lower()), None)
                if match is not None:
                    live["high_bidder"] = match
        live["updated"] = time.time()

    team_name = request.args.get("team") or (request.get_json(silent=True) or {}).get("team") \
        or config.get("team_name") or ""
    return jsonify(_auction_payload(live, league, config, pool, team_name))


def _auction_payload(live, league, config, pool, team_name):
    teams_by_id = {t.team_id: t.team_name for t in league.teams}
    def team_label(tid):
        if tid is None:
            return None
        if tid == live.get("my_team_id") and team_name:
            return team_name
        return teams_by_id.get(tid) or f"Team {tid}"

    out = {
        "player_id": live["player_id"],
        "high_bid": live["high_bid"],
        "high_bidder": team_label(live["high_bidder"]),
        "high_bidder_is_me": live["high_bidder"] is not None and live["high_bidder"] == live.get("my_team_id"),
        "nominating": team_label(live.get("nominating")),
        "nominating_is_me": live.get("nominating") is not None and live.get("nominating") == live.get("my_team_id"),
        "clock_ms": live.get("clock_ms"),
        "phase": live.get("phase"),
        "mock": mock_allowed(league.league_id),
        "updated": live["updated"],
        "events": live["events"][-5:],
    }
    pid = live["player_id"]
    if pid is None or not pool:
        return out
    entry = pool.get(pid)
    if not entry:
        return out
    try:
        state = _draft_state(league, config)
        inflation = state.get_inflation()
        needs = state.get_team_needs(team_name) if team_name else {}
        budgets = state.get_budgets()
        mine = next((b for b in budgets if b["team"].lower() == team_name.lower()), None)
        my_max = mine["max_bid"] if mine else state.budget
        pos = entry.get("position", "")
        count_need = needs.get(pos, 0) > 0
        my_picks = [p for p in state.team_rosters.get(team_name, []) if p.get("position")]

        # Lineup-aware need: how many projected points the player actually
        # adds to my current lineup (plus bench insurance), as a share of his
        # value over a starter. 1.0 = fills an open or replacement-level slot;
        # toward 0.5 = only displaces a good starter / sits on the bench.
        lineup_gain = None
        starts = None
        try:
            profile = league_profile(league)
            mine = [
                {"position": p["position"],
                 "value": pool.get(p["player_id"], {}).get("projected_points", 0.0)}
                for p in my_picks
            ]
            cand = {"position": pos, "value": entry.get("projected_points", 0.0)}
            lineup_gain = round(marginal_value(mine, cand, profile), 1)
            vbd = entry.get("vbd", 0) or 0
            if vbd > 0:
                need_mult = max(0.5, min(1.0, lineup_gain / vbd))
            else:
                need_mult = 1.0 if count_need else 0.6
            starts = lineup_gain > 0.2 * cand["value"]
            if not starts:
                need_mult = min(need_mult, 0.6)   # bench-bound: never a full-price need
        except Exception:
            need_mult = 1.0 if count_need else 0.6
        need = count_need or need_mult >= 0.75

        adjusted = entry.get("value", 1.0) * inflation
        suggested = int(min(my_max, max(1, round(adjusted * need_mult))))

        # Bye-week collisions with my own picks
        bye = entry.get("bye")
        bye_collision = []
        if bye:
            for p in my_picks:
                other = pool.get(p["player_id"], {})
                if other.get("bye") == bye and other.get("position") == pos:
                    bye_collision.append(p["player_name"])

        # League history: what this room has actually paid for a buy of this
        # rank at this position, and how the current high bidder tends to bid
        intel = get_league_intel_cached(config)
        if intel is None:
            warm_league_intel(config)
        hist_price = league_price(intel, pos, entry.get("pos_rank"), state.budget)
        rival = None
        bidder_id = live.get("high_bidder")
        if intel and bidder_id is not None and bidder_id != live.get("my_team_id"):
            team_obj = next((t for t in league.teams if t.team_id == bidder_id), None)
            if team_obj is not None:
                rival = rival_profile(intel, get_manager_key(team_obj)[0], pos)
        # Market price: league-calibrated value under market inflation
        market_value = entry.get("market_value")
        market_price = None
        if market_value:
            market_price = int(round(market_value * state.get_inflation(basis="market")))
        elif hist_price:
            market_price = hist_price
        expect = None
        if market_price:
            expect = max(market_price, int(round(adjusted)))
            if rival and rival.get("pos_ratio") and rival["pos_ratio"] > 1:
                expect = int(round(expect * min(1.5, rival["pos_ratio"])))

        # Three-state verdict: BID under model value; STRETCH between model
        # value and market price only when I need the position and few
        # comparable players remain; PASS above market (or above my max).
        high = live["high_bid"]
        scarce_n = state.scarcity(pid)
        scarce = scarce_n is not None and scarce_n <= 3
        stretch_cap = int(min(my_max, market_price)) if market_price else suggested
        if high >= my_max:
            verdict, reason = "pass", f"at my max bid (${my_max})"
        elif high < suggested:
            verdict, reason = "bid", f"under model value (${suggested})"
        elif market_price and high < stretch_cap and need and scarce:
            verdict = "stretch"
            reason = (f"above model (${suggested}) but this room pays ~${market_price} and only "
                      f"{scarce_n} comparable {pos}{'s' if scarce_n != 1 else ''} left")
        elif market_price and high < stretch_cap and need:
            verdict, reason = "pass", (f"above model (${suggested}); {scarce_n} comparable "
                                       f"{pos}s remain — buy the position cheaper later")
        elif market_price and high < stretch_cap:
            verdict, reason = "pass", f"above model (${suggested}) and I don't need {pos}"
        else:
            verdict, reason = "pass", f"above market (~${market_price or suggested})"

        notes = []
        if lineup_gain is not None:
            notes.append(f"adds +{lineup_gain:.0f} pts to your lineup" if starts
                         else "would sit on your bench")
        avail = entry.get("availability", 1.0)
        if avail is not None and avail < 1:
            status = entry.get("injury_status") or entry.get("sleeper_status") or ""
            notes.append(f"availability {avail:.2f}" + (f" ({status})" if status else ""))
        if bye_collision:
            notes.append(f"shares bye {bye} with {', '.join(bye_collision[:2])}")
        if notes:
            reason = reason + "; " + "; ".join(notes)

        out.update({
            "name": entry["name"], "position": pos, "team": entry.get("team", ""),
            "tier": entry.get("tier"), "pos_rank": entry.get("pos_rank"),
            "value": entry.get("value"),
            "espn_value": entry.get("espn_value"), "adjusted_value": round(adjusted, 1),
            "inflation": inflation, "need": need,
            "need_mult": round(need_mult, 2), "lineup_gain": lineup_gain, "starts": starts,
            "availability": entry.get("availability", 1.0),
            "crowd_value": entry.get("crowd_value"),
            "ceiling_value": entry.get("ceiling_value"), "floor_value": entry.get("floor_value"),
            "bye": bye, "bye_collision": bye_collision,
            "my_max_bid": my_max, "suggested_max_bid": suggested,
            "market_value": market_value, "market_price": market_price,
            "stretch_cap": stretch_cap if market_price else None,
            "scarcity": scarce_n,
            "premium": (getattr(state, "premiums", {}) or {}).get(pos),
            "verdict": verdict, "reason": reason,
            "already_drafted": pid in state.drafted_ids,
            "league_price": hist_price,
            "expected_price": expect,
            "rival": rival,
            "sale_history": player_sale_history(intel, pid),
            "intel_ready": intel is not None,
        })
    except Exception as e:
        out["error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

_trade_cache = {}
_TRADE_TTL = 180


@bp.route("/api/trades")
def api_trades():
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    team_name = request.args.get("team") or config.get("team_name") or ""
    key = (league.league_id, team_name.lower())
    cached = _trade_cache.get(key)
    if cached and time.time() - cached[1] < _TRADE_TTL and not request.args.get("fresh"):
        return jsonify(cached[0])

    my_team = next((t for t in league.teams if t.team_name.lower() == team_name.lower()), None)
    if my_team is None:
        return jsonify({"error": f"team {team_name!r} not found", "team": team_name}), 404
    try:
        needs = identify_team_needs(my_team, league)
    except Exception:
        needs = []
    try:
        pool = get_valued_pool(league, config)[0]
    except Exception:
        pool = {}
    try:
        matches = find_trade_matches(my_team, league, pool=pool)
    except Exception as e:
        matches = []
        needs_err = str(e)
    else:
        needs_err = None
    payload = {
        "team": my_team.team_name,
        "record": f"{my_team.wins}-{my_team.losses}",
        "needs": needs,
        "matches": matches,
        "ai_available": ai_available(config),
    }
    if needs_err:
        payload["error"] = needs_err
    _trade_cache[key] = (payload, time.time())
    return jsonify(payload)


@bp.route("/api/trades-ai", methods=["POST", "OPTIONS"])
def api_trades_ai():
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    if not ai_available(config):
        return jsonify({"error": "Add your Anthropic API key on the Setup page."}), 400
    payload = request.get_json(silent=True) or {}
    team_name = payload.get("team_name") or config.get("team_name")
    try:
        return jsonify({"advice": trade_ai_advice(config, league, team_name)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------

_waiver_cache = {}
_WAIVER_TTL = 180


def _news_json(player_news):
    out = {}
    for name, items in (player_news or {}).items():
        out[name] = [
            {"title": str(i.get("title", ""))[:160], "link": i.get("link", ""),
             "source": i.get("source", ""), "published": str(i.get("published", ""))}
            for i in (items or [])[:3]
        ] if isinstance(items, list) else []
    return out


@bp.route("/api/waivers")
def api_waivers():
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    team_name = request.args.get("team") or config.get("team_name") or ""
    week = request.args.get("week", type=int) or league.current_week
    key = (league.league_id, team_name.lower(), week)
    cached = _waiver_cache.get(key)
    if cached and time.time() - cached[1] < _WAIVER_TTL and not request.args.get("fresh"):
        return jsonify(cached[0])

    try:
        recommendations = get_waiver_recommendations(league, my_team_name=team_name, week=week)
    except Exception:
        recommendations = []
    try:
        streamers = {pos: lst[:3] for pos, lst in find_streamers(league, week=week).items()}
    except Exception:
        streamers = {}
    try:
        top_agents = get_top_free_agents(league, week=week, size=30)[:15]
    except Exception:
        top_agents = []
    news = {}
    try:
        items = fetch_news(max_items=25)
        names = list({a["name"] for a in top_agents} | {r["name"] for r in recommendations[:12]})
        news = _news_json(match_news_to_players(items, names))
    except Exception:
        pass
    payload = {
        "team": team_name, "week": week,
        "recommendations": recommendations[:12],
        "streamers": streamers,
        "top_agents": top_agents,
        "news": news,
        "ai_available": ai_available(config),
    }
    _waiver_cache[key] = (payload, time.time())
    return jsonify(payload)


@bp.route("/api/waivers-ai", methods=["POST", "OPTIONS"])
def api_waivers_ai():
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    if not ai_available(config):
        return jsonify({"error": "Add your Anthropic API key on the Setup page."}), 400
    payload = request.get_json(silent=True) or {}
    team_name = payload.get("team_name") or config.get("team_name")
    week = payload.get("week") or league.current_week
    try:
        return jsonify({"advice": waiver_ai_advice(config, league, team_name, week)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
