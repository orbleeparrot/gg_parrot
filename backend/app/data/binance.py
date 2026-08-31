"""Binance public klines fetch + local SQLite cache.

Only the *public* historical klines REST endpoint is used (no auth, no orders).
Fetched bars are cached in ``cache/market.db`` so re-running the same window
never re-hits the network. If Binance is unreachable and the cache can't cover
the window, a deterministic synthetic series is generated so the app still
demos offline (the response's ``source`` field flags this).
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd

from ..http_runtime import SingleFlightGroup, get_http_client

# Base host is env-configurable so a US-hosted deploy (where api.binance.com is
# geo-blocked) can point at the public data mirror (data-api.binance.vision),
# which serves identical public market data. Defaults to the main host locally.
_BINANCE_BASE = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
_BASE = f"{_BINANCE_BASE}/api/v3/klines"
_TICKER = f"{_BINANCE_BASE}/api/v3/ticker/price"

# USDT-M futures (perp) host — separate from spot, env-configurable for the same
# geo-block reasons. Used to backtest short/leverage macros on real futures
# prices and to read historical funding rates.
_FUTURES_BASE = os.environ.get("BINANCE_FAPI_BASE", "https://fapi.binance.com").rstrip("/")
_FUT_KLINES = f"{_FUTURES_BASE}/fapi/v1/klines"
_FUT_FUNDING = f"{_FUTURES_BASE}/fapi/v1/fundingRate"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache")
_DB_PATH = os.path.join(_CACHE_DIR, "market.db")
_MS_DAY = 86_400_000
_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": _MS_DAY,
}

# Refuse requests whose raw simulation would monopolize the web process. The
# response curve is compacted separately, but the engine still evaluates every
# accepted bar so strategy results remain exact.
MAX_BACKTEST_BARS = max(
    1_000,
    int(os.environ.get("BACKTEST_MAX_BARS", os.environ.get("MAX_BACKTEST_BARS", "20000"))),
)

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# Shown to users when a symbol has no Binance *spot* market (e.g. futures-only
# or delisted). We refuse to simulate rather than fabricate synthetic returns.
NO_SPOT_MSG = "이 종목은 현물 시세 데이터가 없어 시뮬레이션할 수 없습니다."


class NoSpotDataError(Exception):
    """Raised when a symbol has no usable Binance spot price data."""


class TooManyBarsError(ValueError):
    """Raised before I/O when a requested candle window exceeds the safe cap."""


class IncompleteMarketDataError(RuntimeError):
    """Cached rows exist, but their requested window could not be verified."""


def _expected_bar_count(interval: str, start_ms: int, end_ms: int) -> int:
    interval_ms = _INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise ValueError(f"unsupported candle interval: {interval}")
    if end_ms <= start_ms:
        raise ValueError("end must be after start")
    return max(1, math.ceil((end_ms - start_ms) / interval_ms))


def estimate_bar_count(interval: str, start_ms: int, end_ms: int) -> int:
    """Public interval-aware bar estimate used for preflight CPU budgets."""
    return _expected_bar_count(interval, start_ms, end_ms)


def _cache_covers_window(
    cached: pd.DataFrame,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    """Return whether cached bars plausibly cover the requested interval.

    Count alone is unsafe: 365 hourly rows are only about 15 days, not a year.
    Check interval-aware count, both boundaries, and internal continuity.
    """
    if cached is None or len(cached) == 0:
        return False
    interval_ms = _INTERVAL_MS[interval]
    expected = _expected_bar_count(interval, start_ms, end_ms)
    if len(cached) < max(1, math.floor(expected * 0.95)):
        return False

    # Pandas 3 may store timezone-aware values at microsecond rather than
    # nanosecond resolution, so normalize explicitly before integer conversion.
    timestamps_ms = (
        pd.to_datetime(cached["timestamp"], utc=True)
        .to_numpy(dtype="datetime64[ms]")
        .astype("int64")
    )
    if int(timestamps_ms[0]) > start_ms + interval_ms:
        return False
    if int(timestamps_ms[-1]) < end_ms - interval_ms:
        return False
    if len(timestamps_ms) > 1:
        gaps = timestamps_ms[1:] - timestamps_ms[:-1]
        if int(gaps.max()) > interval_ms:
            return False
    return True


def _normalized_coverage_window(interval: str, start_ms: int, end_ms: int) -> tuple[int, int]:
    interval_ms = _INTERVAL_MS[interval]
    first_open = math.ceil(start_ms / interval_ms) * interval_ms
    last_open = (end_ms // interval_ms) * interval_ms
    return int(first_open), int(last_open)


# --- period presets -----------------------------------------------------
def resolve_period(preset: Optional[str], start: Optional[str], end: Optional[str]) -> tuple[int, int]:
    """Resolve a period into (start_ms, end_ms) UTC epoch milliseconds."""
    now = datetime.now(timezone.utc)
    if preset and preset != "custom":
        days = {"1y": 365, "6m": 182, "3m": 91}.get(preset)
        if days is None:
            raise ValueError(f"unknown period preset: {preset}")
        start_dt = now - timedelta(days=days)
        end_dt = now
    else:
        if not start or not end:
            raise ValueError("custom period requires start and end (ISO dates)")
        start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


# --- cache --------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS klines (
               symbol TEXT, interval TEXT, open_time INTEGER,
               open REAL, high REAL, low REAL, close REAL, volume REAL,
               PRIMARY KEY (symbol, interval, open_time))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS kline_coverage (
               symbol TEXT, interval TEXT,
               window_start INTEGER, window_end INTEGER,
               checked_at_ms INTEGER,
               PRIMARY KEY (symbol, interval, window_start, window_end))"""
    )
    return conn


