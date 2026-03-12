"""Command-line interface for the Fantasy Football Analyzer.

Usage:
    python -m fantasy_football_analyzer setup
    python -m fantasy_football_analyzer history [--years 2020-2024]
    python -m fantasy_football_analyzer draft [--team "My Team"]
    python -m fantasy_football_analyzer trades [--team "My Team"]
    python -m fantasy_football_analyzer waivers [--team "My Team"] [--week 5]
    python -m fantasy_football_analyzer full [--team "My Team"] [--years 2020-2024]
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
    config = load_config(args.config)
    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        return

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
    config = load_config(args.config)
    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        return

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_draft_report(league, my_team_name=team_name))


def cmd_trades(args):
    """Run trade analysis and recommendations."""
    config = load_config(args.config)
    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        return

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_trade_report(league, my_team_name=team_name))


def cmd_waivers(args):
    """Run waiver wire analysis and recommendations."""
    config = load_config(args.config)
    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        return

    year = int(args.year) if args.year else config.get("year", 2025)
    team_name = args.team or config.get("team_name")
    week = int(args.week) if args.week else None

    print(f"Loading {year} season...")
    league = connect_league(league_cfg["league_id"], year,
                            league_cfg["espn_s2"], league_cfg["swid"])

    print(format_waiver_report(league, my_team_name=team_name, week=week))


def cmd_full(args):
    """Run full analysis (history + draft + trades + waivers)."""
    config = load_config(args.config)
    league_cfg = get_league_config(config)

    if not league_cfg["league_id"]:
        print("Error: No league configured. Run 'setup' first.")
        return

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
    )
    parser.add_argument("--config", default=None, help="Path to config file")

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

    # waivers
    waiver_parser = subparsers.add_parser("waivers", help="Waiver wire recommendations")
    waiver_parser.add_argument("--team", help="Your team name")
    waiver_parser.add_argument("--year", help="Season year")
    waiver_parser.add_argument("--week", help="NFL week number")

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
        "full": cmd_full,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
