"""Flask route handlers for the Fantasy Football Analyzer web UI."""

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

from ..config import (
    load_config, save_config, get_league_config,
    add_league, remove_league, set_active_league,
)
from ..league_connector import connect_league, connect_multi_year
from ..historical import (
    analyze_team_history, analyze_head_to_head, analyze_draft_history,
    analyze_scoring_trends, analyze_manager_tendencies, analyze_luck,
)
from ..draft import get_draft_recommendations, analyze_draft_picks
from ..trades import (
    evaluate_roster_strength, identify_team_needs, find_trade_targets,
    find_trade_matches,
)
from ..waivers import get_top_free_agents, find_streamers, get_waiver_recommendations
from ..draft_tracker import DraftState
from ..rss_news import fetch_news, match_news_to_players
from ..marks import store as mark_store

from .helpers import (
    ai_available, get_league_or_redirect, clear_league_cache, parse_year_range,
    get_valued_pool, get_league_intel, get_ai_key,
)

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
def dashboard():
    config = load_config()
    connected = False
    league_info = {}

    if config.get("league_id"):
        config, league, err = get_league_or_redirect(config)
        if err is None and league is not None:
            connected = True
            try:
                standings = league.standings()
            except Exception:
                standings = sorted(league.teams, key=lambda t: t.wins, reverse=True)

            max_pf = max((t.points_for for t in league.teams), default=0) or 1

            league_info = {
                "name": getattr(league.settings, "name", "League"),
                "teams": len(league.teams),
                "current_week": league.current_week,
                "year": config.get("year", 2025),
                "standings": [
                    {
                        "rank": i + 1,
                        "name": t.team_name,
                        "logo": getattr(t, "logo_url", ""),
                        "wins": t.wins,
                        "losses": t.losses,
                        "ties": getattr(t, "ties", 0),
                        "points_for": round(t.points_for, 1),
                        "pf_pct": round(t.points_for / max_pf * 100),
                        "streak": _format_streak(t),
                        "playoff_pct": round(getattr(t, "playoff_pct", 0)),
                    }
                    for i, t in enumerate(standings)
                ],
                "scoreboard": _get_scoreboard(league),
                "power_rankings": _get_power_rankings(league),
                "activity": _get_recent_activity(league),
                "my_team": _get_my_team_summary(league, standings, config.get("team_name")),
            }

    return render_template(
        "dashboard.html",
        config=config,
        connected=connected,
        league_info=league_info,
        ai_available=ai_available(),
    )


def _format_streak(team):
    """Format a team's streak as 'W3' / 'L2', or '' if unknown."""
    streak_type = getattr(team, "streak_type", "")
    length = getattr(team, "streak_length", 0)
    if not streak_type or not length:
        return ""
    return f"{streak_type[0]}{length}"


def _get_scoreboard(league):
    """Current-week matchups, or [] if unavailable."""
    try:
        matchups = league.scoreboard()
    except Exception:
        return []
    board = []
    for m in matchups:
        home, away = getattr(m, "home_team", None), getattr(m, "away_team", None)
        if not hasattr(home, "team_name") or not hasattr(away, "team_name"):
            continue
        board.append({
            "home": home.team_name,
            "home_logo": getattr(home, "logo_url", ""),
            "home_record": f"{home.wins}-{home.losses}",
            "home_score": round(m.home_score, 1),
            "away": away.team_name,
            "away_logo": getattr(away, "logo_url", ""),
            "away_record": f"{away.wins}-{away.losses}",
            "away_score": round(m.away_score, 1),
            "is_playoff": getattr(m, "is_playoff", False),
        })
    return board


def _get_power_rankings(league):
    """Power rankings as [{rank, name, logo, score}], or [] if unavailable."""
    try:
        rankings = league.power_rankings()
    except Exception:
        return []
    result = []
    for i, (score, team) in enumerate(rankings, 1):
        result.append({
            "rank": i,
            "name": team.team_name,
            "logo": getattr(team, "logo_url", ""),
            "score": score,
        })
    return result


