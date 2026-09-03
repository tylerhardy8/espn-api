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
from .helpers import get_league_or_redirect, get_valued_pool, get_ai_key, ai_available
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
    return jsonify({"ok": True, "marked": 0})


# ---------------------------------------------------------------------------
# Auction: on-block state + suggested max bid
# ---------------------------------------------------------------------------

_auction_live = {}   # league_id -> {"player_id", "high_bid", "high_bidder", "updated", "events"}
_state_cache = {}    # league_id -> (state, built_at, mark_count)
_STATE_TTL = 4       # seconds; the on-block card polls faster than the board


def _draft_state(league, config):
    """A DraftState reused across quick polls (rebuilt when marks change)."""
    marks = len(mark_store.get(league.league_id))
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


def _decode_event(event, pool, league):
    """Provisional reading of an auction-room token frame.

    Unknown verbs are kept as raw events only. Shapes are refined against
    frames captured from a real room (see chrome-extension/README.md).
    """
    verb = str(event.get("verb", "")).upper()
    ints = [i for i in (event.get("ints") or []) if isinstance(i, int)]
    team_ids = {t.team_id for t in league.teams}
    player = next((i for i in ints if i in pool), None)
    if player is None:
        player = next((i for i in ints if i < 0), None)  # D/ST ids are negative
    team = next((i for i in ints if i in team_ids and i != player), None)
    budget = getattr(league.settings, "auction_budget", 0) or 200
    amount = next((i for i in ints if i not in (player, team) and 0 < i <= budget), None)
    kind = None
    if "NOMINAT" in verb:
        kind = "nominate"
    elif "BID" in verb:
        kind = "bid"
    elif verb in ("SOLD", "WON", "AWARDED", "PURCHASED"):
        kind = "sold"
    return kind, player, team, amount


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
    live = _auction_live.setdefault(league.league_id, {
        "player_id": None, "high_bid": 0, "high_bidder": None, "updated": 0, "events": [],
    })

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        page_league = payload.get("league_id")
        if payload.get("mock") or (isinstance(page_league, int) and page_league != league.league_id):
            return jsonify({"error": "different league than the active profile"}), 409

        if payload.get("clear"):
            live.update({"player_id": None, "high_bid": 0, "high_bidder": None})
        elif payload.get("event"):
            event = payload["event"]
            print(f"AUCTION-EVENT >>> {event.get('raw', '')[:300]}", flush=True)
            live["events"] = (live["events"] + [str(event.get("raw", ""))[:120]])[-20:]
            kind, player, team, amount = _decode_event(event, pool, league)
            if kind == "nominate" and player is not None:
                live.update({"player_id": player, "high_bid": amount or 1, "high_bidder": team})
            elif kind == "bid" and amount:
                if live["player_id"] is not None and amount >= live["high_bid"]:
                    live.update({"high_bid": amount, "high_bidder": team})
            elif kind == "sold" and player is not None:
                if player in pool:
                    mark_store.set(league.league_id, player, team_id=team,
                                   bid=amount or live["high_bid"] or None)
                live.update({"player_id": None, "high_bid": 0, "high_bidder": None})
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
    out = {
        "player_id": live["player_id"],
        "high_bid": live["high_bid"],
        "high_bidder": teams_by_id.get(live["high_bidder"]),
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
        need_mult = 1.0 if needs.get(pos, 0) > 0 else 0.6
        adjusted = entry.get("value", 1.0) * inflation
        suggested = int(min(my_max, max(1, round(adjusted * need_mult))))
        out.update({
            "name": entry["name"], "position": pos, "team": entry.get("team", ""),
            "tier": entry.get("tier"), "value": entry.get("value"),
            "espn_value": entry.get("espn_value"), "adjusted_value": round(adjusted, 1),
            "inflation": inflation, "need": needs.get(pos, 0) > 0,
            "my_max_bid": my_max, "suggested_max_bid": suggested,
            "verdict": ("bid" if live["high_bid"] < suggested else "pass"),
            "already_drafted": pid in state.drafted_ids,
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
