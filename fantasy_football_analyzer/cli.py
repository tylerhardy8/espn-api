"""Command-line interface for the Fantasy Football Analyzer.

Usage:
    python -m fantasy_football_analyzer setup
    python -m fantasy_football_analyzer history [--years 2020-2024]
    python -m fantasy_football_analyzer draft [--team "My Team"]
    python -m fantasy_football_analyzer trades [--team "My Team"] [--ai]
    python -m fantasy_football_analyzer waivers [--team "My Team"] [--week 5] [--ai]
    python -m fantasy_football_analyzer live-draft --team "My Team" [--interval 10]
    python -m fantasy_football_analyzer full [--team "My Team"] [--years 2020-2024]
    python -m fantasy_football_analyzer web [--host 0.0.0.0] [--port 5000]
"""

import argparse
import sys

from .config import load_config, save_config, get_league_config
from .league_connector import connect_league, connect_multi_year
from .historical import format_historical_report
from .draft import format_draft_report
from .trades import format_trade_report
from .waivers import format_waiver_report


def parse_year_range(year_str):
    """Parse a year range like '2020-2024' into a list of years."""
    if "-" in year_str:
        start, end = year_str.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(y.strip()) for y in year_str.split(",")]


def _get_league_or_exit(args):
    """Load config and connect to league; exit on failure."""
    config = load_config(args.config)

    # --league NAME selects a profile for this run without changing the saved default
    league_name = getattr(args, "league", None)
    if league_name:
        from .config import set_active_league
        switched = set_active_league(config, league_name)
        if not switched:
            names = ", ".join(l["name"] for l in config.get("leagues", [])) or "(none)"
            print(f"Error: Unknown league profile '{league_name}'. Available: {names}")
            sys.exit(1)
        config = switched

    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        sys.exit(1)

    return config, league_cfg


def cmd_setup(args):
    """Interactive setup to configure league credentials."""
    print("=" * 50)
    print("Fantasy Football Analyzer - Setup")
    print("=" * 50)
    print()

    config = load_config()

    league_id = input(f"ESPN League ID [{config.get('league_id', '')}]: ").strip()
    if league_id:
        config["league_id"] = int(league_id)

    year = input(f"Current season year [{config.get('year', 2025)}]: ").strip()
    config["year"] = int(year) if year else config.get("year", 2025)

    print()
    print("For private leagues, you need ESPN cookies from your browser.")
    print("(Leave blank for public leagues)")
    print()

    espn_s2 = input(f"espn_s2 cookie [{config.get('espn_s2', '')[:20]}...]: ").strip()
    if espn_s2:
        config["espn_s2"] = espn_s2

    swid = input(f"SWID cookie [{config.get('swid', '')}]: ").strip()
    if swid:
        config["swid"] = swid

    team_name = input(f"Your team name [{config.get('team_name', '')}]: ").strip()
    if team_name:
        config["team_name"] = team_name

    save_config(config)
    print(f"\nConfiguration saved. Testing connection...")

    try:
        league_cfg = get_league_config(config)
        league = connect_league(league_cfg["league_id"], config["year"],
                                league_cfg["espn_s2"], league_cfg["swid"])
        print(f"Connected to: {league.settings.name if hasattr(league.settings, 'name') else 'League'}")
        print(f"Teams: {len(league.teams)}")
        print(f"Current week: {league.current_week}")
        print("\nTeams in your league:")
        for team in league.standings():
            print(f"  {team.standing:>2}. {team.team_name} ({team.wins}-{team.losses})")
        print("\nSetup complete!")
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Check your league ID and credentials and run setup again.")


def cmd_history(args):
    """Run historical analysis across multiple seasons."""
    config, league_cfg = _get_league_or_exit(args)

    years = parse_year_range(args.years) if args.years else [config.get("year", 2025)]
    print(f"Loading {len(years)} season(s): {years}")

    leagues = connect_multi_year(
        league_cfg["league_id"], years,
        league_cfg["espn_s2"], league_cfg["swid"]
    )

    if not leagues:
        print("Error: Could not load any seasons.")
        return

    print(f"Loaded {len(leagues)} season(s). Analyzing...\n")
    print(format_historical_report(leagues))


def cmd_draft(args):
    """Run draft analysis and recommendations."""
    config, league_cfg = _get_league_or_exit(args)

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_draft_report(league, my_team_name=team_name))


def cmd_trades(args):
    """Run trade analysis and recommendations."""
    config, league_cfg = _get_league_or_exit(args)

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_trade_report(league, my_team_name=team_name))

    if getattr(args, "ai", False):
        from .ai_advisor import get_trade_evaluation_ai
        from .trades import evaluate_roster_strength

        print("\n--- AI TRADE ANALYSIS ---")
        context_lines = []
        for team in league.teams:
            strengths = evaluate_roster_strength(team)
            context_lines.append(f"{team.team_name} ({team.wins}-{team.losses}):")
            for pos in ["QB", "RB", "WR", "TE"]:
                if pos in strengths:
                    names = ", ".join(p["name"] for p in strengths[pos]["starters"])
                    context_lines.append(f"  {pos}: {names} ({strengths[pos]['starter_points']:.1f} pts)")

        if team_name:
            prompt = (
                f"I manage '{team_name}'. Analyze my roster and suggest the best "
                f"trade I could propose to improve my team. Consider positional "
                f"needs and what other teams might accept."
            )
        else:
            prompt = "Analyze these rosters and suggest the most impactful trade that could happen in this league."

        advice = get_trade_evaluation_ai(prompt, "\n".join(context_lines))
        print(advice)


