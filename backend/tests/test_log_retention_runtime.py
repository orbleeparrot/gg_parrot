"""Retention runs without visitors, retries safely and owns its transaction."""
import asyncio
import threading
from unittest.mock import Mock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import log_retention_runtime as runtime
from app import notifications, runner
from app.db import NotificationMessage, RunSession, User


async def _health_when(worker, **expected):
    # The batch's thread Event proves work happened, not that the event loop
    # has consumed its Future and published success counters yet.
    async def poll():
        while True:
            state = worker.snapshot()
            if all(state[key] == value for key, value in expected.items()):
                return state
            await asyncio.sleep(.001)
    return await asyncio.wait_for(poll(), timeout=2)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def _seed(engine):
    with Session(engine) as db:
        user = User(username="retention", email="retention@example.invalid", password_hash="x", created_at="2020-01-01T00:00:00Z")
        db.add(user)
        db.flush()
        db.add(RunSession(user_id=user.id, status="stopped", started_at="2020-01-01T00:00:00Z"))
        db.add(NotificationMessage(user_id=None, kind="notice", title="expired", created_at="2020-01-01T00:00:00Z", created_ms=1))
        db.commit()


def test_batch_commits_expired_records_without_a_visit(isolated_store):
    _seed(isolated_store)
    result = runtime.run_batch()
    assert result["deleted_sessions"] == 1
    assert result["deleted_notices"] == 1
    assert result["has_more"] is False
    with Session(isolated_store) as db:
        assert not db.exec(select(RunSession)).all()
        assert not db.exec(select(NotificationMessage)).all()


def test_batch_failure_rolls_back_all_deletions(isolated_store, monkeypatch):
    _seed(isolated_store)
    monkeypatch.setattr(notifications, "prune_expired_notices", Mock(side_effect=RuntimeError("secret must not reach a log")))
    with pytest.raises(RuntimeError):
        runtime.run_batch()
    with Session(isolated_store) as db:
        assert len(db.exec(select(RunSession)).all()) == 1
        assert len(db.exec(select(NotificationMessage)).all()) == 1


def test_worker_retries_failure_without_waiting_for_daily_interval(caplog):
    async def scenario():
        recovered = threading.Event()
        count = 0
        def batch():
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("api_key=do-not-log-this")
            recovered.set()
            return {"deleted_sessions": 1, "has_more": False}
        worker = runtime.RetentionRuntime(batch=batch, initial_delay=0, interval_seconds=3600, retry_seconds=.01)
        worker.start()
        try:
            assert await asyncio.to_thread(recovered.wait, 2)
            state = await _health_when(worker, successful_batches=1)
            assert state["failures"] == 1
            assert state["last_success_ms"] > 0
            assert state["consecutive_failures"] == 0
            assert state["deleted_sessions"] == 1
        finally:
            await worker.stop()
    asyncio.run(scenario())
    assert "RuntimeError" in caplog.text
    assert "do-not-log-this" not in caplog.text


def test_backlog_is_drained_in_separate_bounded_transactions():
    async def scenario():
        drained = threading.Event()
        calls = []
        def batch():
            calls.append(threading.current_thread().name)
            if len(calls) == 2:
                drained.set()
            return {"deleted_receipts": 500, "has_more": len(calls) == 1}
        worker = runtime.RetentionRuntime(batch=batch, initial_delay=0, backlog_seconds=.01, interval_seconds=3600)
        worker.start()
        try:
            assert await asyncio.to_thread(drained.wait, 2)
            state = await _health_when(worker, successful_batches=2)
            assert len(calls) == 2
            assert all(name != threading.current_thread().name for name in calls)
            assert state["deleted_receipts"] == 1000
        finally:
            await worker.stop()
    asyncio.run(scenario())


def test_stop_finishes_inflight_database_work_before_returning():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        def batch():
            entered.set()
            assert release.wait(2)
            return {"has_more": True}
        worker = runtime.RetentionRuntime(batch=batch, initial_delay=0)
        worker.start()
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(worker.stop())
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        await stopping
        assert worker.snapshot()["status"] == "stopped"
    asyncio.run(scenario())


def test_worker_start_is_idempotent_and_disabled_switch_is_testable(monkeypatch):
    async def scenario():
        monkeypatch.setenv("ACTIVITY_LOG_RETENTION_ENABLED", "false")
        runtime.start()
        assert runtime._runtime is None
        monkeypatch.setenv("ACTIVITY_LOG_RETENTION_ENABLED", "true")
        runtime.start()
        first = runtime._runtime
        runtime.start()
        assert runtime._runtime is first
        await runtime.stop()
        assert runtime._runtime is None
    asyncio.run(scenario())


def test_busy_distributed_lock_retries_soon_instead_of_waiting_an_hour():
    async def scenario():
        done = threading.Event()
        calls = 0
        def batch():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"skipped": True}
            done.set()
            return {"has_more": False}
        worker = runtime.RetentionRuntime(batch=batch, initial_delay=0, retry_seconds=.01)
        worker.start()
        try:
            assert await asyncio.to_thread(done.wait, 2)
            await _health_when(worker, successful_batches=1)
        finally:
            await worker.stop()
    asyncio.run(scenario())


def test_application_registers_retention_outside_visit_and_waits_for_shutdown():
    from pathlib import Path
    source = Path(runtime.__file__).with_name("main.py").read_text()
    lifespan = source[source.index("async def lifespan"):source.index("app = FastAPI")]
    assert "log_retention_runtime.start()" in lifespan
    assert "log_retention_runtime.stop()" in lifespan
    assert "runner_mod.maybe_prune_all_ended_sessions(db)" not in source


def test_postgres_batch_uses_ordinary_pool_transaction_lock_and_local_timeouts(monkeypatch):
    statements = []
    db = Mock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.side_effect = lambda statement: (statements.append(str(statement)) or Mock(scalar_one=lambda: True))
    db.__enter__ = Mock(return_value=db)
    db.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(runtime, "get_session", lambda: db)
    monkeypatch.setattr(runner, "prune_ended_sessions_batch", lambda *a, **k: {"deleted_sessions": 2, "has_more": False})
    monkeypatch.setattr(notifications, "prune_expired_notices", lambda *a, **k: {"deleted_notices": 3, "has_more": False})
    result = runtime.run_batch()
    assert result["deleted_sessions"] == 2 and result["deleted_notices"] == 3
    assert any("pg_try_advisory_xact_lock" in query for query in statements)
    assert any("statement_timeout" in query for query in statements)
    assert any("lock_timeout" in query for query in statements)
    db.commit.assert_called_once()


def test_locked_postgres_batch_does_not_touch_records(monkeypatch):
    db = Mock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.return_value.scalar_one.return_value = False
    db.__enter__ = Mock(return_value=db)
    db.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(runtime, "get_session", lambda: db)
    prune = Mock()
    monkeypatch.setattr(runner, "prune_ended_sessions_batch", prune)
    assert runtime.run_batch()["skipped"] is True
    prune.assert_not_called()
    db.commit.assert_not_called()
