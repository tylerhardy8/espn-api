from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pytest
from fantasy_football_analyzer.league_connector import connect_multi_year
from fantasy_football_analyzer.historical import (analyze_team_history, analyze_head_to_head,
    analyze_luck, analyze_scoring_trends, analyze_draft_history)
from fantasy_football_analyzer.history_data import coverage_for, season_complete, identity_labels
from fantasy_football_analyzer.league_intel import build_league_intel


def team(tid,name,owner='a',rank=1):
    return NS(team_id=tid,team_name=name,owners=[{'id':owner,'displayName':name+' Owner'}] if owner else [],
              wins=1,losses=0,ties=0,points_for=100,points_against=50,acquisitions=3,trades=1,drops=2,
              final_standing=rank,standing=rank or 1,schedule=[],outcomes=[],scores=[],mov=[],roster=[],matchup_periods=[])


def league(year,teams,complete=True,previous=None):
    return NS(year=year,league_id=202314,teams=teams,draft=[],previousSeasons=previous or [],
              settings=NS(draft_type='AUCTION',auction_budget=320),
              standings=lambda:sorted(teams,key=lambda t:t.standing),
              _history_status={'isFinished':complete},player_info=Mock(return_value=[]))


def add_game(a,b,score_a=100,score_b=50,period=1,decided=True):
    for t,o,x,y in ((a,b,score_a,score_b),(b,a,score_b,score_a)):
        t.schedule.append(o);t.scores.append(x);t.mov.append(x-y);t.matchup_periods.append(period)
        t.outcomes.append(('W' if x>y else 'L' if x<y else 'T') if decided else 'U')


def test_discovers_all_seasons_recursively_and_deduplicates():
    seed=league(2026,[],previous=[2025,2025,2020])
    data={2025:league(2025,[],previous=[2010]),2020:league(2020,[]),2010:league(2010,[])}
    with patch('fantasy_football_analyzer.league_connector.connect_league',side_effect=lambda _,y,*args,**kw:data[y]) as load:
        result=connect_multi_year(202314,seed=seed)
    assert sorted(result)==[2010,2020,2025,2026] and load.call_count==3
    assert result.coverage['discovery_verified'] and result.coverage['all_requested_loaded']


def test_missing_season_reported_and_error_does_not_leak_secret():
    seed=league(2026,[],previous=[2025])
    with patch('fantasy_football_analyzer.league_connector.connect_league',side_effect=RuntimeError('secret-cookie')):
        result=connect_multi_year(202314,seed=seed)
    assert result.coverage['failed_years'].keys()=={2025}
    assert not result.coverage['all_requested_loaded']
    assert 'secret-cookie' not in str(result.coverage)


def test_explicit_years_restrict_scope_and_discovery_failure_visible():
    seed=league(2026,[],previous=[2025,2024])
    result=connect_multi_year(202314,[2026],seed=seed)
    assert list(result)==[2026]
    with patch('fantasy_football_analyzer.league_connector.connect_league',side_effect=RuntimeError()):
        result=connect_multi_year(202314,current_year=2026)
    assert not result and result.coverage['failed_years'] and not result.coverage['discovery_verified']


def test_franchise_rename_joins_and_same_name_different_ids_do_not():
    old,new,other=team(14,'Quiet, Piggy'),team(14,"Kaitlan Callin' Audibles"),team(2,"Kaitlan Callin' Audibles",owner='b',rank=2)
    result=analyze_team_history({2025:league(2025,[old]),2026:league(2026,[new,other],False)})
    assert len(result)==2
    mine=next(v for v in result.values() if v['num_seasons']==2)
    assert mine['team_names']==["Kaitlan Callin' Audibles",'Quiet, Piggy']
    assert mine['championships']==1 and mine['completed_seasons']==1


def test_manager_id_survives_renames_but_owner_change_stays_separate():
    old,new=team(14,'Old'),team(14,'New')
    old.owners=[{'id':'OWNER-1','displayName':'Before'}]
    new.owners=[{'id':'owner-1','displayName':'After'}]
    data={2024:league(2024,[old]),2025:league(2025,[new])}
    result=analyze_team_history(data,'manager')
    assert list(result)==['After'] and result['After']['num_seasons']==2
    new.owners=[{'id':'owner-2','displayName':'Before'}]
    assert len(analyze_team_history(data,'manager'))==2
    assert len(analyze_team_history(data,'team'))==1


def test_missing_owner_ids_not_guessed_and_coowners_order_stable():
    a,b=team(1,'Same',None),team(1,'Same',None)
    data={2024:league(2024,[a]),2025:league(2025,[b])}
    assert len(analyze_team_history(data,'manager'))==2
    a.owners=[{'id':'A','displayName':'A'},{'id':'B','displayName':'B'}]
    b.owners=list(reversed(a.owners))
    assert len(analyze_team_history(data,'manager'))==1


def test_live_leader_not_champion_or_final_average():
    t=team(14,'Leader',rank=1)
    result=analyze_team_history({2026:league(2026,[t],False)})['Leader']
    assert result['championships']==0 and result['avg_finish'] is None
    intel=build_league_intel({2026:league(2026,[t],False)})
    assert intel['years']==[] and intel['managers']=={}


