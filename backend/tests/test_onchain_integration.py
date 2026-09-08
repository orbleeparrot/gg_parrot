"""Shared on-chain projection and bounded Prefect collection contracts."""
from datetime import datetime, timezone
import json
import pytest
from prefect.flows import load_flow_from_entrypoint
from app import whales
from app.agent_features.whale_activity import onchain_collector as collector
from app.agent_features.whale_activity import onchain_service as service
from app.workflows import onchain_holders as workflow
NOW = 1_800_000_000

def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    service.clear_cache()
    monkeypatch.setattr(service.time, 'time', lambda: NOW)
    monkeypatch.setattr(whales, 'fetch_holder_observation', lambda *_: pytest.fail('read must never collect'))

def observation():
    return {'coin': 'WETH', 'source': 'blockscout', 'status': 'ready', 'collection_status': 'ready',
        'last_success_ms': NOW*1000, 'observed_at': stamp(NOW), 'tracked_count': 49,
        'holders': [{'wallet': 'secret', 'balance': '1'}], 'claim_token': 'secret',
        'items': [{'id': 'onchain:WETH:2', 'occurred_at': stamp(NOW-10),
                   'previous_observed_at': stamp(NOW-610), 'increased_count': 2, 'decreased_count': 1,
                   'compared_count': 49, 'tracked_count': 49, 'wallet': 'secret'}]}

def test_session_start_filters_after_shared_cache_and_secrets_are_private(monkeypatch):
    calls = []
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda coin: calls.append(coin) or observation())
    old = service.get_activity('ETHUSDT', session_started_at=stamp(NOW-100))
    new = service.get_activity('ETHUSDT', session_started_at=stamp(NOW-5))
    assert old['coin'] == 'WETH' and len(old['items']) == 1
    assert new['items'] == [] and calls == ['WETH']
    assert 'secret' not in json.dumps(old)
    old['items'].clear()
    assert len(service.get_activity('ETHUSDT')['items']) == 1

def test_unsupported_macro_does_not_read_other_coins(monkeypatch):
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: pytest.fail('unsupported CHIP'))
    assert service.get_activity('CHIPUSDT') == {'status': 'unsupported', 'items': []}

def test_pending_and_db_outage_never_collect_or_expose_exception(monkeypatch):
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: None)
    assert service.get_activity('XRPUSDT')['status'] == 'pending'
    service.clear_cache()
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: (_ for _ in ()).throw(RuntimeError('secret')))
    result = service.get_activity('XRPUSDT')
    assert result['status'] == 'unavailable' and 'secret' not in json.dumps(result)

def test_expiry_rechecked_after_cache_and_failure_keeps_collection_metadata(monkeypatch):
    row = observation()
    row['last_success_ms'] = (NOW-1199)*1000
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: row)
    assert service.get_activity('ETHUSDT')['status'] == 'ready'
    monkeypatch.setattr(service.time, 'time', lambda: NOW+1.5)
    assert service.get_activity('ETHUSDT')['status'] == 'unavailable'
    service.clear_cache()
    row.update(last_success_ms=NOW*1000, collection_status='rate_limited')
    result = service.get_activity('ETHUSDT')
    assert result['status'] == 'unavailable' and result['stale'] is True
    assert result['last_success_ms'] == NOW*1000

def test_claim_denial_prevents_source_call(monkeypatch):
    monkeypatch.setattr(collector.repository, 'claim_collection', lambda _: None)
    assert collector.collect_coin('PEPE')['status'] == 'skipped'

