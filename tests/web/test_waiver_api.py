"""Run with the web optional dependencies (Flask and feedparser)."""
import pytest
from unittest.mock import patch
from tests.analyzer.waiver_fixture import papa
from fantasy_football_analyzer.web import create_app


@pytest.fixture
def client():
    league=papa()
    config={'team_id':14,'team_name':'Quiet, Piggy'}
    with patch('fantasy_football_analyzer.sources.consensus_projection_map',return_value={}), \
         patch('fantasy_football_analyzer.web.panel_api.get_league_or_redirect',return_value=(config,league,None)), \
         patch('fantasy_football_analyzer.web.routes.get_league_or_redirect',return_value=(config,league,None)), \
         patch('fantasy_football_analyzer.config.load_config',return_value={}), \
         patch('fantasy_football_analyzer.web.panel_api.fetch_news',return_value=[]):
        app=create_app();app.config['TESTING']=True
        yield app.test_client(),league


def test_get_api_identity_balance_metadata_and_no_cache(client):
    http,league=client
    result=http.get('/api/waivers?team=Quiet,%20Piggy')
    assert result.status_code==200
    data=result.get_json()
    assert data['team_id']==14 and data['team']=="Kaitlan Callin' Audibles"
    assert data['faab']['mine']['remaining']==103
    assert data['settings']['source']=='ESPN API' and not data['processing_time']['api_verified']
    assert result.headers['Cache-Control']=='no-store'
    assert league.refresh.call_count==1


def test_api_fails_without_emitting_any_bids(client):
    http,league=client
    for url in ('/api/waivers?team_id=999','/api/waivers?week=garbage'):
        response=http.get(url)
        assert response.status_code==422
        assert response.get_json()['recommendations']==[]
    league.teams[0].acquisition_budget_spent=None
    response=http.get('/api/waivers')
    assert response.status_code==422 and response.get_json()['claim_plan'] is None


def test_post_plan_caps_sum_and_revalidates_refresh(client):
    http,league=client
    claims=[{'player_id':500,'bid':50,'drop_player_id':40}, {'player_id':501,'bid':40,'drop_player_id':41}]
    response=http.post('/api/waiver-plan',json={'team_id':14,'max_spend':95,'claims':claims})
    assert response.status_code==200
    assert response.get_json()['claim_plan']['total_possible_spend']==90
    league.teams[0].acquisition_budget_spent=80
    assert http.post('/api/waiver-plan',json={'team_id':14,'max_spend':95,'claims':claims}).status_code==422
    assert league.refresh.call_count==2


@pytest.mark.parametrize('payload',[{}, {'claims':None,'max_spend':10}, {'claims':[],'max_spend':None},
                                    {'claims':[],'max_spend':True},{'claims':[],'max_spend':-1}])
def test_invalid_plan_inputs(client,payload):
    http,_=client
    assert http.post('/api/waiver-plan',json=payload).status_code in (400,422)


def test_page_renders_reason_budget_plan_and_safe_error(client):
    http,league=client
    response=http.get('/waivers')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'no reserve behind 2 required RB' in html and 'Week 13' in html
    assert 'All-success spend' in html and 'value="14"' in html
    assert 'Advisory only' in html and 'verify in ESPN' in html
    response=http.get('/waivers?max_spend=not-a-number')
    assert 'No bids or claims generated' in response.get_data(as_text=True)
    league.settings.acquisition_budget=0
    assert 'No bids or claims generated' in http.get('/waivers').get_data(as_text=True)