def _get_recent_activity(league, size=8):
    """Recent transactions as [{date, actions: [(team, verb, player)]}]."""
    try:
        activities = league.recent_activity(size=size)
    except Exception:
        return []
    feed = []
    for act in activities:
        actions = []
        for action in act.actions:
            team = action[0] if len(action) > 0 else None
            verb = action[1] if len(action) > 1 else ""
            player = action[2] if len(action) > 2 else ""
            actions.append({
                "team": getattr(team, "team_name", str(team)),
                "verb": verb.title() if isinstance(verb, str) else str(verb),
                "player": getattr(player, "name", str(player)),
            })
        feed.append({
            "date": datetime.fromtimestamp(act.date / 1000).strftime("%b %-d"),
            "actions": actions,
        })
    return feed


def _get_my_team_summary(league, standings, team_name):
    """Stat-card summary for the configured team, or None."""
    if not team_name:
        return None
    team = next(
        (t for t in league.teams if t.team_name.lower() == team_name.lower()), None
    )
    if team is None:
        return None
    rank = next(
        (i + 1 for i, t in enumerate(standings) if t.team_id == team.team_id),
        None,
    )
    return {
        "name": team.team_name,
        "logo": getattr(team, "logo_url", ""),
        "record": f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else ""),
        "rank": rank,
        "streak": _format_streak(team),
        "playoff_pct": round(getattr(team, "playoff_pct", 0)),
        "points_for": round(team.points_for, 1),
    }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

@bp.route("/setup", methods=["GET", "POST"])
def setup():
    config = load_config()

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "add":
            return _setup_add_league(config)
        if action == "remove":
            config = remove_league(config, request.form.get("name", ""))
            save_config(config)
            clear_league_cache()
            flash("League profile removed.", "info")
            return redirect(url_for("main.setup"))
        if action == "activate":
            updated = set_active_league(config, request.form.get("name", ""))
            if updated:
                save_config(updated)
                clear_league_cache()
                flash(f"Switched to {updated['active']}.", "success")
            return redirect(url_for("main.setup"))
        if action == "save_ai_key":
            key = request.form.get("ai_key", "").strip()
            if key:
                config["anthropic_api_key"] = key
                flash("AI key saved. AI features are now enabled.", "success")
            else:
                config.pop("anthropic_api_key", None)
                flash("AI key removed.", "info")
            save_config(config)
            return redirect(url_for("main.setup") + "#ai-setup")

        # Default: save the active profile + account cookies
        if not config.get("leagues"):
            # First-time setup: create the initial profile from the form
            return _setup_add_league(config, name=request.form.get("profile_name") or None)

        try:
            config["league_id"] = int(request.form["league_id"])
        except (ValueError, KeyError):
            flash("League ID must be a number.", "danger")
            return render_template("setup.html", config=config, ai_available=ai_available())

        config["year"] = int(request.form.get("year") or config.get("year", 2025))
        espn_s2 = request.form.get("espn_s2", "").strip()
        if espn_s2:
            config["espn_s2"] = espn_s2
        swid = request.form.get("swid", "").strip()
        if swid:
            config["swid"] = swid
        team_name = request.form.get("team_name", "").strip()
        if team_name:
            config["team_name"] = team_name

        save_config(config)
        clear_league_cache()
        return _test_connection_and_redirect(load_config())

    return render_template(
        "setup.html", config=config, ai_available=ai_available(),
        sources=_sources_status(),
    )


def _sources_status():
    try:
        from ..sources import get_sources_status
        return get_sources_status()
    except Exception:
        return {}


def _setup_add_league(config, name=None):
    """Handle adding a league profile from the setup form."""
    try:
        league_id = int(request.form["league_id"])
    except (ValueError, KeyError):
        flash("League ID must be a number.", "danger")
        return redirect(url_for("main.setup"))

    name = name or request.form.get("name") or f"League {league_id}"
    config = add_league(
        config, name, league_id,
        year=request.form.get("year") or config.get("year", 2025),
        team_name=request.form.get("team_name", ""),
        make_active=True,
    )
    # Cookies may come along with a first-time setup form
    for key in ("espn_s2", "swid"):
        value = request.form.get(key, "").strip()
        if value:
            config[key] = value

    save_config(config)
    clear_league_cache()
    return _test_connection_and_redirect(load_config())


