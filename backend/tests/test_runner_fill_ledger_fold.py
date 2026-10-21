"""서버 전략 루프가 틱마다 체결 기록표를 장부에 접는다(2026-10-21 체결 기준 장부 과제 10).

하트비트는 기록표에 넣기만 하고 루프를 기다리지 않는다. 루프는 다음 틱(≤ 3초)에 안 접은 행 · 끝난 주문을 읽어 장부에
접고, '접음' 표시 · state_json · 실행 기록을 한 트랜잭션에 쓴다. 저장이 실패하거나 서버가 다시 시작돼도 한 번만 반영된다.
"""
import asyncio
import json

import pytest
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunnerFill, RunSession, RunSessionEvent, get_session
from app.engine import Macro
from tests.test_fill_ledger_entries import H_KRW

T0_MS = 1_761_004_800_000  # 2026-10-21 00:00 UTC
STEP = 900_000


class _Feed:
    def __init__(self):
        self.subs = []

    async def history(self, symbol, interval, market, n, **kwargs):
        return [(T0_MS + i * STEP, 500, 500, 500, 500) for i in range(20)]  # 평평한 웜업

    def subscribe(self, symbol, interval, market, cb, **kwargs):
        self.subs.append(cb)
        return ("sub", symbol)

    def unsubscribe(self, sub):
        self.subs.clear()


async def _no_loop(live):
    """틱 루프를 끈다 — 시험이 _tick_once 를 직접 부른다."""


@pytest.fixture(autouse=True)
def _reset():
    eng._drivers.clear()
    yield
    eng._drivers.clear()


@pytest.fixture
def sid():
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-GTC", status="running", started_at=now, last_heartbeat_at=now,
                         runner_version="21", exec_policy=eng.FILL_LEDGER_POLICY,
                         macro_json=Macro.model_validate(H_KRW).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        session_id = row.id
    yield session_id
    with get_session() as db:
        for model in (RunnerFill, RunnerCommand, RunSessionEvent):
            for r in db.exec(select(model).where(model.session_id == session_id)).all():
                db.delete(r)
        db.delete(db.get(RunSession, session_id))
        db.commit()


@pytest.fixture
def feed(monkeypatch):
    fake = _Feed()
    monkeypatch.setattr(eng, "feed", fake)
    monkeypatch.setattr(eng, "_run", _no_loop)
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s, **kw: 500.0)
    return fake


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _base_buy(sid, feed):
    """웜업 뒤 첫 봉에 기본 매수 o1 — 틱이 명령으로 만든다."""
    assert await eng.start_driver(sid)
    live = eng._drivers[sid]
    await feed.subs[0]("KRW-GTC", (T0_MS + 20 * STEP, 500, 501, 499, 500))
    await eng._tick_once(live)
    with get_session() as db:
        [cmd] = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all()
        assert cmd.action == "buy" and cmd.order_ref == "o1"
        return live, cmd.id


def _ack(sid, cmd_id, *, ok=True, qty=99.0, price=505.0, fee=25.0, key="ggp-b1"):
    orders = [{"client_order_id": key, "status": "FILLED", "side": "buy", "executed_qty": qty, "acquired_qty": qty,
               "fill_price": price, "fee_quote": fee, "fee_known": True}] if qty else []
    with get_session() as db:
        row = db.get(RunSession, sid)
        eng.apply_acks(db, row, [{"command_id": cmd_id, "ok": ok, "executed_qty": qty, "fill_price": price if qty else 0.0,
                                  "error": "" if ok else "잔고 부족", "orders": orders}], now_ms=eng._now_ms())
        db.commit()


def _ledger(sid):
    with get_session() as db:
        state = json.loads(db.get(RunSession, sid).state_json)
    return state["legs"][0]["strategy"]["ledger"]


def _fill_rows(sid):
    with get_session() as db:
        return list(db.exec(select(RunnerFill).where(RunnerFill.session_id == sid)).all())


def _events(sid, name):
    with get_session() as db:
        return [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
                if e.event_name == name]


def test_next_tick_folds_the_report_into_the_saved_ledger(sid, feed):
    async def scenario():
        live, cmd = await _base_buy(sid, feed)
        _ack(sid, cmd)
        await eng._tick_once(live)
        ledger = _ledger(sid)
        assert ledger["orders"] == {} and [(lot["ref"], lot["qty"], lot["fill"]) for lot in ledger["lots"]] == [("o1", 99.0, 505.0)]
        assert all(row.applied_ms > 0 for row in _fill_rows(sid))
        await eng._tick_once(live)  # 다시 접을 것이 없다
        [event] = _events(sid, "engine.ledger_entry_filled")
        assert "평단 500 → 505" in event.message
        await eng.stop_driver(sid)

    run(scenario())


