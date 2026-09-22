"""Historical identity, completion and matchup evidence shared by reports."""
import math
from collections import defaultdict


def owner_identity(team):
    owners = getattr(team, 'owners', [])
    owners = owners if isinstance(owners, list) else []
    ids = sorted({str(o['id']).strip().casefold() for o in owners if isinstance(o, dict) and o.get('id')})
    return tuple(ids)


def identity_labels(leagues, group_by='team'):
    """Join by franchise ID or owner-ID set, then choose the latest display label.

    Unknown owners stay season-specific; identical display names never merge IDs.
    Co-owner groups are treated as a group, not credited repeatedly to individuals.
    """
    from .historical import get_manager_key
    latest, references = {}, {}
    for year, league in sorted(leagues.items()):
        league_id = getattr(league, 'league_id', None)
        league_id = str(league_id) if type(league_id) in (int,str) else 'league'
        for team in league.teams:
            ref = (year, str(team.team_id))
            owners = owner_identity(team)
            identity = (league_id, 'team', str(team.team_id)) if group_by == 'team' else (
                (league_id, 'owners', owners) if owners else (league_id, 'unknown-owner', year, str(team.team_id)))
            name = team.team_name if group_by == 'team' else get_manager_key(team)[0]
            latest[identity] = (name, team.team_id)
            references[ref] = identity
    names = defaultdict(list)
    for key, (name, team_id) in latest.items():
        names[name].append(key)
    labels = {}
    used = set()
    for key, (name, team_id) in latest.items():
        label = name if len(names[name]) == 1 else f'{name} [team {team_id}]'
        base, number = label, 2
        while label in used:
            label = f'{base} ({number})'; number += 1
        labels[key] = label; used.add(label)
    return {ref: labels[key] for ref, key in references.items()}


def season_complete(league):
    status = getattr(league, '_history_status', {})
    if isinstance(status, dict):
        if type(status.get('isFinished')) is bool:
            return status['isFinished']
        latest, final = status.get('latestScoringPeriod'), status.get('finalScoringPeriod')
        if type(latest) is int and type(final) is int:
            return latest > final
    # A final rank is evidence; a live standings rank is not. Never use a
    # wall-clock year to declare January playoffs complete.
    current, final = getattr(league, 'current_week', None), getattr(league, 'finalScoringPeriod', None)
    if type(current) is int and type(final) is int and current < final:
        return False
    return bool(league.teams) and all(type(getattr(t, 'final_standing', None)) is int and t.final_standing > 0 for t in league.teams)


def final_rank(team, league):
    rank = getattr(team, 'final_standing', None)
    return rank if season_complete(league) and type(rank) is int and rank > 0 else None


def completed_games(league):
    """Decided matchups keyed by ESPN matchup period, excluding byes/self games.

    scores[] are matchup totals (sometimes TWO weeks), not necessarily weekly
    scores. Preserve their period IDs rather than aligning shifted list indexes.
    """
    teams = {str(t.team_id): t for t in league.teams}
    games = []
    for team in league.teams:
        periods = getattr(team, 'matchup_periods', None)
        for i, opponent in enumerate(team.schedule):
            opponent_id = str(getattr(opponent, 'team_id', opponent))
            if opponent_id not in teams or opponent_id == str(team.team_id):
                continue
            if i >= len(team.outcomes) or team.outcomes[i] not in ('W','L','T') or i >= len(team.scores):
                continue
            score = team.scores[i]
            if type(score) not in (int,float) or not math.isfinite(score):
                continue
            period = periods[i] if isinstance(periods, list) and i < len(periods) else i+1
            mov = team.mov[i] if isinstance(getattr(team, 'mov', None), list) and i < len(team.mov) else None
            games.append({'team': team, 'opponent': teams[opponent_id], 'period': period,
                          'score': score, 'outcome': team.outcomes[i], 'mov': mov})
    return games


def activity_count(team, key):
    flags = getattr(team, 'history_counter_available', None)
    value = getattr(team, key, None)
    if isinstance(flags, dict) and not flags.get(key, False):
        return None
    return value if type(value) is int and value >= 0 else None


def coverage_for(leagues, draft_rows=None):
    load = getattr(leagues, 'coverage', {})
    seasons = []
    for year, league in sorted(leagues.items()):
        games = completed_games(league)
        draft = getattr(league, 'draft', []) or []
        rows = [r for r in (draft_rows or []) if r['year'] == year]
        warnings = []
        if season_complete(league) and any(final_rank(t, league) is None for t in league.teams):
            warnings.append('Final finishing positions missing; title and average-finish totals may be incomplete.')
        if any(activity_count(t, k) is None for t in league.teams for k in ('trades','acquisitions','drops')):
            warnings.append('Some transaction counters were not reported; activity rates use reported seasons only.')
        if not draft: warnings.append('Draft records unavailable or draft not completed.')
        if not games: warnings.append('No decided matchups loaded; scores may be unavailable or season not started.')
        if any(not owner_identity(t) for t in league.teams): warnings.append('Owner IDs missing for some teams; manager history is not merged by name.')
        missing = sum(not r['stats_available'] for r in rows)
        if missing: warnings.append(f'{missing} drafted-player stat records unavailable; excluded from bust rankings.')
        seasons.append({'year': year, 'teams': len(league.teams), 'completed': season_complete(league),
                        'decided_team_matchups': len(games), 'draft_picks': len(draft),
                        'missing_draft_stats': missing, 'warnings': warnings})
    return {**load, 'loaded_years': sorted(leagues), 'seasons': seasons,
            'transaction_detail': 'ESPN season totals for trades, acquisitions and drops; not a complete historical transaction ledger.',
            'score_scope': 'Decided matchup-period totals, including playoffs. Multiweek playoff matchups count once. Record totals come from ESPN and may use different scopes/rules.',
            'identity_scope': 'By Team follows franchise IDs across renames/owner changes. By Manager follows the owner-ID group; missing IDs remain separate.',
            'scoring_scope': 'Each season uses its own scoring rules. Cross-season point totals are not normalized for scoring or lineup changes.'}
