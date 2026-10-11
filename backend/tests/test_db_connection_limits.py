"""Pool limits and runner admission, without production credentials/network."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError, TimeoutError
from sqlmodel import SQLModel, create_engine

from app import db as persistence, runner
from app.db import RunnerKey, RunSession, User


def test_postgres_pools_are_small_finite_and_overflow_cannot_bypass_limits(monkeypatch):
    monkeypatch.setattr(persistence, "_DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "2")
    monkeypatch.setenv("DATABASE_RUNNER_POOL_SIZE", "1")
    # SQLAlchemy -1 is unlimited overflow; old/injected env must not enable it.
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "-1")
    ordinary = persistence._build_engine()
    critical = persistence._build_engine(purpose="runner")
    try:
        assert ordinary.pool.size() == 2
        assert critical.pool.size() == 1
        assert ordinary.pool._max_overflow == critical.pool._max_overflow == 0
        assert ordinary.pool.timeout() == 2
        assert critical.pool.timeout() == 1
    finally:
        ordinary.dispose()
        critical.dispose()


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "500"])
def test_invalid_pool_size_fails_closed_instead_of_unlimited(monkeypatch, value):
    monkeypatch.setattr(persistence, "_DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.setenv("DATABASE_POOL_SIZE", value)
    with pytest.raises(ValueError, match="DATABASE_POOL_SIZE"):
        persistence._build_engine()


def test_postgres_does_not_rely_on_ignored_startup_options(monkeypatch):
    monkeypatch.setattr(persistence, "_DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    original = persistence.create_engine
    captured = {}

    def tracked(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(persistence, "create_engine", tracked)
    engine = persistence._build_engine()
    try:
        assert captured["connect_args"]["connect_timeout"] == 3
        assert captured["connect_args"]["prepare_threshold"] is None
        assert "options" not in captured["connect_args"]
        assert captured["connect_args"]["application_name"].startswith("gg-parrot-ordinary-")
        assert event.contains(engine, "begin", persistence.set_transaction_timeout)
    finally:
        engine.dispose()


def test_unconfigured_process_defaults_to_one_connection_before_blueprint_sync(monkeypatch):
    monkeypatch.setattr(persistence, "_DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.delenv("DATABASE_POOL_SIZE", raising=False)
    engine = persistence._build_engine()
    try:
        assert engine.pool.size() == 1 and engine.pool._max_overflow == 0
    finally:
        engine.dispose()


def test_worker_long_holds_are_reported_without_trace_or_log_flood(monkeypatch):
    messages = []
    monkeypatch.setattr(persistence, "_pool_hold_last_log", {})
    monkeypatch.setattr(persistence.time, "perf_counter", lambda: 100)
    monkeypatch.setattr(persistence.logger, "warning", lambda *args: messages.append(args))
    for _ in range(10):
        record = SimpleNamespace(info={"ggp_checkout_started": 97})
        persistence._connection_checkin(None, record)
        assert "ggp_checkout_started" not in record.info
    assert len(messages) == 1
    assert messages[0][1] == "ordinary"


def test_runner_heartbeat_uses_one_checkout_for_auth_and_snapshot(tmp_path, monkeypatch):
    # Two real pools to the same private file: saturating ordinary requests must
    # not consume the reserved runner slot or trigger an auth re-query after commit.
    url = f"sqlite:///{tmp_path / 'priority.db'}"
    ordinary = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=0.02)
    critical = create_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.02)
    SQLModel.metadata.create_all(ordinary)
    monkeypatch.setattr(persistence, "_engine", ordinary)
    monkeypatch.setattr(persistence, "_runner_engine", critical)
    with persistence.get_session() as session:
        user = User(email="priority@example.test", username="priority", password_hash="hash", created_at="2026-10-08T00:00:00Z")
        session.add(user)
        session.flush()
        session.add(RunnerKey(user_id=user.id, key="local-test-key", created_at=user.created_at))
        row = RunSession(user_id=user.id, symbol="BTCUSDT", started_at=user.created_at, runner_version="7")
        session.add(row)
        session.commit()
        session.refresh(row)
        session_id = row.id
    checkouts = []
    event.listen(critical, "checkout", lambda *args: checkouts.append(1))
    monkeypatch.setattr(runner, "notify_sessions_changed", lambda *args: None)
    from app.main import app
    try:
        with ordinary.connect(), ordinary.connect():
            with pytest.raises(TimeoutError):
                ordinary.connect()
            with ThreadPoolExecutor(max_workers=1) as executor:
                response = executor.submit(
                    lambda: TestClient(app).post("/api/runner/heartbeat", headers={"X-Runner-Key": "local-test-key"},
                                                json={"session_id": session_id, "last_price": 100, "in_position": False})
                ).result(timeout=3)
            assert response.status_code == 200, response.text
            assert response.json() == {"action": "continue", "commands": []}
        assert len(checkouts) == 1
        assert critical.pool.checkedout() == 0
        with critical.connect() as conn:
            assert conn.execute(text("SELECT last_price FROM runsession WHERE id=:id"), {"id": session_id}).scalar() == 100
        with persistence.TracedSession(critical) as session:
            row = session.get(RunSession, session_id)
            assert runner._session_view(row)["last_heartbeat_at"] == row.last_heartbeat_at
    finally:
        ordinary.dispose()
        critical.dispose()


@pytest.mark.parametrize("error", [TimeoutError("pool queue full"), OperationalError("", {}, Exception("EMAXCONNSESSION max clients reached: secret URL"))])
def test_capacity_errors_are_safe_retryable_503(monkeypatch, error):
    from app.main import app
    monkeypatch.setattr(runner, "user_for_key", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/runner/heartbeat", headers={"X-Runner-Key": "test"}, json={"session_id": 1})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "database_busy"
    assert "secret" not in response.text


def test_other_database_errors_are_not_mislabeled_as_capacity(monkeypatch):
    from app.main import app
    error = OperationalError("", {}, Exception("undefined table"))
    monkeypatch.setattr(runner, "user_for_key", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/runner/heartbeat", headers={"X-Runner-Key": "test"}, json={"session_id": 1})
    assert response.status_code == 500


@pytest.mark.skipif(not os.environ.get("GGP_TEST_POSTGRES_URL"), reason="requires an isolated network-none PostgreSQL")
def test_postgres_transaction_timeout_is_applied_and_resets(monkeypatch):
    # This is only the task's disposable Unix-socket DB, not DATABASE_URL.
    url = os.environ["GGP_TEST_POSTGRES_URL"]
    assert "/ggp_pool_test?host=/testpg" in url
    monkeypatch.setattr(persistence, "_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", "1000")
    engine = persistence._build_engine()
    try:
        with engine.begin() as connection:
            assert connection.execute(text("SHOW statement_timeout")).scalar() == "1s"
        with engine.connect() as connection:
            with pytest.raises(OperationalError) as error:
                connection.execute(text("SELECT pg_sleep(1.1)"))
            assert error.value.orig.sqlstate == "57014"
            connection.rollback()
        # Raw DBAPI checkout bypasses begin hook, proving SET LOCAL did not leak.
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                assert cursor.fetchone()[0] == "0"
            raw.rollback()
        finally:
            raw.close()
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("GGP_TEST_POSTGRES_URL"), reason="requires an isolated network-none PostgreSQL")
def test_postgres_two_configured_generations_use_twelve_connections(monkeypatch):
    import psycopg
    url = os.environ["GGP_TEST_POSTGRES_URL"]
    assert "/ggp_pool_test?host=/testpg" in url
    monkeypatch.setattr(persistence, "_DATABASE_URL", url)
    engines = []
    try:
        with ExitStack() as stack:
            connections = []
            for _generation in range(2):
                monkeypatch.setenv("DATABASE_POOL_SIZE", "2")
                web = persistence._build_engine()
                critical = persistence._build_engine(purpose="runner")
                engines.extend((web, critical))
                connections.extend(stack.enter_context(web.connect()) for _ in range(2))
                connections.append(stack.enter_context(critical.connect()))
                listener = stack.enter_context(psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"),
                                                              application_name="gg-parrot-test-listener", autocommit=True))
                listener.execute("LISTEN ggp_pool_test_channel")
                monkeypatch.setenv("DATABASE_POOL_SIZE", "1")
                for _worker in range(2):
                    worker = persistence._build_engine()
                    engines.append(worker)
                    connections.append(stack.enter_context(worker.connect()))
            count = connections[0].execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'gg-parrot-%'")).scalar()
            assert count == 12  # both generations, including real LISTEN sessions
        assert all(engine.pool.checkedout() == 0 for engine in engines)
    finally:
        for engine in engines:
            engine.dispose()