def _coverage_verified(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    window_start, window_end = _normalized_coverage_window(interval, start_ms, end_ms)
    with _conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM kline_coverage
               WHERE symbol=? AND interval=? AND window_start=? AND window_end=?""",
            (symbol, interval, window_start, window_end),
        ).fetchone()
    return row is not None


def _mark_coverage(symbol: str, interval: str, start_ms: int, end_ms: int) -> None:
    """Record a successfully completed upstream window, including empty prefixes."""
    window_start, window_end = _normalized_coverage_window(interval, start_ms, end_ms)
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO kline_coverage
               (symbol, interval, window_start, window_end, checked_at_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, interval, window_start, window_end, int(time.time() * 1000)),
        )


def _read_cache(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT open_time, open, high, low, close, volume FROM klines
               WHERE symbol=? AND interval=? AND open_time BETWEEN ? AND ?
               ORDER BY open_time""",
            (symbol, interval, start_ms, end_ms),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[COLUMNS]


def _write_cache(symbol: str, interval: str, raw: list[list]) -> None:
    with _conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO klines
               (symbol, interval, open_time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (symbol, interval, int(k[0]), float(k[1]), float(k[2]),
                 float(k[3]), float(k[4]), float(k[5]))
                for k in raw
            ],
        )


# --- network fetch ------------------------------------------------------
def _fetch_binance(
    symbol: str, interval: str, start_ms: int, end_ms: int, url: str = _BASE
) -> list[list]:
    """Page klines from ``url`` (spot or futures — the payload shape is identical)."""
    out: list[list] = []
    cursor = start_ms
    client = get_http_client()
    while cursor < end_ms:
        resp = client.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        last_open = int(batch[-1][0])
        # Binance startTime is inclusive. One millisecond after the last
        # returned open works for every supported interval and cannot skip
        # intraday candles (the previous +1 day did).
        next_cursor = last_open + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 1000:
            break
    return out


