import asyncio
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import select
from app import news_sentiment as mod
from app import news_sentiment_model as model
from app.news_sentiment_test import require_test_admin
from app.auth import current_user
from app.db import User, get_session
from app.main import app
from app.agent_features.position_news.articles import NewsArticle, NewsArticleFeed, upsert_articles, article_id

client = TestClient(app)
api_app = app
while not hasattr(api_app, 'dependency_overrides'):
    api_app = api_app.app


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv('SEMIF_API_BASE', 'http://model.invalid:13000')
    monkeypatch.setenv('SEMIF_NEWS_ENABLED', 'true')
    monkeypatch.delenv('SEMIF_API_KEY', raising=False)
    monkeypatch.setattr(mod, '_cache', None)
    monkeypatch.setattr(mod, '_cache_until', 0)
    with get_session() as db:
        for table in (mod.NewsSentimentJob, mod.NewsSentimentState, NewsArticle, NewsArticleFeed):
            db.exec(delete(table))
        db.commit()
    yield
    api_app.dependency_overrides.pop(require_test_admin, None)
    api_app.dependency_overrides.pop(current_user, None)


def admin():
    api_app.dependency_overrides[require_test_admin] = lambda: User(id=1, is_admin=True)


def seed(n=1, scope='BTC', now_ms=None):
    item = {'title': f'Bitcoin ETF inflows {n}', 'source': 'Fixture', 'excerpt': '<p>Net inflows increased.</p>', 'url': 'https://example.com/news'}
    upsert_articles(scope, [item], now_ms=now_ms)
    return article_id(item)


def outcome(ms=1250):
    return {'verdict': 'bullish', 'reason': 'ETF 자금 유입이 늘었습니다.', 'elapsed_ms': ms, 'model_ms': 1200, 'load_ms': 20, 'model': model.MODEL}


def state():
    with get_session() as db:
        return db.get(mod.NewsSentimentState, 'semif')


def test_routes_require_admin_and_old_trigger_is_removed():
    assert client.get('/api/admin/news-test/results').status_code == 401
    api_app.dependency_overrides[current_user] = lambda: User(id=1, is_admin=False)
    assert client.get('/api/admin/news-test/results').status_code == 403
    admin()
    assert client.post('/api/admin/news-test/analyze', json={}).status_code in (404, 405)


def test_collection_enqueues_all_articles_across_feeds_and_deduplicates():
    key = seed(1, 'MARKET')
    seed(1, 'BTC')
    seed(1, 'MARKET')  # An unchanged source refresh still must not duplicate.
    seed(2, 'ETH')
    with get_session() as db:
        jobs = db.exec(select(mod.NewsSentimentJob)).all()
        assert len(jobs) == 2
        assert db.get(mod.NewsSentimentJob, key).status == 'pending'
        assert json.loads(db.get(mod.NewsSentimentJob, key).article_json)['excerpt'] == 'Net inflows increased.'
    assert state().pending == 2


def test_collection_and_enqueue_are_atomic(monkeypatch):
    original = mod.enqueue
    def fail_after_queue(*args):
        original(*args)
        raise RuntimeError('transaction must roll back')
    monkeypatch.setattr(mod, 'enqueue', fail_after_queue)
    with pytest.raises(RuntimeError):
        seed()
    with get_session() as db:
        assert not db.exec(select(NewsArticle)).all()
        assert not db.exec(select(mod.NewsSentimentJob)).all()
        assert db.get(mod.NewsSentimentState, 'semif') is None


def test_automatic_consumer_finishes_without_a_browser_and_preserves_results(monkeypatch):
    first, second = seed(1, now_ms=1), seed(2, now_ms=2)
    calls = []
    async def infer(article):
        calls.append(article['id'])
        return outcome(1250 if article['id'] == first else 2500)
    monkeypatch.setattr(mod, '_infer', infer)
    assert asyncio.run(mod.process_one())
    assert asyncio.run(mod.process_one())
    assert not asyncio.run(mod.process_one())
    assert calls == [first, second]
    assert state().completed == 2 and state().pending == 0
    snapshot = mod._read_snapshot()
    assert snapshot['stats']['average_ms'] == 1875
    assert len(snapshot['history']) == 1
    # Opening/reloading the page reads the stored result and does not infer again.
    admin()
    for _ in range(2):
        response = client.get('/api/admin/news-test/results')
        assert response.status_code == 200
        assert 'no-store' in response.headers['cache-control']
    assert calls == [first, second]


def test_global_lease_prevents_parallel_model_calls():
    seed(1)
    seed(2)
    first = mod.claim(now_ms=1000)
    assert first
    assert mod.claim(now_ms=1001) is None
    assert mod.finish(*first[:2], result=outcome(), now_ms=1100)
    second = mod.claim(now_ms=1101)
    assert second and second[0] != first[0]


def test_crash_recovery_fences_late_result():
    seed()
    old = mod.claim(now_ms=1000)
    assert mod.claim(now_ms=120999) is None
    new = mod.claim(now_ms=121001)
    assert new[0] == old[0] and new[1] != old[1]
    assert not mod.finish(*old[:2], result=outcome(), now_ms=121002)
    assert mod.finish(*new[:2], result=outcome(), now_ms=121003)
    assert state().completed == 1


def test_provider_outage_retains_news_and_backs_off_entire_queue():
    first = seed(1, now_ms=1)
    seed(2, now_ms=2)
    pick = mod.claim(now_ms=1000)
    assert pick[0] == first
    mod.finish(*pick[:2], error='model unavailable', provider_error=True, now_ms=1100)
    assert mod.claim(now_ms=2000) is None
    assert state().pending == 2 and state().completed == 0
    recovered = mod.claim(now_ms=22100)
    assert recovered[0] == first
    mod.finish(*recovered[:2], result=outcome(), now_ms=22101)
    assert state().last_error == ''


