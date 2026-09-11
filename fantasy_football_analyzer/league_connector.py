"""Handles connecting to ESPN leagues across multiple seasons."""

from espn_api.football import League


def connect_league(league_id, year, espn_s2=None, swid=None):
    """Connect to a single season of a league."""
    return League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)


def connect_multi_year(league_id, years, espn_s2=None, swid=None):
    """Connect to multiple seasons and return a dict keyed by year."""
    leagues = {}
    for year in years:
        try:
            leagues[year] = connect_league(league_id, year, espn_s2=espn_s2, swid=swid)
        except Exception as e:
            print(f"  Warning: Could not load {year} season: {e}")
    return leagues