# --- synthetic offline fallback -----------------------------------------
def _synthetic(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Deterministic price walk (seeded by symbol) for offline demos."""
    seed = sum(ord(ch) for ch in symbol.upper())
    n = max(2, (end_ms - start_ms) // _MS_DAY)
    base = 100.0 + (seed % 500)
    times, closes = [], []
    price = base
    for i in range(n):
        # smooth deterministic oscillation + slow drift (no randomness)
        wave = math.sin((i + seed) / 9.0) * 0.05 + math.sin((i + seed) / 23.0) * 0.03
        price *= (1.0 + wave * 0.2 + 0.0005)
        times.append(start_ms + i * _MS_DAY)
        closes.append(round(price, 2))
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(times, unit="ms", utc=True),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [0.0] * n,
        }
    )
    return df[COLUMNS]


# --- live ticker (paper trading) ----------------------------------------
def get_ticker_price(symbol: str) -> Optional[float]:
    """Latest spot price via the public ticker endpoint. None if unreachable.

    Read-only public data; no auth, no account, no orders.
    """
    try:
        resp = get_http_client().get(_TICKER, params={"symbol": symbol.upper()}, timeout=8.0)
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception:
        return None


# Shared per-symbol price cache: many paper sessions on the same symbol reuse one
# fetch instead of each hitting Binance (spec: read from a shared cache, don't
# make one external call per entry).
_price_cache: dict[str, tuple[float, float]] = {}
_price_refreshes = SingleFlightGroup()


def get_ticker_price_cached(symbol: str, ttl: float = 2.0) -> Optional[float]:
    """Latest spot price, cached for ``ttl`` seconds per symbol."""
    symbol = symbol.upper()
    now = time.time()
    hit = _price_cache.get(symbol)
    if hit and hit[1] > now:
        return hit[0]
    price, _state = _price_refreshes.run(symbol, lambda: get_ticker_price(symbol))
    if price is not None:
        _price_cache[symbol] = (price, now + ttl)
    return price


# --- public API ---------------------------------------------------------
NO_FUT_MSG = "이 종목은 USDT-M 선물 시세 데이터가 없어 선물로 시뮬레이션할 수 없습니다."


def get_klines(
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1d",
    *,
    market: str = "spot",
    allow_synthetic: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Return (OHLCV dataframe, source) for the window.

    ``market`` selects the data source:
      * "spot"    — Binance spot klines (default; original behaviour).
      * "futures" — USDT-M perpetual klines (real short/leverage prices). Cached
        under a separate key so it never collides with spot; no synthetic
        fallback — a symbol with no perp market raises :class:`NoSpotDataError`.

    source is one of: "cache", "binance", "binance-futures", "synthetic".

    ``allow_synthetic`` (default True) keeps the offline demo fallback for the
    original share/gallery flows (spot only). Backtest and paper pass ``False`` so
    a symbol with no real data raises instead of fabricating returns.
    """
    symbol = symbol.upper()
    is_fut = market == "futures"
    url = _FUT_KLINES if is_fut else _BASE
    cache_symbol = f"{symbol}#FUT" if is_fut else symbol
    expected_bars = _expected_bar_count(interval, start_ms, end_ms)
    if expected_bars > MAX_BACKTEST_BARS:
        raise TooManyBarsError(
            f"요청한 기간은 약 {expected_bars:,}개 봉입니다. "
            f"최대 {MAX_BACKTEST_BARS:,}개까지 가능하니 기간을 줄이거나 "
            "더 큰 캔들 간격을 선택해 주세요."
        )

    cached = _read_cache(cache_symbol, interval, start_ms, end_ms)
    if _coverage_verified(cache_symbol, interval, start_ms, end_ms) and len(cached) > 0:
        return cached, "cache"
    if _cache_covers_window(cached, interval, start_ms, end_ms):
        return cached, "cache"

    fetch_error: Exception | None = None
    try:
        raw = _fetch_binance(symbol, interval, start_ms, end_ms, url=url)
        if raw:
            _write_cache(cache_symbol, interval, raw)
        # A normal pagination finish verifies the requested window even when a
        # recently listed coin has no bars near its requested start.
        _mark_coverage(cache_symbol, interval, start_ms, end_ms)
        fresh = _read_cache(cache_symbol, interval, start_ms, end_ms)
        if len(fresh) > 0:
            return fresh, "binance-futures" if is_fut else "binance"
    except Exception as exc:
        fetch_error = exc

    if len(cached) > 0:
        raise IncompleteMarketDataError(
            "캐시된 시세가 요청 기간 전체를 포함하는지 확인하지 못했습니다. "
            "잠시 후 다시 시도해 주세요."
        ) from fetch_error
    if is_fut:
        raise NoSpotDataError(NO_FUT_MSG)  # futures never fabricates
    if not allow_synthetic:
        raise NoSpotDataError(NO_SPOT_MSG)
    return _synthetic(symbol, start_ms, end_ms), "synthetic"


# --- live klines (chart) -------------------------------------------------
# The historical path above is deliberately cache-first: once a window is
# covered it never re-hits Binance, and _write_cache stores whatever came back —
# including the still-forming last bar. That is right for backtests (settled
# bars only) but would freeze a live chart, so the chart uses its own path that
# ALWAYS refetches and NEVER persists the in-progress bar.
def get_recent_klines(
    symbol: str, interval: str = "1m", limit: int = 120, *, market: str = "spot"
) -> list[dict]:
    """Most recent ``limit`` klines, newest last, for the live chart.

    The final element is the *in-progress* candle (its close/high/low still move).
    Only settled bars are written to the shared cache; the open bar is returned
    but never stored, so no stale candle can leak into a backtest later.
    """
    symbol = symbol.upper()
    is_fut = market == "futures"
    url = _FUT_KLINES if is_fut else _BASE
    limit = max(2, min(int(limit), 1000))

    resp = get_http_client().get(
        url,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10.0,
    )
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, list) or not raw:
        raise NoSpotDataError(NO_FUT_MSG if is_fut else NO_SPOT_MSG)

    # Binance marks a bar closed when now >= closeTime (raw[6]).
    now_ms = int(time.time() * 1000)
    settled = [k for k in raw if int(k[6]) <= now_ms]
    if settled:
        _write_cache(f"{symbol}#FUT" if is_fut else symbol, interval, settled)

    return [
        {
            "t": int(k[0]),  # open time (ms)
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
            "closed": int(k[6]) <= now_ms,
        }
        for k in raw
    ]