def _test_connection_and_redirect(config):
    """Try connecting to the active league; flash the result."""
    try:
        league_cfg = get_league_config(config)
        league = connect_league(
            league_cfg["league_id"], config["year"],
            league_cfg.get("espn_s2"), league_cfg.get("swid"),
        )
        name = getattr(league.settings, "name", "League")
        flash(f"Connected to {name} ({len(league.teams)} teams)", "success")
        return redirect(url_for("main.dashboard"))
    except Exception as e:
        flash(f"Config saved, but connection failed: {e}", "warning")
        return redirect(url_for("main.setup"))


@bp.route("/league/switch", methods=["POST"])
def switch_league():
    """Navbar league switcher: activate a profile and return to the current page."""
    config = load_config()
    body = request.get_json(silent=True) if request.is_json else None
    name = (body or {}).get("name") if body is not None else request.form.get("name", "")
    updated = set_active_league(config, name or "")
    if updated:
        save_config(updated)
        clear_league_cache()
    if body is not None:
        # JSON clients (the Chrome side panel) get a JSON answer, no redirect
        if not updated:
            return jsonify({"error": "Unknown league profile"}), 404
        return jsonify({"active": updated["active"], "league_id": updated.get("league_id"),
                        "team_name": updated.get("team_name") or ""})
    if updated:
        flash(f"Switched to {updated['active']}.", "success")
    else:
        flash("Unknown league profile.", "danger")
    target = request.form.get("next") or request.referrer or url_for("main.dashboard")
    return redirect(target)


# ---------------------------------------------------------------------------
# Historical Analysis
# ---------------------------------------------------------------------------

@bp.route("/history")
def history():
    config, league, err = get_league_or_redirect()
    if err:
        return err

    years_str = request.args.get("years", str(config.get("year", 2025)))
    try:
        years = parse_year_range(years_str)
    except Exception:
        flash("Invalid year range format. Use '2020-2024' or '2022,2023,2024'.", "danger")
        return redirect(url_for("main.dashboard"))

    league_cfg = get_league_config(config)
    leagues = connect_multi_year(
        league_cfg["league_id"], years,
        league_cfg.get("espn_s2"), league_cfg.get("swid"),
    )

    if not leagues:
        flash("Could not load any seasons.", "danger")
        return redirect(url_for("main.dashboard"))

    group_by = request.args.get("group_by", "team")
    if group_by not in ("team", "manager"):
        group_by = "team"

    team_history = analyze_team_history(leagues, group_by=group_by)
    scoring_trends = analyze_scoring_trends(leagues)
    manager_tendencies = analyze_manager_tendencies(leagues, group_by=group_by)
    h2h = analyze_head_to_head(leagues, group_by=group_by)
    draft_history = analyze_draft_history(leagues)
    luck = analyze_luck(leagues, group_by=group_by)

    # Build flat rivalry list from h2h
    rivalries = _build_rivalry_list(h2h)

    return render_template(
        "history.html",
        team_history=team_history,
        scoring_trends=scoring_trends,
        manager_tendencies=manager_tendencies,
        rivalries=rivalries,
        draft_history=draft_history,
        luck=luck,
        chart_data=_build_chart_data(leagues, team_history, scoring_trends),
        my_team=config.get("team_name", ""),
        years=years_str,
        num_seasons=len(leagues),
        group_by=group_by,
    )


def _build_chart_data(leagues, team_history, scoring_trends):
    """Chart.js-ready series for the history page."""
    years = sorted(leagues.keys())
    rank_by_team = {}
    for name, data in team_history.items():
        by_year = {s["year"]: s["rank"] for s in data["seasons"]}
        rank_by_team[name] = [by_year.get(y) for y in years]

    return {
        "years": years,
        "num_teams": max((len(lg.teams) for lg in leagues.values()), default=0),
        "rank_by_team": rank_by_team,
        "scoring": {
            "years": [t["year"] for t in scoring_trends],
            "avg": [t["avg_score"] for t in scoring_trends],
            "max": [t["max_score"] for t in scoring_trends],
            "min": [t["min_score"] for t in scoring_trends],
        },
    }


def _build_rivalry_list(h2h):
    """Convert nested h2h dict into flat rivalry list for templates."""
    rivalries = []
    seen = set()
    for team_a, opponents in h2h.items():
        for team_b, record in opponents.items():
            pair = tuple(sorted([team_a, team_b]))
            if pair in seen:
                continue
            seen.add(pair)
            total = record["wins"] + record["losses"] + record["ties"]
            rivalries.append({
                "team_a": team_a,
                "team_b": team_b,
                "wins": record["wins"],
                "losses": record["losses"],
                "ties": record["ties"],
                "total_games": total,
            })
    rivalries.sort(key=lambda x: x["total_games"], reverse=True)
    return rivalries[:15]


