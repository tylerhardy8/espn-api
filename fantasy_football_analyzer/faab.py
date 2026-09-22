"""Conservative, explainable advisory FAAB estimates; never submits claims."""
import math
from collections import defaultdict
from .waiver_model import (WaiverError, resolve_team, profile_for, roster_cards,
                           value_components, last_week)

VERIFY_IN_ESPN = {key: 'verify in ESPN' for key in (
    'minimum_bid', 'tiebreak', 'claim_processing_order', 'conditional_claims',
    'drop_eligibility_and_locks', 'player_waiver_availability')}


def dollars(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or int(value) != value:
        raise WaiverError(f'Missing or invalid {label}; no bids calculated.')
    return int(value)


def faab_state(league):
    if getattr(league.settings, 'faab', None) is not True:
        raise WaiverError('FAAB is disabled or unverified; no bids calculated.')
    budget = dollars(getattr(league.settings, 'acquisition_budget', None), 'acquisition budget')
    if budget <= 0:
        raise WaiverError('Acquisition budget must be positive; no $100 fallback.')
    warnings = []
    if str(getattr(league, 'league_id', '')) == '202314' and budget != 150:
        raise WaiverError(f'Papa Trump budget is ${budget}, expected $150. Verify in ESPN before bidding.')
    teams = []
    for t in league.teams:
        row = {'team': t.team_name, 'team_id': t.team_id, 'spent': None, 'remaining': None}
        try:
            if getattr(t, 'acquisition_budget_spent_verified', True) is not True:
                raise WaiverError('ESPN did not provide team spending')
            spent = dollars(getattr(t, 'acquisition_budget_spent', None), 'team spending')
            remaining = budget - spent
            explicit = getattr(t, 'acquisition_budget_remaining', None)
            if remaining < 0 or (explicit is not None and dollars(explicit, 'remaining balance') != remaining):
                raise WaiverError('Contradictory spent/remaining FAAB')
            row.update(spent=spent, remaining=remaining,
                       source='derived from ESPN acquisition budget minus reported spending')
        except WaiverError as exc:
            row['error'] = str(exc)
            warnings.append(f'{t.team_name}: {exc}')
        teams.append(row)
    teams.sort(key=lambda x: -(x['remaining'] if x['remaining'] is not None else -1))
    return {'enabled': True, 'budget': budget, 'teams': teams, 'warnings': warnings,
            'source': 'ESPN settings and transaction counters'}


def verified_balance(league, team):
    state = faab_state(league)
    mine = next((r for r in state['teams'] if r['team_id'] == team.team_id), None)
    if not mine or mine['remaining'] is None:
        raise WaiverError(f"Unverified FAAB balance for team {team.team_id}; no bids calculated.")
    return state, mine


def bid_history(league, size=200):
    out = []
    try:
        acts = league.recent_activity(size=size)
    except Exception:
        return out
    for activity in acts:
        for team, action, player, bid in getattr(activity, 'actions', []):
            if action == 'WAIVER ADDED' and type(bid) in (int, float) and bid >= 0:
                out.append({'player': getattr(player, 'name', str(player)),
                            'team': getattr(team, 'team_name', str(team)), 'bid': int(bid)})
    return out


def replacement_levels(free_agents, league):
    """Per-game pool baseline at league-derived reserve demand, not fixed FA5.

    One quarter of expected league-wide reserves plus unfilled starter demand
    is a modeling prior, exposed alongside the selected rank and pool size.
    """
    profile = profile_for(league)
    by_pos = defaultdict(list)
    for card in free_agents:
        by_pos[card['position']].append(card['value'])
    result = {}
    for pos, values in by_pos.items():
        starter = profile['starter_targets'].get(pos, 0)
        target = profile['roster_targets'].get(pos, 0)
        rostered = sum(sum(p.position == pos for p in t.roster) for t in league.teams)
        demand = max(0, len(league.teams) * starter - rostered)
        rank = max(1, math.ceil(demand + len(league.teams) * max(0, target-starter) / 4))
        rank = min(rank, len(values))
        result[pos] = {'value': sorted(values, reverse=True)[rank-1], 'rank': rank,
                       'pool_size': len(values), 'starter_target': starter, 'roster_target': target}
    return result


def league_aggressiveness(history, budget, default=1.0):
    meaningful = sorted(b['bid'] for b in history if b['bid'] >= 5)
    if len(meaningful) < 4 or not budget:
        return default
    return max(.5, min(2.0, meaningful[len(meaningful)//2] / budget / .085))


def _share(components, over):
    # Depth/insurance/coverage is capped independently of starting-lineup gain.
    marginal = components['lineup_marginal']
    starting = components['starting_gain']
    support = components['depth_premium'] + components['fragility_premium'] + components['coverage_premium']
    depth_share = min(.06, .006 * (max(0, marginal-starting) + support))
    return min(.60, .025 * starting + depth_share + min(.04, .003 * over) * min(1, starting / 3))


def suggest_bid(player, my_team_name, league, free_agents, my_marginal=None,
                rival_needs=None, history=None, current_week=None, team_id=None, config=None,
                components=None, context=None):
    """All value inputs are per remaining playable game. Rivals are ranges.

    Legacy rival_needs is deliberately ignored: use actual roster thinness.
    my_marginal, when supplied, is a per-game sensitivity override.
    """
    me = resolve_team(league, team_id, my_team_name, config)
    state, mine = verified_balance(league, me)
    profile = profile_for(league)
    week = int(current_week or league.current_week)
    if not 1 <= week <= last_week(league):
        raise WaiverError('Requested week is outside the fantasy season.')
    if 'value' not in player or 'weekly' not in player:
        raise WaiverError('Bid needs a normalized waiver player card, not an unqualified ROS total.')
    context = context or {}
    rosters = context.get('rosters') or {t.team_id: roster_cards(t, league, week, profile) for t in league.teams}
    components = dict(components or value_components(rosters[me.team_id], player, profile))
    if my_marginal is not None:
        if type(my_marginal) not in (int,float) or not math.isfinite(my_marginal) or my_marginal < 0:
            raise WaiverError('Invalid lineup marginal value')
        components['lineup_marginal'] = my_marginal
        components['starting_gain'] = min(components['starting_gain'], my_marginal)
    repl = (context.get('replacement') or replacement_levels(free_agents, league)).get(player['position'])
    if repl is None:
        raise WaiverError('No replacement pool for this position; no bid calculated.')
    over = max(0, player['value'] - repl['value'])
    aggression = league_aggressiveness(history or [], state['budget'])
    clock = 1 + .6 * (week-1) / last_week(league)
    rivals = []
    for t in league.teams:
        if t.team_id == me.team_id:
            continue
        balance = next(r for r in state['teams'] if r['team_id'] == t.team_id)
        if balance['remaining'] is None:
            rivals.append({'team': t.team_name, 'team_id': t.team_id, 'error': 'balance unavailable'})
            continue
        demand = value_components(rosters[t.team_id], player, profile)
        share = _share(demand, over)
        central = balance['remaining'] * share * aggression * clock
        rivals.append({'team': t.team_name, 'team_id': t.team_id, 'remaining': balance['remaining'],
                       'low': min(balance['remaining'], int(central*.7)),
                       'high': min(balance['remaining'], math.ceil(central*1.3)), 'demand': demand})
    rivals.sort(key=lambda r: -r.get('high', -1))
    interested = sum(r.get('high', 0) > 0 for r in rivals)
    market = 1 + min(.20, interested * .025)
    share = _share(components, over)
    adjusted_share = min(.80, .08 + .03 * components['starting_gain'], share * aggression * clock * market)
    amount = mine['remaining'] * adjusted_share
    bid = min(mine['remaining'], int(math.floor(amount + .5)))
    low = min(bid, int(amount*.7))
    high = min(mine['remaining'], math.ceil(amount*1.3))
    tier = 'major lineup add' if components['starting_gain'] >= 8 else 'lineup upgrade' if components['starting_gain'] >= 2 else 'depth / insurance'
    return {'bid': bid, 'recommended': bid, 'low': low, 'high': high, 'tier': tier,
            'base_tier': tier, 'replacement_level': repl, 'points_over_replacement': round(over, 3),
            **components, 'market_adjustment': market, 'aggressiveness': aggression,
            'season_clock_adjustment': round(clock, 4), 'base_budget_share': round(share, 4), 'adjusted_budget_share': round(adjusted_share, 4),
            'my_remaining': mine['remaining'], 'rivals': rivals, 'rival_label': 'Estimated rival bid ranges; not winning-bid predictions',
            'units': 'points per remaining playable game', 'verify_in_espn': VERIFY_IN_ESPN,
            'reason': components['reason'] + '; advisory estimate; verify minimum bid in ESPN'}


def plan_claims(league, team, claims, max_spend):
    """Validate a draft list under worst case: ALL claims succeed, even fallbacks.

    Alternative groups document intent, but do not reduce reserved spending or
    permit reusing a drop. This is safe without assuming ESPN conditionals.
    """
    _, mine = verified_balance(league, team)
    limit = dollars(max_spend, 'maximum waiver-run spend')
    if limit > mine['remaining']:
        raise WaiverError('Maximum spend exceeds verified remaining FAAB.')
    profile = profile_for(league)
    roster = {str(p.playerId): p for p in team.roster if getattr(p, 'lineupSlot', '') != 'IR'}
    size, total, seen, drops, ordered = len(roster), 0, set(), set(), []
    if not isinstance(claims, list):
        raise WaiverError('Claims must be an ordered list.')
    for priority, claim in enumerate(claims, 1):
        pid = str(claim.get('player_id', ''))
        drop = claim.get('drop_player_id')
        drop = str(drop) if drop is not None else None
        if not pid or pid in seen or pid in roster:
            raise WaiverError('Each claim must target a unique available player.')
        bid = dollars(claim.get('bid'), 'claim bid')
        if drop is not None and (drop not in roster or drop in drops):
            raise WaiverError('Drops must be distinct currently rostered non-IR players; verify conditional alternatives in ESPN.')
        size += 0 if drop else 1
        if size > profile['roster_size']:
            raise WaiverError('A claim needs a drop to stay within roster capacity.')
        if drop: drops.add(drop)
        seen.add(pid)
        total += bid
        if total > limit:
            raise WaiverError('All-success claim total exceeds maximum waiver-run spend.')
        ordered.append({**claim, 'priority': priority, 'remaining_if_all_prior_succeed': mine['remaining']-total})
    return {'claims': ordered, 'max_spend': limit, 'total_possible_spend': total,
            'remaining_if_all_succeed': mine['remaining']-total,
            'alternatives_policy': 'Reserve every bid. Group labels express mutually exclusive intent only; verify ESPN support or enter one alternative.',
            'verify_in_espn': VERIFY_IN_ESPN, 'advisory': 'Advisory only—claims must be entered in ESPN.'}
