"""Shared waiver response for browser, extension, and AI context."""
from datetime import datetime, timezone
from .waiver_model import (WaiverError, resolve_team, profile_for, player_card,
                           roster_cards, value_components)
from .faab import (verified_balance, bid_history, replacement_levels, suggest_bid,
                   plan_claims, VERIFY_IN_ESPN)

ASSUMPTIONS = [
    'Value and bid coefficients are uncalibrated planning priors, not winning-bid forecasts.',
    'Future weekly projections fall back to league-scored season projection / 17 NFL games. Known byes are zero; missing bye data is unknown.',
    'An OUT/IR/suspended player is unavailable this week; future return dates are unknown. Review injuries before bidding.',
    'Roster targets use flex and bench allocation priors; replacement uses league demand and one quarter of projected reserve demand.',
    'Displayed add value is before a drop. Review the proposed drop and its lost value; do not auto-submit.',
    'Zero-dollar estimates are possible; ESPN minimum bid and claim rules must be verified.',
]


def candidate_pool(league, week, profile):
    try:
        players = league.free_agents(week=week, size=1000)
    except Exception as exc:
        raise WaiverError('ESPN free-agent refresh failed; no bids calculated.') from exc
    owned = {p.playerId for t in league.teams for p in t.roster}
    # free_agents() returns BoxPlayer with only this week's schedule. Attach
    # the full NFL schedule so future bye coverage is not guessed.
    schedules = {}
    loader = getattr(league, '_get_all_pro_schedule', None)
    if callable(loader):
        try:
            from espn_api.football.constant import PRO_TEAM_MAP
            schedules = {PRO_TEAM_MAP.get(int(k), ''): v for k, v in loader().items()}
        except Exception:
            schedules = {}  # per-card unknown bye remains explicit
    cards = {}
    for player in players:
        if player.playerId in owned:
            continue
        card = player_card(player, league, week, profile, schedules.get(getattr(player, 'proTeam', '')))
        if card['position'] in profile['starter_targets'] and card['value'] > 0:
            cards[card['player_id']] = card
    return list(cards.values())


def ranked_candidates(league, team, week, pool=None, profile=None, roster=None):
    profile = profile or profile_for(league)
    roster = roster if roster is not None else roster_cards(team, league, week, profile)
    pool = pool if pool is not None else candidate_pool(league, week, profile)
    result = []
    for candidate in pool:
        components = value_components(roster, candidate, profile)
        if components['score'] > .01:
            result.append({**candidate, 'components': components, 'reason': components['reason'],
                           'upgrade_per_week': components['starting_gain'],
                           'lineup_gain': components['lineup_marginal']})
    return sorted(result, key=lambda r: (-r['components']['score'], str(r['player_id'])))


def build_waiver_payload(league, config=None, team_id=None, team_name=None, week=None,
                         max_spend=None, claims=None, refresh=False):
    if refresh:
        try:
            league.refresh()
        except Exception as exc:
            raise WaiverError('ESPN roster/budget refresh failed; no bids calculated.') from exc
    team = resolve_team(league, team_id, team_name, config)
    week = int(week or league.current_week)
    # Historical lineups/budgets are not reconstructed by this endpoint.
    if week != max(1, league.current_week):
        raise WaiverError('Live waiver bids require the current scoring week.')
    profile = profile_for(league)
    state, mine = verified_balance(league, team)
    pool = candidate_pool(league, week, profile)
    rosters = {t.team_id: roster_cards(t, league, week, profile) for t in league.teams}
    context = {'rosters': rosters, 'replacement': replacement_levels(pool, league)}
    recommendations = ranked_candidates(league, team, week, pool, profile, rosters[team.team_id])
    history = bid_history(league)
    for rec in recommendations:
        rec['faab'] = suggest_bid(rec, team.team_name, league, pool, team_id=team.team_id,
                                 current_week=week, components=rec['components'], history=history, context=context)
    limit = mine['remaining'] if max_spend is None else max_spend
    from .faab import dollars
    limit = dollars(limit, 'maximum waiver-run spend')
    if claims is None:
        claims, spent, used_drops = [], 0, set()
        # Only propose drops from the existing bench; protect scarce positions.
        counts = {pos: sum(c['position'] == pos for c in rosters[team.team_id]) for pos in profile['fixed']}
        drops = sorted((c for c in rosters[team.team_id] if c['lineup_slot'] == 'BE'
                        and counts.get(c['position'], 0) > profile['fixed'].get(c['position'], 0)),
                       key=lambda c: c['value'])
        open_slots = max(0, profile['roster_size'] - sum(c['lineup_slot'] != 'IR' for c in rosters[team.team_id]))
        for rec in recommendations:
            if len(claims) >= 3:
                break
            bid = rec['faab']['bid']
            if spent + bid > limit:
                continue
            drop = None
            if not open_slots:
                drop = next((d for d in drops if d['player_id'] not in used_drops
                             and counts.get(d['position'], 0) > profile['fixed'].get(d['position'], 0)
                             and d['value'] < rec['value']), None)
                if drop is None:
                    continue
                used_drops.add(drop['player_id'])
                counts[drop['position']] -= 1
            else:
                open_slots -= 1
            claims.append({'player_id': rec['player_id'], 'player': rec['name'], 'bid': bid,
                           'role': 'primary' if not claims else 'fallback',
                           'alternative_group': 'run-target',
                           'drop_player_id': drop['player_id'] if drop else None,
                           'drop_player': drop['name'] if drop else None,
                           'drop_ros_per_game': round(drop['value'], 2) if drop else 0})
            spent += bid
    allowed = {str(c['player_id']) for c in pool}
    if not isinstance(claims, list) or any(not isinstance(c, dict) or str(c.get('player_id')) not in allowed for c in claims):
        raise WaiverError('Claim target is not in the refreshed eligible free-agent pool.')
    plan = plan_claims(league, team, claims, limit)
    state['mine'] = mine
    state['history_count'] = len(history)
    papa = str(getattr(league, 'league_id', '')) == '202314'
    timing = {'value': 'Tuesday at 11:00 a.m. America/New_York' if papa else None,
              'source': 'configured league metadata' if papa else 'verify in ESPN', 'api_verified': False}
    return {'team': team.team_name, 'team_id': team.team_id, 'week': week,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'settings': {'source': 'ESPN API', 'lineup_slots': league.settings.position_slot_counts,
                         'faab_enabled': True, 'initial_faab': state['budget']},
            'processing_time': timing, 'verify_in_espn': VERIFY_IN_ESPN,
            'advisory': 'Advisory only—claims must be entered in ESPN.',
            'franchise_rule': 'Waiver acquisitions are not eligible for the 2027 franchise tag.',
            'assumptions': ASSUMPTIONS, 'pool': {'evaluated': len(pool), 'fetch_limit': 1000,
                                               'unknown_bye_count': sum(c['bye_week'] is None for c in pool),
                                               'scope': 'eligible positive-projection players in ESPN response'},
            'recommendations': recommendations[:30], 'top_agents': sorted(pool, key=lambda c: -c['projected_points'])[:20],
            'streamers': {pos: [dict(c, streamer_score=c['projected_points']) for c in
                               sorted(pool, key=lambda c: -c['projected_points']) if c['position'] == pos and not c['on_bye']
                               and not c['unavailable_now']][:3]
                          for pos in ('QB','TE','K','D/ST') if pos in profile['starter_targets']},
            'faab': state, 'recent_bids': history[:10], 'claim_plan': plan,
            'news': {}, 'ai_available': False}
