"""Public Upbit/Bithumb KRW spot data. No authentication or order endpoints.

Both APIs omit candles when no trade occurred. A successfully paged time window
is therefore stored separately from its actual candles: a sparse verified
window is not a network failure and must not be fetched repeatedly or filled
with fabricated trades. Open candles never enter the historical cache.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import threading
import time

import pandas as pd

from ..cache_runtime import ResponseCache
from ..exchanges import normalize_exchange, validate_symbol
from ..http_runtime import SingleFlightGroup, get_http_client
from . import binance

BASES = {"upbit": "https://api.upbit.com", "bithumb": "https://api.bithumb.com"}
REQUEST_GAP_SECONDS = .15  # below the documented 10 requests/sec public limit
MAX_PAGES = 100
MAX_HISTORY_SECONDS = 45.0
SYMBOL_CACHE_TTL_SECONDS = 60
# This is the total age since the last successful fetch, not five extra
# minutes after freshness expires. Failed refreshes never extend this bound.
SYMBOL_MAX_AGE_SECONDS = 300
_KST = timezone(timedelta(hours=9))
_locks = {exchange: threading.Lock() for exchange in BASES}
_next_request = {exchange: 0.0 for exchange in BASES}
_flights = SingleFlightGroup()
_symbols_cache = ResponseCache("krw-symbols", max_entries=2, max_bytes=2_000_000)
_prices_cache = ResponseCache("krw-prices", max_entries=512, max_bytes=250_000, retry_seconds=2)
_tickers_cache = ResponseCache("krw-tickers", max_entries=2, max_bytes=2_000_000, retry_seconds=5)


def _domestic(exchange: str) -> str:
    exchange = normalize_exchange(exchange)
    if exchange not in BASES:
        raise ValueError("KRW 시세는 업비트 또는 빗썸을 선택하세요.")
    return exchange


def _spot(symbol: str, exchange: str, market: str = "spot") -> tuple[str, str]:
    exchange = _domestic(exchange)
    if market != "spot":
        raise ValueError("업비트·빗썸은 KRW 현물만 지원합니다.")
    return validate_symbol(symbol, exchange), exchange


def _request(exchange: str, path: str, params: dict):
    """At most two HTTP attempts; rate limits never cause an unbounded retry."""
    for attempt in range(2):
        with _locks[exchange]:
            wait = _next_request[exchange] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _next_request[exchange] = time.monotonic() + REQUEST_GAP_SECONDS
        response = get_http_client().get(BASES[exchange] + path, params=params, timeout=10.0)
        if response.status_code == 429 and attempt == 0:
            try:
                delay = max(.25, float(response.headers.get("Retry-After", "1")))
            except (TypeError, ValueError):
                delay = 1.0
            if delay <= 2:
                with _locks[exchange]:
                    _next_request[exchange] = max(_next_request[exchange], time.monotonic() + delay)
                continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("거래소 시세 응답이 올바르지 않습니다.")
        return payload
    raise RuntimeError("거래소 요청 한도를 초과했습니다.")


def list_symbols(exchange: str = "upbit", *, now=None) -> dict:
    exchange = _domestic(exchange)
    def load():
        detail_key = "is_details" if exchange == "upbit" else "isDetails"
        raw = _request(exchange, "/v1/market/all", {detail_key: "true"})
        items = []
        for row in raw:
            symbol = str(row.get("market", ""))
            if not symbol.startswith("KRW-"):
                continue
            symbol = validate_symbol(symbol, exchange)
            event = row.get("market_event") or {}
            warning = row.get("market_warning", "NONE")
            items.append({"symbol": symbol, "base": symbol[4:], "quote": "KRW",
                          "exchange": exchange, "spot": True, "futures": False,
                          "korean_name": row.get("korean_name", ""), "english_name": row.get("english_name", ""),
                          "warning": bool(warning != "NONE" or event.get("warning"))})
        if not items:
            raise ValueError("거래소 KRW 종목 목록이 비어 있습니다.")
        return {"items": sorted(items, key=lambda row: row["symbol"]),
                "fetched_at": time.time() if now is None else now}
    payload, state = _symbols_cache.get_or_load(
        exchange, load, ttl=SYMBOL_CACHE_TTL_SECONDS,
        stale_ttl=SYMBOL_MAX_AGE_SECONDS - SYMBOL_CACHE_TTL_SECONDS, now=now,
    )
    served_at = time.time() if now is None else now
    return {**payload, "count": len(payload["items"]), "exchange": exchange, "quote_currency": "KRW",
            "stale": state == "stale", "partial": False,
            "cache_age_seconds": max(0, served_at - payload["fetched_at"]),
            "refresh_seconds": SYMBOL_CACHE_TTL_SECONDS, "max_age_seconds": SYMBOL_MAX_AGE_SECONDS,
            "sources": {"spot": {"status": "stale" if state == "stale" else "ready",
                                   "fetched_at": payload["fetched_at"]}}}


def _ticker_rows(exchange: str, symbols: list[str]) -> list[dict]:
    rows = []
    for offset in range(0, len(symbols), 100):
        rows.extend(_request(exchange, "/v1/ticker", {"markets": ",".join(symbols[offset:offset + 100])}))
    return rows


def get_all_tickers(exchange: str) -> list[dict]:
    """Candidate selection projection; prices retain their native KRW units."""
    exchange = _domestic(exchange)
    def load():
        symbols = [row["symbol"] for row in list_symbols(exchange)["items"]]
        rows = []
        for raw in _ticker_rows(exchange, symbols):
            symbol = str(raw.get("market", ""))
            if symbol not in symbols:
                continue
            rows.append({"symbol": symbol, "base": symbol[4:], "quote": "KRW", "exchange": exchange,
                         "lastPrice": float(raw["trade_price"]), "highPrice": float(raw["high_price"]),
                         "lowPrice": float(raw["low_price"]),
                         "priceChangePercent": 100 * float(raw.get("signed_change_rate", 0)),
                         "quoteVolume": float(raw.get("acc_trade_price_24h", 0))})
        if not rows:
            raise ValueError("거래소 현재가 목록이 비어 있습니다.")
        return rows
    return _tickers_cache.get_or_load(exchange, load, ttl=30, stale_ttl=120)[0]


def batch_prices(symbols: list[str], exchange: str, *, ttl: float = 2.0) -> dict[str, float]:
    exchange = _domestic(exchange)
    wanted = list(dict.fromkeys(validate_symbol(symbol, exchange) for symbol in symbols))
    if not wanted:
        return {}
    key = (exchange, tuple(sorted(wanted)))
    def load():
        out = {}
        for row in _ticker_rows(exchange, wanted):
            price = float(row.get("trade_price", 0))
            if row.get("market") in wanted and math.isfinite(price) and price > 0:
                out[row["market"]] = price
        if not out:
            raise binance.NoSpotDataError("KRW 현재가를 불러오지 못했습니다.")
        return out
    # No stale fallback: simulated fills cannot execute against old prices.
    return _prices_cache.get_or_load(key, load, ttl=ttl)[0]


def get_ticker_price(symbol: str, exchange: str) -> float | None:
    symbol, exchange = _spot(symbol, exchange)
    try:
        return batch_prices([symbol], exchange).get(symbol)
    except Exception:
        return None


def get_ticker_price_cached(symbol: str, exchange: str, ttl: float = 2.0) -> float | None:
    # batch_prices itself provides the bounded shared fresh cache.
    symbol, exchange = _spot(symbol, exchange)
    try:
        return batch_prices([symbol], exchange, ttl=ttl).get(symbol)
    except Exception:
        return None


def ensure_spot_available(symbol: str, exchange: str) -> None:
    symbol, exchange = _spot(symbol, exchange)
    if symbol not in {row["symbol"] for row in list_symbols(exchange)["items"]}:
        raise binance.NoSpotDataError("선택한 거래소의 KRW 마켓에 없는 종목입니다.")


def _path(interval: str) -> str:
    if interval == "1d":
        return "/v1/candles/days"
    if interval not in binance._INTERVAL_MS:
        raise ValueError("지원하지 않는 캔들 간격입니다.")
    minutes = binance._INTERVAL_MS[interval] // 60_000
    if minutes not in (1, 3, 5, 10, 15, 30, 60, 240):
        raise ValueError("국내 거래소가 지원하지 않는 캔들 간격입니다.")
    return f"/v1/candles/minutes/{minutes}"


def _to_cursor(stamp_ms: int, exchange: str) -> str:
    tz = timezone.utc if exchange == "upbit" else _KST
    stamp = datetime.fromtimestamp(stamp_ms / 1000, tz)
    # Bithumb documents `to` in KST without an offset; do not rely on an
    # undocumented offset parser. Upbit explicitly accepts UTC ISO offsets.
    if exchange == "bithumb":
        stamp = stamp.replace(tzinfo=None)
    return stamp.isoformat(timespec="milliseconds")


def interval_grid_offset(exchange: str, interval: str) -> int:
    """Bithumb candles begin on KST boundaries; Upbit uses UTC boundaries.

    Keep native candle UTC timestamps intact. Only boundary arithmetic changes
    (Bithumb 4h offset=03:00 UTC; daily offset=15:00 UTC).
    """
    step = binance._INTERVAL_MS[interval]
    return (-9 * 3600_000) % step if exchange == "bithumb" else 0


def _normalized_window(exchange: str, interval: str, start_ms: int, end_ms: int):
    step = binance._INTERVAL_MS[interval]
    offset = interval_grid_offset(exchange, interval)
    first = math.ceil((start_ms - offset) / step) * step + offset
    last = ((min(end_ms, int(time.time() * 1000)) - offset) // step - 1) * step + offset
    return int(first), int(last)


def _candle(raw: dict, interval: str, exchange: str = "upbit") -> dict:
    # `timestamp` is the last trade time, not the candle open. Always use the
    # documented UTC candle field; KST is only a fallback with an explicit zone.
    value = raw.get("candle_date_time_utc")
    tz = timezone.utc
    if not value:
        value, tz = raw.get("candle_date_time_kst"), _KST
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=tz)
    t = int(stamp.timestamp() * 1000)
    step = binance._INTERVAL_MS[interval]
    if (t - interval_grid_offset(exchange, interval)) % step:
        raise ValueError("거래소 캔들 시작 시각이 봉 간격과 일치하지 않습니다.")
    o, h, low, close = (float(raw[key]) for key in ("opening_price", "high_price", "low_price", "trade_price"))
    volume = float(raw.get("candle_acc_trade_volume", 0))
    if (not all(math.isfinite(n) for n in (o, h, low, close, volume))
            or min(o, h, low, close) <= 0 or volume < 0 or h < max(o, close) or low > min(o, close)):
        raise ValueError("거래소 OHLCV 값이 올바르지 않습니다.")
    return {"t": t, "o": o, "h": h, "l": low, "c": close, "v": volume,
            "closed": t + step <= int(time.time() * 1000)}


class _CandlePage(list):
    def __init__(self, rows, source_count):
        super().__init__(rows)
        self.source_count = source_count


def _page(symbol: str, interval: str, exchange: str, count: int, cursor: int | None) -> list[dict]:
    params = {"market": symbol, "count": min(200, count)}
    if cursor is not None:
        params["to"] = _to_cursor(cursor, exchange)
    raw = _request(exchange, _path(interval), params)
    rows = [_candle(row, interval, exchange) for row in raw]
    return _CandlePage(sorted({row["t"]: row for row in rows}.values(), key=lambda row: row["t"]), len(raw))


def _conn():
    # Reuse the existing configured/test-isolated market-cache file, with fully
    # separate tables and an exchange column rather than colliding native codes.
    return binance._conn()


def _initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS krw_klines (
        exchange TEXT, symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY(exchange, symbol, interval, open_time))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS krw_coverage (
        exchange TEXT, symbol TEXT, interval TEXT, window_start INTEGER, window_end INTEGER,
        PRIMARY KEY(exchange, symbol, interval, window_start, window_end))""")


