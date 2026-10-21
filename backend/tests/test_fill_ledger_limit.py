"""체결 기준 장부 2단계 — 익절 · 물타기는 지정가 IOC(2026-10-21 설계 §4 · §6).

지정가형 가격선(익절 · 물타기)은 그 가격보다 불리하게 체결되면 안 된다 — 봉 꼬리만 익절선에 닿고 내려온 뒤 시장가로
팔아 손해 보던 일(세션 321)을 막는다. 스탑형(손절 · 강제 정리)과 첫 매수(봉 종가 진입)는 시장가 그대로다.
"""
import pytest
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, get_session
from app.engine import Macro
from tests.test_fill_ledger_entries import H_KRW, bar, buy, done, driver, sim_of
from tests.test_fill_ledger_exits import STOP, holding

HOLD = {**H_KRW, "risk": {"invest_ratio": 1.0, "max_holding_hours": 1}}


def cmd(d, fill):
    return eng.command_from_fill(fill, d.initial)


def test_take_profit_is_a_limit_ioc_at_the_take_profit_line():
    d, sim = holding()
    [tp] = bar(d, 1, 500, 511, 499, 505)
    assert (tp.order_type, tp.limit_price) == ("limit_ioc", pytest.approx(510.0))
    assert cmd(d, tp)["order_type"] == "limit_ioc" and cmd(d, tp)["limit_price"] == pytest.approx(510.0)
    assert sim.ledger.orders[tp.order_ref]["order_type"] == "limit_ioc"


def test_safety_orders_and_reopened_tiers_are_limit_ioc_at_their_line():
    d, sim = holding()
    [so] = bar(d, 1, 500, 500, 494.0, 495)
    assert (so.order_type, so.limit_price) == ("limit_ioc", pytest.approx(495.0))
    d.fold(resolutions=[done(so.order_ref, "acked")])  # IOC 무체결 — 그 차수를 다시 연다
    [again] = bar(d, 2, 495, 496, 494.5, 495)
    assert again.order_ref != so.order_ref and (again.order_type, again.limit_price) == ("limit_ioc", pytest.approx(495.0))


def test_first_buy_stop_loss_and_forced_exits_stay_market():
    d = driver()
    [base] = bar(d, 0, 500, 501, 499, 500)
    assert base.order_type == "" and cmd(d, base)["order_type"] == ""
    d, sim = holding(STOP)
    [stop] = bar(d, 1, 500, 501, 474.0, 476)
    assert stop.reason == "손절" and stop.order_type == "" and stop.limit_price == 0.0
    d, sim = holding(HOLD)
    fills = []
    for i in range(1, 5):  # 0번 봉에 샀다 — 4번 봉(60분)에 최대 보유 1시간이 찬다
        fills += bar(d, i, 500, 501, 499, 500)
    forced = [f for f in fills if f.side == "sell"]
    assert forced and forced[0].order_type == ""  # 최대 보유 시간 정리 — 시장가


def test_legacy_sim_never_asks_for_limit_orders():
    d = driver(ledger=False)
    bar(d, 0, 500, 501, 499, 500)
    [tp] = bar(d, 1, 500, 511, 499, 505)
    assert tp.order_type == "" and cmd(d, tp)["order_type"] == ""


def _row() -> int:
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-GTC", status="running", started_at=now, last_heartbeat_at=now,
                         runner_version="21", exec_policy=eng.FILL_LEDGER_POLICY,
                         macro_json=Macro.model_validate(H_KRW).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_command_row_and_runner_view_carry_the_limit_only_when_set():
    sid = _row()
    base = {"notional_frac": 0.0, "qty_frac": 1.0, "signal_price": 510.0, "reason": "청산", "order_ref": "o2"}
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            limit = eng.insert_command(db, row, {**base, "action": "sell", "order_type": "limit_ioc",
                                                 "limit_price": 510.0}, now_ms=1_000)
            market = eng.insert_command(db, row, {**base, "action": "sell", "signal_price": 475.0, "order_ref": "o3"},
                                        now_ms=2_000)
            db.commit()
            assert (limit.order_type, limit.limit_price) == ("limit_ioc", 510.0)
            assert eng._view(limit)["order_type"] == "limit_ioc" and eng._view(limit)["limit_price"] == 510.0
            assert "order_type" not in eng._view(market) and "limit_price" not in eng._view(market)
    finally:
        with get_session() as db:
            for c in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all():
                db.delete(c)
            db.delete(db.get(RunSession, sid))
            db.commit()
