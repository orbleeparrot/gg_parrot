"""주문이 안 나간 진입은 서버 전략도 되돌린다 — '안 샀는데 보유 중' 을 없앤다(2026-10-13).

실행기가 진입 주문을 못 내면(잔고 부족 · 최소 금액 · 로컬 리스크 보류 = 실패, 못 가져감 = 만료) 서버 전략은
신호 직전 상태로 돌아가고, 몇 분 동안 새로 진입하지 않는다. 확실하지 않으면 되돌리지 않고 알린다.
"""
import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, RunSessionEvent, get_session
from app.engine import Macro
from app.engine.driver import Leg, StrategyDriver
from app.engine.stepper import make_sim

T0 = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)
FEES = {"commission_pct": 0.1, "slippage_pct": 0.05}
A = {"symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "params": {"take_profit_pct": 3.0, "initial_capital": 1000},
     "risk": {"invest_ratio": 0.5, "stop_loss_pct": 2.0}, "period": {"preset": "3m"}, "fees": FEES}
RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1, "candle_interval": "5m",
       "period": {"preset": "1w"}, "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0, "cooldown_minutes": 0}, "fees": FEES}
GRID = {"symbol": "BTCUSDT", "rule_type": "D", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"lower_price": 90, "upper_price": 130, "grid_count": 10, "initial_capital": 1000},
        "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}
MARTINGALE = {"symbol": "BTCUSDT", "rule_type": "H", "candle_interval": "1h", "period": {"preset": "3m"},
              "params": {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 1.0, "take_profit": 1.0,
                         "max_safety_orders": 2, "initial_capital": 1000},
              "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _driver(body: dict) -> StrategyDriver:
    macro = Macro.model_validate(body)
    return eng.build_driver(macro, macro.symbol)


# --- 드라이버: 체결 직전 상태와 되돌리기 ------------------------------------------
def test_rule_a_entry_rolls_back_to_flat_and_waits_out_the_hold():
    d = _driver(A)
    fill = d.tick(100.0, T0)
    assert fill is not None and fill.side == "buy" and d.state()["in_position"] is True
    snap = d.last_fill_snapshot
    assert snap["state"]["in_position"] is False and snap["trade_count"] == 0 and snap["equity"] == pytest.approx(1000.0)

    hold = _ms(T0 + timedelta(minutes=5))
    d.rollback(snap, retry_at_ms=hold)
    st = d.state()
    assert st["in_position"] is False and st["trade_count"] == 0 and st["entry_hold_until_ms"] == hold
    assert d.equity == pytest.approx(1000.0) and d.last_fill is None
    assert d.tick(100.0, T0 + timedelta(minutes=4)) is None  # 대기 중 — A 는 비면 바로 사지만 지금은 안 산다
    again = d.tick(100.0, T0 + timedelta(minutes=5, seconds=1))
    assert again is not None and again.side == "buy" and again.qty == pytest.approx(fill.qty)  # 현금도 그대로 돌아왔다


def test_hold_survives_a_restart_through_state_json():
    d = _driver(A)
    d.tick(100.0, T0)
    d.rollback(d.last_fill_snapshot, retry_at_ms=_ms(T0 + timedelta(minutes=5)))
    state = json.loads(json.dumps(d.state()))
    revived = _driver(A)
    revived.restore(state, leg_equity={}, total_equity=revived.initial)
    assert revived.tick(100.0, T0 + timedelta(minutes=4)) is None
    assert revived.tick(100.0, T0 + timedelta(minutes=6)).side == "buy"


def test_indicator_intent_is_dropped_under_hold_and_needs_a_new_signal():
    d = _driver(RSI)
    d.warmup({"ONEUSDT": [(i, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(20)]})
    d.push_candle("ONEUSDT", (_ms(T0), 50, 50, 50, 50))
    fill = d.tick(50.0, T0 + timedelta(seconds=3))  # 봉 뒤 첫 틱 = 다음 시가에 체결
    assert fill is not None and fill.side == "buy"
    snap = d.last_fill_snapshot
    assert snap["state"]["legs"][0]["strategy"]["pending"] == "enter"  # 틱 직전 — 의도가 걸려 있던 상태
    d.rollback(snap, retry_at_ms=_ms(T0 + timedelta(minutes=5)))
    assert d.state()["in_position"] is False
    assert d.tick(50.0, T0 + timedelta(seconds=6)) is None  # 대기 중 — 걸려 있던 의도는 버린다
    assert d.tick(50.0, T0 + timedelta(minutes=6)) is None  # 대기가 끝나도 새 신호 전에는 안 산다
    d.push_candle("ONEUSDT", (_ms(T0 + timedelta(minutes=10)), 49, 49, 49, 49))
    assert d.tick(49.0, T0 + timedelta(minutes=10, seconds=3)).side == "buy"


def test_grid_candle_with_two_buys_only_the_first_can_roll_back_and_grid_buys_wait():
    d = _driver(GRID)
    d.tick(124.0, T0)
    assert d.push_candle("BTCUSDT", (_ms(T0), 125, 125, 121, 124)) == 2  # 122 · 126 두 칸
    first = d.tick(124.0, T0 + timedelta(seconds=3))
    first_snap = d.last_fill_snapshot
    second = d.tick(124.0, T0 + timedelta(seconds=6))
    assert first.side == second.side == "buy"
    assert first_snap is not None and first_snap["state"]["in_position"] is False and first_snap["trade_count"] == 0
    assert d.last_fill_snapshot is None  # 둘째부터는 직전 상태가 없다 — 되돌리면 앞 체결까지 지워진다
    d.rollback(first_snap, retry_at_ms=_ms(T0 + timedelta(hours=3)))
    st = d.state()
    assert st["in_position"] is False and st["trade_count"] == 0 and st["legs"][0]["strategy"]["grid"] == []
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=1)), 124, 124, 121, 123)) == 0  # 대기 중엔 칸을 안 산다
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=4)), 124, 124, 121, 123)) == 2