# ---------------------------------------------------------------------------
# Draft Analysis
# ---------------------------------------------------------------------------

@bp.route("/draft")
def draft():
    config, league, err = get_league_or_redirect()
    if err:
        return err

    team_name = request.args.get("team", config.get("team_name"))
    teams = sorted([t.team_name for t in league.teams])

    try:
        recommendations, scarcity = get_draft_recommendations(league, my_team_name=team_name)
    except Exception as e:
        flash(f"Draft analysis error: {e}", "danger")
        recommendations, scarcity = [], {}

    try:
        draft_picks = analyze_draft_picks(league)
    except Exception:
        draft_picks = []

    # Calculate steals and busts
    steals = sorted(draft_picks, key=lambda x: x.get("value", 0), reverse=True)[:5] if draft_picks else []
    busts = sorted(draft_picks, key=lambda x: x.get("value", 0))[:5] if draft_picks else []

    return render_template(
        "draft.html",
        recommendations=recommendations,
        scarcity=scarcity,
        draft_picks=draft_picks,
        steals=steals,
        busts=busts,
        team_name=team_name,
        teams=teams,
    )


# ---------------------------------------------------------------------------
# Trade Analysis
# ---------------------------------------------------------------------------

@bp.route("/trades")
def trades():
    config, league, err = get_league_or_redirect()
    if err:
        return err

    team_name = request.args.get("team", config.get("team_name"))
    teams = sorted([t.team_name for t in league.teams])

    # Roster strengths for all teams
    roster_strengths = {}
    for team in sorted(league.teams, key=lambda t: t.points_for, reverse=True):
        roster_strengths[team.team_name] = {
            "record": f"{team.wins}-{team.losses}",
            "points_for": round(team.points_for, 1),
            "strengths": evaluate_roster_strength(team),
        }

    needs = []
    trade_matches = []
    if team_name:
        my_team = next(
            (t for t in league.teams if t.team_name.lower() == team_name.lower()), None
        )
        if my_team:
            try:
                needs = identify_team_needs(my_team, league)
            except Exception:
                needs = []
            try:
                pool = get_valued_pool(league, config)[0]
            except Exception:
                pool = {}
            try:
                trade_matches = find_trade_matches(my_team, league, pool=pool)
            except Exception:
                trade_matches = []

    return render_template(
        "trades.html",
        roster_strengths=roster_strengths,
        needs=needs,
        trade_matches=trade_matches,
        team_name=team_name,
        teams=teams,
        ai_available=ai_available(),
    )


@bp.route("/trades/ai", methods=["POST"])
def trades_ai():
    config, league, err = get_league_or_redirect()
    if err:
        return "<p class='text-danger'>Could not connect to league.</p>"

    team_name = request.form.get("team_name", config.get("team_name"))
    try:
        advice = trade_ai_advice(config, league, team_name)
        return render_template("partials/_ai_section.html", advice=advice, title="AI Trade Analysis")
    except Exception as e:
        return f"<p class='text-danger'>AI analysis error: {e}</p>"


