"""체결 기록표(runnerfill)에 실행기 보고를 한 번만 넣는다(2026-10-21 체결 기준 장부 과제 8).

ack 의 ``orders`` · 하트비트의 ``local_fills`` 가 들어온다. 응답 유실로 같은 보고가 다시 와도 한 행이고, 부분 청산을
다시 보내 두 번에 나눠 팔면 명령의 체결 수량 · 가격은 두 행의 합 · 가중평균이다(지금은 시도마다 덮어써 앞 체결을 잃는다).
"""
import pytest
from sqlmodel import select

from app import runner_engine as eng
from app import runner_fills
from app.db import RunnerCommand, RunnerFill, RunSession, RunSessionEvent, get_session
from app.engine import Macro
from tests.test_fill_ledger_entries import H_KRW


def _session(policy=eng.FILL_LEDGER_POLICY, version="21") -> int:
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-GTC", status="running", started_at=now, last_heartbeat_at=now,
                         runner_version=version, exec_policy=policy,
                         macro_json=Macro.model_validate(H_KRW).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _cleanup(sid):
    with get_session() as db:
        for model in (RunnerFill, RunnerCommand, RunSessionEvent):
            for r in db.exec(select(model).where(model.session_id == sid)).all():
                db.delete(r)
        db.delete(db.get(RunSession, sid))
        db.commit()


@pytest.fixture
def sid():
    session_id = _session()
    yield session_id
    _cleanup(session_id)


def order(key, qty, price, *, side="buy", fee=2.5, acquired=None, status="FILLED"):
    return {"client_order_id": key, "exchange_order_id": f"ex-{key}", "status": status, "side": side,
            "executed_qty": qty, "acquired_qty": qty if acquired is None else acquired, "fill_price": price,
            "fee_quote": fee, "fee_known": True, "ts_ms": 1_761_000_000_000}


def _command(db, row, action="buy", ref="o1"):
    body = {"action": action, "notional_frac": 0.1 if action == "buy" else 0.0,
            "qty_frac": 1.0 if action == "sell" else 0.0, "signal_price": 500.0, "reason": "진입", "order_ref": ref}
    cmd = eng.insert_command(db, row, body, now_ms=1_000)
    db.commit()
    return cmd


def _fills(db, sid):
    return list(db.exec(select(RunnerFill).where(RunnerFill.session_id == sid).order_by(RunnerFill.id)).all())


def test_ack_orders_are_recorded_once_even_when_the_same_ack_comes_back(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row)
        ack = {"command_id": cmd.id, "ok": True, "executed_qty": 100.0, "fill_price": 500.0,
               "orders": [order("ggp-a", 100.0, 500.0, fee=25.0)]}
        eng.apply_acks(db, row, [ack], now_ms=5_000)
        db.commit()
        eng.apply_acks(db, row, [ack], now_ms=6_000)  # 응답 유실 — 실행기가 같은 ack 를 다시 보낸다
        db.commit()
        rows = _fills(db, sid)
        assert [(r.fill_key, r.command_id, r.source, r.side, r.executed_qty, r.price, r.fee_quote, r.applied_ms)
                for r in rows] == [("ggp-a", cmd.id, "command", "buy", 100.0, 500.0, 25.0, 0)]
        assert rows[0].exchange_order_id == "ex-ggp-a" and rows[0].fee_known is True


def test_partial_exit_retry_keeps_both_fills_and_the_command_totals(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row, action="sell", ref="o2")
        first = {"command_id": cmd.id, "ok": False, "executed_qty": 40.0, "fill_price": 510.0,
                 "outcome": "partially_executed", "error": "일부만 체결",
                 "orders": [order("ggp-s1", 40.0, 510.0, side="sell", status="CANCELED")]}
        eng.apply_acks(db, row, [first], now_ms=5_000)
        db.commit()
        db.refresh(cmd)
        assert cmd.status == "pending" and cmd.executed_qty == 40.0  # 남은 수량을 다시 판다
        eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 60.0, "fill_price": 508.0,
                                  "orders": [order("ggp-s2", 60.0, 508.0, side="sell")]}], now_ms=9_000)
        db.commit()
        db.refresh(cmd)
        assert cmd.status == "acked" and cmd.executed_qty == pytest.approx(100.0)
        assert cmd.fill_price == pytest.approx((40 * 510.0 + 60 * 508.0) / 100)
        assert [r.fill_key for r in _fills(db, sid)] == ["ggp-s1", "ggp-s2"]