def test_martingale_safety_order_rolls_back_to_the_base_position_exactly():
    tiny = 0.00001234  # state() 는 진입가를 소수 4자리로 반올림한다 — 되돌리기는 정확한 값이어야 한다
    d = _driver({**MARTINGALE})
    d.tick(tiny, T0)
    assert d.push_candle("BTCUSDT", (_ms(T0), tiny, tiny, tiny, tiny)) == 1
    base = d.tick(tiny, T0 + timedelta(seconds=3))
    assert base.side == "buy"
    inner = d.sim.inner
    base_qty, base_avg, base_cash = inner.total_qty(), inner.avg_entry(), inner.cash
    dip = tiny * 0.985  # 1% 아래 첫 안전주문 가격을 지난다
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=1)), tiny, tiny, dip, dip)) == 1
    add = d.tick(dip, T0 + timedelta(hours=1, seconds=3))
    snap = d.last_fill_snapshot
    assert add.side == "buy" and snap["state"]["in_position"] is True and snap["state"]["legs"][0]["strategy"]["so_done"] == 0
    d.rollback(snap, retry_at_ms=_ms(T0 + timedelta(hours=3, minutes=30)))
    assert inner.total_qty() == pytest.approx(base_qty, rel=1e-12)
    assert inner.avg_entry() == pytest.approx(base_avg, rel=1e-12)
    assert inner.cash == pytest.approx(base_cash, rel=1e-9) and inner._so_done == 0
    # 대기 중에는 안전주문(추가매수)도 다시 내지 않는다 — T0+2h 봉은 T0+3h 에 닫혀 아직 대기 중이다.
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=2)), dip, dip, dip, dip)) == 0
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=3)), dip, dip, dip, dip)) == 1


def test_candles_arriving_while_fills_are_still_queued_get_no_snapshot():
    """봉이 몰려 들어와 앞 봉의 체결이 큐에 남아 있으면, 뒤 봉의 체결은 정확히 되돌릴 기준이 없다 — 되돌리지 않는다."""
    d = _driver(GRID)
    d.tick(127.0, T0)
    assert d.push_candle("BTCUSDT", (_ms(T0), 127, 127, 125.9, 126)) == 1  # 126 칸
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=1)), 126, 126, 121.9, 123)) == 1  # 122 칸 — 앞 체결이 아직 큐에
    first = d.tick(123.0, T0 + timedelta(hours=1, seconds=3))
    assert first.side == "buy" and d.last_fill_snapshot is not None  # 첫 봉의 체결 — 그 봉 직전 상태
    second = d.tick(123.0, T0 + timedelta(hours=1, seconds=6))
    assert second.side == "buy" and d.last_fill_snapshot is None


TRAIL_1H = {"symbol": "BTCUSDT", "rule_type": "E", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"entry_mode": "immediate", "trail_percent": 2.0, "activation_profit": 5.0, "initial_capital": 1000},
            "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}


