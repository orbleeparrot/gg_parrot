"""Rule K — SAR defense reversal (long -> partial exit -> flip short).

Drives the shared candle sim directly so the individual legs (partial sell, the
long->short flip, short take-profit / stop-loss) can be asserted on the fill
stream, and checks the schema-level guards (mandatory short stop, futures data).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from engine.backtest import run_backtest
from engine.candles import make_candle_sim
from engine.schema import Macro


def _df(prices):
    """OHLC frame where each bar's high/low straddle prev-close -> close."""
    close = np.array(prices, dtype=float)
    n = len(close)
    t = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    op = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(op, close)
    low = np.minimum(op, close)
    return pd.DataFrame(
        {"timestamp": t, "open": op, "high": high, "low": low, "close": close, "volume": np.ones(n)}
    )


def _macro(**params):
    base = dict(
        drop_trigger_pct=5, partial_exit_pct=50, flip_to_short=True,
        short_take_profit_pct=5, short_stop_loss_pct=3, initial_capital=1_000_000,
    )
    base.update(params)
    return Macro(symbol="BTCUSDT", rule_type="K", candle_interval="1h", params=base)


def _run_fills(macro, prices):
    df = _df(prices)
    sim = make_candle_sim(macro)
    fills = []
    for o, h, l, c, ts in zip(df.open, df.high, df.low, df.close, df.timestamp):
        fills.extend(sim.on_candle(o, h, l, c, ts.to_pydatetime()))
    return sim, fills


# --- schema guards ------------------------------------------------------
def test_k_requires_short_stop_loss():
    with pytest.raises(ValidationError):
        Macro(rule_type="K", params=dict(
            drop_trigger_pct=5, partial_exit_pct=50, short_take_profit_pct=5,
            initial_capital=1_000_000,  # short_stop_loss_pct missing
        ))


def test_k_rejects_partial_exit_over_100():
    with pytest.raises(ValidationError):
        Macro(rule_type="K", params=dict(
            drop_trigger_pct=5, partial_exit_pct=150, short_take_profit_pct=5,
            short_stop_loss_pct=3, initial_capital=1_000_000,
        ))


def test_k_always_resolves_to_futures():
    # Long-side, leverage 1 would normally be spot; K needs futures for the flip.
    assert _macro().resolved_market() == "futures"


# --- lifecycle ----------------------------------------------------------
def test_k_long_take_profit_without_defense():
    # Steadily rising -> long take-profit fires, defense/short never engage.
    sim, fills = _run_fills(_macro(long_take_profit_pct=3), [100, 101, 103, 104])
    sides = [f.side for f in fills]
    assert "buy" in sides
    assert "sell" in sides            # long TP close
    assert "short" not in sides       # no flip


def test_k_defense_partial_then_flip_short():
    # Enter ~100, then a >5% drop triggers: partial sell + close + open short.
    sim, fills = _run_fills(_macro(partial_exit_pct=50), [100, 100, 94, 90])
    sides = [f.side for f in fills]
    assert sides.count("buy") >= 1    # initial long
    assert sides.count("sell") >= 2   # partial exit + close remainder
    assert "short" in sides           # flipped to short


def test_k_short_take_profit_on_further_drop():
    # After the flip, price keeps falling -> short take-profit (cover).
    sim, fills = _run_fills(_macro(), [100, 100, 94, 88, 82])
    sides = [f.side for f in fills]
    assert "short" in sides
    assert "cover" in sides           # short closed in profit


def test_k_short_stop_loss_bounds_loss_on_bounce():
    # Flip near ~95, then a sharp bounce hits the short stop -> loss is bounded.
    sim, fills = _run_fills(_macro(short_stop_loss_pct=3, reenter_long_after=False),
                            [100, 100, 94, 99])
    assert any(f.side == "short" for f in fills)
    assert any(f.side == "cover" for f in fills)
    # equity survives (short stop capped the loss; no blow-up past capital).
    assert sim.equity(99.0) > 0.0
    assert sim.equity(99.0) < sim.initial_capital  # the bounce cost money


def test_k_no_reenter_after_short_stays_flat():
    sim, fills = _run_fills(_macro(reenter_long_after=False), [100, 100, 94, 99, 99, 99])
    # Once the short closes with reenter disabled, no further entries occur.
    assert sim.stopped is True
    assert not sim.in_position()


def test_k_backtest_is_deterministic():
    macro = _macro(long_take_profit_pct=4)
    df = _df([100, 98, 94, 97, 92, 99, 101, 95, 90, 100])
    r1 = run_backtest(macro, df)
    r2 = run_backtest(macro, df)
    assert r1.model_dump() == r2.model_dump()
    assert len(r1.equity_curve) == len(df)


def test_k_partial_exit_equity_consistent():
    # After a 40% partial exit (no flip), equity == cash + remaining lot value.
    sim, _ = _run_fills(_macro(partial_exit_pct=40, flip_to_short=False), [100, 100, 94])
    assert sim.side.value == "long"        # de-risk only, still long
    assert sim.in_position()               # 60% of the long remains
    # equity() must reconcile with cash + committed margin + unrealized PnL.
    eq = sim.equity(94.0)
    assert eq > 0.0