def cmd_waivers(args):
    """Run waiver wire analysis and recommendations."""
    config, league_cfg = _get_league_or_exit(args)

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")
    week = int(args.week) if args.week else None

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    report = format_waiver_report(league, my_team_name=team_name, week=week)
    print(report)

    if getattr(args, "ai", False):
        from .ai_advisor import get_waiver_advice_ai

        print("\n--- AI WAIVER WIRE ANALYSIS ---")
        prompt = f"Here is my league's waiver wire report"
        if team_name:
            prompt += f" (I manage '{team_name}')"
        prompt += (
            ". Provide strategic recommendations on who to pick up, who to drop, "
            "and any sleepers to target.\n\n" + report
        )
        advice = get_waiver_advice_ai(prompt)
        print(advice)


def cmd_live_draft(args):
    """Run the live draft tracker with AI-powered recommendations."""
    config, league_cfg = _get_league_or_exit(args)

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")
    interval = int(args.interval) if args.interval else 10
    model = args.model or None

    if not team_name:
        print("Error: --team is required for live-draft. Set it in setup or pass --team.")
        return

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    from .ai_advisor import LiveDraftAdvisor

    advisor = LiveDraftAdvisor(
        league, team_name,
        poll_interval=interval,
        model=model,
    )
    advisor.auto_advise = not args.no_auto
    advisor.run()


def cmd_web(args):
    """Start the web UI server."""
    from .web import create_app

    app = create_app()
    print(f"Starting Fantasy Football Analyzer web UI...")
    print(f"Open http://{args.host}:{args.port} in your browser")
    app.run(host=args.host, port=args.port, debug=args.debug)


def cmd_full(args):
    """Run full analysis (history + draft + trades + waivers)."""
    config, league_cfg = _get_league_or_exit(args)

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")
    years = parse_year_range(args.years) if args.years else [year]

    # Historical
    if len(years) > 1:
        print(f"Loading {len(years)} seasons for historical analysis...")
        leagues = connect_multi_year(league_cfg["league_id"], years,
                                     league_cfg["espn_s2"], league_cfg["swid"])
        if leagues:
            print(format_historical_report(leagues))
            print()

    # Current season analysis
    print(f"Loading {year} season for in-season analysis...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_draft_report(league, my_team_name=team_name))
    print()
    print(format_trade_report(league, my_team_name=team_name))
    print()
    print(format_waiver_report(league, my_team_name=team_name))


def main():
    parser = argparse.ArgumentParser(
        description="Fantasy Football Analyzer - ESPN League Analysis Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s setup                                  Configure league credentials
  %(prog)s history --years 2020-2025              Multi-year trend analysis
  %(prog)s draft --team "My Team"                 Draft review and VBD analysis
  %(prog)s trades --team "My Team" --ai           Trade suggestions with AI analysis
  %(prog)s waivers --team "My Team" --week 8      Waiver wire recommendations
  %(prog)s live-draft --team "My Team"            Real-time draft tracker with AI advisor
  %(prog)s full --team "My Team" --years 2020-2025  Run everything
        """,
    )
    parser.add_argument("--config", default=None, help="Path to config file")
    parser.add_argument("--league", default=None,
                        help="League profile name to use for this run (see 'setup')")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # setup
    subparsers.add_parser("setup", help="Configure league credentials")

    # history
    hist_parser = subparsers.add_parser("history", help="Analyze historical trends")
    hist_parser.add_argument("--years", help="Year range (e.g., 2020-2024)")

    # draft
    draft_parser = subparsers.add_parser("draft", help="Draft analysis and recommendations")
    draft_parser.add_argument("--team", help="Your team name")
    draft_parser.add_argument("--year", help="Season year")

    # trades
    trade_parser = subparsers.add_parser("trades", help="Trade analysis and recommendations")
    trade_parser.add_argument("--team", help="Your team name")
    trade_parser.add_argument("--year", help="Season year")
    trade_parser.add_argument("--ai", action="store_true",
                              help="Include AI-powered trade analysis (requires ANTHROPIC_API_KEY)")

    # waivers
    waiver_parser = subparsers.add_parser("waivers", help="Waiver wire recommendations")
    waiver_parser.add_argument("--team", help="Your team name")
    waiver_parser.add_argument("--year", help="Season year")
    waiver_parser.add_argument("--week", help="NFL week number")
    waiver_parser.add_argument("--ai", action="store_true",
                               help="Include AI-powered waiver analysis (requires ANTHROPIC_API_KEY)")

    # live-draft
    live_parser = subparsers.add_parser("live-draft",
                                        help="Real-time draft tracker with AI advisor")
    live_parser.add_argument("--team", help="Your team name (required)")
    live_parser.add_argument("--year", help="Season year")
    live_parser.add_argument("--interval", default="10",
                             help="Poll interval in seconds (default: 10)")
    live_parser.add_argument("--model", default=None,
                             help="Claude model to use (default: claude-opus-5)")
    live_parser.add_argument("--no-auto", action="store_true",
                             help="Disable automatic AI advice before your picks")

    # web
    web_parser = subparsers.add_parser("web", help="Start the web UI")
    web_parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    web_parser.add_argument("--port", default=5000, type=int, help="Port to listen on (default: 5000)")
    web_parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    # full
    full_parser = subparsers.add_parser("full", help="Run all analyses")
    full_parser.add_argument("--team", help="Your team name")
    full_parser.add_argument("--year", help="Season year")
    full_parser.add_argument("--years", help="Year range for history (e.g., 2020-2024)")

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "history": cmd_history,
        "draft": cmd_draft,
        "trades": cmd_trades,
        "waivers": cmd_waivers,
        "live-draft": cmd_live_draft,
        "web": cmd_web,
        "full": cmd_full,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