def test_no_position_ack_records_nothing(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row, action="sell", ref="o2")
        eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 0.0, "fill_price": 0.0,
                                  "outcome": "no_position_no_order", "orders": []}], now_ms=5_000)
        db.commit()
        assert _fills(db, sid) == []


def test_ack_without_orders_still_books_its_fill_once(sid):
    """레벨 21 실행기는 orders 를 싣는다 — 빠졌어도 ack 의 체결 수량 · 가격으로 한 행을 만든다(수수료는 모름)."""
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row)
        ack = {"command_id": cmd.id, "ok": True, "executed_qty": 100.0, "fill_price": 500.0}
        eng.apply_acks(db, row, [ack], now_ms=5_000)
        db.commit()
        [only] = _fills(db, sid)
        assert only.fill_key.startswith(f"cmd:{cmd.id}:") and only.fee_known is False and only.executed_qty == 100.0


def test_bad_order_facts_are_ignored_with_a_note(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row)
        bad = [order("", 1.0, 500.0), order("ggp-neg", -1.0, 500.0), order("ggp-nan", float("nan"), 500.0),
               {"client_order_id": "ggp-str", "executed_qty": "many", "fill_price": 500.0}, "junk"]
        eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 1.0, "fill_price": 500.0,
                                  "orders": [order("ggp-ok", 1.0, 500.0)] + bad}], now_ms=5_000)  # ack 하나에 5개까지
        db.commit()
        assert [r.fill_key for r in _fills(db, sid)] == ["ggp-ok"]
        names = [e.event_name for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()]
        assert "engine.fill_report_ignored" in names


def test_local_fills_are_recorded_once(sid):
    local = [order("ggp-loc", 100.0, 474.0, side="sell", fee=23.7) | {"reason": "stop_loss"}]
    with get_session() as db:
        row = db.get(RunSession, sid)
        assert runner_fills.record_local_fills(db, row, local, now_ms=5_000) == 1
        assert runner_fills.record_local_fills(db, row, local, now_ms=6_000) == 0
        db.commit()
        [only] = _fills(db, sid)
        assert (only.source, only.command_id, only.side, only.reason) == ("local", None, "sell", "stop_loss")


def test_legacy_session_keeps_overwriting_and_records_nothing():
    legacy = _session(policy="", version="20")
    try:
        with get_session() as db:
            row = db.get(RunSession, legacy)
            cmd = _command(db, row, ref="")
            eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 30.0, "fill_price": 1.01,
                                      "orders": [order("ggp-x", 30.0, 1.01)]}], now_ms=5_000)
            db.commit()
            db.refresh(cmd)
            assert cmd.executed_qty == 30.0 and cmd.fill_price == 1.01 and _fills(db, legacy) == []
    finally:
        _cleanup(legacy)


def test_fill_reported_for_a_cancelled_command_is_booked_with_a_warning(sid):
    with get_session() as db:
        row = db.get(RunSession, sid)
        cmd = _command(db, row)
        cmd.status = "cancelled"
        db.add(cmd)
        db.commit()
        eng.apply_acks(db, row, [{"command_id": cmd.id, "ok": True, "executed_qty": 10.0, "fill_price": 500.0,
                                  "orders": [order("ggp-late", 10.0, 500.0)]}], now_ms=5_000)
        db.commit()
        assert [r.fill_key for r in _fills(db, sid)] == ["ggp-late"]
        late = [e for e in db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)).all()
                if e.event_name == "engine.late_execution_reported"]
        assert late and late[0].severity == "WARN" and "서버 장부에 넣었어요" in late[0].message
