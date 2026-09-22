import json
from pathlib import Path
from espn_api.football.team import Team


def test_team_retains_actual_period_ids_across_schedule_gaps():
    data=json.loads(Path('tests/football/unit/data/league_2018_data.json').read_text())
    raw=data['teams'][0]
    tid=raw['id']
    schedule=[{'matchupPeriodId':p,'winner':'HOME','home':{'teamId':tid,'totalPoints':v},
               'away':{'teamId':999,'totalPoints':0}} for p,v in ((1,0),(3,-2),(15,200))]
    team=Team(raw,{},schedule,2018)
    assert team.matchup_periods==[1,3,15] and team.scores==[0,-2,200]
