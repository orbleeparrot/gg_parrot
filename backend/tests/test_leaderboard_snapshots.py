"""DB-only delivery, atomic publication, permissions and fenced background work."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app import challenge, db as database, leaderboard as lb, leaderboard_runtime as runtime
from app import leaderboard_snapshot as snapshots
from app.db import LeaderboardCarryover, LeaderboardEntry, MacroUnlock, PaperSession
from app.main import app


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'board.db'}")
    SQLModel.metadata.create_all(engine)
    for module in (database, lb, challenge, snapshots):
        monkeypatch.setattr(module, 'get_session', lambda: Session(engine))
    monkeypatch.setattr(lb, '_carryover_done_date', None)
    monkeypatch.setattr(lb, '_crown_cache', {})
    monkeypatch.setattr(lb, '_carryover_lock', asyncio.Lock())
    monkeypatch.setattr(challenge, '_lock', asyncio.Lock())
    yield engine
    engine.dispose()


def entry(engine, eid, *, owner=None, ret=1, yesterday=False):
    with Session(engine) as db:
        db.add(PaperSession(id=eid, macro_id=f'm-{eid}', symbol='BTCUSDT', status='stopped',
            started_at='2026-09-14T00:00:00Z', current_return=ret, current_equity=1000))
        db.add(LeaderboardEntry(id=eid, user_id=f'anon-{eid}', owner_user_id=owner,
            nickname='author', username='author', symbol='BTCUSDT', password_hash='never-share',
            macro_json=json.dumps({'secret_strategy': eid}), human_summary=f'private-{eid}',
            paper_session_id=eid, created_at=f'2026-09-14T00:{eid % 60:02}:00Z',
            created_ms=lb.today_start_ms() + eid - (lb.DAY_MS if yesterday else 0)))
        db.commit()


def publish():
    token = snapshots.claim_refresh()
    assert token
    version = snapshots.publish_snapshot(token, lb._today_kst(), lb.compute_entries()['items'])
    snapshots.release_refresh(token, delay_seconds=0)
    return version


def test_cold_get_only_reads_and_never_starts_generation(isolated, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('GET performed background work')
    for module, name in ((lb, 'ensure_today_carryover'), (challenge, 'ensure_today'),
                         (lb, 'compute_entries'), (lb, '_vote_tallies'), (lb, '_crown_owner_ids'),
                         (lb.paper_mod, 'get_statuses')):
        monkeypatch.setattr(module, name, forbidden)
    queries = []
    event.listen(isolated, 'before_cursor_execute', lambda _c, _u, sql, *_a: queries.append(sql))
    client = TestClient(app)
    response = client.get('/api/leaderboard')
    assert response.status_code == 200
    assert response.json()['items'] == [] and response.json()['preparing'] is True
    assert client.get('/api/challenge/today').json()['active'] is False
    assert queries and all(q.lstrip().upper().startswith('SELECT') for q in queries)


def test_snapshot_rank_and_current_permissions_hide_private_macro(isolated, monkeypatch):
    entry(isolated, 1, owner=10, ret=2)
    entry(isolated, 2, ret=7)
    version = publish()
    with Session(isolated) as db:
        payloads = db.exec(select(snapshots.LeaderboardSnapshotItem)).all()
        assert all('never-share' not in p.public_json and 'secret_strategy' not in p.public_json for p in payloads)
    monkeypatch.setattr(lb, 'compute_entries', lambda: pytest.fail('read computed ranking'))
    public = lb.list_entries(viewer_id='anon-1')['items']
    assert [p['id'] for p in public] == [2, 1]
    assert public[1]['macro'] is None and public[1]['human_summary'] == ''
    assert public[1]['is_mine'] is True and public[1]['locked'] is True
    own = lb.list_entries(viewer_user_id=10)['items'][1]
    assert own['macro'] == {'secret_strategy': 1} and own['human_summary'] == 'private-1'
    with Session(isolated) as db:
        db.add(MacroUnlock(user_id=20, entry_id=1, price=100, created_at='now'))
        db.commit()
    bought = lb.list_entries(viewer_user_id=20)['items'][1]
    assert bought['unlocked'] is True and bought['macro'] == own['macro']
    assert lb.list_entries()['snapshot_id'] == version


def test_votes_stay_current_without_rebuilding_snapshot(isolated):
    entry(isolated, 1)
    version = publish()
    voted = lb.vote(1, 'viewer', 1)
    view = lb.list_entries(viewer_id='viewer')['items'][0]
    assert view['likes'] == voted['likes'] == 1 and view['my_vote'] == 1
    assert lb.list_entries()['snapshot_id'] == version
    lb.vote(1, 'viewer', -1)
    view = lb.list_entries(viewer_id='viewer')['items'][0]
    assert (view['likes'], view['dislikes'], view['my_vote']) == (0, 1, -1)
    lb.vote(1, 'viewer', -1)
    view = lb.list_entries(viewer_id='viewer')['items'][0]
    assert (view['likes'], view['dislikes'], view['my_vote']) == (0, 0, 0)


def test_bounded_pagination_pins_generation_and_hides_deleted_entries(isolated):
    for eid in range(1, 13):
        entry(isolated, eid, ret=eid)
    version = publish()
    pages = [lb.list_entries(page=p, page_size=5, snapshot_id=version) for p in (1, 2, 3)]
    assert [len(p['items']) for p in pages] == [5, 5, 2]
    assert [v['id'] for p in pages for v in p['items']] == list(range(12, 0, -1))
    assert [p['has_more'] for p in pages] == [True, True, False]
    assert lb.list_entries(page_size=500)['page_size'] == 100
    lb.delete_entry(12)
    assert all(v['id'] != 12 for v in lb.list_entries()['items'])
    # A deletion on an earlier page must not shift later page boundaries.
    assert [v['id'] for v in lb.list_entries(page=2, page_size=5, snapshot_id=version)['items']] == [7, 6, 5, 4, 3]


def test_smaller_generation_clamps_page_after_deletion_and_day_reset(isolated, monkeypatch):
    for eid in range(1, 13):
        entry(isolated, eid, ret=eid)
    previous = publish()
    assert lb.list_entries(page=3, page_size=5)['page'] == 3
    for eid in range(4, 13):
        lb.delete_entry(eid)
    publish()
    current = lb.list_entries(page=3, page_size=5)
    assert current['snapshot_id'] != previous
    assert current['page'] == 1 and current['total'] == 3 and not current['has_more']
    assert [item['id'] for item in current['items']] == [3, 2, 1]
    # A completed empty next day also has a valid first page, with no GET writes.
    monkeypatch.setattr(lb, '_today_kst', lambda: '2099-01-01')
    token = snapshots.claim_refresh()
    snapshots.publish_snapshot(token, lb._today_kst(), [])
    empty = lb.list_entries(page=3, page_size=5)
    assert empty['page'] == 1 and empty['total'] == 0 and empty['items'] == []
    assert empty['date_kst'] == '2099-01-01' and not empty['preparing']


def test_entry_location_returns_bounded_target_page_without_private_leaks(isolated):
    for eid in range(1, 121):
        entry(isolated, eid, owner=10, ret=eid)
    version = publish()
    queries = []
    capture = lambda _c, _u, sql, *_a: queries.append(sql)
    event.listen(isolated, 'before_cursor_execute', capture)
    try:
        last = lb.list_entries(viewer_id='viewer', viewer_user_id=20, page_size=7,
                               snapshot_id=version, entry_id=1)
        assert last['entry_location'] == {'entry_id': 1, 'rank': 120, 'page': 18}
        assert last['page'] == 18 and [item['id'] for item in last['items']] == [1]
        assert last['items'][0]['macro'] is None and last['items'][0]['human_summary'] == ''
        assert len(queries) == 5
        queries.clear()
        first = lb.list_entries(viewer_id='viewer', viewer_user_id=20, page=18,
                                page_size=7, snapshot_id=version, entry_id=120)
        assert first['entry_location'] == {'entry_id': 120, 'rank': 1, 'page': 1}
        # The public generation metadata is reused; current entry access, votes
        # and location still run their four bounded queries.
        assert first['page'] == 1 and len(first['items']) == 7 and len(queries) == 4
        assert all(sql.lstrip().upper().startswith('SELECT') for sql in queries)
        assert not any('papersession' in sql.lower() for sql in queries)
        locator = next(sql for sql in queries if 'LIMIT' in sql and 'entry_id = ?' in sql)
        assert 'version_id = ?' in locator  # composite primary-key lookup
    finally:
        event.remove(isolated, 'before_cursor_execute', capture)
    lb.delete_entry(1)
    missing = lb.list_entries(entry_id=1, snapshot_id=version, page_size=7)
    assert missing['entry_location'] is None and missing['page'] == 1
    assert all(item['id'] != 1 for item in missing['items'])


def test_entry_location_http_query_loads_the_matching_page(isolated):
    for eid in range(1, 5):
        entry(isolated, eid, ret=eid)
    publish()
    response = TestClient(app).get('/api/leaderboard?entry_id=1&page_size=2')
    assert response.status_code == 200
    data = response.json()
    assert data['entry_location'] == {'entry_id': 1, 'rank': 4, 'page': 2}
    assert data['page'] == 2 and [item['id'] for item in data['items']] == [2, 1]


def test_failed_publish_keeps_previous_board_and_expired_writer_is_fenced(isolated):
    entry(isolated, 1)
    previous = publish()
    token = snapshots.claim_refresh()
    with pytest.raises(TypeError):
        snapshots.publish_snapshot(token, lb._today_kst(), [{'id': 1, 'bad': object()}])
    assert lb.list_entries()['snapshot_id'] == previous
    later = snapshots.now_ms() + snapshots.LEASE_MS + 1
    replacement = snapshots.claim_refresh(now=later)
    assert replacement and replacement != token
    with pytest.raises(RuntimeError, match='superseded'):
        snapshots.publish_snapshot(token, lb._today_kst(), [], now=later)
    assert lb.list_entries()['snapshot_id'] == previous
    assert snapshots.claim_refresh(now=later) is None


def test_previous_day_snapshot_is_usable_until_today_finishes(isolated, monkeypatch):
    entry(isolated, 1)
    version = publish()
    monkeypatch.setattr(lb, '_today_kst', lambda: '2099-01-01')
    result = lb.list_entries()
    assert result['snapshot_id'] == version and result['items']
    assert result['stale'] is True and result['preparing'] is True


def test_carryover_failure_rolls_back_marker_and_entries_then_retries(isolated, monkeypatch):
    entry(isolated, 1, ret=9, yesterday=True)
    original = lb._carry_previous_day_top
    def fail(db=None):
        original(db=db)
        raise RuntimeError('failed before commit')
    monkeypatch.setattr(lb, '_carry_previous_day_top', fail)
    with pytest.raises(RuntimeError):
        lb._perform_carryover(lb._today_kst())
    with Session(isolated) as db:
        assert db.exec(select(LeaderboardCarryover)).all() == []
        assert db.get(LeaderboardEntry, 1).created_ms < lb.today_start_ms()
    monkeypatch.setattr(lb, '_carry_previous_day_top', original)
    assert lb._perform_carryover(lb._today_kst()) == 1
    assert lb._perform_carryover(lb._today_kst()) == 0
    with Session(isolated) as db:
        assert db.get(LeaderboardEntry, 1).streak_days == 2


def test_challenge_slot_and_entry_commit_together_on_retry(isolated):
    from app.ai_challenge import generate_macros
    from app.engine import Macro
    date = challenge._today_kst()
    token = challenge._claim_daily_challenge(date)['claim_token']
    macro = Macro(**generate_macros('BTCUSDT', 1)[0])
    challenge._store_bot(date, 1, token, macro, None)
    challenge._store_bot(date, 1, token, macro, None)
    with Session(isolated) as db:
        assert len(db.exec(select(LeaderboardEntry)).all()) == 1
        assert len(db.exec(select(snapshots.LeaderboardChallengeBot)).all()) == 1
    challenge._fail_daily_challenge(date, claim_token=token, error=RuntimeError('retry'))
    replacement = challenge._claim_daily_challenge(date)['claim_token']
    with pytest.raises(RuntimeError, match='superseded'):
        challenge._store_bot(date, 2, token, macro, None)
    challenge._store_bot(date, 2, replacement, macro, None)
    with Session(isolated) as db:
        assert len(db.exec(select(LeaderboardEntry)).all()) == 2


def test_background_refresh_prepares_and_publishes(isolated, monkeypatch):
    events = []
    entry(isolated, 1, ret=3)
    async def carry(): events.append('carry')
    async def ai(): events.append('challenge')
    monkeypatch.setattr(lb, 'ensure_today_carryover', carry)
    monkeypatch.setattr(challenge, 'ensure_today', ai)
    assert asyncio.run(runtime.refresh_once()) is True
    assert events == ['carry', 'challenge']
    assert lb.list_entries()['items'][0]['return_pct'] == 3
    assert asyncio.run(runtime.refresh_once()) is False


def test_ready_reader_query_count_is_bounded_and_uses_no_write(isolated):
    for eid in range(1, 121):
        entry(isolated, eid, owner=10, ret=eid)
    publish()
    queries = []
    capture = lambda _c, _u, sql, *_a: queries.append(sql)
    event.listen(isolated, 'before_cursor_execute', capture)
    result = lb.list_entries(viewer_id='viewer', viewer_user_id=20, page_size=7)
    event.remove(isolated, 'before_cursor_execute', capture)
    assert len(result['items']) == 7 and result['total'] == 120
    assert len(queries) == 4
    assert all(q.lstrip().upper().startswith('SELECT') for q in queries)
    assert any('LIMIT' in q for q in queries)
    assert not any('papersession' in q.lower() for q in queries)


def test_competing_workers_have_only_one_valid_lease(isolated):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return snapshots.claim_refresh()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(token is not None for token in claims) == 1


def test_concurrent_same_user_vote_toggles_do_not_duplicate_votes(isolated):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.db import LeaderboardVote
    entry(isolated, 1)
    publish()
    barrier = threading.Barrier(2)
    def vote():
        barrier.wait(timeout=5)
        return lb.vote(1, 'same-viewer', 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: vote(), range(2)))
    assert sorted(r['my_vote'] for r in results) == [0, 1]
    with Session(isolated) as db:
        assert db.exec(select(LeaderboardVote)).all() == []
    assert lb.list_entries()['items'][0]['likes'] == 0


def test_partial_challenge_retries_missing_slot_without_duplicate_entries(isolated, monkeypatch):
    calls = []
    async def start(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary price failure')
        return {'session_id': None}
    monkeypatch.setattr(challenge, '_pick_symbol', lambda: 'BTCUSDT')
    monkeypatch.setattr(challenge.paper_mod, 'start_session', start)
    with pytest.raises(RuntimeError, match='not ready'):
        asyncio.run(challenge.ensure_today())
    with Session(isolated) as db:
        assert len(db.exec(select(LeaderboardEntry)).all()) == 2
    asyncio.run(challenge.ensure_today())
    assert len(calls) == 4
    with Session(isolated) as db:
        assert len(db.exec(select(LeaderboardEntry)).all()) == 3
    assert challenge.get_today()['active'] is True
