"""Whole-roster waiver value, projection units, and league scoring."""
from unittest.mock import patch
import pytest
from .waiver_fixture import papa, player
from fantasy_football_analyzer.waivers import get_top_free_agents, get_waiver_recommendations, find_streamers
from fantasy_football_analyzer.waiver_model import player_card, profile_for, value_components, roster_cards, WaiverError
from fantasy_football_analyzer.waiver_service import build_waiver_payload
from fantasy_football_analyzer.sources import score_stat_line


@pytest.fixture(autouse=True)
def no_external_consensus():
    with patch('fantasy_football_analyzer.sources.consensus_projection_map',return_value={}):yield


def test_get_top_sorted_and_fetch_error():
    league=papa()
    agents=get_top_free_agents(league,week=3)
    assert len(agents)>1 and agents[0]['projected_points']>=agents[1]['projected_points']
    league.free_agents.side_effect=RuntimeError('offline')
    assert get_top_free_agents(league,week=3)==[]


def test_two_excellent_rbs_still_recommend_lower_projected_depth():
    league=papa()
    recs=get_waiver_recommendations(league,team_id=14)
    rb=next(r for r in recs if r['player_id']==500)
    assert rb['value']<19
    assert rb['components']['starting_gain']==0
    assert rb['components']['depth_premium']>0
    assert rb['components']['fragility_premium']>0
    assert 'no reserve' in rb['reason'] and 'Week 13' in rb['reason']
    assert rb['components']['bye_week_gains'][13]>0
    assert recs.index(rb)<3


def test_lineup_marginal_is_a_core_signal():
    league=papa()
    with patch('fantasy_football_analyzer.waiver_model.marginal_value',return_value=7.123) as marginal:
        recs=get_waiver_recommendations(league,team_id=14)
    assert marginal.call_count>0
    assert all(r['components']['lineup_marginal']==7.123 for r in recs)


def test_on_bye_still_ros_depth_target():
    league=papa();league.free_agents.return_value[0].on_bye_week=True
    rec=next(r for r in get_waiver_recommendations(league,team_id=14) if r['player_id']==500)
    assert rec['on_bye'] and rec['projected_points']==0 and rec['value']>0


def test_no_dst_and_noncore_positions_preserved():
    league=papa();payload=build_waiver_payload(league,team_id=14)
    assert 'D/ST' not in payload['streamers']
    assert all(c['position']!='D/ST' for c in payload['recommendations']+payload['top_agents'])
    assert any(c['position']=='P' for c in payload['recommendations'])
    p=profile_for(league)
    assert p['fixed']['RB']==2 and p['bench']==6
    assert p['flex']==[(('RB','WR','TE'),2)]
    assert all(p['fixed'][pos]==1 for pos in ('P','HC','LB','DL','DB'))
    assert 'D/ST' not in find_streamers(league)
    assert all(c['franchise_2027_eligible'] is False for c in payload['recommendations'])


def test_ros_uses_remaining_playable_weeks_including_current_and_known_bye():
    league=papa();league.current_week=12
    p=player(1,'Test','RB',10,bye=13)
    card=player_card(p,league)
    assert card['remaining_playable_weeks']==5
    assert card['ros']==50 and card['ros_per_game']==10
    assert card['weekly'][12]==10 and card['weekly'][13]==0
    p.stats={12:{'projected_points':20}}
    card=player_card(p,league)
    assert card['ros']==60 and card['ros_per_game']==12
    p.schedule={12:{},14:{}}  # partial schedule must not invent week 1 bye
    card=player_card(p,league)
    assert card['bye_week'] is None and card['remaining_playable_weeks']==6


def test_te_premium_retained_through_consensus_and_weekly_projection():
    league=papa()
    te=score_stat_line({'rec_rec':100},'TE',league)
    rb=score_stat_line({'rec_rec':100},'RB',league)
    assert te==150 and rb==100
    p=player(900,'Premium TE','TE',0);p.projected_total_points=0;p.projected_points=0
    with patch('fantasy_football_analyzer.sources.consensus_projection_map',return_value={('premium te','TE'):te}):
        card=player_card(p,league)
    assert card['ros_per_game']==pytest.approx(150/17)
    assert card['projection_source']=='ESPN + league-scored FantasyPros'


def test_current_injury_and_bye_coverage_not_just_roster_counts():
    league=papa();league.teams[0].roster[0].injuryStatus='OUT'
    rec=next(r for r in get_waiver_recommendations(league,team_id=14) if r['player_id']==500)
    assert rec['components']['injury_coverage_gain']>0
    assert 'injury coverage' in rec['reason']


def test_unknown_positive_slots_and_missing_slots_block_bid():
    for slots in ({}, {'999':1,'RB':2}):
        league=papa();league.settings.position_slot_counts=slots
        with pytest.raises(WaiverError):build_waiver_payload(league,team_id=14)


def test_current_week_required_for_live_bid():
    with pytest.raises(WaiverError,match='current scoring week'):
        build_waiver_payload(papa(),team_id=14,week=13)


def test_box_player_full_schedule_enrichment_prevents_false_bye_coverage():
    from unittest.mock import Mock
    league=papa()
    candidate=league.free_agents.return_value[0]
    candidate.schedule={};candidate.proTeam='IND'
    league._get_all_pro_schedule=Mock(return_value={11:{str(w):[{}] for w in range(1,19) if w!=13}})
    rec=next(r for r in get_waiver_recommendations(league,team_id=14) if r['player_id']==500)
    assert rec['bye_week']==13
    assert 13 not in rec['components']['bye_week_gains']
