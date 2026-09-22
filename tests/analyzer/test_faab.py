"""Spending safety, continuous value and rival demand regressions."""
import copy
import pytest
from unittest.mock import patch
from .waiver_fixture import papa, player
from fantasy_football_analyzer.faab import (faab_state, suggest_bid, replacement_levels,
    league_aggressiveness, plan_claims, verified_balance)
from fantasy_football_analyzer.waiver_model import WaiverError, player_card, resolve_team
from fantasy_football_analyzer.waiver_service import build_waiver_payload, candidate_pool
from fantasy_football_analyzer.waiver_model import profile_for


@pytest.fixture(autouse=True)
def no_external_consensus():
    with patch('fantasy_football_analyzer.sources.consensus_projection_map', return_value={}):
        yield


def test_verified_150_spending():
    league = papa()
    state, mine = verified_balance(league, league.teams[0])
    assert state['budget'] == 150 and mine['spent'] == 47 and mine['remaining'] == 103


@pytest.mark.parametrize('budget',[None,0,-1,100,True,float('nan'),150.5])
def test_bad_budget_fails_closed(budget):
    league=papa();league.settings.acquisition_budget=budget
    with pytest.raises(WaiverError): build_waiver_payload(league,team_id=14)


@pytest.mark.parametrize('spent',[None,-1,151,True,float('nan'),1.2])
def test_bad_spending_fails_closed(spent):
    league=papa();league.teams[0].acquisition_budget_spent=spent
    with pytest.raises(WaiverError): build_waiver_payload(league,team_id=14)


@pytest.mark.parametrize('remaining',[151,104,-1,True])
def test_contradictory_remaining(remaining):
    league=papa();league.teams[0].acquisition_budget_remaining=remaining
    with pytest.raises(WaiverError): build_waiver_payload(league,team_id=14)


def test_missing_counter_not_treated_as_zero():
    league=papa();league.teams[0].acquisition_budget_spent_verified=False
    with pytest.raises(WaiverError): build_waiver_payload(league,team_id=14)


def test_disabled_and_unknown_faab():
    for flag in (False,None):
        league=papa();league.settings.faab=flag
        with pytest.raises(WaiverError): faab_state(league)


def test_failed_lookup_never_produces_bid():
    league=papa();card=player_card(league.free_agents.return_value[0],league)
    with pytest.raises(WaiverError,match='team configuration'):
        suggest_bid(card,'Quiet, Piggy',league,[card])


def test_stable_id_over_stale_name_and_unique_legacy_exposes_id():
    league=papa()
    payload=build_waiver_payload(league,config={'team_id':14,'team_name':'Quiet, Piggy'})
    assert payload['team_id']==14 and payload['team']=="Kaitlan Callin' Audibles"
    assert resolve_team(league,team_name=payload['team']).team_id==14
    with pytest.raises(WaiverError): resolve_team(league,team_id=999,team_name=payload['team'])
    league.teams[1].team_name=payload['team']
    with pytest.raises(WaiverError): resolve_team(league,team_name=payload['team'])


def test_thin_rival_estimated_demand_above_deep_rival():
    payload=build_waiver_payload(papa(),team_id=14)
    card=next(r for r in payload['recommendations'] if r['player_id']==500)
    rivals={r['team_id']:r for r in card['faab']['rivals']}
    assert rivals[2]['demand']['no_reserves']
    assert not rivals[3]['demand']['no_reserves']
    assert rivals[2]['high'] > rivals[3]['high']
    assert 'not winning-bid' in card['faab']['rival_label']


def test_monotonic_bid_continuous_marginal_and_caps():
    league=papa();pool=candidate_pool(league,3,profile_for(league));card=pool[0]
    bids=[]
    for marginal in (0,1,2,4,8,12,20):
        components={'lineup_marginal':marginal,'starting_gain':marginal,'depth_premium':0,
                    'fragility_premium':0,'coverage_premium':0,'reason':'sensitivity'}
        b=suggest_bid(card,None,league,pool,team_id=14,components=components)
        assert 0 <= b['low'] <= b['bid'] <= b['high'] <= 103
        bids.append(b['bid'])
    assert bids==sorted(bids) and len(set(bids))>4


def test_depth_is_not_major_add_and_better_player_never_lower():
    league=papa();payload=build_waiver_payload(league,team_id=14)
    rbs=[r for r in payload['recommendations'] if r['position']=='RB']
    assert all(r['faab']['tier']=='depth / insurance' for r in rbs)
    pairs=sorted((r['value'],r['faab']['bid']) for r in rbs)
    assert [b for _,b in pairs]==sorted(b for _,b in pairs)
    assert max(b for _,b in pairs)<20


