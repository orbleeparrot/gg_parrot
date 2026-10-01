from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app import admin
from app.db import CollectorRun, CommunityPostSummary, MarketNewsSummary, NewsTitleTranslation, TickerNewsState, User, Visit


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_internal_visits_exclude_admin_qa_and_entire_internal_session(db, monkeypatch):
    now = int(datetime(2026, 9, 30, 3, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(admin, '_now', lambda: ('2026-09-30T03:00:00Z', now))
    monkeypatch.setattr(admin, '_today_kst', lambda: '2026-09-30')
    db.add(User(id=1, email='internal@example.test', username='internal', is_admin=True, password_hash='', created_at='2026-09-01T00:00:00Z'))
    db.add(User(id=2, email='reader@example.test', username='reader', password_hash='', created_at='2026-09-01T00:00:00Z'))
    db.commit()
    for name, user_id, path, channel, session_key in [
        ('reader', 2, '/news', 'direct', 'reader'),
        ('reader-other-browser', 2, '/news', 'direct', 'reader2'),
        ('anonymous-admin-before-login', None, '/', 'direct', 'admin-session'),
        ('admin', 1, '/news', 'direct', 'admin-session'),
        ('qa', None, '/news', 'internal', 'qa'),
        ('admin-path', None, '/admin', 'direct', 'admin-path'),
    ]:
        db.add(Visit(day_kst='2026-09-30', visitor_hash=name, session_key=session_key,
                     user_id=user_id, path=path, channel=channel, created_ms=now-1000))
    db.commit()
    admin.clear_cache()
    external = admin.users_report(db, days=7)
    included = admin.users_report(db, days=7, include_internal=True)
    assert external['kpis']['dau'] == 2
    assert external['kpis']['logged_in_accounts'] == 1
    assert external['kpis']['online_5m'] == 2
    assert included['kpis']['dau'] == 6
    assert admin.users_report(db, days=7)['kpis']['dau'] == 2  # cache scopes cannot mix
    assert external['traffic']['include_internal'] is False


def test_record_visit_marks_admin_paths_and_explicit_qa(db):
    for path, internal in [('/admin', False), ('/admin/settings', False), ('/news', True)]:
        row = admin.record_visit(db, path=path, is_internal=internal, user_id=None, secret='test')
        assert row.channel == 'internal'
    assert admin.record_visit(db, path='/administrator', user_id=None, secret='test').channel == 'direct'


def test_news_pipeline_separates_current_model_jobs_from_collection_and_old_provider(db, monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-6-luna')
    now = admin._day_start_ms('2026-09-30') + 12*3600000
    today = admin._day_start_ms('2026-09-30')
    for key, status, updated, claimed, model in [
        ('ready', 'ready', now-1000, 0, 'gpt-6-luna'),
        ('error', 'error', now-2000, 0, 'gpt-6-luna'),
        ('waiting', 'retryable', now-2000, 0, 'gpt-6-luna'),
        ('running', 'pending', now-2000, now-2000, 'gpt-6-luna'),
        ('stale-claim', 'pending', now-900000, now-900000, 'gpt-6-luna'),
        ('old', 'error', now-1000, 0, 'gemini-3.5-flash-lite'),
    ]:
        db.add(NewsTitleTranslation(title_hash=key, original_title=key, processing_status=status,
             updated_ms=updated, claimed_ms=claimed, prompt_version=f'coin-news-title-ko-v9:{model}'))
    db.add(CommunityPostSummary(summary_key='summary', post_id='123', body_hash='a'*64,
         prompt_version='community-body-summary-ko-v1:gpt-6-luna', processing_status='ready', updated_ms=now-500))
    db.add(CollectorRun(engine='public_news', day_kst='2026-09-30', status='ok',
         started_ms=now-20000, finished_ms=now-10000, items=3))
    db.add(CollectorRun(engine='article_enrichment', day_kst='2026-09-30', status='error',
         started_ms=now-900000, finished_ms=now-800000, failures=99))
    db.commit()
    stages = {row['key']: row for row in admin.news_pipeline_report(db, now_ms=now, today_start=today)}
    assert stages['collection']['last_success_ms'] == now-10000
    assert stages['collection']['failures_today'] == 0
    assert stages['translation']['last_success_ms'] == now-1000
    assert stages['translation']['completed_today'] == 1
    assert stages['translation']['processing'] == 1
    assert stages['translation']['pending'] == 2
    assert stages['translation']['errors'] == 1
    assert stages['translation']['status'] == 'processing'
    assert stages['summary']['last_success_ms'] == now-500
    assert stages['summary']['pending'] == 0
    assert stages['market_summary']['status'] == 'missing'
    assert stages['market_summary']['pending'] is None


def test_collection_claims_and_daily_market_summary_are_independent(db):
    from app.public_news import PublicNewsLease

    today = admin._day_start_ms('2026-09-30')
    now = today + 12 * 3600000
    db.add(TickerNewsState(asset_symbol='BTC', collection_claim_token='claim', collection_claimed_ms=now-1000))
    db.add(TickerNewsState(asset_symbol='ETH', collection_status='pending'))
    db.add(TickerNewsState(asset_symbol='SOL', collection_status='error'))
    db.add(PublicNewsLease(scope='MARKET', token='claim', lease_until_ms=now+1000))
    db.add(PublicNewsLease(scope='EXPIRED', token='claim', lease_until_ms=now-1000))
    previous = MarketNewsSummary(summary_key='market_news_summary:2026-09-29', overview='어제 요약', updated_ms=today-1000)
    db.add(previous)
    db.commit()
    stages = {row['key']: row for row in admin.news_pipeline_report(db, now_ms=now, today_start=today)}
    assert stages['collection']['processing'] == 2
    assert stages['collection']['pending'] == 1
    assert stages['collection']['errors'] == 1
    assert stages['market_summary']['status'] == 'missing'
    assert stages['market_summary']['last_success_ms'] == today-1000
    db.add(MarketNewsSummary(summary_key='market_news_summary:2026-09-30', overview='오늘 요약', updated_ms=now-1000))
    db.commit()
    stages = {row['key']: row for row in admin.news_pipeline_report(db, now_ms=now, today_start=today)}
    assert stages['market_summary']['status'] == 'ready'
    assert stages['market_summary']['completed_today'] == 1
    assert stages['market_summary']['last_success_ms'] == now-1000
