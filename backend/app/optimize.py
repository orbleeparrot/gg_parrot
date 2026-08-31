"""Parameter sweep (자동 최적화) over take-profit × stop-loss.

Runs the deterministic backtest across a grid of (take_profit_pct, stop_loss_pct)
combinations on the SAME fetched candles (fetched once, then reused for every
cell), so a user can see which region of the parameter space would have performed
best. Only these two universal knobs are swept — the macro supplies everything
else.

OVERFITTING GUARD — the reason this module is more than a grid search: picking
the top cell of a sweep and calling it "최적" is textbook curve-fitting. The
period is therefore split chronologically into

    학습(in-sample)  = the earlier ``SPLIT_RATIO`` of bars — the surface we fit
    검증(out-of-sample) = the remaining later bars — never used for picking

Every cell is scored on BOTH. The winner is chosen on 학습 only (that is the
honest simulation of a user tuning on history), and its 검증 number is reported
next to it, so a setting that only worked because it was fitted is visible
immediately. ``generalization_rate`` summarises how much of the profitable
region survived the split. Splitting costs almost nothing: 학습 + 검증 together
span the same bars a single full-period run would.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import List, Optional

import pandas as pd

from .data import estimate_bar_count, resolve_period
from .engine import Macro, run_backtest
from .marketdata import fetch_klines_for_macro

# Bound each axis so one request can't explode into thousands of backtests.
MAX_AXIS = 12
DEFAULT_TP: List[float] = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
DEFAULT_SL: List[float] = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]

# Fraction of the period used for fitting; the rest is held out for validation.
SPLIT_RATIO = 0.7
# Both halves need enough bars for a backtest to mean anything. Below this the
# split is skipped and the sweep degrades to the old single-period behaviour.
MIN_SPLIT_BARS = 30

# A grid evaluates each accepted bar many times, so its cap is deliberately
# stricter than the ordinary one-shot backtest cap.
OPTIMIZE_MAX_BARS = max(1_000, int(os.environ.get("OPTIMIZE_MAX_BARS", "10000")))

# Only rule types that carry a take_profit_pct in params can be swept this way.
_TP_PARAM = "take_profit_pct"

UNSUPPORTED_MSG = "이 규칙 타입은 익절/손절 자동 최적화를 지원하지 않습니다 (규칙 A에서 사용하세요)."


@dataclass(frozen=True)
class PreparedOptimization:
    """Network/cache preparation done in web process; CPU payload is picklable."""

    macro_payload: dict
    frame: pd.DataFrame
    source: str
    tp_values: List[float]
    sl_values: List[float]
    cache_key: str


def _clean_axis(values: Optional[List[float]], fallback: List[float]) -> List[float]:
    """Sanitize a user-supplied axis: positive, de-duped, sorted, capped."""
    if not values:
        return list(fallback)
    cleaned = sorted({round(float(v), 4) for v in values if float(v) > 0})
    return cleaned[:MAX_AXIS] or list(fallback)


def split_period(df: pd.DataFrame, ratio: float = SPLIT_RATIO):
    """Chronological (train, test) split. Returns (None, None) when too short."""
    n = len(df)
    cut = int(n * ratio)
    if cut < MIN_SPLIT_BARS or (n - cut) < MIN_SPLIT_BARS:
        return None, None
    return df.iloc[:cut], df.iloc[cut:]


def _label(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0:
        return ""
    return f"{pd.Timestamp(df['timestamp'].iloc[0]).date()} ~ {pd.Timestamp(df['timestamp'].iloc[-1]).date()}"


def _variant(macro: Macro, tp: float, sl: float) -> Macro:
    return macro.model_copy(
        update={
            "params": {**macro.params, _TP_PARAM: tp},
            "risk": macro.risk.model_copy(update={"stop_loss_pct": sl}),
        }
    )


def _frame_fingerprint(df: pd.DataFrame) -> str:
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    normalized = df.loc[:, columns].reset_index(drop=True)
    hashed = pd.util.hash_pandas_object(normalized, index=False).to_numpy().tobytes()
    return hashlib.sha256(hashed).hexdigest()


def _macro_calculation_payload(macro: Macro) -> dict:
    payload = macro.model_dump(mode="json")
    for identity_key in ("macro_id", "share_slug", "created_at"):
        payload.pop(identity_key, None)
    return payload


def prepare_optimization(
    macro: Macro,
    tp_values: Optional[List[float]] = None,
    sl_values: Optional[List[float]] = None,
) -> PreparedOptimization:
    """Fetch once, enforce the grid budget, and build a data-aware cache key."""
    if _TP_PARAM not in macro.params:
        raise ValueError(UNSUPPORTED_MSG)

    tps = _clean_axis(tp_values, DEFAULT_TP)
    sls = _clean_axis(sl_values, DEFAULT_SL)
    start_ms, end_ms = resolve_period(macro.period.preset, macro.period.start, macro.period.end)
    estimated = estimate_bar_count(macro.candle_interval, start_ms, end_ms)
    if estimated > OPTIMIZE_MAX_BARS:
        raise ValueError(
            f"최적화 기간은 약 {estimated:,}개 봉입니다. 최대 {OPTIMIZE_MAX_BARS:,}개까지 "
            "가능하니 기간을 줄이거나 더 큰 캔들 간격을 선택해 주세요."
        )

    df, source = fetch_klines_for_macro(macro, start_ms, end_ms)
    if len(df) > OPTIMIZE_MAX_BARS:
        raise ValueError(
            f"최적화 데이터가 {len(df):,}개 봉으로 최대 {OPTIMIZE_MAX_BARS:,}개를 초과했습니다."
        )

    macro_payload = _macro_calculation_payload(macro)
    key_payload = {
        "version": 1,
        "macro": macro_payload,
        "tp_values": tps,
        "sl_values": sls,
        "frame": _frame_fingerprint(df),
    }
    cache_key = hashlib.sha256(
        json.dumps(key_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PreparedOptimization(
        macro_payload=macro_payload,
        frame=df,
        source=source,
        tp_values=tps,
        sl_values=sls,
        cache_key=cache_key,
    )


def run_prepared_optimization(prepared: PreparedOptimization) -> dict:
    """CPU-only grid evaluation, safe to execute in a worker process."""
    macro = Macro.model_validate(prepared.macro_payload)
    tps = prepared.tp_values
    sls = prepared.sl_values
    df = prepared.frame
    source = prepared.source

    return _run_grid(macro, df, source, tps, sls)


def optimize_tp_sl(
    macro: Macro,
    tp_values: Optional[List[float]] = None,
    sl_values: Optional[List[float]] = None,
) -> dict:
    """Sweep take-profit × stop-loss for ``macro`` and return a scored grid.

    Raises ``ValueError`` when the rule type has no ``take_profit_pct`` to sweep.
    Propagates ``NoSpotDataError`` from the data layer (no synthetic fallback).
    """
    return run_prepared_optimization(prepare_optimization(macro, tp_values, sl_values))


def _run_grid(
    macro: Macro,
    df: pd.DataFrame,
    source: str,
    tps: List[float],
    sls: List[float],
) -> dict:

    train_df, test_df = split_period(df)
    split_on = train_df is not None
    # Without a split there is no honest validation, so the heat surface falls
    # back to the whole period and the payload says so (the UI warns harder).
    fit_df = train_df if split_on else df

    cells: List[dict] = []
    best: Optional[dict] = None
    for sl in sls:
        for tp in tps:
            m = _variant(macro, tp, sl)
            fit = run_backtest(m, fit_df)
            oos = run_backtest(m, test_df) if split_on else None
            cell = {
                "tp": tp,
                "sl": sl,
                # In-sample score — what the heat-map colours, and the only
                # thing `best` is allowed to look at.
                "final_return_pct": fit.final_return_pct,
                "mdd_pct": fit.mdd_pct,
                "sharpe": fit.sharpe,
                "total_trades": fit.total_trades,
                # Held-out score — the reality check.
                "oos_return_pct": oos.final_return_pct if oos else None,
                "oos_trades": oos.total_trades if oos else None,
            }
            cells.append(cell)
            if best is None or cell["final_return_pct"] > best["final_return_pct"]:
                best = cell

    # How much of the profitable region survived out-of-sample? A low rate means
    # the surface is noise and the "best" cell is luck.
    generalization_rate = None
    if split_on:
        winners = [c for c in cells if c["final_return_pct"] > 0]
        if winners:
            held = sum(1 for c in winners if (c["oos_return_pct"] or 0) > 0)
            generalization_rate = round(100.0 * held / len(winners), 1)

    overfit_gap = None
    if best and best.get("oos_return_pct") is not None:
        overfit_gap = round(best["final_return_pct"] - best["oos_return_pct"], 4)

    cur_sl = macro.risk.stop_loss_pct
    return {
        "tp_values": tps,
        "sl_values": sls,
        "cells": cells,
        "best": best,
        "current": {
            "tp": round(float(macro.params[_TP_PARAM]), 4),
            "sl": round(float(cur_sl), 4) if cur_sl is not None else None,
        },
        "validation": {
            "split": split_on,
            "ratio": SPLIT_RATIO if split_on else None,
            "train_bars": len(fit_df),
            "test_bars": len(test_df) if split_on else 0,
            "train_label": _label(fit_df),
            "test_label": _label(test_df) if split_on else "",
            "generalization_rate": generalization_rate,
            "overfit_gap": overfit_gap,
            "note": (
                "학습 구간에서 고른 값을 검증 구간(고를 때 쓰지 않은 기간)에서 다시 평가한 결과입니다."
                if split_on
                else "기간이 짧아 학습/검증 분리를 못 했습니다. 전 구간에 맞춘 값이라 과최적화 위험이 큽니다."
            ),
        },
        "data_source": source,
        "disclaimer": "past-fit only; high risk of overfitting",
    }