def test_source_failure_records_safe_backoff(monkeypatch):
    monkeypatch.setattr(collector.repository, 'claim_collection', lambda _: 'lease')
    calls = []
    monkeypatch.setattr(collector.repository, 'record_failure', lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    def fetch(_):
        raise whales.OnchainSourceError('rate_limited', http_status=429, retry_after_seconds=333)
    result = collector.collect_coin('PEPE', fetcher=fetch)
    assert result['status'] == 'error' and result['http_status'] == 429
    assert calls == [(('PEPE', 'lease'), {'error_code': 'rate_limited', 'delay_seconds': 333})]

def test_success_reports_metadata_only_and_fences_stale_claim(monkeypatch):
    monkeypatch.setattr(collector.repository, 'claim_collection', lambda _: 'lease')
    monkeypatch.setattr(collector.repository, 'store_result', lambda *_: False)
    result = collector.collect_coin('PEPE', fetcher=lambda _: {'coin': 'PEPE', 'source': 'blockscout',
        'holders': [{'wallet': 'secret', 'balance': '99'}], 'tracked_count': 1, 'http_status': 200})
    assert result['status'] == 'superseded' and 'secret' not in json.dumps(result)

def test_workflow_collects_one_due_coin_and_surfaces_failure(monkeypatch):
    monkeypatch.setattr(workflow, '_schedule_lag_seconds', lambda: 0)
    monkeypatch.setattr(workflow, 'discover_coins_task', lambda: ['PEPE', 'WETH', 'XRP'])
    calls = []
    monkeypatch.setattr(workflow, 'collect_coin_task', lambda coin: calls.append(coin) or {'coin': coin, 'status': 'ready'})
    result = workflow.collect_onchain_holders_flow.fn()
    assert calls == ['PEPE'] and result['deferred_coin_count'] == 2 and result['ai_calls'] == 0
    monkeypatch.setattr(workflow, 'collect_coin_task', lambda _: {'status': 'error'})
    with pytest.raises(workflow.OnchainCollectionUnavailable):
        workflow.collect_onchain_holders_flow.fn()

def test_late_or_no_due_workflow_never_fetches(monkeypatch):
    monkeypatch.setattr(workflow, '_schedule_lag_seconds', lambda: 121)
    monkeypatch.setattr(workflow, 'discover_coins_task', lambda: pytest.fail('late'))
    assert workflow.collect_onchain_holders_flow.fn()['status'] == 'skipped_late'
    monkeypatch.setattr(workflow, '_schedule_lag_seconds', lambda: 0)
    monkeypatch.setattr(workflow, 'discover_coins_task', lambda: [])
    assert workflow.collect_onchain_holders_flow.fn()['checked_coin_count'] == 0

def test_deployment_is_independent_versioned_and_importable(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'onchain-test')
    deployment = workflow.create_deployment()
    assert deployment.name == 'shared-onchain-holders' and deployment.version == 'onchain-test'
    assert deployment.paused is False and deployment.concurrency_limit == 1
    assert deployment.schedules[0].schedule.interval.total_seconds() == 60
    assert load_flow_from_entrypoint(deployment.entrypoint) is workflow.collect_onchain_holders_flow


def test_binance_outage_does_not_hide_supported_onchain(monkeypatch):
    from app.agent_features.whale_activity import service as combined
    monkeypatch.setattr(combined, '_get_trade_activity', lambda *_: {'status': 'unavailable', 'items': []})
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: observation())
    result = whales.get_large_trade_activity('ETHUSDT', session_started_at=stamp(NOW-100))
    assert result['status'] == 'unavailable' and result['onchain']['items'][0]['id'] == 'onchain:WETH:2'


def test_public_route_is_readonly_cache_projection(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: None)
    with TestClient(app) as client:
        response = client.get('/api/whale-activity')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'public, max-age=2'
    result = response.json()
    assert [coin['coin'] for coin in result['coins']] == ['PEPE', 'WETH', 'XRP']
    assert all(coin['status'] == 'pending' and coin['items'] == [] for coin in result['coins'])


def test_real_owned_session_filters_events_before_macro_start(monkeypatch):
    import secrets
    from fastapi.testclient import TestClient
    from app.main import app
    from app import runner
    from app.agent_features.whale_activity import service as combined
    now = datetime.now(timezone.utc).timestamp()
    monkeypatch.setattr(service.time, 'time', lambda: now)
    row = observation()
    row.update(last_success_ms=int(now*1000), observed_at=stamp(now))
    row['items'][0].update(occurred_at=stamp(now-60), previous_observed_at=stamp(now-660))
    monkeypatch.setattr(service.repository, 'read_snapshot', lambda _: row)
    monkeypatch.setattr(combined, '_get_trade_activity', lambda *_: {'status': 'empty', 'items': []})
    with TestClient(app) as client:
        name = 'oc' + secrets.token_hex(5)
        account = client.post('/api/auth/signup', json={
            'email': name+'@example.invalid', 'username': name, 'password': 'password123'}).json()
        auth = {'Authorization': 'Bearer '+account['token']}
        key = client.get('/api/me/runner/key', headers=auth).json()['key']
        session_id = client.post('/api/runner/start', headers={'X-Runner-Key': key},
                                 json={'symbol': 'ETHUSDT'}).json()['session_id']
        response = client.get(f'/api/me/agents/sessions/{session_id}/whale-activity', headers=auth)
        assert response.status_code == 200
        assert response.json()['onchain']['coin'] == 'WETH'
        assert response.json()['onchain']['items'] == []
        monkeypatch.setattr(runner, 'get_owned_session', lambda *_args, **_kwargs: {'status': 'stopped'})
        monkeypatch.setattr(whales, 'get_large_trade_activity', lambda *_args, **_kwargs: pytest.fail('stopped'))
        assert client.get(f'/api/me/agents/sessions/{session_id}/whale-activity', headers=auth).json()['status'] == 'stopped'


def test_storage_validation_failure_uses_backoff(monkeypatch):
    monkeypatch.setattr(collector.repository, 'claim_collection', lambda _: 'lease')
    monkeypatch.setattr(collector.repository, 'store_result', lambda *_: (_ for _ in ()).throw(ValueError('badpayload')))
    calls = []
    monkeypatch.setattr(collector.repository, 'record_failure', lambda *args, **kwargs: calls.append((args, kwargs)) or True)
    result = collector.collect_coin('PEPE', fetcher=lambda _: {})
    assert result['status'] == 'error' and calls[0][1]['error_code'] == 'invalid_response'
