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


# --- 요행인지 꾸준한지를 해설이 말한다 (2026-10-29) -----------------------------
# 물어볼까가 "이미 고점에 올라간 코인" 을 보여준다는 피드백의 답이다. 고점 찍은
# 코인에서는 수익이 한 구간에 쏠리므로, 이 줄이 "지나간 상승에 맞춰진 설정" 이라고
# 스스로 말해 준다. 변명이 아니라 사용자가 판단할 근거고, 지금 화면에는 이 정보가 없다.


def _months(month_ends: list[float]):
    from engine.backtest import EquityPoint
    out = [EquityPoint(t="2026-01-01T12:00:00", equity=100.0)]
    for index, day in enumerate(["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]):
        out.append(EquityPoint(t=f"{day}T12:00:00", equity=month_ends[index]))
    return out


def test_a_few_trades_carrying_the_profit_is_said_out_loud():
    exp = explain_result(_macro(), _result(top_trade_share_pct=87.0))
    assert any("상위" in p and "매매" in p for p in exp.points)


def test_a_spread_out_profit_is_not_nagged_about():
    exp = explain_result(_macro(), _result(top_trade_share_pct=20.0))
    assert not any("상위" in p and "매매" in p for p in exp.points)


def test_profit_packed_into_one_stretch_is_said_out_loud():
    exp = explain_result(_macro(), _result(equity_curve=_months([200.0, 200.0, 200.0, 200.0])))
    assert any("한 구간" in p or "몰렸" in p for p in exp.points)


def test_a_short_window_is_not_accused_of_packing():
    # validation.py 의 기간 하한을 따른다 — 짧은 구간은 몫에 정보가 없다.
    from engine.backtest import EquityPoint
    short = [EquityPoint(t="2026-01-01T12:00:00", equity=100.0),
             EquityPoint(t="2026-01-03T12:00:00", equity=200.0)]
    exp = explain_result(_macro(), _result(equity_curve=short))
    assert not any("한 구간" in p or "몰렸" in p for p in exp.points)