def test_candle_hold_ends_at_the_first_candle_that_closes_after_it():
    """봉 시각은 여는 시각이다 — 대기가 끝난 뒤 처음 닫히는 봉에서 다시 판단해야 한다(한 봉을 더 건너뛰지 않는다)."""
    d = _driver(TRAIL_1H)
    d.tick(100.0, T0)
    assert d.push_candle("BTCUSDT", (_ms(T0 - timedelta(hours=1)), 100, 100, 100, 100)) == 1  # T0 에 닫힌 봉
    assert d.tick(100.0, T0 + timedelta(seconds=3)).side == "buy"
    d.rollback(d.last_fill_snapshot, retry_at_ms=_ms(T0 + timedelta(minutes=5, seconds=10)))
    assert d.push_candle("BTCUSDT", (_ms(T0), 100, 100, 100, 100)) == 1  # T0+1h 에 닫힌다 — 대기는 끝났다
    d2 = _driver(TRAIL_1H)
    d2.tick(100.0, T0)
    d2.push_candle("BTCUSDT", (_ms(T0 - timedelta(hours=1)), 100, 100, 100, 100))
    d2.tick(100.0, T0 + timedelta(seconds=3))
    d2.rollback(d2.last_fill_snapshot, retry_at_ms=_ms(T0 + timedelta(hours=1, minutes=5)))
    assert d2.push_candle("BTCUSDT", (_ms(T0), 100, 100, 100, 100)) == 0  # T0+1h 에 닫혀도 아직 대기 중


def test_add_on_rollback_keeps_the_original_entry_time():
    """추가매수만 되돌리면 처음 진입한 시각(최대 보유 시간 기준)은 그대로다 — 실행기의 타이머와 어긋나지 않게."""
    d = _driver({**MARTINGALE, "risk": {"invest_ratio": 1.0, "max_holding_hours": 24}})
    d.tick(100.0, T0)
    assert d.push_candle("BTCUSDT", (_ms(T0), 100, 100, 100, 100)) == 1
    d.tick(100.0, T0 + timedelta(seconds=3))
    entered = d.sim.inner._entry_time
    assert d.push_candle("BTCUSDT", (_ms(T0 + timedelta(hours=20)), 100, 100, 98.5, 98.5)) == 1
    d.tick(98.5, T0 + timedelta(hours=20, seconds=3))
    d.rollback(d.last_fill_snapshot, retry_at_ms=_ms(T0 + timedelta(hours=21)))
    assert d.state()["in_position"] is True and d.sim.inner._entry_time == entered


def test_paper_drivers_do_not_track_snapshots_or_change_state_json():
    macro = Macro.model_validate(A)
    d = StrategyDriver([Leg("BTCUSDT", make_sim(macro, initial_capital=1000.0), 1000.0)], 1000.0, macro=macro)
    assert d.tick(100.0, T0).side == "buy"
    assert d.last_fill_snapshot is None and "entry_hold_until_ms" not in d.state()


# --- DB · heartbeat ----------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_engine_memory(monkeypatch):
    eng._drivers.clear()
    eng._MISMATCH_NOTED.clear()
    monkeypatch.setattr(eng, "_loop", None)  # 테스트에서는 heartbeat 와 드라이버가 같은 스레드다
    yield
    eng._drivers.clear()
    eng._MISMATCH_NOTED.clear()


async def _no_loop(live):
    """틱 루프를 끈다 — 테스트가 _tick_once 를 직접 부른다."""


def _session(body=A, **over):
    now = eng._now_iso()
    fields = dict(user_id=1, symbol=body["symbol"], position_side="long", leverage=1, market="spot", status="running",
                  started_at=now, last_heartbeat_at=now, runner_version="8",
                  macro_json=Macro.model_validate(body).model_dump_json())
    fields.update(over)
    with get_session() as db:
        row = RunSession(**fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _cleanup(session_id):
    with get_session() as db:
        for t in (RunnerCommand, RunSessionEvent):
            for r in db.exec(select(t).where(t.session_id == session_id)).all():
                db.delete(r)
        row = db.get(RunSession, session_id)
        if row:
            db.delete(row)
        db.commit()


def _commands(db, sid):
    return db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid).order_by(RunnerCommand.seq)).all()


def _messages(db, sid):
    return [e.message for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)
                                       .order_by(RunSessionEvent.id)).all()]


@pytest.fixture
def live_a(monkeypatch):
    """규칙 A 실행기 세션 + 루프 없는 드라이버. 첫 틱에 바로 산다(비어 있으면 늘 진입)."""
    price = {"now": 100.0}
    monkeypatch.setattr(eng, "get_ticker_price_cached", lambda s, **kw: price["now"])
    monkeypatch.setattr(eng, "_run", _no_loop)
    sid = _session()
    loop = asyncio.new_event_loop()
    assert loop.run_until_complete(eng.start_driver(sid)) is True
    try:
        yield sid, eng._drivers[sid], loop, price
    finally:
        loop.run_until_complete(eng.stop_driver(sid))
        loop.close()
        _cleanup(sid)


