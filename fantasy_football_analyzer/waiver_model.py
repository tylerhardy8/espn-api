"""Roster-aware waiver values in points per remaining playable game.

Deliberately separate from draft/trade projections: no playoff weighting and
no division of a ROS total by a full season. All premiums are model priors.
"""
import math
from collections import Counter
from .lineup import slot_profile, marginal_value, optimal_lineup_value, NONCORE_SLOTS


class WaiverError(ValueError):
    """Insufficient or contradictory inputs; callers must not manufacture bids."""


def resolve_team(league, team_id=None, team_name=None, config=None):
    config = config or {}
    profile = next((p for p in config.get('leagues', [])
                    if p.get('name') == config.get('active')), config)
    configured_id = profile.get('team_id')
    identity = team_id if team_id not in (None, '') else configured_id
    if identity not in (None, ''):
        matches = [t for t in league.teams if str(t.team_id) == str(identity)]
    else:
        name = (team_name or profile.get('team_name') or '').strip().casefold()
        matches = [t for t in league.teams if name and t.team_name.strip().casefold() == name]
    if len(matches) != 1:
        raise WaiverError(f"Missing or ambiguous team configuration ({identity or team_name or 'unset'}). Select a valid ESPN team ID; no bids calculated.")
    return matches[0]


def profile_for(league):
    slots = getattr(league.settings, 'position_slot_counts', None)
    if not isinstance(slots, dict) or not slots or any(type(v) is not int or v < 0 for v in slots.values()):
        raise WaiverError('Missing or invalid roster-slot settings; no bids calculated.')
    known = set(NONCORE_SLOTS) | {'QB','RB','WR','TE','K','D/ST','BE','IR','Rookie','',
                                  'RB/WR/TE','RB/WR','WR/TE','FLEX','OP'}
    if any(v and k not in known for k, v in slots.items()):
        raise WaiverError('An active roster slot is unknown; verify the league lineup settings.')
    profile = slot_profile(slots)
    for pos in NONCORE_SLOTS:
        if slots.get(pos):
            profile['fixed'][pos] = slots[pos]
            profile['starter_targets'][pos] = float(slots[pos])
            profile['roster_targets'][pos] = float(slots[pos])
    if not profile['starter_targets']:
        raise WaiverError('No starting slots available.')
    return profile


def position_for(position, profile):
    if position in profile['starter_targets']:
        return position
    for slot, eligible in {'DL': ('DE','DT','ER'), 'DB': ('CB','S'),
                           'DP': ('LB','DL','DE','DT','DB','CB','S','ER')}.items():
        if slot in profile['fixed'] and position in eligible:
            return slot
    return position


def _number(value, default=0.0):
    return float(value) if type(value) in (float, int) and math.isfinite(value) else default


def last_week(league):
    periods = getattr(league.settings, 'matchup_periods', {})
    weeks = [int(w) for values in periods.values() for w in values] if isinstance(periods, dict) else []
    return max(weeks, default=17)


