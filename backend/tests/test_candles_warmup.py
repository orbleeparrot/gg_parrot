"""실봉 어댑터 — 틱은 전략을 돌리지 않고, 봉이 와야 판단한다. 웜업은 지표만 채우고 장부는 비운다."""
from datetime import datetime, timezone

import pytest

from app.engine import Macro
from app.engine.candles import LiveCandleSim
from app.engine.stepper import make_sim

T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _macro(rule, params, **over):
    base = {
        "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"}, "params": {"initial_capital": 1000, **params},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


RSI = _macro("F", {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70})


def _bars(closes, start_ms=1_700_000_000_000, step=300_000):
    return [(start_ms + i * step, c, c, c, c) for i, c in enumerate(closes)]


def test_make_sim_returns_live_candle_sim_for_candle_types():
    assert isinstance(make_sim(RSI, 1000.0), LiveCandleSim)
    assert not isinstance(make_sim(_macro("A", {"take_profit_pct": 2}), 1000.0), LiveCandleSim)


def test_ticks_never_trade_only_candles_do():
    sim = make_sim(RSI, 1000.0)
    for p in [100, 90, 80, 70, 60, 50, 40]:
        assert sim.step(p, T) is None  # 3틱 합성봉이 없다
    assert sim.state()["in_position"] is False
    for c in [100, 105, 102, 106, 60]:  # RSI(2)가 마지막 봉에서야 30 아래로 (그 전엔 진입 없음)
        sim.on_candle(c, c, c, c, T)
    n = sim.on_candle(60, 60, 60, 60, T)  # 직전 봉의 enter 의도가 이 봉 시가에 실행
    assert n == 1 and sim.pending() == 1
    fill = sim.step(60.0, T)
    assert fill is not None and fill.side == "buy" and sim.pending() == 0
    assert sim.step(61.0, T) is None
    assert abs(sim.equity(66.0) - 1000.0 * 66.0 / 60.0) < 1e-6  # 틱은 평가만 갱신


def test_warmup_keeps_indicator_but_resets_book():
    sim = make_sim(RSI, 1000.0)
    sim.warmup(_bars([100, 90, 80, 70, 60, 50, 60, 70, 100, 130, 160]))  # 안에서 매수·매도가 일어났다
    st = sim.state()
    assert st["in_position"] is False and st["qty"] == 0.0
    assert sim.inner.cash == 1000.0 and sim.inner.lots == [] and sim.inner.closed_trades == []
    assert sim.inner.rsi._count > 0  # 지표 상태는 살아 있다
    assert sim.pending() == 0  # 웜업 체결은 큐에 남지 않는다


def test_warmup_then_first_live_candle_can_trade_immediately():
    sim = make_sim(RSI, 1000.0)
    sim.warmup(_bars([100, 105, 102, 106, 103, 50]))  # 마지막 봉에서 RSI(2)<30 → enter 의도 유지
    sim.on_candle(50, 50, 50, 50, T)
    assert sim.step(50.0, T).side == "buy"


@pytest.mark.parametrize("rule,params", [
    ("E", {"trail_percent": 2.0, "activation_profit": 1.0}),
    ("D", {"lower_price": 50, "upper_price": 150, "grid_count": 4}),
    ("H", {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 5, "take_profit": 3}),
    ("I", {"k": 0.5}),
    ("G", {"bb_period": 3, "bb_std": 1.0}),
    ("J", {"fast_period": 2, "slow_period": 3}),
])
def test_every_candle_sim_resets_position_state_after_warmup(rule, params):
    sim = make_sim(_macro(rule, params), 1000.0)
    sim.warmup(_bars([100, 95, 90, 85, 80, 90, 100, 110, 120, 110, 100]))
    inner = sim.inner
    assert inner.state()["in_position"] is False and inner.cash == 1000.0 and inner.stopped is False
    for name in ("_peak", "_armed", "_ref", "_so_done", "_base_price", "_exit_next_open", "_defended"):
        if hasattr(inner, name):
            assert not getattr(inner, name), name
    if hasattr(inner, "holdings"):
        assert inner.holdings == {}


