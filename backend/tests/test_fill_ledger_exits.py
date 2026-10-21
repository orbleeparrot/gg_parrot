"""체결 기준 장부 — 청산 대기 · 체결만큼 줄이기 · 먼지(2026-10-21 설계 §5 · §9 결정 2 · 3).

정책 세션의 청산 신호는 장부를 바꾸지 않는다('청산 대기'). 보고된 체결량만큼 줄이고, 다 팔리면(먼지만 남으면) 그때
포지션 끝 처리(차수 초기화 · 손절 대기)를 한다. 0 체결로 끝나면 대기만 풀린다 — 매도 전 상태로 통째로 되돌릴 일이 없다.
"""
from datetime import timedelta

import pytest

from app import runner_engine as eng
from tests.test_fill_ledger_entries import H_KRW, T0, bar, buy, done, driver, sim_of

STOP = {**H_KRW, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 5.0, "cooldown_minutes": 30}}


def sell(key, ref, qty, price, fee, *, source="command", reason="", ts_ms=0):
    return {"key": key, "ref": ref, "side": "sell", "executed_qty": qty, "acquired_qty": qty, "price": price,
            "fee_quote": fee, "fee_known": True, "source": source, "reason": reason, "ts_ms": ts_ms}


def holding(body=H_KRW):
    """기본 매수 50,000원을 500원에 100개 실제로 산 드라이버."""
    d = driver(body)
    bar(d, 0, 500, 501, 499, 500)
    d.fold(fills=[buy("b1", "o1", 100.0, 500.0, 25.0)], resolutions=[done("o1")])
    return d, sim_of(d)


def test_take_profit_signal_leaves_the_book_alone_and_asks_to_sell_everything():
    d, sim = holding()
    [tp] = bar(d, 1, 500, 511, 499, 505)  # 익절선 510
    assert tp.side == "sell" and tp.order_ref == "o2" and tp.price == pytest.approx(510.0)
    assert tp.qty == pytest.approx(100.0) and tp.qty_before == pytest.approx(100.0)
    assert eng.command_from_fill(tp, d.initial)["qty_frac"] == 1.0
    assert sim.total_qty() == pytest.approx(100.0) and sim.in_position() and sim.ledger.exit == "o2"
    assert sim._close_all(510, 510, is_stop=False, ts=T0) is None  # 청산 대기 중 두 번째 청산 신호는 없다


def test_partial_exit_keeps_the_rest_and_a_failed_exit_only_clears_the_wait():
    d, sim = holding()
    bar(d, 1, 500, 511, 499, 505)
    d.fold(fills=[sell("s1", "o2", 40.0, 510.0, 10.2)])  # 10 중 4 체결 — 명령은 남은 수량으로 다시 나간다
    assert sim.total_qty() == pytest.approx(60.0) and sim.avg_entry() == pytest.approx(500.0)
    assert sim.ledger.exit == "o2" and sim.in_position()
    d.fold(resolutions=[done("o2", "failed")])  # 남은 수량은 끝내 안 팔렸다
    assert sim.ledger.exit is None and sim.total_qty() == pytest.approx(60.0) and sim._so_done == 0


def test_full_exit_ends_the_position_with_the_actual_price():
    d, sim = holding()
    bar(d, 1, 500, 511, 499, 505)
    cash = sim.cash
    result = d.fold(fills=[sell("s1", "o2", 100.0, 508.0, 25.4)], resolutions=[done("o2")])
    assert not sim.in_position() and sim.lots == [] and sim.ledger.dust is None
    assert sim.cash == pytest.approx(cash + 100 * 508.0 - 25.4)
    assert sim.closed_trades[-1] == pytest.approx(100 * (508.0 - 500.0) - 25.4)
    assert sim.ledger.exit is None and sim.ledger.orders == {} and sim.ledger.epoch == 1 and sim._so_done == 0
    assert result["events"][-1]["kind"] == "exit_filled" and result["events"][-1]["ended"] is True


def test_unsellable_rest_is_kept_as_dust_and_joins_the_next_position():
    d, sim = holding()
    bar(d, 1, 500, 511, 499, 505)
    d.fold(fills=[sell("s1", "o2", 99.99, 510.0, 25.5)], resolutions=[done("o2")])
    assert not sim.in_position() and sim.ledger.dust["qty"] == pytest.approx(0.01)  # 0.01 × 510 = 5.1원 < 5,100원
    cash = sim.cash
    assert sim.equity(520.0) == pytest.approx(cash + 0.01 * 520.0)  # 먼지도 자산이다
    [base] = bar(d, 2, 505, 506, 504, 505)  # 먼지는 무포지션으로 본다 — 다시 산다
    assert base.side == "buy" and base.order_ref == "o3"
    d.fold(fills=[buy("b2", "o3", 99.0, 505.0, 25.0)], resolutions=[done("o3")])
    assert sim.total_qty() == pytest.approx(99.01)
    assert sim.avg_entry() == pytest.approx((0.01 * 500.0 + 99.0 * 505.0) / 99.01)
    assert sim._base_price == pytest.approx(505.0) and sim.ledger.dust is None  # 물타기 기준은 새 첫 매수가


def test_stop_exit_starts_the_cooldown_from_the_signal_bar_only_when_it_fills():
    d, sim = holding(STOP)
    [stop] = bar(d, 1, 500, 501, 474.0, 476)  # 손절선 500 × 0.95 = 475
    assert stop.reason == "손절" and sim.ledger.orders[stop.order_ref]["is_stop"] is True
    d.fold(resolutions=[done(stop.order_ref, "failed")])  # 안 팔렸다 — 손절 대기도 없다
    assert sim._cooldown_until is None and sim.in_position()
    [again] = bar(d, 2, 476, 477, 470.0, 471)
    d.fold(fills=[sell("s2", again.order_ref, 100.0, 474.0, 23.7)], resolutions=[done(again.order_ref)])
    assert not sim.in_position()
    assert sim._cooldown_until == T0 + timedelta(minutes=30) + timedelta(minutes=30)  # 2번 봉(30분) + 30분


def test_runner_local_stop_loss_is_booked_and_ends_the_position():
    d, sim = holding(STOP)
    ts = int((T0 + timedelta(minutes=20)).timestamp() * 1000)
    result = d.fold(fills=[sell("loc1", "", 100.0, 474.5, 23.7, source="local", reason="stop_loss", ts_ms=ts)])
    assert not sim.in_position() and result["events"][-1]["ended"] is True
    assert sim._cooldown_until == T0 + timedelta(minutes=50)  # 실행기 체결 시각 + 30분


def test_dust_does_not_reset_the_daily_halt():
    d, sim = holding()
    sim._halted_day = sim._day
    bar(d, 1, 500, 511, 499, 505)
    d.fold(fills=[sell("s1", "o2", 99.99, 510.0, 25.5)], resolutions=[done("o2")])
    assert sim._halted_day == sim._day and sim.ledger.dust is not None


def test_ledger_book_refuses_liquidation_and_partial_reduce():
    d, sim = holding()
    with pytest.raises(RuntimeError):
        sim._liquidate_all(400.0, 400.0, T0)
    with pytest.raises(RuntimeError):
        sim._reduce_long(0.5, 400.0, 400.0, T0)