def _cached(symbol: str, interval: str, exchange: str, first: int, last: int):
    with _conn() as conn:
        _initialize(conn)
        verified = conn.execute("""SELECT 1 FROM krw_coverage WHERE exchange=? AND symbol=? AND interval=?
            AND window_start<=? AND window_end>=? LIMIT 1""", (exchange, symbol, interval, first, last)).fetchone()
        if not verified:
            return None
        return conn.execute("""SELECT open_time, open, high, low, close, volume FROM krw_klines
            WHERE exchange=? AND symbol=? AND interval=? AND open_time BETWEEN ? AND ? ORDER BY open_time""",
            (exchange, symbol, interval, first, last)).fetchall()


def _missing_windows(symbol: str, interval: str, exchange: str, first: int, last: int):
    with _conn() as conn:
        _initialize(conn)
        ranges = conn.execute("""SELECT window_start, window_end FROM krw_coverage
            WHERE exchange=? AND symbol=? AND interval=? ORDER BY window_start""",
            (exchange, symbol, interval)).fetchall()
    step, cursor, gaps = binance._INTERVAL_MS[interval], first, []
    for left, right in ranges:
        if right < cursor:
            continue
        if left > last:
            break
        if left > cursor:
            gaps.append((cursor, min(last, left - step)))
        cursor = max(cursor, right + step)
    if cursor <= last:
        gaps.append((cursor, last))
    return gaps