# --- funding rates (futures) --------------------------------------------
def get_funding_history(symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Historical USDT-M funding rates as ``[(fundingTime_ms, rate), ...]``.

    Funding settles every 8h (three times a day). ``rate`` is the raw per-interval
    fraction (e.g. 0.0001 == 0.01%). Returns [] on any error or missing market.
    """
    symbol = symbol.upper()
    out: list[tuple[int, float]] = []
    cursor = start_ms
    try:
        client = get_http_client()
        while cursor < end_ms:
            resp = client.get(
                _FUT_FUNDING,
                params={"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000},
                timeout=12.0,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for row in batch:
                out.append((int(row["fundingTime"]), float(row["fundingRate"])))
            last = int(batch[-1]["fundingTime"])
            if len(batch) < 1000:
                break
            cursor = last + 1
    except Exception:
        return out  # partial/empty is fine; caller degrades gracefully
    return out


def average_daily_funding_pct(symbol: str, start_ms: int, end_ms: int) -> Optional[float]:
    """Average *daily* funding cost as a positive percent, or None if unavailable.

    Uses the mean absolute per-interval rate × 3 settlements/day × 100. Absolute
    because the engine models funding as a one-sided holding COST (charged on
    shorts); this yields a realistic magnitude to prefill instead of guessing.
    """
    hist = get_funding_history(symbol, start_ms, end_ms)
    if not hist:
        return None
    mean_abs = sum(abs(r) for _, r in hist) / len(hist)
    return round(mean_abs * 3.0 * 100.0, 4)


def ensure_spot_available(symbol: str) -> None:
    """Raise :class:`NoSpotDataError` if ``symbol`` has no Binance spot market.

    Best-effort: a definitive "invalid symbol" (HTTP 400) is rejected; on a
    network error we accept the symbol only if we already hold cached bars for
    it, so a transient outage never fabricates data for an unknown coin.
    """
    symbol = symbol.upper()
    try:
        resp = get_http_client().get(_TICKER, params={"symbol": symbol}, timeout=8.0)
        if resp.status_code == 200:
            return
        if resp.status_code == 400:
            raise NoSpotDataError(NO_SPOT_MSG)
        resp.raise_for_status()
    except NoSpotDataError:
        raise
    except Exception:
        pass  # network/unknown -> fall back to the cache check below
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM klines WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
    if row is None:
        raise NoSpotDataError(NO_SPOT_MSG)
