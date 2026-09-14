"""Kimchi-premium aggregator (reference indicator only, NOT a trading signal).

Combines three PUBLIC, unauthenticated price sources into a single number:

    premium(%) = (upbit_krw / (binance_usdt * usdkrw) - 1) * 100

The frontend polls one backend endpoint (``/api/kimchi-premium``) instead of
hitting the exchanges directly, which sidesteps browser CORS and shares a short
in-memory cache across all viewers. Every external call is wrapped so a single
source failing (esp. the FX API) degrades gracefully with a fallback rate.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .http_runtime import get_http_client, run_parallel
from .cache_runtime import ResponseCache

_UPBIT = "https://api.upbit.com/v1/ticker"
# Env-configurable base so a US-hosted deploy can use data-api.binance.vision
# (api.binance.com is geo-blocked from US IPs). Same public data either way.
_BINANCE_BASE = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
_BINANCE = f"{_BINANCE_BASE}/api/v3/ticker/price"
_FX = "https://open.er-api.com/v6/latest/USD"  # free, no key; rates.KRW

# Supported reference coins -> (upbit market, binance symbol).
_MARKETS: dict[str, tuple[str, str]] = {
    "BTC": ("KRW-BTC", "BTCUSDT"),
    "ETH": ("KRW-ETH", "ETHUSDT"),
    "XRP": ("KRW-XRP", "XRPUSDT"),
    "SOL": ("KRW-SOL", "SOLUSDT"),
}

CACHE_SECONDS = float(os.environ.get("KIMCHI_CACHE_SECONDS", "10"))
FX_CACHE_SECONDS = max(60.0, float(os.environ.get("FX_CACHE_SECONDS", "3600")))
FX_FALLBACK = float(os.environ.get("KIMCHI_FX_FALLBACK", "1380.0"))

# component caches: key -> (value, expires_at)
_cache = ResponseCache("kimchi-components", max_entries=16, retry_seconds=15)


def supported_symbols() -> list[str]:
    return list(_MARKETS.keys())


def get_usdkrw() -> dict:
    """Current USD->KRW rate, for showing an approximate KRW value next to USDT.

    Reference only (rough conversion, not a quote). Reuses the same free FX
    source and in-memory cache as the kimchi premium; on failure it returns the
    fallback constant with ``is_fallback`` set so the UI can flag it as a guess.
    """
    payload, state = _fx_payload()
    return {"usdkrw": round(payload["value"], 2), "is_fallback": state == "fallback",
            "stale": state == "stale", "updated_at": payload["observed_at"]}


def _price(key, fetch):
    def load():
        value = fetch()
        if value <= 0:
            raise ValueError("Invalid market price")
        return value
    try:
        return _cache.get_or_load(key, load, ttl=CACHE_SECONDS, stale_ttl=30)[0]
    except Exception:
        return None


def _upbit_price(market: str) -> Optional[float]:
    def fetch():
        response = get_http_client().get(_UPBIT, params={"markets": market})
        response.raise_for_status()
        return float(response.json()[0]["trade_price"])
    return _price(f"upbit:{market}", fetch)


def _binance_price(symbol: str) -> Optional[float]:
    def fetch():
        response = get_http_client().get(_BINANCE, params={"symbol": symbol})
        response.raise_for_status()
        return float(response.json()["price"])
    return _price(f"binance:{symbol}", fetch)


def _fx_payload():
    def load():
        response = get_http_client().get(_FX)
        response.raise_for_status()
        rate = float(response.json()["rates"]["KRW"])
        if rate <= 0:
            raise ValueError("Invalid exchange rate")
        return {"value": rate, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        return _cache.get_or_load("fx:USDKRW", load, ttl=FX_CACHE_SECONDS, stale_ttl=86_400)
    except Exception:
        return {"value": FX_FALLBACK, "observed_at": None}, "fallback"


def _usdkrw() -> tuple[float, bool]:
    payload, state = _fx_payload()
    return payload["value"], state == "fallback"


def get_premium(symbol: str = "BTC") -> dict:
    """Aggregate the current kimchi premium for ``symbol`` (default BTC).

    Never raises for a missing source; the caller renders whatever fields are
    present. ``ok`` is False when a required price is unavailable.
    """
    coin = (symbol or "BTC").upper()
    if coin not in _MARKETS:
        coin = "BTC"
    upbit_market, binance_symbol = _MARKETS[coin]

    sources = run_parallel(
        {
            "upbit": lambda: _upbit_price(upbit_market),
            "binance": lambda: _binance_price(binance_symbol),
            "fx": _usdkrw,
        }
    )
    upbit = sources["upbit"]
    binance = sources["binance"]
    fx_rate, fx_fallback = sources["fx"]

    result: dict = {
        "symbol": coin,
        "upbit_market": upbit_market,
        "binance_symbol": binance_symbol,
        "upbit_price_krw": round(upbit, 2) if upbit is not None else None,
        "binance_price_usdt": round(binance, 4) if binance is not None else None,
        "usdkrw": round(fx_rate, 2),
        "fx_is_fallback": fx_fallback,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disclaimer": "reference only; not investment advice",
    }
    # Preserve each source's status instead of presenting a retained FX rate
    # as a new observation. The value check avoids attaching newer metadata to
    # a price that was read immediately before a background refresh finished.
    fx_entry = _cache.peek("fx:USDKRW")
    if fx_entry and fx_entry[0]["value"] == fx_rate:
        result.update(fx_stale=fx_entry[1] == "stale",
                      fx_observed_at=fx_entry[0]["observed_at"])
    result["stale"] = bool(result.get("fx_stale"))
    for key, value in ((f"upbit:{upbit_market}", upbit), (f"binance:{binance_symbol}", binance)):
        entry = _cache.peek(key)
        if entry and entry[0] == value and entry[1] == "stale":
            result["stale"] = True

    if upbit is None or binance is None:
        result["ok"] = False
        result["error"] = "upbit" if upbit is None else "binance"
        result["premium_pct"] = None
        return result

    binance_krw = binance * fx_rate
    premium = (upbit / binance_krw - 1.0) * 100.0
    result["ok"] = True
    result["binance_price_krw"] = round(binance_krw, 2)
    result["premium_pct"] = round(premium, 3)
    result["label"] = "김프" if premium >= 0 else "역프"
    return result