def test_zero_balance_caps_every_range():
    league=papa();league.teams[0].acquisition_budget_spent=150
    p=build_waiver_payload(league,team_id=14)
    assert all(r['faab'][k]==0 for r in p['recommendations'] for k in ('low','bid','high'))
    assert p['claim_plan']['total_possible_spend']==0


def test_replacement_changes_with_league_slots_and_pool():
    league=papa();pool=candidate_pool(league,3,profile_for(league))
    r=replacement_levels(pool,league)['RB']
    assert r['rank'] != 5 and r['pool_size']==7
    league.settings.position_slot_counts['RB/WR/TE']=0
    league.settings.position_slot_counts['BE']=0
    assert replacement_levels(pool,league)['RB']['rank']<r['rank']


def test_claim_plan_all_success_and_fallback_reserve():
    league=papa();mine=league.teams[0]
    claims=[{'player_id':500,'bid':50,'drop_player_id':40,'role':'primary','alternative_group':'RB'},
            {'player_id':501,'bid':40,'drop_player_id':41,'role':'fallback','alternative_group':'RB'}]
    result=plan_claims(league,mine,claims,95)
    assert result['total_possible_spend']==90 and result['remaining_if_all_succeed']==13
    assert [c['priority'] for c in result['claims']]==[1,2]
    assert result['claims'][0]['remaining_if_all_prior_succeed']==53
    for change in ('overspend','cap','drop','no_drop','duplicate'):
        bad=copy.deepcopy(claims);cap=95
        if change=='overspend':bad[1]['bid']=60
        if change=='cap':cap=104
        if change=='drop':bad[1]['drop_player_id']=40
        if change=='no_drop':bad[0]['drop_player_id']=None
        if change=='duplicate':bad[1]['player_id']=500
        with pytest.raises(WaiverError):plan_claims(league,mine,bad,cap)


def test_service_validates_claim_player_and_unknown_rules():
    league=papa()
    with pytest.raises(WaiverError):build_waiver_payload(league,team_id=14,claims=[{'player_id':999,'bid':2}],max_spend=3)
    payload=build_waiver_payload(league,team_id=14,max_spend=12)
    assert payload['claim_plan']['total_possible_spend']<=12
    assert len(payload['claim_plan']['claims'])>=1
    assert all(v=='verify in ESPN' for v in payload['verify_in_espn'].values())
    assert payload['settings']['source']=='ESPN API'
    assert payload['processing_time']['api_verified'] is False
    assert payload['processing_time']['source']=='configured league metadata'
    assert 'Tuesday' in payload['processing_time']['value']
    assert 'Advisory only' in payload['advisory']


def test_refresh_failure_and_pool_failure_never_serve_cached_bids():
    league=papa();league.refresh.side_effect=RuntimeError('offline')
    with pytest.raises(WaiverError,match='refresh failed'):build_waiver_payload(league,team_id=14,refresh=True)
    league.refresh.side_effect=None;league.free_agents.side_effect=RuntimeError('offline')
    with pytest.raises(WaiverError,match='refresh failed'):build_waiver_payload(league,team_id=14,refresh=True)


def test_history_and_clock():
    assert league_aggressiveness([],150)==1
    assert league_aggressiveness([{'bid':v} for v in (30,40,25,45)],150)>1.5
    assert league_aggressiveness([{'bid':v} for v in (5,6,5,7)],150)<.7
    league=papa();pool=candidate_pool(league,3,profile_for(league))
    early=suggest_bid(pool[0],None,league,pool,team_id=14,current_week=3)
    late=suggest_bid(pool[0],None,league,pool,team_id=14,current_week=14)
    assert late['high']>=early['high'] and late['season_clock_adjustment']>early['season_clock_adjustment']


def test_team_id_survives_config_normalization_and_league_switch():
    from fantasy_football_analyzer.config import _normalize
    league=papa()
    flat=_normalize({'league_id':202314,'team_id':14,'team_name':'Quiet, Piggy'})
    assert resolve_team(league,config=flat).team_id==14
    profiles={'active':'Papa','leagues':[{'name':'Papa','league_id':202314,'team_id':14,'team_name':'Old'},
                                      {'name':'Other','league_id':99,'team_id':3,'team_name':'Different'}]}
    assert resolve_team(league,config=_normalize(profiles)).team_id==14
    profiles['active']='Other'
    assert _normalize(profiles)['team_id']==3