def player_card(player, league, week=None, profile=None, schedule_override=None):
    from .sources import blended_projection
    profile = profile or profile_for(league)
    week = int(week or league.current_week or 1)
    end = last_week(league)
    if not 1 <= week <= end:
        raise WaiverError('Requested week is outside the fantasy season.')
    season = _number(getattr(player, 'projected_total_points', None))
    # ESPN totals are already league-scored; FantasyPros raw stats are scored
    # by blended_projection using the league's positional reception overrides.
    season, source = blended_projection(player.name, player.position, season, league)
    baseline = max(0.0, season / 17.0)  # full NFL season projection, NOT ROS / 17
    schedule = schedule_override if schedule_override is not None else getattr(player, 'schedule', {})
    scheduled = {int(w) for w in schedule if str(w).isdigit()} if isinstance(schedule, dict) else set()
    bye = getattr(player, 'bye_week', None)
    if type(bye) is not int:
        missing = set(range(1,19)) - scheduled
        bye = next(iter(missing)) if len(scheduled) == 17 and len(missing) == 1 else None
    if getattr(player, 'on_bye_week', False) is True and week == league.current_week:
        bye = week
    published = {}
    stats = getattr(player, 'stats', {})
    for w, row in (stats.items() if isinstance(stats, dict) else []):
        if str(w).isdigit() and isinstance(row, dict) and type(row.get('projected_points')) in (int,float):
            published[int(w)] = max(0.0, _number(row['projected_points']))
    current = _number(getattr(player, 'projected_points', None))
    if week == league.current_week and current > 0:
        published.setdefault(week, current)
    injury = str(getattr(player, 'injuryStatus', '') or 'Active')
    unavailable_now = injury.upper() in {'OUT','O','IR','INJURY_RESERVE','SUSPENSION','SUSPENDED'}
    # No invented return date: zero this week, future weeks baseline, explicitly disclosed.
    timeline = {w: (0.0 if w == bye or (w == week and unavailable_now) else published.get(w, baseline))
                for w in range(week, end + 1)}
    playable = len(timeline) - int(bye in timeline)
    ros = sum(timeline.values())
    rate = ros / playable if playable else 0.0
    return {'name': player.name, 'player_id': player.playerId,
            'position': position_for(player.position, profile), 'original_position': player.position,
            'value': rate, 'ros_per_game': rate, 'ros': ros, 'projected_total': ros,
            'remaining_playable_weeks': playable, 'weekly': timeline, 'bye_week': bye,
            'projection_source': 'ESPN + league-scored FantasyPros' if source is not None else 'ESPN', 'injury_status': injury, 'unavailable_now': unavailable_now,
            'lineup_slot': getattr(player, 'lineupSlot', ''),
            'projected_points': timeline[week], 'on_bye': bye == week,
            'team': str(getattr(player, 'proTeam', '')), 'avg_points': _number(getattr(player, 'avg_points', 0)),
            'percent_owned': _number(getattr(player, 'percent_owned', 0)),
            'pro_opponent': str(getattr(player, 'pro_opponent', '')),
            'franchise_2027_eligible': False}


def roster_cards(team, league, week=None, profile=None):
    return [player_card(p, league, week, profile) for p in team.roster]


def value_components(roster, candidate, profile):
    pos, rate = candidate['position'], candidate['value']
    counts = Counter(p['position'] for p in roster)
    required = profile['fixed'].get(pos, 0)
    shortage = max(0.0, profile['roster_targets'].get(pos, 0) - counts[pos])
    no_reserves = bool(required and counts[pos] <= required)
    marginal = max(0.0, marginal_value(roster, candidate, profile))
    starting = max(0.0, optimal_lineup_value(roster + [candidate], profile) - optimal_lineup_value(roster, profile))
    depth = rate * min(.20, .06 * shortage)
    fragility = rate * .12 if no_reserves else 0.0
    bye_gains, injury_gain = {}, 0.0
    for w, points in candidate['weekly'].items():
        if points <= 0:
            continue
        available = [dict(p, value=p['weekly'].get(w, 0)) for p in roster if p['weekly'].get(w, 0) > 0]
        gain = max(0.0, optimal_lineup_value(available + [dict(candidate, value=points)], profile)
                   - optimal_lineup_value(available, profile) - starting)
        if gain and any(p['bye_week'] == w for p in roster):
            bye_gains[w] = round(gain, 3)
        elif gain and any(p['unavailable_now'] and w == min(candidate['weekly']) for p in roster):
            injury_gain += gain
    coverage = min(rate * .25, (sum(bye_gains.values()) + injury_gain) / max(1, len(candidate['weekly'])))
    reason = []
    if starting > .01: reason.append('starting lineup / flex improvement')
    elif marginal > .01: reason.append('bench insurance value')
    if depth: reason.append(f'{pos} depth: {counts[pos]} rostered vs {profile["roster_targets"][pos]:.1f} target')
    if no_reserves: reason.append(f'no reserve behind {required} required {pos} slots')
    if bye_gains: reason.append('bye coverage: ' + ', '.join('Week ' + str(w) for w in bye_gains))
    if injury_gain: reason.append('current injury coverage')
    return {'lineup_marginal': round(marginal, 4), 'starting_gain': round(starting, 4),
            'depth_premium': round(depth, 4), 'fragility_premium': round(fragility, 4),
            'coverage_premium': round(coverage, 4), 'bye_week_gains': bye_gains,
            'injury_coverage_gain': round(injury_gain, 4), 'roster_count': counts[pos],
            'roster_target': profile['roster_targets'].get(pos, 0), 'no_reserves': no_reserves,
            'score': round(marginal + depth + fragility + coverage, 4),
            'reason': '; '.join(reason) or 'no material roster gain'}
