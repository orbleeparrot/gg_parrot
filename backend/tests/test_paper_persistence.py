from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine, select

from app import paper
from app.db import PaperSession, PaperTrade


class _FakeSim:
    def __init__(self, fill=None):
        self.fill = fill
        self.liquidations = 0
        self.liquidated_loss = 0.0

    def step(self, price, ts):
        return self.fill

    def equity(self, price):
        return 1_010.0


def _runner(fill=None):
    return paper._Runner(7, _FakeSim(fill), "BTCUSDT", "live", 1_000.0)


def _run(coro):
    """Run one coroutine without this Python build's Runner executor-shutdown hang."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        executor = getattr(loop, "_default_executor", None)
        loop._default_executor = None
        if executor is not None:
            executor.shutdown(wait=True)
        loop.close()


def test_tick_only_updates_memory_and_never_opens_a_database_session(monkeypatch):
    fill = SimpleNamespace(side="buy", price=100.0, qty=2.0, return_pct=1.0)
    runner = _runner(fill)

    def fail_if_called():
        raise AssertionError("a price tick must not perform synchronous DB I/O")

    monkeypatch.setattr(paper, "get_session", fail_if_called)

    returned = paper._tick(runner, 100.0)

    assert returned is fill
    assert runner.equity == 1_010.0
    assert runner.ret == 1.0


def test_checkpoint_batches_equity_but_persists_a_fill_immediately(monkeypatch):
    calls = []

    def persist(snapshot, fill):
        calls.append((snapshot, fill, threading.current_thread().name))
        if fill:
            return {"id": 11, **fill}
        return None

    monkeypatch.setattr(paper, "_persist_checkpoint", persist)
    runner = _runner()
    runner.last_checkpoint_monotonic = 100.0

    _run(paper._checkpoint(runner, now=110.0))
    assert calls == []

    _run(paper._checkpoint(runner, now=121.0))
    assert len(calls) == 1
    assert calls[0][2] != threading.current_thread().name

    fill = SimpleNamespace(side="sell", price=101.0, qty=2.0, return_pct=1.0)
    _run(paper._checkpoint(runner, fill=fill, now=122.0))
    assert len(calls) == 2
    assert runner.recent[0]["id"] == 11
    assert runner.last_checkpoint_monotonic == 122.0


def test_checkpoint_database_wait_does_not_block_the_event_loop(monkeypatch):
    def slow_persist(snapshot, fill):
        time.sleep(0.08)
        return None

    monkeypatch.setattr(paper, "_persist_checkpoint", slow_persist)
    runner = _runner()
    runner.last_checkpoint_monotonic = 0.0

    async def exercise():
        checkpoint = asyncio.create_task(paper._checkpoint(runner, now=100.0))
        await asyncio.sleep(0.01)
        assert not checkpoint.done()
        heartbeats = 0
        while not checkpoint.done():
            heartbeats += 1
            await asyncio.sleep(0.005)
        await checkpoint
        assert heartbeats >= 2

    _run(exercise())


def test_finalize_is_idempotent_and_runs_database_work_off_loop(monkeypatch):
    calls = []

    def persist_finalize(snapshot):
        calls.append(threading.current_thread().name)

    monkeypatch.setattr(paper, "_persist_finalize", persist_finalize)
    runner = _runner()

    async def exercise():
        await asyncio.gather(
            paper._finalize_async(runner),
            paper._finalize_async(runner),
        )

    _run(exercise())

    assert runner.status == "stopped"
    assert len(calls) == 1
    assert calls[0] != threading.current_thread().name


def test_late_fill_is_not_inserted_for_stopped_or_missing_session(monkeypatch):
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        row = PaperSession(
            macro_id="m1",
            symbol="BTCUSDT",
            mode="live",
            status="stopped",
            started_at="2026-08-26T00:00:00Z",
            virtual_balance=1_000,
            current_equity=1_000,
            current_return=0.0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        stopped_id = row.id

    @contextmanager
    def session_factory():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(paper, "get_session", session_factory)
    fill = {
        "ts": "2026-08-26T00:00:01Z",
        "side": "buy",
        "price": 100.0,
        "qty": 1.0,
        "return_at_trade": 0.0,
    }
    snapshot = {
        "session_id": stopped_id,
        "equity": 1_001.0,
        "return": 0.1,
        "liquidations": 0,
        "liquidated_loss": 0.0,
    }

    assert paper._persist_checkpoint(snapshot, fill) is None
    assert paper._persist_checkpoint({**snapshot, "session_id": 999}, fill) is None
    with Session(engine) as db:
        assert db.exec(select(PaperTrade)).all() == []


def test_stop_waits_for_inflight_checkpoint_before_finalize(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    order = []

    def persist_checkpoint(snapshot, fill):
        started.set()
        release.wait(timeout=2)
        order.append("checkpoint")
        return None

    def persist_finalize(snapshot):
        order.append("finalize")

    monkeypatch.setattr(paper, "_persist_checkpoint", persist_checkpoint)
    monkeypatch.setattr(paper, "_persist_finalize", persist_finalize)
    runner = _runner()

    async def exercise():
        runner.task = asyncio.create_task(paper._checkpoint(runner, force=True))
        paper._running[runner.session_id] = runner
        while not started.is_set():
            await asyncio.sleep(0.005)
        stop_task = asyncio.create_task(paper.stop_session(runner.session_id))
        await asyncio.sleep(0.03)
        assert not stop_task.done()
        release.set()
        while not stop_task.done():
            await asyncio.sleep(0.005)
        return await stop_task

    try:
        result = _run(exercise())
    finally:
        release.set()
        paper._running.pop(runner.session_id, None)

    assert result["status"] == "stopped"
    assert order == ["checkpoint", "finalize"]


def test_checkpoint_error_does_not_prevent_terminal_status(monkeypatch):
    finalized = []

    def fail_checkpoint(snapshot, fill):
        raise RuntimeError("database unavailable")

    def persist_finalize(snapshot):
        finalized.append(snapshot["session_id"])

    monkeypatch.setattr(paper, "_persist_checkpoint", fail_checkpoint)
    monkeypatch.setattr(paper, "_persist_finalize", persist_finalize)
    runner = _runner()

    async def exercise():
        runner.task = asyncio.create_task(paper._checkpoint(runner, force=True))
        paper._running[runner.session_id] = runner
        while not runner.task.done():
            await asyncio.sleep(0.005)
        stop_task = asyncio.create_task(paper.stop_session(runner.session_id))
        while not stop_task.done():
            await asyncio.sleep(0.005)
        return await stop_task

    try:
        result = _run(exercise())
    finally:
        paper._running.pop(runner.session_id, None)

    assert result["status"] == "stopped"
    assert finalized == [runner.session_id]


def test_shutdown_drains_running_tasks_before_finalizing(monkeypatch):
    order = []
    runner = _runner()

    async def finalize(current):
        order.append(f"finalized-{current.session_id}")
        current.finalized = True
        current.status = "stopped"

    monkeypatch.setattr(paper, "_finalize_async", finalize)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        async def running_task():
            started.set()
            await release.wait()
            order.append("task-drained")

        runner.task = asyncio.create_task(running_task())
        paper._running[runner.session_id] = runner
        await started.wait()

        shutdown = asyncio.create_task(paper.shutdown_running_sessions())
        await asyncio.sleep(0.03)
        assert not shutdown.done()
        assert order == []
        release.set()
        await shutdown

    try:
        _run(exercise())
    finally:
        paper._running.pop(runner.session_id, None)

    assert order == ["task-drained", f"finalized-{runner.session_id}"]
    assert runner.stop_flag is True
    assert paper._running == {}