def trade_ai_advice(config, league, team_name):
    """Claude's trade analysis for a team (shared by the page and the panel API)."""
    from ..ai_advisor import get_trade_evaluation_ai

    context_lines = []
    for team in league.teams:
        strengths = evaluate_roster_strength(team)
        team_needs = identify_team_needs(team, league)
        weak = [n["position"] for n in team_needs if n["deficit"] > 0][:3]
        context_lines.append(
            f"{team.team_name} ({team.wins}-{team.losses})"
            + (f" — weak at: {', '.join(weak)}" if weak else " — no clear weakness")
            + ":"
        )
        for pos in ["QB", "RB", "WR", "TE"]:
            if pos in strengths:
                names = ", ".join(p["name"] for p in strengths[pos]["starters"])
                bench = ", ".join(p["name"] for p in strengths[pos]["bench"][:2])
                context_lines.append(
                    f"  {pos}: {names} ({strengths[pos]['starter_points']:.1f} pts)"
                    + (f" | bench: {bench}" if bench else "")
                )

    # Computed mutual-fit matches so the AI grounds advice in real partners
    if team_name:
        my_team = next(
            (t for t in league.teams if t.team_name.lower() == team_name.lower()),
            None,
        )
        if my_team is not None:
            try:
                ai_pool = get_valued_pool(league, config)[0]
            except Exception:
                ai_pool = {}
            matches = find_trade_matches(my_team, league, pool=ai_pool)
            if matches:
                context_lines.append(
                    "\nCOMPUTED TRADE MATCHES (both starting lineups improve "
                    "net of what leaves them — these are realistic asks):"
                )
                for m in matches:
                    context_lines.append(
                        f"  {m['partner']} (fit {m['fit_score']}): "
                        f"needs {', '.join(m['their_needs']) or '-'}"
                    )
                    for s in m["proposals"]:
                        gives = " + ".join(s["give_players"])
                        gets = " + ".join(s["receive_players"])
                        context_lines.append(
                            f"    {gives} for {gets} — my roster {s['my_net']:+.1f}, "
                            f"theirs {s['their_net']:+.1f}, market ratio "
                            f"{s['market_ratio']:.2f}"
                        )

    if team_name:
        prompt = (
            f"I manage '{team_name}'. Analyze my roster and suggest the best "
            f"trade I could propose to improve my team. Consider positional "
            f"needs and what other teams might accept."
        )
    else:
        prompt = "Analyze these rosters and suggest the most impactful trade."

    return get_trade_evaluation_ai(prompt, "\n".join(context_lines),
                                   api_key=get_ai_key(config))


# ---------------------------------------------------------------------------
# Waiver Wire Analysis
# ---------------------------------------------------------------------------

@bp.route("/waivers")
def waivers():
    config, league, err = get_league_or_redirect()
    if err:
        return err

    team_name = request.args.get("team", config.get("team_name"))
    teams = sorted([t.team_name for t in league.teams])
    week = request.args.get("week", type=int) or league.current_week

    try:
        top_agents = get_top_free_agents(league, week=week, size=30)
    except Exception:
        top_agents = []

    try:
        streamers = find_streamers(league, week=week)
    except Exception:
        streamers = {}

    try:
        recommendations = get_waiver_recommendations(league, my_team_name=team_name, week=week)
    except Exception:
        recommendations = []

    # Fetch RSS news and match to players
    news_items = []
    player_news = {}
    try:
        news_items = fetch_news(max_items=25)
        # Build list of player names from top agents and recommendations
        player_names = [a["name"] for a in top_agents]
        player_names += [r["name"] for r in recommendations if r.get("name")]
        player_names = list(set(player_names))  # deduplicate
        player_news = match_news_to_players(news_items, player_names)
    except Exception:
        pass

    return render_template(
        "waivers.html",
        top_agents=top_agents,
        streamers=streamers,
        recommendations=recommendations,
        team_name=team_name,
        week=week,
        teams=teams,
        ai_available=ai_available(),
        news_items=news_items,
        player_news=player_news,
    )


@bp.route("/waivers/ai", methods=["POST"])
def waivers_ai():
    config, league, err = get_league_or_redirect()
    if err:
        return "<p class='text-danger'>Could not connect to league.</p>"

    team_name = request.form.get("team_name", config.get("team_name"))
    week = request.form.get("week", type=int) or league.current_week
    try:
        advice = waiver_ai_advice(config, league, team_name, week)
        return render_template("partials/_ai_section.html", advice=advice, title="AI Waiver Analysis")
    except Exception as e:
        return f"<p class='text-danger'>AI analysis error: {e}</p>"


def waiver_ai_advice(config, league, team_name, week):
    """Claude's waiver analysis (shared by the page and the panel API)."""
    from ..ai_advisor import get_waiver_advice_ai
    from ..waivers import format_waiver_report

    report = format_waiver_report(league, my_team_name=team_name, week=week)
    prompt = "Here is my league's waiver wire report"
    if team_name:
        prompt += f" (I manage '{team_name}')"
    prompt += (
        ". Provide strategic recommendations on who to pick up, who to drop, "
        "and any sleepers to target.\n\n" + report
    )
    return get_waiver_advice_ai(prompt, api_key=get_ai_key(config))


# ---------------------------------------------------------------------------
# Live Draft
# ---------------------------------------------------------------------------