def test_champion_uses_final_rank_not_standings_sort_index():
    a,b=team(1,'Regular winner',rank=2),team(2,'Actual champion','b',rank=1)
    lg=league(2025,[a,b]);lg.standings=lambda:[a,b]
    data=analyze_team_history({2025:lg})
    assert data['Actual champion']['championships']==1 and data['Regular winner']['championships']==0


def test_ties_count_half_win():
    a,b=team(1,'A'),team(2,'B','b',2);a.wins=0;a.ties=1
    add_game(a,b,0,0)
    data={2025:league(2025,[a,b])}
    assert analyze_team_history(data)['A']['all_time_win_pct']==.5
    assert analyze_luck(data)['A']['actual_wins']==.5
    assert analyze_luck(data)['A']['luck_delta']==0


def test_scores_include_negative_zero_exclude_live_and_byes():
    a,b=team(1,'A'),team(2,'B','b',2)
    add_game(a,b,0,-2);add_game(a,b,100,99,2,False)
    a.schedule.append(a);a.outcomes.append('W');a.scores.append(500);a.mov.append(500);a.matchup_periods.append(3)
    data={2025:league(2025,[a,b])}
    trend=analyze_scoring_trends(data)[0]
    assert trend['avg_score']==-1 and trend['min_score']==-2 and trend['periods_played']==1
    assert analyze_head_to_head(data)['A']=={'B':{'wins':1,'losses':0,'ties':0}}
    assert analyze_luck(data)['A']['games']==1


def test_luck_aligns_actual_periods_after_byes_not_list_indices():
    a,b,c,d=[team(i,str(i),str(i),i) for i in range(1,5)]
    add_game(a,b,200,100,2)
    add_game(c,d,1,2,1)
    add_game(c,d,300,400,2)
    data={2025:league(2025,[a,b,c,d])}
    # A beats B but would lose to C and D in period 2: one of three opponents.
    assert analyze_luck(data)['1']['expected_wins']==.3


def test_missing_drafted_player_stats_unknown_not_zero():
    a=team(14,'A');lg=league(2025,[a])
    lg.draft=[NS(playerId=999,playerName='Unavailable stats',team=a,round_num=1,round_pick=1,bid_amount=50,keeper_status=False)]
    lg.player_info.side_effect=RuntimeError('fetch failed')
    rows=analyze_draft_history({2025:lg})
    assert rows[0]['total_points'] is None and not rows[0]['stats_available'] and rows[0]['pos_rank'] is None
    assert coverage_for({2025:lg},rows)['seasons'][0]['missing_draft_stats']==1


def test_completed_intel_joins_owner_identity_and_does_not_invent_zero_stat_value():
    a,b=team(14,'Old'),team(14,'Current')
    lg=league(2025,[a]);live=league(2026,[b],False)
    lg.draft=[NS(playerId=999,playerName='Unknown',team=a,round_num=1,round_pick=1,bid_amount=50,keeper_status=False)]
    intel=build_league_intel({2025:lg,2026:live})
    assert intel['years']==[2025] and list(intel['managers'])==['Current Owner']
    m=intel['managers']['Current Owner']
    assert m['seasons']==1 and m['draft_style']['avg_price_per_point'] is None


def test_no_final_rank_means_no_title_even_finished():
    lg=league(2025,[team(14,'Unknown final',rank=0)])
    assert analyze_team_history({2025:lg})['Unknown final']['championships']==0
    assert build_league_intel({2025:lg})['managers']['Unknown final Owner']['titles']==0


def test_manager_profile_uses_owner_id_even_after_display_rename():
    from fantasy_football_analyzer.league_intel import manager_profile
    old,current=team(14,'Old name'),team(14,'New name')
    intel=build_league_intel({2025:league(2025,[old])})
    assert manager_profile(intel,current)['seasons']==1
    current.owners=[{'id':'different-person','displayName':'Old name Owner'}]
    assert manager_profile(intel,current) is None


def test_complete_season_uses_espn_scoring_period_not_calendar():
    lg=league(2025,[team(14,'Winner')])
    lg._history_status={'latestScoringPeriod':16,'finalScoringPeriod':17}
    assert not season_complete(lg)
    lg._history_status['latestScoringPeriod']=18
    assert season_complete(lg)


def test_missing_activity_counter_is_not_a_zero_activity_season():
    from fantasy_football_analyzer.historical import analyze_manager_tendencies,format_historical_report
    a,b=team(14,'Old'),team(14,'New')
    a.history_counter_available={'trades':False,'acquisitions':False,'drops':False}
    data={2024:league(2024,[a]),2025:league(2025,[b])}
    manager=analyze_manager_tendencies(data,'manager')['New Owner']
    assert manager['trades_reported_seasons']==1 and manager['avg_trades_per_season']==1
    intel=build_league_intel(data)
    assert intel['managers']['New Owner']['trades_per_season']==1
    assert coverage_for(data)['seasons'][0]['warnings']
    missing=analyze_manager_tendencies({2024:data[2024]})['Old']
    assert missing['avg_trades_per_season'] is None
    assert 'HISTORICAL' in format_historical_report({2024:data[2024]})
