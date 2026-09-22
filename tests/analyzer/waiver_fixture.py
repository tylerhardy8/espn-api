"""Synthetic Papa Trump scenario; dollar balances are NOT live league data."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

SLOTS = {'QB':1,'RB':2,'WR':2,'TE':1,'RB/WR/TE':2,'K':1,'LB':1,'DL':1,'DB':1,'P':1,'HC':1,'BE':6,'IR':1,'D/ST':0}


def player(pid, name, pos, rate, slot='BE', bye=9, injury='Active'):
    return NS(playerId=pid, name=name, position=pos, projected_total_points=rate*17,
              projected_points=rate, points=0, total_points=0, avg_points=0,
              lineupSlot=slot, proTeam='TEST', percent_owned=10, percent_started=0,
              pro_opponent='TEST', pro_pos_rank=10, on_bye_week=False, injuryStatus=injury,
              schedule={str(w): {} for w in range(1,19) if w != bye}, stats={})


def papa():
    roster = [player(1,'Jonathan Taylor','RB',20,'RB',13), player(2,'Breece Hall','RB',19,'RB',13),
              player(3,'Zay Flowers','WR',15,'WR',13),player(4,'Brock Bowers','TE',17,'TE',13),
              player(5,'Starting QB','QB',23,'QB')]
    roster += [player(10+i,f'WR {i}','WR',16-i,'WR' if i == 0 else 'BE') for i in range(5)]
    roster += [player(30+i,p,p,8,p) for i,p in enumerate(('K','LB','DL','DB','P','HC'))]
    roster += [player(40+i,f'Bench TE {i}','TE',3-i) for i in range(3)]
    roster += [player(50,'Backup QB','QB',10)]
    mine = NS(team_id=14, team_name="Kaitlan Callin' Audibles", acquisition_budget_spent=47, roster=roster)
    thin = NS(team_id=2, team_name='Thin RB rival', acquisition_budget_spent=20,
              roster=[player(100+i,f'Rival RB {i}','RB',20-i,'RB') for i in range(2)])
    deep = NS(team_id=3, team_name='Deep RB rival', acquisition_budget_spent=20,
              roster=[player(200+i,f'Deep RB {i}','RB',20-i,'RB' if i<2 else 'BE') for i in range(6)])
    teams=[mine,thin,deep] + [NS(team_id=i,team_name=f'Team {i}',acquisition_budget_spent=50,roster=list(deep.roster)) for i in range(4,13)]
    agents=[player(500+i,f'Available RB {i}','RB',12-i,bye=7) for i in range(7)]
    agents += [player(600,'Available TE','TE',8),player(601,'Available WR','WR',9),
               player(602,'Forbidden DST','D/ST',25),player(603,'Available P','P',9)]
    settings = NS(position_slot_counts=dict(SLOTS), faab=True, acquisition_budget=150,
                  matchup_periods={str(w):[w] for w in range(1,18)},
                  _raw_scoring_settings={'scoringItems':[{'statId':53,'points':0,'pointsOverrides':{'2':1,'3':1,'4':1.5}}]})
    league=NS(league_id=202314,year=2026,current_week=3,settings=settings,teams=teams,
              free_agents=Mock(return_value=agents),recent_activity=Mock(return_value=[]),refresh=Mock())
    return league
