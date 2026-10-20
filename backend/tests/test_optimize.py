"""Parameter sweep (자동 최적화) — grid shape, best pick, and guards."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.schema import Fees, Macro, Period, Risk
from app.optimize import (
    DEFAULT_SL,
    DEFAULT_TP,
    MAX_AXIS,
    SPLIT_RATIO,
    _clean_axis,
    optimize_tp_sl,
    split_period,
)

NO_COST = Fees(commission_pct=0.0, slippage_pct=0.0, funding_pct=0.0)


def _macro(rule_type="A", params=None):
    return Macro(
        symbol="BTCUSDT",
        rule_type=rule_type,
        position_side="long",
        params=params or {"take_profit_pct": 5.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=3.0),
        period=Period(preset="1y"),  # optimize resolves the period (unlike run_backtest)
        fees=NO_COST,
    )


def _df(closes):
    ts = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"timestamp": ts, "open": closes, "high": closes, "low": closes,
         "close": closes, "volume": [0.0] * len(closes)}
    )


# --- axis sanitizer -----------------------------------------------------
def test_clean_axis_dedupes_sorts_and_caps():
    assert _clean_axis([5, 3, 3, 8], DEFAULT_TP) == [3.0, 5.0, 8.0]
    assert _clean_axis(None, DEFAULT_TP) == DEFAULT_TP
    assert _clean_axis([-1, 0], DEFAULT_TP) == DEFAULT_TP  # all invalid -> fallback
    big = list(range(1, 30))
    assert len(_clean_axis(big, DEFAULT_TP)) == MAX_AXIS


# --- sweep over a stubbed data source -----------------------------------
def test_optimize_grid_shape_and_best(monkeypatch):
    closes = [100, 105, 103, 110, 108, 120, 118, 130, 128, 140]
    monkeypatch.setattr("app.optimize.fetch_klines_for_macro", lambda *a, **k: (_df(closes), "cache"))

    res = optimize_tp_sl(_macro(), tp_values=[3, 5, 8], sl_values=[2, 4])
    assert res["tp_values"] == [3.0, 5.0, 8.0]
    assert res["sl_values"] == [2.0, 4.0]
    assert len(res["cells"]) == 6  # 3 tp × 2 sl
    # best is the max-return cell in the grid
    assert res["best"]["final_return_pct"] == max(c["final_return_pct"] for c in res["cells"])
    assert res["current"] == {"tp": 5.0, "sl": 3.0}
    assert "overfit" in res["disclaimer"].lower()


def test_optimize_rejects_unsupported_rule():
    # Rule C (DCA) has no take_profit_pct to sweep.
    m = _macro("C", params={"amount_per_buy": 100.0, "interval_days": 7})
    with pytest.raises(ValueError):
        optimize_tp_sl(m)


# --- overfitting guard: train/test split --------------------------------
def _rising(n):
    """Long enough (and trending) that both halves have trades."""
    out, p = [], 100.0
    for i in range(n):
        p *= 1.03 if i % 2 == 0 else 0.985
        out.append(round(p, 4))
    return out


def test_split_is_chronological_and_disjoint():
    df = _df(_rising(200))
    train, test = split_period(df)
    assert len(train) + len(test) == len(df)
    # test must start strictly after train ends — no peeking, no overlap
    assert train["timestamp"].iloc[-1] < test["timestamp"].iloc[0]
    assert len(train) == int(len(df) * SPLIT_RATIO)


def test_short_period_skips_the_split():
    assert split_period(_df(_rising(20))) == (None, None)


def test_sweep_reports_out_of_sample_for_every_cell(monkeypatch):
    monkeypatch.setattr(
        "app.optimize.fetch_klines_for_macro", lambda *a, **k: (_df(_rising(300)), "cache")
    )
    res = optimize_tp_sl(_macro(), tp_values=[3, 5], sl_values=[2, 4])
    v = res["validation"]
    assert v["split"] is True
    assert v["train_bars"] + v["test_bars"] == 300
    assert v["train_label"] and v["test_label"]
    # Every cell carries a held-out score, not just the winner.
    assert all(c["oos_return_pct"] is not None for c in res["cells"])


def test_best_is_picked_on_training_data_only(monkeypatch):
    """The winner must be the in-sample max even if another cell beat it OOS —
    picking on the held-out data would leak it and defeat the whole guard."""
    monkeypatch.setattr(
        "app.optimize.fetch_klines_for_macro", lambda *a, **k: (_df(_rising(300)), "cache")
    )
    res = optimize_tp_sl(_macro(), tp_values=[3, 5, 8], sl_values=[2, 4])
    cells = res["cells"]
    assert res["best"]["final_return_pct"] == max(c["final_return_pct"] for c in cells)
    # overfit_gap ties the two together: in-sample minus out-of-sample.
    gap = res["validation"]["overfit_gap"]
    assert gap == pytest.approx(
        res["best"]["final_return_pct"] - res["best"]["oos_return_pct"], abs=1e-6
    )


def test_generalization_rate_is_share_of_winners_that_held(monkeypatch):
    monkeypatch.setattr(
        "app.optimize.fetch_klines_for_macro", lambda *a, **k: (_df(_rising(300)), "cache")
    )
    res = optimize_tp_sl(_macro(), tp_values=[3, 5], sl_values=[2, 4])
    cells = res["cells"]
    winners = [c for c in cells if c["final_return_pct"] > 0]
    rate = res["validation"]["generalization_rate"]
    if not winners:
        assert rate is None
    else:
        held = sum(1 for c in winners if (c["oos_return_pct"] or 0) > 0)
        assert rate == pytest.approx(100.0 * held / len(winners), abs=0.1)
        assert 0.0 <= rate <= 100.0


def test_unsplittable_period_is_flagged_not_silently_trusted(monkeypatch):
    """A short period can't be validated — the payload must say so rather than
    presenting a fitted number as if it had been checked."""
    monkeypatch.setattr(
        "app.optimize.fetch_klines_for_macro", lambda *a, **k: (_df(_rising(20)), "cache")
    )
    res = optimize_tp_sl(_macro(), tp_values=[3], sl_values=[2])
    v = res["validation"]
    assert v["split"] is False
    assert v["generalization_rate"] is None and v["overfit_gap"] is None
    assert all(c["oos_return_pct"] is None for c in res["cells"])
    assert "과최적화" in v["note"]