def _store(symbol: str, interval: str, exchange: str, rows: list[dict], coverage=None):
    with _conn() as conn:
        _initialize(conn)
        conn.executemany("""INSERT OR REPLACE INTO krw_klines
            (exchange, symbol, interval, open_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?,?)""",
            [(exchange, symbol, interval, row["t"], row["o"], row["h"], row["l"], row["c"], row["v"])
             for row in rows if row["closed"]])
        if coverage is not None:
            first, last = coverage
            step = binance._INTERVAL_MS[interval]
            conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
            ranges = conn.execute("""SELECT window_start, window_end FROM krw_coverage
                WHERE exchange=? AND symbol=? AND interval=? ORDER BY window_start""",
                (exchange, symbol, interval)).fetchall()
            for left, right in ranges:
                if left <= last + step and right >= first - step:
                    first, last = min(first, left), max(last, right)
            conn.execute("""DELETE FROM krw_coverage WHERE exchange=? AND symbol=? AND interval=?
                AND window_start<=? AND window_end>=?""", (exchange, symbol, interval, last + step, first - step))
            conn.execute("INSERT INTO krw_coverage VALUES (?,?,?,?,?)", (exchange, symbol, interval, first, last))


def get_recent_klines(symbol: str, interval: str = "1m", limit: int = 120, *,
                      market: str = "spot", exchange: str = "upbit") -> list[dict]:
    symbol, exchange = _spot(symbol, exchange, market)
    _path(interval)
    limit = max(2, min(int(limit), 1000))
    def load():
        rows, cursor = {}, None
        deadline = time.monotonic() + MAX_HISTORY_SECONDS
        for _ in range(MAX_PAGES):
            count = min(200, limit - len(rows))
            page = _page(symbol, interval, exchange, count, cursor)
            if not page:
                break
            oldest = page[0]["t"]
            if cursor is not None and oldest >= cursor:
                raise binance.IncompleteMarketDataError("거래소 캔들 페이지가 반복되어 중지했습니다.")
            rows.update({row["t"]: row for row in page})
            if len(rows) >= limit or getattr(page, "source_count", len(page)) < count:
                break
            if time.monotonic() > deadline:
                raise binance.IncompleteMarketDataError("KRW 차트 수집 제한 시간을 초과했습니다.")
            cursor = oldest
        else:
            raise binance.IncompleteMarketDataError("KRW 차트 페이지 한도를 초과했습니다.")
        out = sorted(rows.values(), key=lambda row: row["t"])[-limit:]
        if not out:
            raise binance.NoSpotDataError("선택한 거래소에 사용할 수 있는 KRW 캔들이 없습니다.")
        _store(symbol, interval, exchange, out)
        return out
    return _flights.run(("recent", exchange, symbol, interval, limit), load)[0]


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[binance.COLUMNS]