def _exchange(sid, acks, *, now_ms=None, in_position=False, position_qty=0.0, previous_qty=0.0, uncertain=False):
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.in_position, row.position_qty, row.position_uncertain = in_position, position_qty, uncertain
        out = eng.exchange_commands(db, row, acks, eng._now_ms() if now_ms is None else now_ms, previous_qty=previous_qty)
        db.add(row)
        db.commit()
        return out


def test_failed_entry_rolls_server_strategy_back_and_backs_off(live_a):
    sid, live, loop, price = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        (cmd,) = _commands(db, sid)
        assert cmd.action == "buy" and json.loads(cmd.rollback_state)["state"]["in_position"] is False
        cmd_id = cmd.id
    before = eng._now_ms()
    assert _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "insufficient_funds"}]) == []
    with get_session() as db:
        cmd = db.get(RunnerCommand, cmd_id)
        assert cmd.status == "failed" and cmd.rollback_state == eng.ROLLED_BACK
        state = json.loads(db.get(RunSession, sid).state_json)
        assert state["in_position"] is False and state["trade_count"] == 0
        assert before + 5 * 60_000 <= state["entry_hold_until_ms"] <= eng._now_ms() + 5 * 60_000
        msgs = _messages(db, sid)
        assert any(m.startswith("⚠ 진입 실패 · insufficient_funds") for m in msgs)
        assert any("서버 전략도 진입 전으로 되돌렸어요" in m and "5분" in m for m in msgs)
    assert live.driver.state()["in_position"] is False and live.driver.trade_count == 0
    loop.run_until_complete(eng._tick_once(live))  # 대기 중 — 새 진입 명령이 없다
    with get_session() as db:
        assert len(_commands(db, sid)) == 1

    # 연속으로 안 나가면 대기가 길어진다: 5 → 15 → 60분.
    for minutes in (15, 60, 60):
        live.driver.hold_entries_until(None)  # 대기가 끝났다고 치고
        loop.run_until_complete(eng._tick_once(live))
        with get_session() as db:
            cmd_id = _commands(db, sid)[-1].id
        _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "insufficient_funds"}])
        with get_session() as db:
            assert f"{minutes}분" in _messages(db, sid)[-1]

    # 진입이 한 번 실제로 체결되면 처음(5분)부터 다시 센다.
    live.driver.hold_entries_until(None)
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        buy_id = _commands(db, sid)[-1].id
    _exchange(sid, [{"command_id": buy_id, "ok": True, "executed_qty": 5.0, "fill_price": 100.0}], in_position=True, position_qty=5.0)
    price["now"] = 104.0  # 익절 3% → 청산 신호
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        sell_id = _commands(db, sid)[-1].id
    _exchange(sid, [{"command_id": sell_id, "ok": True, "executed_qty": 5.0, "fill_price": 104.0}], previous_qty=5.0)
    loop.run_until_complete(eng._tick_once(live))  # 비면 다시 산다
    with get_session() as db:
        again_id = _commands(db, sid)[-1].id
    _exchange(sid, [{"command_id": again_id, "ok": False, "error": "insufficient_funds"}])
    with get_session() as db:
        assert "5분" in _messages(db, sid)[-1]


def test_expired_entry_rolls_back_too(live_a):
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        (cmd,) = _commands(db, sid)
        late = cmd.expires_ms + 1
    _exchange(sid, [], now_ms=late)
    with get_session() as db:
        (cmd,) = _commands(db, sid)
        assert cmd.status == "expired" and cmd.rollback_state == eng.ROLLED_BACK
        assert "실행기에 전달되지 않아" in _messages(db, sid)[-1]
    assert live.driver.state()["in_position"] is False


