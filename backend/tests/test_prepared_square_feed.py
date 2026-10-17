"""Square is selected before caps; only public JSON projections leave the DB."""
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import delete

from app import prepared_square_feed as square
from app.agent_features.position_news.articles import NewsArticle
from app.db import get_session


@pytest.fixture(autouse=True)
def empty_square():
    with get_session() as db:
        db.exec(delete(NewsArticle))
        db.commit()
    square._responses.clear()
    yield
    square._responses.clear()


def post(identifier='123', **extra):
    now = int(time.time() * 1000)
    return {'title':'비트코인 커뮤니티의 실제 제목', 'source':'Binance Square',
            'content_type':'community', 'community_post_id':identifier,
            'url':f'https://www.binance.com/en/square/post/{identifier}',
            'published':datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
            'categories':['BTC'], 'community_summary':'실제로 저장된 요약',
            'community_summary_status':'ready', 'community_body':'PRIVATE_BODY_MUST_NOT_LEAVE_DB',
            'author':'PRIVATE_AUTHOR', **extra}


def insert(item, *, scope='BTC', key='post123', last_seen=None, ready=True):
    now = int(time.time() * 1000)
    with get_session() as db:
        db.add(NewsArticle(asset_symbol=scope, article_id=key, revision=0, ready=ready,
                          item_json=json.dumps(item, ensure_ascii=False), first_seen_ms=now,
                          last_seen_ms=now if last_seen is None else last_seen))
        db.commit()


def test_1000_newer_normal_articles_cannot_hide_square():
    now = int(time.time() * 1000)
    insert(post(), last_seen=now)
    with get_session() as db:
        for n in range(1000):
            db.add(NewsArticle(asset_symbol='MARKET', article_id=f'news{n}', revision=0,
                ready=True, item_json=json.dumps({'content_type':'article', 'source':'CoinDesk',
                    'title':'비트코인 뉴스'}), first_seen_ms=now, last_seen_ms=now+n+1))
        db.commit()
    result = square.read_prepared_square()
    assert result['status'] == 'ready'
    assert len(result['items']) == 1
    assert result['coverage']['candidate_count'] == 1
    assert not result['coverage']['truncated']


def test_projection_does_not_select_private_body_or_author():
    insert(post())
    captured = []
    with get_session() as db:
        engine = db.get_bind()
    def record(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith('SELECT') and 'newsarticle' in statement:
            captured.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = square.read_prepared_square()
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert len(captured) == 1
    statement = captured[0]
    assert 'json_extract' in statement
    assert not statement.startswith('SELECT newsarticle.item_json')
    assert 'community_body' not in statement and 'author' not in statement
    assert 'PRIVATE' not in json.dumps(result)
    assert result['items'][0]['community_summary'] == '실제로 저장된 요약'


def test_cache_is_shared_copied_and_does_not_query_per_reader(monkeypatch):
    insert(post())
    calls = []
    original = square.get_session
    @contextmanager
    def counted():
        calls.append(1)
        with original() as db:
            yield db
    monkeypatch.setattr(square, 'get_session', counted)
    first = square.read_prepared_square()
    first['items'][0]['title'] = 'mutated'
    second = square.read_prepared_square()
    assert len(calls) == 1
    assert second['items'][0]['title'] == '비트코인 커뮤니티의 실제 제목'
    assert second['cached']


def test_duplicate_scopes_share_one_identity_and_merge_asset_aliases():
    insert(post(categories=['BTC']), scope='BTC', key='one')
    insert(post(categories=['ETH']), scope='ETH', key='two')
    result = square.read_prepared_square()
    assert len(result['items']) == 1
    assert result['items'][0]['asset_symbols'] == ['BTC', 'ETH']
    assert result['coverage']['deduplicated_count'] == 1
    assert len(result['items'][0]['id']) == 20


@pytest.mark.parametrize('changes', [
    {'source':'CoinDesk'}, {'content_type':'article'},
    {'url':'https://www.binance.com.evil/en/square/post/123'},
    {'url':'https://www.binance.com/en/square/post/999'},
    {'published':'not-a-date'}, {'published':'2020-01-01T00:00:00+00:00'},
    {'published':'2099-01-01T00:00:00+00:00'}, {'published':'2026-10-17'},
    {'community_post_id':'bad'}, {'title':''},
])
def test_invalid_or_expired_rows_are_not_public(changes):
    insert(post(**changes))
    assert square.read_prepared_square()['items'] == []


def test_unready_posts_and_invalid_json_do_not_break_prepared_read():
    insert(post(), ready=False)
    now = int(time.time() * 1000)
    with get_session() as db:
        db.add(NewsArticle(asset_symbol='BTC', article_id='invalid', revision=0, ready=True,
                          item_json='{invalid JSON', first_seen_ms=now, last_seen_ms=now))
        db.commit()
    assert square.read_prepared_square()['status'] == 'pending'


def test_candidate_limit_is_after_kind_filter_and_declared(monkeypatch):
    monkeypatch.setattr(square, 'CANDIDATE_LIMIT', 2)
    now = int(time.time() * 1000)
    for n in range(3):
        insert(post(str(n+1)), key=f'post{n}', last_seen=now+n)
    result = square.read_prepared_square()
    assert len(result['items']) == 2
    assert result['coverage']['candidate_count'] == 2
    assert result['coverage']['truncated']
    assert result['coverage']['candidate_limit'] == 2


def test_postgresql_query_projects_jsonb_fields_instead_of_selecting_body():
    statement = square._statement('postgresql')
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds':True}))
    assert 'CAST(newsarticle.item_json AS JSONB)' in sql
    assert "'content_type'" in sql and "'community'" in sql and "'Binance Square'" in sql
    assert 'community_body' not in sql and 'author' not in sql
    assert not sql.startswith('SELECT newsarticle.item_json')
    sqlite_sql = str(square._statement('sqlite').compile(
        dialect=sqlite.dialect(), compile_kwargs={'literal_binds':True}))
    assert 'json_valid' in sqlite_sql and 'json_extract' in sqlite_sql


def test_cached_items_are_rechecked_against_current_date_window(monkeypatch):
    now = time.time()
    published = datetime.fromtimestamp(now-7*86400+5,timezone.utc).isoformat()
    insert(post(published=published))
    assert square.read_prepared_square()['items']
    monkeypatch.setattr(square.time, 'time', lambda: now+6)
    assert square.read_prepared_square()['items'] == []