@bp.route("/live-draft")
def live_draft():
    config, league, err = get_league_or_redirect()
    if err:
        return err

    team_name = request.args.get("team", config.get("team_name"))
    teams = sorted([t.team_name for t in league.teams])

    return render_template(
        "live_draft.html",
        team_name=team_name,
        teams=teams,
        ai_available=ai_available(),
    )


# Drafted-player marks (extension feed, history scraper, manual) live in
# fantasy_football_analyzer.marks — persisted to disk, ordered, with prices.


@bp.after_request
def _api_cors(response):
    # The Chrome extension's service worker posts picks here
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@bp.route("/api/me")
def api_me():
    """Active league + team for external clients (the Chrome side panel)."""
    config = load_config()
    return jsonify({
        "league": config.get("active"),
        "league_id": config.get("league_id"),
        "year": config.get("year"),
        "team_name": config.get("team_name") or "",
        "ai_available": ai_available(config),
    })


@bp.route("/api/debug-frame", methods=["POST", "OPTIONS"])
def api_debug_frame():
    """Log raw draft-room frame samples from the extension (format debugging)."""
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    sample = str(payload.get("sample", ""))[:4000]
    print(f"FRAME-SAMPLE >>> {sample}", flush=True)
    return jsonify({"ok": True})


@bp.route("/api/mark-drafted", methods=["POST", "OPTIONS"])
def api_mark_drafted():
    if request.method == "OPTIONS":
        return ("", 204)
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500
    payload = request.get_json(silent=True) or {}
    pid = payload.get("player_id")

    # Marks from another ESPN league (a mock draft room, a friend's league)
    # must never land on the active board.
    from .panel_api import _foreign_room
    if _foreign_room(payload, league):
        return jsonify({"error": "different league than the active profile",
                        "active_league_id": league.league_id}), 409

    try:
        pool, *_ = get_valued_pool(league, config)
    except Exception:
        pool = {}

    bid = payload.get("bid_amount")
    bid = int(bid) if isinstance(bid, (int, float)) and bid > 0 else None

    # Row-based marking: leaf texts from a pick-history row. Match the player
    # against the pool and the team against league team names.
    team_id_from_row = None
    if not isinstance(pid, int) and payload.get("row"):
        try:
            from ..sources import normalize_name
            texts = [str(t) for t in payload["row"]][:8]
            for t in texts:
                wanted = normalize_name(t)
                pid = next(
                    (p for p, e in pool.items() if normalize_name(e["name"]) == wanted),
                    None,
                )
                if pid is not None:
                    break
            if pid is not None:
                team_names = {t.team_name.strip().lower(): t.team_id for t in league.teams}
                for t in texts:
                    tid = team_names.get(t.strip().lower())
                    if tid is not None:
                        team_id_from_row = tid
                        break
                if bid is None:
                    # Auction history rows carry the sale price as "$NN"
                    price = _price_in_texts(texts)
                    if price:
                        bid = price
        except Exception:
            pid = None
        if pid is None:
            return jsonify({"error": "no pool match in row"}), 404
        if team_id_from_row is not None:
            payload["team_id"] = team_id_from_row

    # Name-based marking (e.g. picks scraped from the draft room page)
    if not isinstance(pid, int) and payload.get("name"):
        try:
            from ..sources import normalize_name
            wanted = normalize_name(payload["name"])
            pid = next(
                (p for p, e in pool.items() if normalize_name(e["name"]) == wanted),
                None,
            )
        except Exception:
            pid = None
        if pid is None:
            return jsonify({"error": f"no pool match for {payload['name']!r}"}), 404

    if not isinstance(pid, int):
        return jsonify({"error": "player_id or name required"}), 400
    if pool and pid not in pool:
        # Liberal candidate mining (extension) sends stray ints; reject them
        return jsonify({"error": f"player_id {pid} not in draft pool"}), 404
    if payload.get("undo"):
        count = mark_store.remove(league.league_id, pid)
    else:
        team_id = payload.get("team_id")
        count = mark_store.set(league.league_id, pid,
                               team_id=team_id if isinstance(team_id, int) else None,
                               bid=bid)
    return jsonify({"marked": count, "player_id": pid, "bid_amount": bid})


def _price_in_texts(texts):
    """First '$NN' amount in a list of short strings, or None."""
    import re
    for t in texts:
        m = re.search(r"\$\s?(\d{1,3})\b", str(t))
        if m:
            return int(m.group(1))
    return None