def test_failed_save_is_written_next_tick_and_the_fill_counts_once(sid, feed, monkeypatch):
    real = eng._persist_fold
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return real(*args)

    monkeypatch.setattr(eng, "_persist_fold", flaky)

    async def scenario():
        live, cmd = await _base_buy(sid, feed)
        _ack(sid, cmd)
        await eng._tick_once(live)  # 장부는 접었지만 저장 실패
        assert live.unsaved_fold is not None and all(row.applied_ms == 0 for row in _fill_rows(sid))
        await eng._tick_once(live)
        assert live.unsaved_fold is None and all(row.applied_ms > 0 for row in _fill_rows(sid))
        inner = live.driver.legs[0].sim.inner
        assert inner.cash == pytest.approx(300000 - 99.0 * 505.0 - 25.0)
        await eng.stop_driver(sid)

    run(scenario())


def test_restart_between_report_and_fold_folds_once_in_the_new_process(sid, feed):
    async def scenario():
        live, cmd = await _base_buy(sid, feed)
        _ack(sid, cmd)
        await eng.stop_driver(sid)  # 접기 전에 프로세스가 끝났다
        assert await eng.start_driver(sid)
        again = eng._drivers[sid]
        assert list(again.driver.legs[0].sim.inner.ledger.orders) == ["o1"]  # 명령이 있으니 그대로 기다린다
        await eng._tick_once(again)
        assert [(lot["ref"], lot["fill"]) for lot in _ledger(sid)["lots"]] == [("o1", 505.0)]
        await eng.stop_driver(sid)

    run(scenario())


def test_lost_applied_mark_does_not_fold_twice(sid, feed):
    async def scenario():
        live, cmd = await _base_buy(sid, feed)
        _ack(sid, cmd)
        await eng._tick_once(live)
        inner = live.driver.legs[0].sim.inner
        cash = inner.cash
        with get_session() as db:  # state_json 은 저장됐는데 '접음' 표시만 잃었다
            for row in db.exec(select(RunnerFill).where(RunnerFill.session_id == sid)).all():
                row.applied_ms = 0
                db.add(row)
            db.commit()
        await eng._tick_once(live)
        assert inner.cash == cash and all(row.applied_ms > 0 for row in _fill_rows(sid))
        await eng.stop_driver(sid)

    run(scenario())


def test_entry_that_never_filled_waits_before_buying_again(sid, feed):
    async def scenario():
        live, cmd = await _base_buy(sid, feed)
        _ack(sid, cmd, ok=False, qty=0.0)
        await eng._tick_once(live)
        hold = live.driver.entry_hold_until_ms
        assert hold and 4 * 60_000 < hold - eng._now_ms() <= 5 * 60_000
        assert _ledger(sid)["lots"] == [] and _ledger(sid)["orders"] == {}
        with get_session() as db:
            assert db.get(RunnerCommand, cmd).rollback_state == eng.ROLLED_BACK
        [event] = _events(sid, "engine.ledger_entry_unfilled")
        assert event.message.startswith("첫 매수 주문이 체결되지 않아 서버 장부에서 뺐어요")
        await eng.stop_driver(sid)

    run(scenario())


def test_open_order_without_a_command_is_dropped_on_restart(sid, feed):
    """신호를 명령으로 저장하기 전에 서버가 끝났다 — 보낸 적이 없는 주문은 취소로 정리한다(남기면 판단이 영영 보류된다)."""
    macro = Macro.model_validate(H_KRW)
    d = eng.build_driver(macro, macro.symbol)
    d.enable_fill_ledger()
    d.push_candle("KRW-GTC", (T0_MS + 20 * STEP, 500, 501, 499, 500))  # 가상 매수 o1 — 명령 행 없음
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.state_json = json.dumps(d.state())
        db.add(row)
        db.commit()

    async def scenario():
        assert await eng.start_driver(sid)
        inner = eng._drivers[sid].driver.legs[0].sim.inner
        assert inner.ledger.orders == {} and inner.lots == []
        assert _ledger(sid)["orders"] == {} and len(_events(sid, "engine.ledger_order_dropped")) == 1
        await eng.stop_driver(sid)

    run(scenario())
