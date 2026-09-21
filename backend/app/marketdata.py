"""Macro-aware candle fetch: picks spot vs futures data for a macro.

Keeps the market-selection + graceful-fallback rule in one place so the backtest
endpoint and the optimizer behave identically.
"""
from __future__ import annotations

import re

import pandas as pd

from .cache_runtime import ResponseCache
from .data import NoSpotDataError, get_klines
from .data.binance import _TICKER
from .engine import Macro
from .http_runtime import get_http_client

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")
MAX_PRICE_SYMBOLS = 30


def fetch_all_prices() -> dict[str, float]:
    """전 종목 시세를 한 번에 받아온다 (symbol 파라미터 없는 Binance ticker/price)."""
    out: dict[str, float] = {}
    resp = get_http_client().get(_TICKER, timeout=8.0)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return out
    for row in data:
        try:
            out[str(row["symbol"])] = float(row["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


# 전 종목 스냅샷 하나를 2s 공유 — 요청 심볼이 몇 개든, 얼마나 자주 바뀌든 상류 호출은
# 창당 최대 1회 (심볼별 캐시는 요청마다 다른 심볼을 섞는 클라이언트에 우회당한다).
_all_prices_cache = ResponseCache("all-prices", max_entries=1, max_bytes=2_000_000, retry_seconds=2)


def all_prices_cached(ttl: float = 2.0) -> dict[str, float]:
    try:
        return _all_prices_cache.get_or_load("all", fetch_all_prices, ttl=ttl)[0]
    except Exception:
        return {}


def batch_prices(symbols: list[str]) -> dict[str, float]:
    """리더보드 실시간 미실현용 일괄 시세 — 전 종목 스냅샷을 2s 캐시 공유, 못 받은 종목은 생략."""
    table = all_prices_cached()
    return {symbol: table[symbol] for symbol in symbols if symbol in table}


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