def test_bad_response_retries_then_marks_failed_without_stopping_other_news():
    seed()
    for now in (1000, 30000, 90000):
        pick = mod.claim(now_ms=now)
        assert pick
        assert mod.finish(*pick[:2], error='invalid JSON', now_ms=now+1)
    assert state().failed == 1 and state().pending == 0 and state().completed == 0
    current = mod._read_snapshot()['current']
    assert current['status'] == 'failed' and current['result'] is None
    seed(2)
    assert mod.claim(now_ms=100000)


def test_runtime_starts_without_page_requests_and_shutdown_preserves_job(monkeypatch):
    seed()
    calls = []
    async def infer(article):
        calls.append(article)
        await asyncio.sleep(60)
    monkeypatch.setattr(mod, '_infer', infer)
    async def run():
        mod.start()
        try:
            for _ in range(100):
                if calls: break
                await asyncio.sleep(.01)
            assert len(calls) == 1
        finally:
            await mod.stop()
    asyncio.run(run())
    assert state().pending == 1 and state().completed == 0
    with get_session() as db:
        assert db.exec(select(mod.NewsSentimentJob)).one().status == 'processing'


def test_unconfigured_worker_does_not_contact_any_model(monkeypatch):
    monkeypatch.delenv('SEMIF_API_BASE')
    monkeypatch.delenv('OLLAMA_BASE_URL', raising=False)
    assert not mod.enabled()
    seed()
    mod.start()
    assert mod._task is None
    assert mod._read_snapshot()['enabled'] is False


def test_read_snapshot_is_shared_and_bounded(monkeypatch):
    calls = []
    def read():
        calls.append(1)
        return {'fixture': True}
    monkeypatch.setattr(mod, '_read_snapshot', read)
    assert mod.snapshot() == mod.snapshot() == {'fixture': True}
    assert len(calls) == 1


def test_unchanged_poll_only_returns_version_and_new_jobs_invalidate_snapshot(monkeypatch):
    admin()
    seed()
    first = client.get('/api/admin/news-test/results').json()
    same = client.get('/api/admin/news-test/results', params={'after': first['version']}).json()
    assert same == {'unchanged': True, 'version': first['version']}
    seed(2)
    monkeypatch.setattr(mod, '_cache_until', 0)
    next_result = client.get('/api/admin/news-test/results', params={'after': first['version']}).json()
    assert next_result['stats']['pending'] == 2
    assert next_result['version'] != first['version']


def test_concurrent_consumers_claim_only_one_job():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    seed(1)
    seed(2)
    barrier = Barrier(2)
    def consume():
        barrier.wait()
        return mod.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        picks = list(pool.map(lambda _: consume(), range(2)))
    assert sum(pick is not None for pick in picks) == 1


def test_worker_retains_article_when_transport_fails(monkeypatch):
    seed()
    async def disconnected(article):
        raise httpx.ConnectError('fixture')
    monkeypatch.setattr(mod, '_infer', disconnected)
    assert asyncio.run(mod.process_one())
    assert state().pending == 1 and state().completed == 0
    assert state().last_error
    assert not asyncio.run(mod.process_one())


def test_article_rejection_does_not_pause_the_entire_queue(monkeypatch):
    seed(1, now_ms=1)
    second = seed(2, now_ms=2)
    async def reject(article):
        response = httpx.Response(422, request=httpx.Request('POST', 'http://model.invalid/v1/decide'))
        response.raise_for_status()
    monkeypatch.setattr(mod, '_infer', reject)
    assert asyncio.run(mod.process_one())
    assert state().next_run_ms == 0
    assert mod.claim()[0] == second


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(model.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_model_request_and_real_duration_units(monkeypatch):
    monkeypatch.setenv('SEMIF_API_KEY', 'fixture-only')
    def handler(request):
        prompt = json.loads(request.content)
        assert request.url == 'http://model.invalid:13000/v1/decide'
        assert request.headers['Authorization'] == 'Bearer fixture-only'
        assert prompt['id'] == 'ggparrot-news'
        assert {row['id'] for row in prompt['options']} == {'bullish', 'bearish', 'neutral'}
        assert prompt['state']['title'] == 'ETF 유입'
        return httpx.Response(200, json={'id': prompt['id'], 'choice': 'bullish', 'probabilities': {'bullish': .8, 'bearish': .1, 'neutral': .1}, 'seconds': .1131})
    transport(monkeypatch, handler)
    result = asyncio.run(model._infer({'scope': 'BTC', 'title': 'ETF 유입', 'original_title': '', 'excerpt': ''}))
    assert result['model_ms'] == 113.1
    assert result['elapsed_ms'] >= 0
    assert result['probabilities']['bullish'] == .8
    assert 'reason' not in result  # This model does not generate natural-language rationales.
    assert 'fixture-only' not in json.dumps(result)


@pytest.mark.parametrize('payload', [
    {'id': 'ggparrot-news', 'choice': 'unknown'},
    {'id': 'another-article', 'choice': 'bullish'},
    {'id': 'ggparrot-news', 'choice': 'bullish', 'probabilities': {'bullish': 1}},
    {'id': 'ggparrot-news', 'choice': 'bullish', 'probabilities': {'bullish': -1, 'bearish': 1, 'neutral': 1}},
    {'id': 'ggparrot-news', 'choice': 'bullish', 'probabilities': {'bullish': .8, 'bearish': .1, 'neutral': .1}, 'seconds': -1},
])
def test_invalid_model_response_never_becomes_verdict(monkeypatch, payload):
    transport(monkeypatch, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(ValueError):
        asyncio.run(model._infer({'scope': 'BTC', 'title': 'test', 'original_title': '', 'excerpt': ''}))