def test_checkpoint_that_finishes_after_a_rollback_is_rewritten(live_a, monkeypatch):
    """체크포인트 저장 중에 heartbeat 가 되돌리면, 늦게 끝난 저장이 되돌리기 전 상태를 DB 에 남기지 않는다."""
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))  # 매수 → 서버 전략 보유 중
    snap = json.loads(_commands_json(sid)[0])
    real = eng._persist_state
    written = []

    def racing(live_, state_json):
        written.append(json.loads(state_json)["in_position"])
        if len(written) == 1:  # 첫 저장이 도는 사이 heartbeat 가 되돌렸다
            live_.driver.rollback(snap, retry_at_ms=eng._now_ms() + 60_000)
        real(live_, state_json)

    monkeypatch.setattr(eng, "_persist_state", racing)
    live.driver.hold_entries_until(eng._now_ms() + 60_000)  # 이 틱에 새로 사지 않게
    live.last_checkpoint = 0.0
    loop.run_until_complete(eng._tick_once(live))
    assert written == [True, False]
    with get_session() as db:
        assert json.loads(db.get(RunSession, sid).state_json)["in_position"] is False


def _commands_json(sid):
    with get_session() as db:
        return [c.rollback_state for c in _commands(db, sid)]


def test_no_rollback_when_runner_already_holds(live_a):
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        cmd_id = _commands(db, sid)[0].id
    _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "x"}], in_position=True, position_qty=3.0, previous_qty=3.0)
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).rollback_state.startswith("{")  # 쓰지 않았다
        assert "실행기가 이미 포지션을 들고 있어" in _messages(db, sid)[-1]
    assert live.driver.state()["in_position"] is True


def test_no_rollback_when_position_is_uncertain(live_a):
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        cmd_id = _commands(db, sid)[0].id
    _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "x"}], uncertain=True)
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).rollback_state.startswith("{")
        assert not any("되돌" in m for m in _messages(db, sid))  # 실행기가 스스로 '포지션 확인 필요' 로 멈춘다
    assert live.driver.state()["in_position"] is True


def test_later_pending_commands_are_cancelled_with_the_rollback(live_a):
    sid, live, loop, price = live_a
    loop.run_until_complete(eng._tick_once(live))  # #1 buy
    price["now"] = 104.0
    loop.run_until_complete(eng._tick_once(live))  # #2 sell (익절)
    loop.run_until_complete(eng._tick_once(live))  # #3 buy (비면 다시)
    assert live.driver.trade_count == 3
    with get_session() as db:
        ids = [c.id for c in _commands(db, sid)]
    out = _exchange(sid, [{"command_id": ids[0], "ok": False, "error": "insufficient_funds"}])
    assert out == []  # 되돌린 시간선의 명령은 보내지 않는다
    with get_session() as db:
        assert [c.status for c in _commands(db, sid)] == ["failed", "cancelled", "cancelled"]
    assert live.driver.trade_count == 0 and live.driver.state()["in_position"] is False


def test_no_rollback_when_a_later_command_was_executed(live_a):
    sid, live, loop, price = live_a
    loop.run_until_complete(eng._tick_once(live))
    price["now"] = 104.0
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        first, second = _commands(db, sid)
        second.status, second.executed_qty = "acked", 1.0  # 뒤 명령이 이미 체결됐다
        db.add(second)
        db.commit()
        first_id = first.id
    _exchange(sid, [{"command_id": first_id, "ok": False, "error": "x"}])
    with get_session() as db:
        assert "뒤이은 주문이 이미 체결돼" in _messages(db, sid)[-1]
    assert live.driver.trade_count == 2


def test_driver_that_moved_on_is_retried_on_later_heartbeats(live_a):
    """그 순간 드라이버가 다음 체결을 저장하는 중이면 되돌리지 않고, 다음 heartbeat 에 다시 본다."""
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        cmd_id = _commands(db, sid)[0].id
        state_before = db.get(RunSession, sid).state_json
    live.driver.trade_count += 1  # 아직 명령으로 저장 못 한 체결이 있다
    _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "x"}])
    with get_session() as db:
        assert db.get(RunSession, sid).state_json == state_before
        assert not any("되돌" in m for m in _messages(db, sid))  # 아직 포기하지 않았다 — 알리지 않는다
    assert eng._ROLLBACK_RETRY[sid] == {cmd_id: 1}
    live.driver.trade_count -= 1  # 저장이 끝났다(이 테스트에선 체결이 없던 일로)
    _exchange(sid, [])  # 다음 heartbeat — ack 없이도 다시 시도한다
    with get_session() as db:
        assert db.get(RunnerCommand, cmd_id).rollback_state == eng.ROLLED_BACK
    assert sid not in eng._ROLLBACK_RETRY and live.driver.state()["in_position"] is False


