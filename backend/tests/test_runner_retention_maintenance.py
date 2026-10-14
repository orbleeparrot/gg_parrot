"""Bounded history maintenance against an isolated, caller-owned transaction."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, insert
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import runner
from app.db import RunnerCommand, RunSession, RunSessionEvent, User

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        users = [User(email=f"retention-{number}@example.invalid", username=f"retention-{number}",
                      password_hash="not-a-login-password", created_at=NOW.isoformat()) for number in range(2)]
        db.add_all(users); db.commit()
        yield db, [user.id for user in users]
    engine.dispose()


def add_sessions(db, user_id, count=1, *, ended=None, status="stopped", pinned=False, **changes):
    stopped = (ended or NOW - timedelta(days=1)).isoformat()
    rows = [RunSession(user_id=user_id, symbol="BTCUSDT", status=status, pinned=pinned,
                       started_at=(NOW - timedelta(days=90)).isoformat(), stopped_at=stopped,
                       **changes) for _ in range(count)]
    db.add_all(rows); db.flush()
    return [row.id for row in rows]


def session_ids(db):
    return set(db.exec(select(RunSession.id)).all())


def test_batch_keeps_latest_30_per_user_and_never_loads_full_sessions(store):
    db, (first, second) = store
    a = add_sessions(db, first, 35)
    b = add_sessions(db, second, 32)
    pinned = add_sessions(db, first, ended=NOW - timedelta(days=100), pinned=True)
    running = add_sessions(db, first, ended=NOW - timedelta(days=100), status="running")
    db.add(RunSessionEvent(session_id=a[0], user_id=first, ts=NOW.isoformat()))
    db.add(RunnerCommand(session_id=a[0], status="acked"))
    db.commit()
    statements = []
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        result = runner.prune_ended_sessions_batch(db, now=NOW)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert result == {"deleted_sessions": 7, "deleted_events": 1, "deleted_commands": 1, "has_more": False}
    assert session_ids(db) == set(a[5:] + b[2:] + pinned + running)
    assert any("row_number() over" in sql for sql in statements)
    assert all("runsession.macro_json" not in sql for sql in statements)


def test_age_cutoff_handles_exact_boundary_offsets_and_heartbeat_fallback(store):
    db, (uid, _) = store
    cutoff = NOW - timedelta(days=30)
    old = add_sessions(db, uid, ended=cutoff - timedelta(seconds=1))
    boundary = add_sessions(db, uid, ended=cutoff)
    offset = add_sessions(db, uid, ended=cutoff.astimezone(timezone(timedelta(hours=9))))
    recent = add_sessions(db, uid, ended=cutoff + timedelta(seconds=1))
    heartbeat = add_sessions(db, uid, last_heartbeat_at=(NOW - timedelta(days=2)).isoformat())
    no_timestamp = add_sessions(db, uid)
    db.get(RunSession, heartbeat[0]).stopped_at = ""
    db.get(RunSession, no_timestamp[0]).stopped_at = "invalid-time"
    db.commit()
    result = runner.prune_ended_sessions_batch(db, now=NOW)
    assert result["deleted_sessions"] == 1
    assert session_ids(db) == set(boundary + offset + recent + heartbeat + no_timestamp)
    assert old[0] not in session_ids(db)


def test_history_policy_does_not_add_permanent_exceptions_for_ended_snapshots(store):
    db, (uid, _) = store
    old = add_sessions(db, uid, ended=NOW - timedelta(days=40), in_position=True, position_uncertain=True)
    db.add(RunnerCommand(session_id=old[0], status="pending")); db.commit()
    result = runner.prune_ended_sessions_batch(db, now=NOW)
    assert result["deleted_sessions"] == 1
    assert old[0] not in session_ids(db)


def test_backlog_is_paged_and_limit_is_capped_at_200(store):
    db, (uid, _) = store
    ids = add_sessions(db, uid, 425, ended=NOW - timedelta(days=40)); db.commit()
    first = runner.prune_ended_sessions_batch(db, now=NOW, limit=9999)
    assert first["deleted_sessions"] == 200 and first["has_more"] is True
    db.commit()
    second = runner.prune_ended_sessions_batch(db, now=NOW, limit=200)
    assert second["deleted_sessions"] == 200 and second["has_more"] is True
    db.commit()
    last = runner.prune_ended_sessions_batch(db, now=NOW, limit=200)
    assert last["deleted_sessions"] == 25 and last["has_more"] is False
    assert not session_ids(db) & set(ids)


def test_each_child_table_is_capped_at_1000_and_parent_waits_until_drained(store):
    db, (uid, _) = store
    sid = add_sessions(db, uid, ended=NOW - timedelta(days=40))[0]
    db.execute(insert(RunSessionEvent), [{"session_id": sid, "user_id": uid, "ts": NOW.isoformat()} for _ in range(1501)])
    db.execute(insert(RunnerCommand), [{"session_id": sid, "status": "acked"} for _ in range(1501)])
    db.commit()
    first = runner.prune_ended_sessions_batch(db, now=NOW)
    assert first == {"deleted_sessions": 0, "deleted_events": 1000, "deleted_commands": 1000, "has_more": True}
    assert sid in session_ids(db)
    assert db.exec(select(func.count(RunSessionEvent.id))).one() == 501
    assert db.exec(select(func.count(RunnerCommand.id))).one() == 501
    db.commit()
    second = runner.prune_ended_sessions_batch(db, now=NOW)
    assert second == {"deleted_sessions": 1, "deleted_events": 501, "deleted_commands": 501, "has_more": False}
    assert sid not in session_ids(db)


def test_batch_never_commits_and_caller_rollback_restores_parent_and_children(store, monkeypatch):
    db, (uid, _) = store
    sid = add_sessions(db, uid, ended=NOW - timedelta(days=40))[0]
    db.add(RunSessionEvent(session_id=sid, user_id=uid, ts=NOW.isoformat()))
    db.add(RunnerCommand(session_id=sid, status="acked")); db.commit()
    commit = Mock(wraps=db.commit); monkeypatch.setattr(db, "commit", commit)
    assert runner.prune_ended_sessions_batch(db, now=NOW)["deleted_sessions"] == 1
    commit.assert_not_called()
    db.rollback()
    assert sid in session_ids(db)
    assert db.exec(select(func.count(RunSessionEvent.id))).one() == 1
    assert db.exec(select(func.count(RunnerCommand.id))).one() == 1


def test_failed_mid_batch_can_be_rolled_back_without_partial_event_loss(store):
    db, (uid, _) = store
    sid = add_sessions(db, uid, ended=NOW - timedelta(days=40))[0]
    db.add(RunSessionEvent(session_id=sid, user_id=uid, ts=NOW.isoformat()))
    db.add(RunnerCommand(session_id=sid, status="acked")); db.commit()
    def fail_command_delete(_conn, _cursor, statement, _params, _context, _many):
        if statement.lower().startswith("delete from runnercommand"):
            raise RuntimeError("injected child-delete failure")
    event.listen(db.get_bind(), "before_cursor_execute", fail_command_delete)
    try:
        with pytest.raises(RuntimeError):
            runner.prune_ended_sessions_batch(db, now=NOW)
        db.rollback()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", fail_command_delete)
    assert sid in session_ids(db)
    assert db.exec(select(func.count(RunSessionEvent.id))).one() == 1
    assert db.exec(select(func.count(RunnerCommand.id))).one() == 1


def test_legacy_daily_failure_retries_after_60s_and_only_success_starts_24h_throttle(monkeypatch, caplog):
    clock = [NOW]
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(runner, "datetime", FakeDatetime)
    monkeypatch.setattr(runner, "_last_daily_prune_ms", 0)
    monkeypatch.setattr(runner, "_daily_prune_lock", Lock())
    if hasattr(runner, "_next_daily_prune_retry_ms"):
        monkeypatch.setattr(runner, "_next_daily_prune_retry_ms", 0)
    calls = []
    def prune(_db):
        calls.append(clock[0])
        if len(calls) == 1:
            raise RuntimeError("postgres://user:do-not-log-me@private/database")
        return 3
    monkeypatch.setattr(runner, "prune_all_ended_sessions", prune)
    db = SimpleNamespace(rollback=Mock())
    assert runner.maybe_prune_all_ended_sessions(db) == 0
    assert runner._last_daily_prune_ms == 0
    clock[0] += timedelta(seconds=59)
    assert runner.maybe_prune_all_ended_sessions(db) == 0 and len(calls) == 1
    clock[0] += timedelta(seconds=1)
    assert runner.maybe_prune_all_ended_sessions(db) == 3 and len(calls) == 2
    success = runner._last_daily_prune_ms
    assert success == int(clock[0].timestamp() * 1000)
    clock[0] += timedelta(hours=23)
    assert runner.maybe_prune_all_ended_sessions(db) == 0 and len(calls) == 2
    clock[0] += timedelta(hours=1)
    assert runner.maybe_prune_all_ended_sessions(db) == 3 and len(calls) == 3
    assert "RuntimeError" in caplog.text
    assert "do-not-log-me" not in caplog.text and "postgres://" not in caplog.text
    db.rollback.assert_called_once()


def test_legacy_daily_cleanup_rejects_overlapping_calls(monkeypatch):
    entered, release = Event(), Event()
    monkeypatch.setattr(runner, "_last_daily_prune_ms", 0)
    monkeypatch.setattr(runner, "_daily_prune_lock", Lock())
    if hasattr(runner, "_next_daily_prune_retry_ms"):
        monkeypatch.setattr(runner, "_next_daily_prune_retry_ms", 0)
    def blocking(_db):
        entered.set()
        assert release.wait(2)
        return 4
    monkeypatch.setattr(runner, "prune_all_ended_sessions", blocking)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runner.maybe_prune_all_ended_sessions, SimpleNamespace(rollback=Mock()))
        assert entered.wait(1)
        try:
            assert runner.maybe_prune_all_ended_sessions(SimpleNamespace(rollback=Mock())) == 0
        finally:
            release.set()
        assert first.result(timeout=2) == 4


def test_legacy_retry_and_success_deadlines_start_when_attempt_finishes(monkeypatch):
    clock = [NOW]
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(runner, "datetime", FakeDatetime)
    monkeypatch.setattr(runner, "_last_daily_prune_ms", 0)
    monkeypatch.setattr(runner, "_next_daily_prune_retry_ms", 0)
    monkeypatch.setattr(runner, "_daily_prune_lock", Lock())
    attempts = []
    def slow_attempt(_db):
        attempts.append(clock[0])
        clock[0] += timedelta(seconds=10)
        if len(attempts) == 1:
            raise RuntimeError("fixture-only failure")
        return 2
    monkeypatch.setattr(runner, "prune_all_ended_sessions", slow_attempt)
    db = SimpleNamespace(rollback=Mock())
    assert runner.maybe_prune_all_ended_sessions(db) == 0
    clock[0] += timedelta(seconds=59)
    assert runner.maybe_prune_all_ended_sessions(db) == 0 and len(attempts) == 1
    clock[0] += timedelta(seconds=1)
    assert runner.maybe_prune_all_ended_sessions(db) == 2
    assert runner._last_daily_prune_ms == int(clock[0].timestamp() * 1000)
