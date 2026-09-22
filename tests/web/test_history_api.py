from unittest.mock import patch
import pytest
from fantasy_football_analyzer.web import create_app
from tests.analyzer.test_history_integrity import team,league,add_game


@pytest.fixture
def client():
    a,b=team(14,'Current name'),team(2,'Opponent','b',2)
    add_game(a,b,100,90)
    seed=league(2026,[a,b],False,previous=[2025,2024])
    old_a,old_b=team(14,'Old name'),team(2,'Opponent','b',2)
    add_game(old_a,old_b,120,80)
    past=league(2025,[old_a,old_b])
    config={'league_id':202314,'year':2026,'team_id':14,'team_name':'Current name'}
    def load(_,year,*args,**kwargs):
        if year==2025:return past
        raise RuntimeError('access failed')
    with patch('fantasy_football_analyzer.web.routes.get_league_or_redirect',return_value=(config,seed,None)), \
         patch('fantasy_football_analyzer.league_connector.connect_league',side_effect=load), \
         patch('fantasy_football_analyzer.config.load_config',return_value={}):
        app=create_app();app.config['TESTING']=True
        yield app.test_client()


def test_api_default_loads_all_exposes_missing_season_and_preserves_rename(client):
    response=client.get('/api/history')
    assert response.status_code==200
    d=response.get_json()
    assert d['coverage']['loaded_years']==[2025,2026]
    assert d['coverage']['requested_years']==[2024,2025,2026]
    assert '2024' in d['coverage']['failed_years']
    assert not d['coverage']['all_requested_loaded']
    assert d['teams']['Current name']['num_seasons']==2
    assert d['teams']['Current name']['championships']==1
    assert 'not a complete' in d['coverage']['transaction_detail']
    assert response.headers['Cache-Control']=='no-store'


def test_history_page_shows_coverage_and_pending_not_fake_titles(client):
    response=client.get('/history')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'href="/history?' in html
    assert '2024 missing' in html and '2025, 2026' in html
    assert 'final results unconfirmed' in html and 'History coverage' in html
    assert 'not a complete historical transaction ledger' in html


def test_explicit_year_is_respected(client):
    data=client.get('/api/history?years=2025').get_json()
    assert data['coverage']['loaded_years']==[2025]
    assert data['coverage']['failed_years']=={}


def test_failed_only_year_returns_coverage_not_successful_empty_history(client):
    response=client.get('/api/history?years=2024')
    assert response.status_code==503 and '2024' in response.get_json()['coverage']['failed_years']


def test_api_bad_years_is_json_error(client):
    response=client.get("/api/history?years=bad")
    assert response.status_code==422 and response.is_json
