"""Connect to ESPN seasons with explicit discovery and load coverage."""
from espn_api.football import League


class HistorySeasons(dict):
    """Mapping-compatible result that also reports missing seasons."""
    def __init__(self):
        super().__init__()
        self.coverage = {'requested_years': [], 'advertised_years': [], 'failed_years': {},
                         'discovery_verified': False, 'all_requested_loaded': False}


def connect_league(league_id, year, espn_s2=None, swid=None):
    return League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)


def connect_multi_year(league_id, years=None, espn_s2=None, swid=None, seed=None, current_year=None):
    """Default: recursively discover every season advertised by ESPN.

    Explicit years restrict scope. Never substitute a guessed four-season
    window or silently call failed seasons complete. Errors omit raw URLs and
    credential-bearing exception strings.
    """
    result = HistorySeasons()
    discover = years is None
    if discover and seed is None:
        if current_year is None:
            raise ValueError('A current season or seed league is required to discover history.')
        try:
            seed = connect_league(league_id, current_year, espn_s2, swid)
        except Exception:
            result.coverage['failed_years'][int(current_year)] = 'Could not authenticate or load ESPN season metadata; retry with valid league access.'
            result.coverage['requested_years'] = [int(current_year)]
            return result
    requested = {int(y) for y in years} if not discover else {int(seed.year)}
    advertised = set()
    while requested - set(result) - set(result.coverage['failed_years']):
        year = min(requested - set(result) - set(result.coverage['failed_years']))
        try:
            league = seed if seed is not None and year == seed.year else connect_league(league_id, year, espn_s2, swid)
            result[year] = league
            previous = getattr(league, 'previousSeasons', None)
            if isinstance(previous, list):
                earlier = {int(y) for y in previous if str(y).isdigit() and 1900 <= int(y) < year}
                advertised.update(earlier)
                if seed is not None and year == seed.year:
                    result.coverage['discovery_verified'] = True
                if discover:
                    requested.update(earlier)
        except Exception:
            result.coverage['failed_years'][year] = 'ESPN season unavailable or access failed; not included in totals. Retry to verify.'
    result.coverage.update(requested_years=sorted(requested), advertised_years=sorted(advertised),
                           all_requested_loaded=not result.coverage['failed_years'])
    return result