def _apply_manual_picks(state, league, pool):
    """Fold marked drafted players (with team + price) into the draft state."""
    marked = mark_store.get(league.league_id)
    new = [pid for pid in marked if pid not in state.drafted_ids and pid in pool]
    if not new:
        return
    from ..draft_tracker import _SyntheticPick
    teams_by_id = {t.team_id: t for t in league.teams}
    base = len(state.picks)
    num_teams = max(1, len(league.teams))
    # Arrival order is the pick order (marks are sequenced as they land)
    picks = [
        _SyntheticPick(teams_by_id.get(marked[pid]["team_id"]), pid, pool[pid]["name"],
                       (base + i) // num_teams + 1, (base + i) % num_teams + 1,
                       bid_amount=marked[pid]["bid"])
        for i, pid in enumerate(new)
    ]
    state.add_picks(picks)
    state.synthetic_picks = True


def _build_draft_state(league, config):
    """Construct a DraftState with the valued auction pool when available."""
    try:
        pool, budget, targets, roster_size = get_valued_pool(league, config)
    except Exception:
        pool, budget, targets, roster_size = {}, None, None, None

    # The cached league object's picks are from connect time (up to 60s old);
    # a live draft needs them fresh on every poll.
    try:
        league.draft = []
        league.refresh_draft()
    except Exception:
        pass

    # League-calibrated market values (history-driven positional premiums);
    # cheap and idempotent, so refreshed on every build in case intel just landed
    if pool:
        try:
            from .helpers import get_league_intel_cached
            from ..league_intel import positional_premiums, apply_market_values
            intel = get_league_intel_cached(config)
            premiums = positional_premiums(intel, pool, len(league.teams), roster_size or 16)
            apply_market_values(pool, premiums, len(league.teams),
                                budget or getattr(league.settings, "auction_budget", 0) or 200,
                                roster_size or 16, intel=intel)
        except Exception:
            premiums = {}
    else:
        premiums = {}

    state = DraftState(
        league, pool=pool, budget=budget, targets=targets, roster_size=roster_size,
    )
    state.premiums = premiums
    if league.draft:
        state.apply_picks(league.draft)

    # Manual/extension marks first — they carry team attribution, and a pick
    # applied once is skipped by later, less-informed sources.
    if pool:
        _apply_manual_picks(state, league, pool)

    if not league.draft and pool:
        # ESPN's draft feed can lag a live draft badly; reconstruct remaining
        # picks from the free-agent pool (drafted set exact, order approximate)
        from ..draft_tracker import synthesize_picks_from_pool
        added = state.add_picks(synthesize_picks_from_pool(league, pool))
        if added:
            state.synthetic_picks = True
    return state


@bp.route("/api/draft-state")
def api_draft_state():
    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500

    team_name = request.args.get("team") or config.get("team_name")

    try:
        from .helpers import warm_league_intel
        warm_league_intel(config)  # history for the on-block card + advice, off-thread
    except Exception:
        pass

    try:
        state = _build_draft_state(league, config)

        summary = state.get_board_summary()
        recent = state.get_recent_picks(count=20)

        # Team rosters summary
        team_picks = {}
        for team_name_key, picks in state.team_rosters.items():
            team_picks[team_name_key] = [
                {
                    "player": p["player_name"],
                    "position": p.get("position", ""),
                    "round": p["round"],
                    "pick": p["round_pick"],
                    "price": p.get("bid_amount", 0),
                }
                for p in picks
            ]

        payload = {
            "summary": summary,
            "recent": recent,
            "team_picks": team_picks,
            "is_auction": state.is_auction,
            "synthetic": getattr(state, "synthetic_picks", False),
            "mock": mark_store.is_mock(league.league_id),
        }

        if team_name and not state.is_auction:
            slot, total = state.get_my_slot(team_name)
            if slot:
                payload["my_slot"] = {
                    "slot": slot,
                    "total": total,
                    "upcoming": state.get_upcoming_picks(team_name),
                }

        if state.pool:
            payload["inflation"] = state.get_inflation()
            payload["market_inflation"] = (state.get_inflation(basis="market")
                                           if state.has_market() else None)
            payload["premiums"] = getattr(state, "premiums", {}) or {}
            payload["budgets"] = state.get_budgets()
            payload["best_available"] = [
                {
                    "player_id": e["player_id"],
                    "name": e["name"],
                    "position": e["position"],
                    "team": e["team"],
                    "tier": e.get("tier", 0),
                    "value": e.get("value", 1.0),
                    "espn_value": e.get("espn_value"),
                    "adjusted_value": e["adjusted_value"],
                    "market_value": e.get("market_value"),
                    "market_price": e.get("market_price"),
                    "projected_points": e.get("projected_points", 0),
                    "injury_status": e.get("injury_status", ""),
                    "practice": e.get("practice", ""),
                    "depth_chart": e.get("depth_chart", ""),
                    "fp_ecr": e.get("fp_ecr"),
                    "fp_tier": e.get("fp_tier"),
                    "trending_adds": e.get("trending_adds", 0),
                }
                for e in state.get_available_ranked(limit=60)
            ]
            payload["active_run"] = state.active_run
            if team_name:
                payload["my_needs"] = state.get_team_needs(team_name)
            if state.is_auction:
                payload["cash_rich"] = payload["budgets"][:3]
                payload["nominate_next"] = nomination_candidates(state, team_name)
            try:
                from ..sources import get_sources_status
                payload["sources"] = get_sources_status()
            except Exception:
                pass

        return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/draft-recommendation", methods=["POST"])
