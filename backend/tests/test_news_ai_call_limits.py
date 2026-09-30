"""Real DB and validation path; only the paid provider and clock are fake."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import ai_runtime, community_summaries, community_summary_repository, news
from app import news_ai_budget as budget
from app.agent_features.position_news import articles, repository

TITLE = 'Bitcoin ETF inflows hit record'
KO = '비트코인 ETF 자금 유입 사상 최대'
BODY = 'Bitcoin ETF inflows increased.'


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}", connect_args={'check_same_thread': False, 'timeout': 30})
    SQLModel.metadata.create_all(engine)
    for module in (budget, repository, community_summary_repository):
        monkeypatch.setattr(module, 'get_session', lambda: Session(engine))
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-no-network')
    monkeypatch.setattr(news, '_title_translation_cache', {})
    monkeypatch.setattr(news, '_title_translation_retry_at', {})
    runtime = ai_runtime.AiCallRuntime()
    monkeypatch.setattr(news, 'get_ai_runtime', lambda: runtime)
    monkeypatch.setattr(community_summaries, 'get_ai_runtime', lambda: runtime)
    community_summaries.clear_memory_cache()
    clock = [1_800_000_000.0]
    monkeypatch.setattr(news.time, 'time', lambda: clock[0])
    yield engine, clock, runtime
    community_summaries.clear_memory_cache()
    engine.dispose()


def fake_provider(monkeypatch, module, *, kind, mode='invalid'):
    calls = []
    def create(**kwargs):
        batch = json.loads(kwargs['messages'][0]['content'])
        calls.append(batch)
        if mode == 'timeout':
            raise TimeoutError('simulated provider timeout')
        if mode == 'malformed':
            text = '{'
        else:
            field = 'title_ko' if kind == 'title' else 'summary_ko'
            value = KO if mode == 'valid' else 'The author predicts gains.'
            text = json.dumps({'items': [{'id': item['id'], field: value} for item in batch]})
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=text)])
    monkeypatch.setattr(module, 'get_ai_client', lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    return calls


@pytest.mark.parametrize('mode', ['invalid', 'malformed', 'timeout'])
def test_titles_stop_at_ten_provider_calls_across_days_and_cache_resets(isolated, monkeypatch, mode):
    _, clock, runtime = isolated
    calls = fake_provider(monkeypatch, news, kind='title', mode=mode)
    for cycle in range(400):
        clock[0] += 301
        # Model/prompt changes and process cache resets must not grant more calls.
        if cycle == 200:
            runtime.clear()
            news._title_translation_retry_at.clear()
            monkeypatch.setenv('OPENAI_MODEL', 'another-model')
        news._localize_coin_news_items([{'title': TITLE}])
    assert len(calls) == 10
    assert news._localize_coin_news_items([{'title': TITLE}]) == []


@pytest.mark.parametrize('mode', ['invalid', 'malformed', 'timeout'])
def test_summary_validation_and_transport_failures_stop_at_ten(isolated, monkeypatch, mode):
    _, clock, runtime = isolated
    calls = fake_provider(monkeypatch, community_summaries, kind='summary', mode=mode)
    item = {'content_type': 'community', 'community_post_id': '123456',
            'title': KO, 'community_body': BODY, 'community_body_status': 'ready'}
    for cycle in range(400):
        clock[0] += 301
        if cycle == 200:
            runtime.clear()
            community_summaries.clear_memory_cache()
            monkeypatch.setenv('OPENAI_MODEL', 'another-model')
        community_summaries.enrich_items([item], wait=True)
    assert len(calls) == 10
    assert community_summaries.enrich_items([item], wait=True)[0][0]['community_summary_status'] == 'failed'


def test_concurrent_workers_share_one_ten_call_budget(isolated):
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(lambda _: bool(budget.reserve('title', [TITLE])), range(40)))
    assert sum(accepted) == 10
    assert budget.reserve('title', [TITLE]) == set()
    assert budget.reserve('title', ['Other news']) == {'Other news'}


def test_last_title_slot_cannot_trigger_an_eleventh_correction(isolated, monkeypatch):
    for _ in range(9):
        budget.reserve('title', [TITLE])
    calls = fake_provider(monkeypatch, news, kind='title')
    assert news._localize_coin_news_items([{'title': TITLE}]) == []
    assert len(calls) == 1


def test_db_reservation_failure_prevents_provider_call(isolated, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(budget, 'get_session', unavailable)
    calls = fake_provider(monkeypatch, news, kind='title')
    assert news._localize_coin_news_items([{'title': TITLE}]) == []
    assert calls == []


def test_success_cache_is_reused_and_new_titles_are_not_globally_capped(isolated, monkeypatch):
    calls = fake_provider(monkeypatch, news, kind='title', mode='valid')
    for _ in range(20):
        assert news._localize_coin_news_items([{'title': TITLE}])[0]['title'] == KO
    assert len(calls) == 1
    for index in range(25):
        assert budget.reserve('title', [f'Unrelated title {index}'])


def test_stale_ready_flag_cannot_expose_failed_title_or_misalign_analysis(isolated):
    engine, _, _ = isolated
    with Session(engine) as db:
        articles.upsert_articles('BUDGETTEST', [
            {'title': KO, 'url': 'https://example.test/ready'},
            {'title': '임시 한국어 제목', 'url': 'https://example.test/bad'}], db=db)
        # Simulate a legacy row marked ready before translation validation.
        from sqlmodel import select
        rows = db.exec(select(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == 'BUDGETTEST')).all()
        for row in rows:
            item = json.loads(row.item_json)
            if item['url'].endswith('/bad'):
                item['title'] = TITLE
                row.item_json = json.dumps(item)
                db.add(row)
        db.commit()
        payload = articles.read_article_feed('BUDGETTEST', db=db)
        assert [item['title'] for item in payload['items']] == [KO]
        assert len(payload['analysis']['items']) == 1


def test_exhausted_item_does_not_block_other_titles_in_same_batch(isolated, monkeypatch):
    for _ in range(10):
        budget.reserve('title', [TITLE])
    calls = fake_provider(monkeypatch, news, kind='title', mode='valid')
    other = 'Bitcoin ETF inflows surge'
    result = news._localize_coin_news_items([{'title': TITLE}, {'title': other}])
    assert len(calls) == 1
    assert [row['title'] for row in calls[0]] == [other]
    assert [item['original_title'] for item in result] == [other]


def test_busy_runtime_does_not_consume_provider_budget(isolated, monkeypatch):
    def busy(*args, **kwargs):
        raise ai_runtime.AiBusyError('busy')
    monkeypatch.setattr(news, 'get_ai_runtime', lambda: SimpleNamespace(call=busy))
    calls = fake_provider(monkeypatch, news, kind='title')
    news._localize_coin_news_items([{'title': TITLE}])
    assert calls == []
    assert sum(bool(budget.reserve('title', [TITLE])) for _ in range(20)) == 10


def test_failed_summary_without_body_stays_failed_and_hidden(isolated):
    item = {'title': KO, 'content_type': 'community', 'community_post_id': '123456',
            'community_summary_status': 'failed'}
    enriched, metadata = community_summaries.enrich_items([item], schedule=False)
    assert enriched[0]['community_summary_status'] == 'failed'
    assert metadata['failed_count'] == 1
    assert not articles._ready(enriched[0])


def test_success_on_tenth_call_remains_visible(isolated, monkeypatch):
    for _ in range(9):
        budget.reserve('title', [TITLE])
    calls = fake_provider(monkeypatch, news, kind='title', mode='valid')
    result = news._localize_coin_news_items([{'title': TITLE}])
    assert result[0]['title'] == KO
    assert len(calls) == 1
    assert news._localize_coin_news_items([{'title': TITLE}]) == result
    assert len(calls) == 1