def get_klines(symbol: str, start_ms: int, end_ms: int, interval: str = "1d", *,
               market: str = "spot", allow_synthetic: bool = False, exchange: str = "upbit"):
    symbol, exchange = _spot(symbol, exchange, market)
    _path(interval)
    expected = binance.estimate_bar_count(interval, start_ms, end_ms)
    if expected > binance.MAX_BACKTEST_BARS:
        raise binance.TooManyBarsError(f"요청한 기간은 약 {expected:,}개 봉입니다. 최대 {binance.MAX_BACKTEST_BARS:,}개입니다.")
    first, last = _normalized_window(exchange, interval, start_ms, end_ms)
    if last < first:
        raise binance.NoSpotDataError("요청 기간에 마감된 KRW 캔들이 없습니다.")
    def load():
        cached = _cached(symbol, interval, exchange, first, last)
        if cached is not None:
            if not cached:
                raise binance.NoSpotDataError("요청 기간에 거래가 발생한 KRW 캔들이 없습니다.")
            return _frame(cached), f"{exchange}:spot"
        deadline = time.monotonic() + MAX_HISTORY_SECONDS
        pages = 0
        for gap_first, gap_last in _missing_windows(symbol, interval, exchange, first, last):
            cursor, rows = gap_last + binance._INTERVAL_MS[interval], {}
            while True:
                if pages >= MAX_PAGES:
                    raise binance.IncompleteMarketDataError("KRW 과거 시세 페이지 한도를 초과했습니다.")
                if time.monotonic() > deadline:
                    raise binance.IncompleteMarketDataError("KRW 과거 시세 수집 제한 시간을 초과했습니다.")
                page = _page(symbol, interval, exchange, 200, cursor)
                pages += 1
                if not page:
                    break
                oldest = page[0]["t"]
                if oldest >= cursor or any(row["t"] >= cursor for row in page):
                    raise binance.IncompleteMarketDataError("거래소 캔들 페이지가 반복되어 중지했습니다.")
                rows.update({row["t"]: row for row in page
                             if gap_first <= row["t"] <= gap_last and row["closed"]})
                if oldest <= gap_first or getattr(page, "source_count", len(page)) < 200:
                    break
                cursor = oldest
            # A completed sparse gap includes periods with no trades. Only a
            # fully paged gap is verified; reuse old covered portions unchanged.
            _store(symbol, interval, exchange, list(rows.values()), (gap_first, gap_last))
        cached = _cached(symbol, interval, exchange, first, last)
        if cached is None:
            raise binance.IncompleteMarketDataError("KRW 과거 시세 범위를 검증하지 못했습니다.")
        if not cached:
            raise binance.NoSpotDataError("요청 기간에 거래가 발생한 KRW 캔들이 없습니다.")
        return _frame(cached), f"{exchange}:spot"
    return _flights.run(("history", exchange, symbol, interval, first, last), load)[0]


def reset_cache():
    for cache in (_symbols_cache, _prices_cache, _tickers_cache):
        cache.clear()
    for exchange in _next_request:
        _next_request[exchange] = 0.0
