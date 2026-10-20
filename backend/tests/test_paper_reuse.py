"""Contract test: paper trading and the backtest share the SAME execution.

Driving the shared PositionSim/DcaSim over a price series tick-by-tick must
reproduce the backtest's equity curve exactly (no duplicated / drifting logic).
"""
from __future__ import annotations

from engine import Macro, run_backtest
from engine.schema import Fees, Period, Risk
from engine.stepper import make_sim
from tests.test_engine import make_df, NO_COST


def _macro(rule_type, side, params, risk=None):
    return Macro(
        symbol="BTCUSDT",
        rule_type=rule_type,
        position_side=side,
        params=params,
        risk=risk or Risk(invest_ratio=1.0, stop_loss_pct=None),
        period=Period(preset="custom"),
        fees=Fees(commission_pct=0.1, slippage_pct=0.05, funding_pct=0.0),
    )


def _drive(macro, closes):
    """Feed prices tick-by-tick, marking equity each step (as paper trading does)."""
    sim = make_sim(macro)
    curve = []
    for c in closes:
        sim.step(c)
        curve.append(round(sim.equity(c), 4))
    return curve


def test_paper_sim_matches_backtest_a_long():
    closes = [100, 103, 98, 106, 101, 110, 107, 112]
    m = _macro("A", "long", {"take_profit_pct": 3.0, "initial_capital": 1000.0},
               risk=Risk(invest_ratio=0.7, stop_loss_pct=4.0))
    bt = run_backtest(m, make_df(closes))
    paper_curve = _drive(m, closes)
    assert [p.equity for p in bt.equity_curve] == paper_curve


def test_paper_sim_matches_backtest_b_short():
    closes = [95, 112, 98, 120, 90, 130]
    m = _macro("B", "short", {"buy_price": 100, "sell_price": 110, "initial_capital": 1000.0},
               risk=Risk(invest_ratio=1.0, stop_loss_pct=50.0))
    bt = run_backtest(m, make_df(closes))
    paper_curve = _drive(m, closes)
    assert [p.equity for p in bt.equity_curve] == paper_curve
