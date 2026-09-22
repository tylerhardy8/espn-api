import json
from pathlib import Path
from espn_api.football.settings import Settings
from espn_api.football.team import Team
from tests.analyzer.waiver_fixture import SLOTS


def settings_data():
    data=json.loads(Path('tests/football/unit/data/league_2018_data.json').read_text())
    return data['settings']


def test_sparse_out_of_order_and_unknown_slots():
    data=settings_data()
    data['rosterSettings']['lineupSlotCounts']={'23':2,'999':3,'2':2,'0':1,'20':6,'6':1}
    slots=Settings(data).position_slot_counts
    assert slots=={'RB/WR/TE':2,'999':3,'RB':2,'QB':1,'BE':6,'TE':1}


def test_exact_papa_slots():
    data=settings_data()
    data['rosterSettings']['lineupSlotCounts']={'23':2,'20':6,'21':1,'0':1,'2':2,'4':2,'6':1,
                                               '17':1,'10':1,'11':1,'14':1,'18':1,'19':1,'16':0}
    assert Settings(data).position_slot_counts==SLOTS


def test_missing_spending_counter_flag_survives_team_parse():
    data=json.loads(Path('tests/football/unit/data/league_2018_data.json').read_text())['teams'][0]
    data['transactionCounter']={}
    team=Team(data,{},[],2018)
    assert team.acquisition_budget_spent==0  # legacy non-waiver callers unchanged
    assert team.acquisition_budget_spent_verified is False
    data['transactionCounter']={'acquisitionBudgetSpent':0}
    assert Team(data,{},[],2018).acquisition_budget_spent_verified is True
