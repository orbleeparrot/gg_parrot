"""체결 기준 장부 — 진입 체결 반영(2026-10-21 설계 §5).

정책 세션의 서버 전략은 진입마다 주문 번호를 매기고, 실행기가 보고한 실제 체결(수량 · 평균가 · 수수료)로 그 주문의
가상 롯을 바꾼다. 세션 321 처럼 가상 평단 426 · 실제 427 이면 익절선은 427 기준이어야 한다.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app import runner_engine as eng
from app.db import RunnerCommand, RunSession, get_session
from app.engine import Macro

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
H_KRW = {"symbol": "KRW-GTC", "exchange": "upbit", "rule_type": "H", "position_side": "long", "market": "spot",
         "leverage": 1, "candle_interval": "15m", "period": {"preset": "1w"},
         "params": {"base_order_size": 50000, "safety_order_size": 50000, "price_deviation": 1.0, "take_profit": 2.0,
                    "max_safety_orders": 3, "initial_capital": 300000},
         "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.05, "slippage_pct": 0.0}}
H_USDT = {**H_KRW, "symbol": "BTCUSDT", "exchange": "binance",
          "params": {**H_KRW["params"], "base_order_size": 100, "safety_order_size": 100, "initial_capital": 1000},
          "fees": {"commission_pct": 0.1, "slippage_pct": 0.0}}
RSI = {"symbol": "ONEUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
       "candle_interval": "5m", "period": {"preset": "1w"},
       "params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": 32},
       "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0.1, "slippage_pct": 0.05}}
# 실행기는 수수료 포함 금액 50,000원 → 주문 49,975.01원(업비트 0.05%) → 427원에 117.037…개 · 수수료 24.99원
ACTUAL_QTY = 50000 / 1.0005 / 427


def driver(body=H_KRW, ledger=True):
    macro = Macro.model_validate(body)
    d = eng.build_driver(macro, macro.symbol)
    if ledger:
        d.enable_fill_ledger()
    return d


def sim_of(d):
    return d.legs[0].sim.inner


def bar(d, i, o, h, l, c):
    """15분 봉 i 번째가 마감 — 나온 체결을 틱으로 모두 꺼낸다(서버 루프와 같은 길)."""
    t = T0 + timedelta(minutes=15 * i)
    d.push_candle(d.symbol, (int(t.timestamp() * 1000), o, h, l, c))
    fills = []
    while (f := d.tick(c, t + timedelta(minutes=15, seconds=1))) is not None:
        fills.append(f)
    return fills


def buy(key, ref, qty, price, fee, acquired=None):
    return {"key": key, "ref": ref, "side": "buy", "executed_qty": qty,
            "acquired_qty": qty if acquired is None else acquired, "price": price, "fee_quote": fee,
            "fee_known": True, "source": "command"}


def done(ref, status="acked"):
    return {"ref": ref, "status": status}


def test_base_buy_gets_an_order_number_and_a_virtual_order():
    d = driver()
    [f] = bar(d, 0, 426, 427, 425, 426)
    assert f.side == "buy" and f.order_ref == "o1" and f.price == 426
    order = sim_of(d).ledger.orders["o1"]
    assert order["kind"] == "base" and order["price"] == 426 and order["margin"] == pytest.approx(50000)
    assert eng.command_from_fill(f, d.initial)["order_ref"] == "o1"


def test_actual_fill_replaces_the_virtual_lot_and_moves_the_take_profit_line():
    d = driver()
    bar(d, 0, 426, 427, 425, 426)
    sim = sim_of(d)
    result = d.fold(fills=[buy("ggp-1", "o1", ACTUAL_QTY, 427.0, 24.99)], resolutions=[done("o1")])
    assert result["applied"] == ["ggp-1"] and result["resolved"] == ["o1"]
    assert sim.avg_entry() == pytest.approx(427.0) and sim._base_price == pytest.approx(427.0)
    assert sim.total_qty() == pytest.approx(ACTUAL_QTY)
    assert sim.cash == pytest.approx(300000 - ACTUAL_QTY * 427.0 - 24.99)
    assert sim.equity(427.0) == pytest.approx(300000 - 24.99)  # 427 에서 평가하면 수수료만큼 줄었다
    # 가상 평단(426)이면 익절선 434.52 — 고가 435 는 익절이었다. 실제 평단 427 의 익절선은 435.54.
    assert bar(d, 1, 427, 435.0, 426, 430) == []
    [tp] = bar(d, 2, 430, 436, 429, 433)
    assert tp.side == "sell" and tp.price == pytest.approx(427.0 * 1.02)


def test_same_fill_key_is_folded_once():
    d = driver()
    bar(d, 0, 426, 427, 425, 426)
    sim = sim_of(d)
    d.fold(fills=[buy("ggp-1", "o1", ACTUAL_QTY, 427.0, 24.99)])
    cash, qty = sim.cash, sim.total_qty()
    again = d.fold(fills=[buy("ggp-1", "o1", ACTUAL_QTY, 427.0, 24.99)])
    assert again["applied"] == [] and sim.cash == cash and sim.total_qty() == qty


def test_fee_taken_in_coin_is_not_taken_from_cash_again():
    """바이낸스 현물: 1.0 개를 사고 0.001 개를 수수료로 떼어 0.999 개만 받는다."""
    d = driver(H_USDT)
    bar(d, 0, 100, 101, 99, 100)
    sim = sim_of(d)
    d.fold(fills=[buy("ggp-b", "o1", 1.0, 100.0, 0.1, acquired=0.999)])
    assert sim.total_qty() == pytest.approx(0.999) and sim.avg_entry() == pytest.approx(100.0)
    assert sim.equity(100.0) == pytest.approx(1000 - 0.1)


def test_ledger_survives_a_checkpoint_and_restore():
    d = driver()
    bar(d, 0, 426, 427, 425, 426)
    d.fold(fills=[buy("ggp-1", "o1", ACTUAL_QTY, 427.0, 24.99)], resolutions=[done("o1")])
    [so] = bar(d, 1, 427, 428, 422.0, 423)  # 1차 물타기선 427 × 0.99 = 422.73 — 아직 보고 전
    assert so.order_ref == "o2" and so.price == pytest.approx(422.73)
    state = json.loads(json.dumps(d.state()))
    before = sim_of(d)
    again = driver()
    again.restore(state, leg_equity={}, total_equity=before.equity(423))
    after = sim_of(again)
    assert [(lot.ref, lot.qty, lot.fill) for lot in after.lots] == [(lot.ref, lot.qty, lot.fill) for lot in before.lots]
    assert after.ledger.orders == before.ledger.orders and after.ledger.keys == ["ggp-1"] and after.ledger.seq == 2
    assert after._base_price == pytest.approx(427.0) and after._so_done == 1
    assert after.cash == pytest.approx(before.cash)
    cash = after.cash
    again.fold(fills=[buy("ggp-1", "o1", ACTUAL_QTY, 427.0, 24.99)])  # 이미 접은 키
    assert after.cash == cash


def test_ledger_off_keeps_the_current_shape():
    d = driver(ledger=False)
    [f] = bar(d, 0, 426, 427, 425, 426)
    assert f.order_ref == "" and "ledger" not in d.state()["legs"][0]["strategy"]
    assert eng.command_from_fill(f, d.initial).get("order_ref", "") == ""


def test_fill_ledger_is_only_for_long_spot_martingale():
    with pytest.raises(ValueError):
        driver(RSI)
    with pytest.raises(ValueError):
        driver({**H_USDT, "market": "futures", "leverage": 2})


def _session_row() -> int:
    now = eng._now_iso()
    with get_session() as db:
        row = RunSession(user_id=1, symbol="KRW-GTC", status="running", started_at=now, last_heartbeat_at=now,
                         runner_version="21", exec_policy=eng.FILL_LEDGER_POLICY,
                         macro_json=Macro.model_validate(H_KRW).model_dump_json())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_command_row_keeps_the_order_number_and_duplicate_check_respects_it():
    """잔예산 재매수는 같은 가격선 · 60초 안에 나올 수 있다 — 번호가 다르면 중복이 아니다(삼키면 주문이 영영 미결)."""
    sid = _session_row()
    now = eng._now_ms()
    cmd = {"action": "buy", "notional_frac": 0.1, "qty_frac": 0.0, "signal_price": 422.73, "reason": "물타기"}
    try:
        with get_session() as db:
            row = db.get(RunSession, sid)
            a, made_a = eng.upsert_command(db, row, {**cmd, "order_ref": "o2"}, now)
            b, made_b = eng.upsert_command(db, row, {**cmd, "order_ref": "o3"}, now + 1000)
            c, made_c = eng.upsert_command(db, row, {**cmd, "order_ref": "o3"}, now + 2000)
            assert made_a and made_b and not made_c and c.id == b.id
            assert (a.order_ref, b.order_ref) == ("o2", "o3")
            old = {**cmd, "signal_price": 500.0}
            _, made_old = eng.upsert_command(db, row, old, now + 3000)
            _, made_dup = eng.upsert_command(db, row, old, now + 4000)
            assert made_old and not made_dup  # 번호 없는 옛 명령은 지금처럼 같은 신호 중복
            db.commit()
    finally:
        with get_session() as db:
            for c in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == sid)).all():
                db.delete(c)
            db.delete(db.get(RunSession, sid))
            db.commit()
