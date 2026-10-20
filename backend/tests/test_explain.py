"""Rule-based 껄무새 해설 — mood selection, grounded points, determinism, safety."""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.backtest import BacktestResult, run_backtest
from engine.explain import MOODS, Explanation, explain_result
from engine.schema import Macro


def _macro(**kw):
    params = dict(take_profit_pct=5, initial_capital=1_000_000)
    return Macro(symbol=kw.pop("symbol", "BTCUSDT"), rule_type=kw.pop("rule_type", "A"),
                 params=params, **kw)


def _result(**kw) -> BacktestResult:
    base = dict(
        final_return_pct=10.0, win_rate_pct=55.0, mdd_pct=8.0, total_trades=12,
        initial_capital=1_000_000.0, final_equity=1_100_000.0,
        equity_curve=[], buy_hold_return_pct=4.0,
    )
    base.update(kw)
    return BacktestResult(**base)


def test_mood_is_always_in_the_closed_set():
    for r in [
        _result(total_trades=0),
        _result(final_return_pct=-50, liquidation_count=2, liquidated_loss=500_000),
        _result(final_return_pct=-25),
        _result(final_return_pct=-3),
        _result(final_return_pct=6, buy_hold_return_pct=20),
        _result(final_return_pct=40, buy_hold_return_pct=5),
        _result(final_return_pct=8, buy_hold_return_pct=4),
    ]:
        exp = explain_result(_macro(), r)
        assert exp.mood in MOODS


def test_idle_when_no_trades():
    exp = explain_result(_macro(), _result(total_trades=0))
    assert exp.mood == "idle"
    assert "진입" in exp.lesson


def test_liquidation_dominates_mood_and_is_mentioned():
    exp = explain_result(
        _macro(leverage=10),
        _result(final_return_pct=-90, liquidation_count=3, liquidated_loss=300_000, total_trades=5),
    )
    assert exp.mood == "liquidated"
    assert any("청산" in p for p in exp.points)
    assert "레버리지" in exp.lesson


def test_beats_hold_reads_as_win():
    exp = explain_result(_macro(), _result(final_return_pct=15, buy_hold_return_pct=5))
    assert exp.mood == "win"
    assert any("홀딩" in p for p in exp.points)


def test_profit_but_worse_than_hold():
    exp = explain_result(_macro(), _result(final_return_pct=5, buy_hold_return_pct=20))
    assert exp.mood == "lost_to_hold"
    assert any("뒤졌" in p or "홀딩" in p for p in exp.points)


def test_high_streak_surfaces_in_points_and_lesson():
    exp = explain_result(_macro(), _result(final_return_pct=-8, max_consecutive_losses=7))
    assert any("연속" in p for p in exp.points)


def test_explanation_never_gives_advice_language():
    # A few representative results shouldn't produce buy/sell recommendations.
    for r in [_result(final_return_pct=40, buy_hold_return_pct=2), _result(final_return_pct=-30)]:
        exp = explain_result(_macro(), r)
        blob = exp.headline + " " + " ".join(exp.points) + " " + exp.lesson
        for banned in ("사세요", "파세요", "매수하세요", "매도하세요", "추천"):
            assert banned not in blob
        assert "투자 조언이 아닙니다" in exp.disclaimer


def test_deterministic_over_real_backtest():
    macro = Macro(symbol="BTCUSDT", rule_type="A", candle_interval="1d",
                  params=dict(take_profit_pct=5, initial_capital=1_000_000),
                  risk={"stop_loss_pct": 3})
    n = 120
    t = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = 100 + 10 * np.sin(np.arange(n) / 9.0)
    df = pd.DataFrame({"timestamp": t, "open": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close, "volume": np.ones(n)})
    res = run_backtest(macro, df)
    a = explain_result(macro, res)
    b = explain_result(macro, res)
    assert a.model_dump() == b.model_dump()
    assert isinstance(a, Explanation)
    assert len(a.points) >= 1
