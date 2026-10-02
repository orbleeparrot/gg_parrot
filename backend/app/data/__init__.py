from .binance import (
    NO_SPOT_MSG,
    IncompleteMarketDataError,
    NoSpotDataError,
    TooManyBarsError,
    average_daily_funding_pct,
    estimate_bar_count,
    get_funding_history,
    resolve_period,
)
from . import binance as _binance
from ..exchanges import normalize_exchange, validate_symbol


def get_klines(symbol, start_ms, end_ms, interval="1d", *, market="spot", allow_synthetic=True, exchange="binance"):
    exchange = normalize_exchange(exchange)
    symbol = validate_symbol(symbol, exchange)
    if exchange == "binance":
        return _binance.get_klines(symbol, start_ms, end_ms, interval=interval,
                                  market=market, allow_synthetic=allow_synthetic)
    from . import krw
    return krw.get_klines(symbol, start_ms, end_ms, interval=interval, market=market,
                          allow_synthetic=False, exchange=exchange)


def get_recent_klines(symbol, interval="1m", limit=120, *, market="spot", exchange="binance"):
    exchange = normalize_exchange(exchange)
    symbol = validate_symbol(symbol, exchange)
    if exchange == "binance":
        return _binance.get_recent_klines(symbol, interval=interval, limit=limit, market=market)
    from . import krw
    return krw.get_recent_klines(symbol, interval=interval, limit=limit, market=market, exchange=exchange)


def get_ticker_price(symbol, *, exchange="binance"):
    exchange = normalize_exchange(exchange)
    symbol = validate_symbol(symbol, exchange)
    if exchange == "binance":
        return _binance.get_ticker_price(symbol)
    from . import krw
    return krw.get_ticker_price(symbol, exchange)


def get_ticker_price_cached(symbol, ttl=2.0, *, exchange="binance"):
    exchange = normalize_exchange(exchange)
    symbol = validate_symbol(symbol, exchange)
    if exchange == "binance":
        return _binance.get_ticker_price_cached(symbol, ttl=ttl)
    from . import krw
    return krw.get_ticker_price_cached(symbol, exchange, ttl=ttl)


def ensure_spot_available(symbol, *, exchange="binance"):
    exchange = normalize_exchange(exchange)
    symbol = validate_symbol(symbol, exchange)
    if exchange == "binance":
        return _binance.ensure_spot_available(symbol)
    from . import krw
    return krw.ensure_spot_available(symbol, exchange)

__all__ = [
    "coin_logo_png",
    "list_symbols",
    "reset_symbol_cache",
    "get_klines",
    "get_recent_klines",
    "get_ticker_price",
    "get_ticker_price_cached",
    "get_funding_history",
    "average_daily_funding_pct",
    "resolve_period",
    "ensure_spot_available",
    "estimate_bar_count",
    "NoSpotDataError",
    "IncompleteMarketDataError",
    "TooManyBarsError",
    "NO_SPOT_MSG",
]
from .symbols import coin_logo_png, list_symbols, reset_cache as reset_symbol_cache  # noqa: E402
