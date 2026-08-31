"""'오늘의 경주마' — hot-coin aggregator (reference only, NOT a trading signal).

Picks coins that are both *surging* and *actively traded* from Binance's public
24h ticker, so the bottom marquee can nudge users toward building a macro on a
trending symbol. The result is GLOBAL and SERVER-CACHED: the exchange is hit at
most once per cache window no matter how many clients poll (spec §1.5).

Selection (spec §1.2):
  1. keep USDT-quoted pairs only; drop leverage tokens and stable/fiat pairs;
  2. drop anything below a 24h quote-volume floor (illiquid noise);
  3. take the top-N by quote volume (= "actively traded");
  4. of those, take the top ``limit`` by 24h price change (= "surging").
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .http_runtime import SingleFlightGroup, get_http_client

# Env-configurable base so a US-hosted deploy can use data-api.binance.vision
# (api.binance.com is geo-blocked from US IPs). Same public data either way.
_BINANCE_BASE = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
_TICKER_24H = f"{_BINANCE_BASE}/api/v3/ticker/24hr"

# Tunables (env-configurable).
MIN_QUOTE_VOLUME = float(os.environ.get("HOTCOINS_MIN_QUOTE_VOLUME", "10000000"))  # 10M USDT
CANDIDATE_POOL = int(os.environ.get("HOTCOINS_CANDIDATE_POOL", "100"))
CACHE_SECONDS = float(os.environ.get("HOTCOINS_CACHE_SECONDS", "45"))

# Leverage-token suffixes (e.g. BTCUP / ETHDOWN / XRPBULL / SOLBEAR).
_LEV_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "HALF", "HEDGE")
# Stable/fiat bases whose *USDT pair is effectively a currency peg, not a coin.
_STABLE_BASES = frozenset(
    {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "UST", "USTC", "PAX", "GUSD",
     "EUR", "GBP", "AUD", "TRY", "BRL", "RUB", "JPY", "NGN", "ZAR"}
)


def _is_leverage_token(base: str) -> bool:
    """True for leverage tokens (BTCUP), but not real coins that merely end in a
    suffix (e.g. JUP -> 'J' underlying is too short to be one)."""
    for suf in _LEV_SUFFIXES:
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            return True
    return False


def select_hot_coins(
    tickers: list[dict],
    *,
    limit: int = 10,
    min_quote_volume: float = MIN_QUOTE_VOLUME,
    candidate_pool: int = CANDIDATE_POOL,
) -> list[dict]:
    """Pure selection over raw Binance 24h ticker dicts (testable, no I/O)."""
    candidates: list[dict] = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        if not base or base in _STABLE_BASES or _is_leverage_token(base):
            continue
        try:
            quote_volume = float(t["quoteVolume"])
            change_pct = float(t["priceChangePercent"])
            last_price = float(t["lastPrice"])
        except (KeyError, ValueError, TypeError):
            continue
        if quote_volume < min_quote_volume:
            continue
        candidates.append(
            {
                "symbol": symbol,
                "base": base,
                "change_pct": round(change_pct, 2),
                "last_price": last_price,
                "quote_volume": round(quote_volume, 2),
            }
        )

    # 1) most actively traded -> 2) biggest gainers among them.
    candidates.sort(key=lambda c: c["quote_volume"], reverse=True)
    pool = candidates[: max(1, candidate_pool)]
    pool.sort(key=lambda c: c["change_pct"], reverse=True)
    return pool[: max(1, limit)]


# Shared cache: (coins, expires_at). Keyed by limit.
_cache: dict[int, tuple[list[dict], float]] = {}
_refreshes = SingleFlightGroup()


def _fetch_tickers() -> Optional[list[dict]]:
    try:
        resp = get_http_client().get(_TICKER_24H)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception:
        return None


def get_hot_coins(limit: int = 10) -> dict:
    """Return the cached hot-coins list (fetches Binance at most once per window)."""
    limit = max(1, min(int(limit), 50))
    hit = _cache.get(limit)
    if hit and hit[1] > time.time():
        coins = hit[0]
        return _envelope(coins, cached=True)

    if hit:
        tickers, refresh_state = _refreshes.run(
            "binance:24h",
            _fetch_tickers,
            stale_value=None,
        )
    else:
        tickers, refresh_state = _refreshes.run("binance:24h", _fetch_tickers)
    if tickers is None:
        # Serve a stale cache if we have one; otherwise report empty (UI hides).
        if hit:
            return _envelope(hit[0], cached=True, stale=True)
        return _envelope([], cached=False, error="binance")

    coins = select_hot_coins(tickers, limit=limit)
    _cache[limit] = (coins, time.time() + CACHE_SECONDS)
    return _envelope(coins, cached=refresh_state == "shared")


def _envelope(coins: list[dict], *, cached: bool, stale: bool = False, error: str | None = None) -> dict:
    out: dict = {
        "coins": coins,
        "cached": cached,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disclaimer": "reference only; not investment advice",
    }
    if stale:
        out["stale"] = True
    if error:
        out["error"] = error
    return out
