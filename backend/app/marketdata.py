"""Macro-aware candle fetch: picks spot vs futures data for a macro.

Keeps the market-selection + graceful-fallback rule in one place so the backtest
endpoint and the optimizer behave identically.
"""
from __future__ import annotations

import pandas as pd

from .data import NoSpotDataError, get_klines
from .engine import Macro


def fetch_klines_for_macro(macro: Macro, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, str]:
    """Return (df, source) using the macro's resolved market.

    When "auto" selects futures but the symbol has no perp market, fall back to
    spot data so a short/leverage macro on a spot-only coin still backtests. An
    explicitly forced market ("spot"/"futures") is never overridden.
    """
    market = macro.resolved_market()
    try:
        return get_klines(
            macro.symbol, start_ms, end_ms,
            interval=macro.candle_interval, market=market, allow_synthetic=False,
        )
    except NoSpotDataError as first:
        if macro.market != "auto":
            raise
        # "auto" may pick the wrong venue for a coin listed on only one of them
        # (perp-only like 1000PEPEUSDT, or spot-only). Try the other one before giving up.
        other = "spot" if market == "futures" else "futures"
        try:
            return get_klines(
                macro.symbol, start_ms, end_ms,
                interval=macro.candle_interval, market=other, allow_synthetic=False,
            )
        except NoSpotDataError:
            raise first