def test_retry_gives_up_after_a_few_heartbeats_and_says_so(live_a):
    sid, live, loop, _ = live_a
    loop.run_until_complete(eng._tick_once(live))
    with get_session() as db:
        cmd_id = _commands(db, sid)[0].id
    live.driver.trade_count += 1
    _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "x"}])
    for _ in range(eng.ROLLBACK_RETRY_MAX - 1):
        _exchange(sid, [])
    with get_session() as db:
        assert "다음 신호로 넘어가" in _messages(db, sid)[-1]
    assert sid not in eng._ROLLBACK_RETRY


def _snapshot_json(in_position: bool, qty: float = 0.0) -> str:
    leg = {"symbol": "BTCUSDT", "qty": qty, "dir": 1, "entry_price": 100.0 if in_position else 0.0,
           "last_price": 100.0, "in_position": in_position}
    return json.dumps({"state": {"in_position": in_position, "trade_count": 4, "cooldown_until_ms": None, "legs": [leg]},
                       "equity": 1000.0, "leg_equity": {"BTCUSDT": 1000.0}, "trade_count": 4, "last_fill": None,
                       "entry_returns": {}})


def _insert(sid, action, *, rollback_state="", seq_now=None):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = eng.insert_command(db, row, {"action": action, "notional_frac": 0.5, "qty_frac": 1.0 if action == "sell" else 0.0,
                                           "signal_price": 100.0 + (seq_now or 0), "reason": "t"}, now_ms=eng._now_ms())
        cmd.rollback_state = rollback_state
        db.add(cmd)
        db.commit()
        return cmd.id


def test_failed_commit_after_rollback_is_retried_with_the_same_result(live_a):
    """드라이버를 되돌린 뒤 heartbeat 저장이 실패하면, 실행기가 다시 보낸 같은 ack 로 같은 되돌리기를 마저 기록한다.

    전에는 드라이버 체결 수가 이미 줄어 '다음 신호로 넘어가' 로 거절됐고, 되돌린 시간선의 명령(매도 · 재매수)이
    그대로 나가 실행기만 포지션을 들게 됐다.
    """
    sid, live, loop, price = live_a
    loop.run_until_complete(eng._tick_once(live))  # #1 buy
    price["now"] = 104.0
    loop.run_until_complete(eng._tick_once(live))  # #2 sell (익절)
    loop.run_until_complete(eng._tick_once(live))  # #3 buy (다시)
    with get_session() as db:
        ids = [c.id for c in _commands(db, sid)]
    ack = [{"command_id": ids[0], "ok": False, "error": "insufficient_funds"}]
    with get_session() as db:
        row = db.get(RunSession, sid)
        row.in_position = False
        eng.exchange_commands(db, row, ack, eng._now_ms(), previous_qty=0.0)
        db.rollback()  # 저장 실패(연결 끊김 · 시간 초과)
    assert live.driver.trade_count == 0  # 드라이버는 이미 되돌렸다
    out = _exchange(sid, ack)  # 실행기가 같은 ack 를 다시 보낸다
    assert out == []
    with get_session() as db:
        assert [c.status for c in _commands(db, sid)] == ["failed", "cancelled", "cancelled"]
        assert db.get(RunnerCommand, ids[0]).rollback_state == eng.ROLLED_BACK
        assert json.loads(db.get(RunSession, sid).state_json)["in_position"] is False


def test_expired_reentry_behind_an_unrun_exit_rolls_back(live_a):
    """실행기가 90초 넘게 꺼져 익절 청산 → 재진입이 둘 다 밀렸다. 재진입은 만료되고, 실행기는 청산 전이라 아직 들고 있다.

    그 청산이 앞에 남아 있으니 재진입은 되돌린다 — 안 그러면 청산이 나간 뒤 실행기는 비고 서버만 보유 중이 된다.
    """
    sid, live, loop, price = live_a
    loop.run_until_complete(eng._tick_once(live))  # 매수(P)
    with get_session() as db:
        buy_id = _commands(db, sid)[0].id
    _exchange(sid, [{"command_id": buy_id, "ok": True, "executed_qty": 5.0, "fill_price": 100.0}],
              in_position=True, position_qty=5.0)
    price["now"] = 104.0
    loop.run_until_complete(eng._tick_once(live))  # 익절 매도
    loop.run_until_complete(eng._tick_once(live))  # 바로 재진입
    with get_session() as db:
        sell, again = _commands(db, sid)[1:]
        sell_id, again_id, late = sell.id, again.id, again.expires_ms + 1
    out = _exchange(sid, [], now_ms=late, in_position=True, position_qty=5.0, previous_qty=5.0)
    assert [c["id"] for c in out] == [sell_id]
    with get_session() as db:
        assert db.get(RunnerCommand, again_id).rollback_state == eng.ROLLED_BACK
    assert live.driver.state()["in_position"] is False  # 청산 뒤의 상태 — 실행기도 곧 빈다
    _exchange(sid, [{"command_id": sell_id, "ok": True, "executed_qty": 5.0, "fill_price": 104.0}], previous_qty=5.0)
    assert live.driver.state()["in_position"] is False