def api_draft_recommendation():
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY is not set. See Setup page for details."}), 400

    config, league, err = get_league_or_redirect()
    if err:
        return jsonify({"error": "Could not connect to league"}), 500

    payload = request.get_json(silent=True) or {}
    team_name = payload.get("team_name") or request.form.get("team_name")
    if not team_name:
        return jsonify({"error": "No team name provided"}), 400
    web_search = bool(payload.get("web_search", False))

    try:
        from ..ai_advisor import get_ai_recommendation

        state = _build_draft_state(league, config)
        intel_text = _get_intel_text(league, config, team_name)
        advice = get_ai_recommendation(
            state, team_name, league, intel_text=intel_text, web_search=web_search,
            api_key=get_ai_key(config),
        )
        return jsonify({"recommendation": advice, "web_search": web_search})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def nomination_candidates(state, team_name, limit=3):
    """Players worth nominating to drain rivals' cash.

    Prefers high-value players at positions I've already filled; otherwise
    players the crowd prices well above our model *relative to the rest of
    the board* (ESPN's values assume a $200 budget, so only the ratio is
    meaningful); finally top one-slot positions (QB/TE) I can wait on.
    Never my own targets.
    """
    needs = state.get_team_needs(team_name) if team_name else {}
    ranked = state.get_available_ranked(limit=40)
    picks = []

    def take(cands, why):
        for e in cands:
            if len(picks) >= limit:
                break
            if all(e["player_id"] != x[0]["player_id"] for x in picks):
                picks.append((e, why))

    if needs:
        take([e for e in ranked if e.get("position") and needs.get(e["position"], 0) <= 0],
             "position filled")

    ratios = [(e, e["espn_value"] / max(1.0, e.get("value", 1.0)))
              for e in ranked if e.get("espn_value")]
    if ratios:
        med = sorted(r for _, r in ratios)[len(ratios) // 2]
        hot = [e for e, r in sorted(ratios, key=lambda x: x[1], reverse=True) if r > med * 1.15]
        take(hot, "crowd overprices")

    take([e for e in ranked if e.get("position") in ("QB", "TE")], "one-slot position")

    return [
        {
            "player_id": e["player_id"], "name": e["name"], "position": e["position"],
            "adjusted_value": e["adjusted_value"], "espn_value": e.get("espn_value"),
            "why": why,
        }
        for e, why in picks[:limit]
    ]


def _get_intel_text(league, config, team_name):
    """League-history intel block for the AI, or None — never blocks advice."""
    try:
        from ..league_intel import format_intel_for_ai
        from ..historical import get_manager_key

        intel = get_league_intel(config)
        if not intel:
            return None

        my_manager = None
        if team_name:
            team = next(
                (t for t in league.teams if t.team_name.lower() == team_name.lower()),
                None,
            )
            if team is not None:
                my_manager = get_manager_key(team)[0]

        return format_intel_for_ai(intel, my_manager=my_manager) or None
    except Exception:
        return None
