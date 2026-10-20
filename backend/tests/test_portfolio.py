"""Multi-symbol (portfolio) backtest: schema, aggregation, per-symbol breakdown."""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.backtest import run_backtest
from engine import portfolio
from engine.schema import Macro


def _df(prices):
    close = np.array(prices, dtype=float)
    n = len(close)
    t = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    op = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"timestamp": t, "open": op, "high": close * 1.01,
                         "low": close * 0.99, "close": close, "volume": np.ones(n)})


def _macro(symbols):
    return Macro(symbol=symbols[0], symbols=symbols, rule_type="A", candle_interval="1d",
                 params=dict(take_profit_pct=5, initial_capital=1_000_000),
                 risk={"stop_loss_pct": 3})


def test_symbols_normalized_and_flagged():
    m = _macro(["btcusdt", "ETHUSDT", "btcusdt"])  # lower + dup
    assert m.symbol == "BTCUSDT"
    assert m.symbols == ["BTCUSDT", "ETHUSDT"]  # deduped, upper
    assert m.is_portfolio() is True
    assert m.all_symbols() == ["BTCUSDT", "ETHUSDT"]


def test_single_symbol_not_portfolio():
    m = Macro(symbol="BTCUSDT", symbols=["BTCUSDT"], rule_type="A",
              params=dict(take_profit_pct=5, initial_capital=1_000_000))
    assert m.is_portfolio() is False
    assert m.symbols is None


def test_for_symbol_recapitalizes():
    m = _macro(["BTCUSDT", "ETHUSDT"])
    leg = m.for_symbol("ETHUSDT", 500_000)
    assert leg.symbol == "ETHUSDT"
    assert leg.symbols is None
    assert leg.params["initial_capital"] == 500_000


def test_aggregate_sums_capital_and_breaks_down_by_symbol():
    m = _macro(["BTCUSDT", "ETHUSDT"])
    rising = _df([100, 102, 104, 106, 108, 110])
    falling = _df([100, 98, 96, 94, 92, 90])
    r_btc = run_backtest(m.for_symbol("BTCUSDT", 500_000), rising)
    r_eth = run_backtest(m.for_symbol("ETHUSDT", 500_000), falling)

    agg, per = portfolio.aggregate([("BTCUSDT", r_btc), ("ETHUSDT", r_eth)], candle_interval="1d")

    # aggregated capital is the sum of the legs
    assert abs(agg.initial_capital - 1_000_000) < 1
    assert len(agg.equity_curve) == len(rising)
    # per-symbol breakdown carries each leg
    assert {p["symbol"] for p in per} == {"BTCUSDT", "ETHUSDT"}
    assert len(per) == 2
    # combined final equity == sum of the legs' final equity
    assert abs(agg.final_equity - (r_btc.final_equity + r_eth.final_equity)) < 1.0