# --- 다음 시가 의도는 봉이 온 뒤 첫 틱에 실행된다(C1) ------------------------------
def test_pending_signal_executes_on_first_tick_not_next_candle():
    """봉 N 마감에 정한 진입은 다음 on_candle(N+1 마감) 이 아니라 그 사이 첫 틱(≈ N+1 시가)에 체결된다."""
    sim = make_sim(RSI, 1000.0)
    for c in [100, 105, 102, 106]:
        sim.on_candle(c, c, c, c, T)
    assert sim.on_candle(60, 60, 60, 60, T) == 0  # 이 봉에서 RSI(2)<30 → enter 의도만
    assert sim.pending() == 0 and sim.inner._pending == "enter"
    fill = sim.step(61.0, T)  # 봉 도착 뒤 첫 틱 — 여기서 체결
    assert fill is not None and fill.side == "buy" and abs(fill.price - 61.0) < 1e-9
    assert sim.inner._pending is None and sim.state()["in_position"] is True
    assert sim.step(62.0, T) is None  # 다음 틱은 아무것도 안 한다
    assert sim.on_candle(62, 62, 62, 62, T) == 0  # 다음 봉이 같은 의도를 다시 체결하지 않는다
    assert abs(sim.state()["qty"] - 1000.0 / 61.0) < 1e-9


def test_pending_exit_executes_on_first_tick():
    sim = make_sim(RSI, 1000.0)
    for c in [100, 105, 102, 106, 60]:
        sim.on_candle(c, c, c, c, T)
    assert sim.step(60.0, T).side == "buy"
    for c in [70, 80, 90, 100]:  # RSI(2) 가 70 위로 가는 첫 봉에서 exit 의도
        assert sim.on_candle(c, c, c, c, T) == 0
        if sim.inner._pending == "exit":
            break
    assert sim.inner._pending == "exit" and sim.pending() == 0
    fill = sim.step(101.0, T)
    assert fill is not None and fill.side == "sell" and abs(fill.price - 101.0) < 1e-9
    assert sim.state()["in_position"] is False and sim.inner._pending is None
    assert sim.on_candle(101, 101, 101, 101, T) == 0


def test_indicator_backtest_path_unchanged_by_execute_pending():
    """백테스트 경로(on_candle 만 연달아)는 예전처럼 다음 봉 시가에 체결한다 — 평가 기준가는 그 봉 종가."""
    from app.engine.candles import make_candle_sim
    inner = make_candle_sim(RSI, 1000.0)
    for c in [100, 105, 102, 106, 60]:
        assert inner.on_candle(c, c, c, c, T) == []
    fills = inner.on_candle(58, 58, 58, 65, T)  # o=58 체결, c=65 로 평가
    assert len(fills) == 1 and fills[0].side == "buy" and abs(fills[0].price - 58.0) < 1e-9
    assert abs(fills[0].equity_after - 1000.0 * 65.0 / 58.0) < 1e-6


def test_breakout_next_open_exit_executes_on_first_tick():
    sim = make_sim(_macro("I", {"k": 0.5, "exit_mode": "next_open"}), 1000.0)
    sim.on_candle(100, 110, 90, 100, T)  # 전일 레인지 20
    n = sim.on_candle(100, 120, 99, 118, T)  # 100 + 0.5*20 = 110 돌파 → 진입, 다음 시가 청산 예약
    assert n == 1 and sim.step(118.0, T).side == "buy"
    assert sim.inner._exit_next_open is True
    fill = sim.step(119.0, T)  # 봉 도착 뒤 첫 틱에 청산
    assert fill is not None and fill.side == "sell" and abs(fill.price - 119.0) < 1e-9
    assert sim.inner._exit_next_open is False and sim.state()["in_position"] is False
    assert sim.step(120.0, T) is None
    assert sim.on_candle(119, 121, 118, 120, T) == 0  # 다음 봉이 다시 청산하지 않는다


def test_step_without_pending_intent_is_a_pure_price_update():
    sim = make_sim(RSI, 1000.0)
    sim.on_candle(100, 100, 100, 100, T)
    assert sim.inner._pending is None
    assert sim.step(50.0, T) is None and sim.state()["in_position"] is False