def test_exit_behind_a_held_entry_never_jumps_ahead_of_it():
    """[청산, 진입(비어 있던 전략), 청산] — 뒤 청산이 먼저 나가면 실행기는 청산 · 청산 · 진입 순서로 해서 포지션이 남는다."""
    sid = _session()
    try:
        c0 = _insert(sid, "sell", seq_now=1)
        c1 = _insert(sid, "buy", rollback_state=_snapshot_json(False), seq_now=2)
        c2 = _insert(sid, "sell", seq_now=3)
        out = _exchange(sid, [], in_position=True, position_qty=1.0, previous_qty=1.0)
        assert [c["id"] for c in out] == [c0]
        out = _exchange(sid, [{"command_id": c0, "ok": True, "executed_qty": 1.0, "fill_price": 100.0}],
                        in_position=False, position_qty=0.0, previous_qty=1.0)
        assert [c["id"] for c in out] == [c1, c2]  # 서버와 같은 순서 — 실행기도 사고 판 뒤 빈다
    finally:
        _cleanup(sid)


def test_entry_held_on_a_stale_position_lets_the_closing_exit_out_after_it_expires():
    """실행기가 옛 포지션 X 를 들고 있는데 서버는 [진입, 청산] 을 냈다. 진입은 붙잡혀 만료되고(되돌리지 않음 — X 를 닫을
    청산이 그 뒤에 있다), 청산이 나가 X 를 닫는다 — 둘 다 빈다."""
    sid = _session()
    try:
        c1 = _insert(sid, "buy", rollback_state=_snapshot_json(False), seq_now=1)
        c2 = _insert(sid, "sell", seq_now=2)
        assert _exchange(sid, [], in_position=True, position_qty=1.0, previous_qty=1.0) == []
        with get_session() as db:
            late = db.get(RunnerCommand, c1).expires_ms + 1
        out = _exchange(sid, [], now_ms=late, in_position=True, position_qty=1.0, previous_qty=1.0)
        assert [c["id"] for c in out] == [c2]
        with get_session() as db:
            assert db.get(RunnerCommand, c1).rollback_state.startswith("{")
            assert "실행기가 이미 포지션을 들고 있어" in _messages(db, sid)[-1]
    finally:
        _cleanup(sid)


def test_without_a_live_driver_only_state_json_is_rolled_back():
    sid = _session()
    try:
        cmd_id = _insert(sid, "buy", rollback_state=_snapshot_json(False))
        _exchange(sid, [{"command_id": cmd_id, "ok": False, "error": "x"}])
        with get_session() as db:
            state = json.loads(db.get(RunSession, sid).state_json)
            assert state["in_position"] is False and state["trade_count"] == 4 and state["entry_hold_until_ms"] > eng._now_ms()
            assert db.get(RunnerCommand, cmd_id).rollback_state == eng.ROLLED_BACK
    finally:
        _cleanup(sid)


def test_add_on_rolls_back_only_when_runner_quantity_did_not_move():
    sid = _session()
    try:
        moved = _insert(sid, "buy", rollback_state=_snapshot_json(True, qty=2.0), seq_now=1)
        _exchange(sid, [{"command_id": moved, "ok": False, "error": "x"}], in_position=True, position_qty=2.5, previous_qty=2.0)
        with get_session() as db:
            assert db.get(RunnerCommand, moved).rollback_state.startswith("{")
            assert "일부라도 체결됐는지 확인할 수 없어" in _messages(db, sid)[-1]
        same = _insert(sid, "buy", rollback_state=_snapshot_json(True, qty=2.0), seq_now=2)
        _exchange(sid, [{"command_id": same, "ok": False, "error": "x"}], in_position=True, position_qty=2.0, previous_qty=2.0)
        with get_session() as db:
            assert db.get(RunnerCommand, same).rollback_state == eng.ROLLED_BACK
            assert json.loads(db.get(RunSession, sid).state_json)["in_position"] is True  # 추가매수 전(기본 포지션)으로
    finally:
        _cleanup(sid)


