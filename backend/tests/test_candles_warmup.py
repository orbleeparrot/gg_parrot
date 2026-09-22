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