def test_delivery_waits_for_exits_and_never_stacks_a_fresh_entry_on_a_held_position():
    sid = _session()
    try:
        sell = _insert(sid, "sell", seq_now=1)
        fresh = _insert(sid, "buy", rollback_state=_snapshot_json(False), seq_now=2)
        with get_session() as db:
            row = db.get(RunSession, sid)
            row.in_position = True
            # 청산 결과를 보기 전에는 뒤의 진입을 보내지 않는다.
            assert [c["id"] for c in eng.pending_commands(db, row, eng._now_ms())] == [sell]
            db.get(RunnerCommand, sell).status = "failed"  # 청산이 끝내 실패했다 — 실행기는 아직 들고 있다
            db.flush()
            assert eng.pending_commands(db, row, eng._now_ms()) == []  # 비어 있던 전략의 진입은 그 위에 얹히지 않게
            row.in_position = False
            assert [c["id"] for c in eng.pending_commands(db, row, eng._now_ms())] == [fresh]
            db.rollback()
        add_on = _insert(sid, "buy", rollback_state=_snapshot_json(True, qty=2.0), seq_now=3)
        legacy = _insert(sid, "buy", seq_now=4)
        with get_session() as db:
            row = db.get(RunSession, sid)
            row.in_position = True
            assert [c["id"] for c in eng.pending_commands(db, row, eng._now_ms())] == [sell]  # 청산 뒤는 모두 기다린다
            db.get(RunnerCommand, sell).status = "acked"
            db.flush()
            # 붙잡힌 진입(fresh) 뒤의 명령은 그 진입을 앞질러 나가지 않는다 — 순서가 바뀌면 실행기 결과가 달라진다.
            assert eng.pending_commands(db, row, eng._now_ms()) == []
            db.get(RunnerCommand, fresh).status = "expired"
            db.flush()
            # 추가매수 · 직전 상태를 모르는 진입은 실행기가 들고 있어도 그대로 보낸다.
            assert [c["id"] for c in eng.pending_commands(db, row, eng._now_ms())] == [add_on, legacy]
            db.rollback()
    finally:
        _cleanup(sid)


def test_late_ok_ack_on_a_cancelled_command_is_recorded_and_flagged():
    sid = _session()
    try:
        cmd_id = _insert(sid, "buy")
        with get_session() as db:
            cmd = db.get(RunnerCommand, cmd_id)
            cmd.status = "cancelled"
            db.add(cmd)
            db.commit()
        _exchange(sid, [{"command_id": cmd_id, "ok": True, "executed_qty": 1.0, "fill_price": 100.0}], in_position=True)
        with get_session() as db:
            assert db.get(RunnerCommand, cmd_id).status == "acked"
            assert "취소한 주문" in _messages(db, sid)[-1]
    finally:
        _cleanup(sid)


# --- heartbeat 스레드 → 루프 스레드 --------------------------------------------
class _ThreadLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        self.loop.close()


def _fake_live(session_id):
    d = _driver(A)
    d.tick(100.0, T0)
    live = eng._Live(session_id, d)
    eng._drivers[session_id] = live
    return live


def test_rollback_runs_on_the_loop_thread(monkeypatch):
    runner = _ThreadLoop()
    monkeypatch.setattr(eng, "_loop", runner.loop)
    try:
        live = _fake_live(991)
        seen = []
        real = live.driver.rollback

        def spy(*args, **kwargs):
            seen.append(threading.get_ident())
            return real(*args, **kwargs)

        live.driver.rollback = spy
        out = eng.rollback_driver(991, 7, live.driver.last_fill_snapshot, expected_trade_count=1,
                                  retry_at_ms=_ms(T0) + 60_000, cancel_ids=[8])
        assert out is not None and json.loads(out[0])["in_position"] is False and out[1] == [8]
        assert seen == [runner.thread.ident]
    finally:
        runner.close()


def test_rollback_gives_up_cleanly_when_the_loop_is_busy(monkeypatch):
    runner = _ThreadLoop()
    monkeypatch.setattr(eng, "_loop", runner.loop)
    monkeypatch.setattr(eng, "ROLLBACK_WAIT_SECONDS", 0.05)
    try:
        live = _fake_live(992)
        runner.loop.call_soon_threadsafe(time.sleep, 0.4)  # 루프가 다른 일로 막혀 있다
        assert eng.rollback_driver(992, 7, live.driver.last_fill_snapshot, expected_trade_count=1, retry_at_ms=1,
                                   cancel_ids=[]) is None
        time.sleep(0.6)  # 루프가 풀린 뒤에도 포기한 되돌리기는 실행되지 않는다(heartbeat 기록과 어긋나지 않게)
        assert live.driver.state()["in_position"] is True
    finally:
        runner.close()
