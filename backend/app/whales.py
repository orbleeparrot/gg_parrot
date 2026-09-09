"""Public aggregate trades and on-chain holder observations for Prefect workers.

Source adapters make one bounded public HTTP request. Shared persistence, claims,
and scheduled observation comparisons belong to their collection services.
Balances are observations of token holdings, not proof of buys or sells: exchange,
contract, bridge and AMM wallets can remain after the partial known-address filter.
WETH observations cover the Ethereum WETH contract, not all native ETH holdings.
XRPSCAN publishes its rich list nightly, so its observation interval is six hours.

Legacy pure diff helpers remain for compatibility; the collection service uses
increase/decrease language and only compares wallets present in both snapshots.
"""
from __future__ import annotations

import os
import math
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from .http_runtime import get_http_client


# Public fills identify taker direction, not wallet owners or their holdings.
LARGE_TRADE_SAMPLE_LIMIT = 500
LARGE_TRADE_WINDOW_SECONDS = 600
LARGE_TRADE_MAX_ITEMS = 30
LARGE_TRADE_REFRESH_SECONDS = 30
LARGE_TRADE_TIMEOUT_SECONDS = 8


class LargeTradeSourceError(RuntimeError):
    """Safe source failure metadata; never include upstream text or URLs."""

    def __init__(self, code: str, *, http_status: int | None = None,
                 retry_after_seconds: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_sec = retry_after_seconds
        super().__init__(f"Binance aggregate trades: {code}" +
                         (f" (HTTP {http_status})" if http_status is not None else ""))


def _threshold_quote() -> float:
    try:
        threshold = float(os.environ.get("AGENT_LARGE_TRADE_MIN_QUOTE", "100000"))
    except (TypeError, ValueError, OverflowError):
        threshold = 100000.0
    return max(1000.0, threshold) if math.isfinite(threshold) else 100000.0


def configuration() -> dict:
    """Safe source settings for Prefect logs; no credentials or configured URLs."""
    return {
        "provider": "binance_public_aggregate_trades", "threshold_quote": _threshold_quote(),
        "sample_limit": LARGE_TRADE_SAMPLE_LIMIT, "window_seconds": LARGE_TRADE_WINDOW_SECONDS,
        "max_items": LARGE_TRADE_MAX_ITEMS, "refresh_seconds": LARGE_TRADE_REFRESH_SECONDS,
        "timeout_seconds": LARGE_TRADE_TIMEOUT_SECONDS, "api_calls_per_fetch": 1, "ai_calls": 0,
    }


def base_payload(symbol: str, market: str = "spot", *, status: str = "pending") -> dict:
    """Build the shared HTTP contract, rejecting unsupported pairs and markets."""
    symbol = str(symbol or "").strip().upper()
    symbol = symbol if re.fullmatch(r"[A-Z0-9]{1,21}", symbol) else ""
    if market not in ("spot", "futures"):
        raise ValueError("unsupported aggregate-trade market")
    quote = next((value for value in ("USDT", "USDC") if symbol.endswith(value) and len(symbol) > len(value)), "")
    if not symbol or not quote:
        raise ValueError("unsupported aggregate-trade symbol")
    return {
        "feature_key": "whale_activity", "symbol": symbol, "market": market,
        "status": status, "items": [], "stale": False,
        "threshold_quote": _threshold_quote(), "quote_asset": quote, "sampled_trades": 0,
        "refresh_seconds": LARGE_TRADE_REFRESH_SECONDS,
        "disclaimer": "수집 시 최대 500건씩 확인한 최근 10분의 대규모 체결 표본입니다. 전체 거래나 특정 고래의 보유량을 뜻하지 않습니다.",
    }


def _retry_after_seconds(value: str | None, *, default: int, max_seconds: int = 3600) -> int:
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            target = parsedate_to_datetime(value or "")
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delay = target.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = default
    if not math.isfinite(delay):
        delay = default
    return max(1, min(max_seconds, math.ceil(delay)))


def _fetch_aggregate_trades(symbol: str, market: str) -> list:
    futures = market == "futures"
    base = (os.environ.get("BINANCE_FAPI_BASE", "https://fapi.binance.com") if futures
            else os.environ.get("BINANCE_API_BASE", "https://data-api.binance.vision"))
    path = "/fapi/v1/aggTrades" if futures else "/api/v3/aggTrades"
    try:
        response = get_http_client().get(base.rstrip("/") + path,
            params={"symbol": symbol, "limit": LARGE_TRADE_SAMPLE_LIMIT}, timeout=LARGE_TRADE_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        raise LargeTradeSourceError("timeout") from None
    except httpx.RequestError:
        raise LargeTradeSourceError("network_error") from None
    status = response.status_code
    if status in (429, 418):
        raise LargeTradeSourceError("rate_limited", http_status=status,
            retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After"),
                default=300 if status == 418 else 60))
    if not 200 <= status < 300:
        raise LargeTradeSourceError("http_error", http_status=status)
    try:
        rows = response.json()
    except (TypeError, ValueError):
        raise LargeTradeSourceError("invalid_response", http_status=status) from None
    if not isinstance(rows, list):
        raise LargeTradeSourceError("invalid_response", http_status=status)
    return rows


def _trade_integer(value) -> int:
    """Read exact nonnegative IDs/timestamps without float rounding or bools."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("invalid trade integer")


def fetch_large_trade_activity(symbol: str, market: str = "spot") -> dict:
    """Collect one public sample; persistence, shared claims and retries live elsewhere."""
    payload = base_payload(symbol, market)
    symbol = payload["symbol"]
    rows = _fetch_aggregate_trades(symbol, market)
    if not isinstance(rows, list):
        raise LargeTradeSourceError("invalid_response")
    now = time.time()  # The response can include fills occurring while the request was in flight.
    payload.update(observed_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
        window_start=datetime.fromtimestamp(now - LARGE_TRADE_WINDOW_SECONDS, timezone.utc).isoformat())
    items = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            if isinstance(row["p"], bool) or isinstance(row["q"], bool) or not isinstance(row.get("m"), bool):
                continue
            price, quantity = float(row["p"]), float(row["q"])
            notional = price * quantity
            timestamp, aggregate_id = _trade_integer(row["T"]), _trade_integer(row["a"])
            if (not all(math.isfinite(value) and value > 0 for value in (price, quantity, notional))
                or notional < payload["threshold_quote"]
                or not (now - LARGE_TRADE_WINDOW_SECONDS) * 1000 <= timestamp <= (now + 5) * 1000):
                continue
            identity = f"{market}:{symbol}:{aggregate_id}"
            items[identity] = {"id": identity, "price": price, "quantity": quantity,
                "notional": notional, "side": "sell" if row["m"] else "buy",
                "occurred_at": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()}
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    payload.update(status="ready" if items else "empty", sampled_trades=len(rows),
        items=sorted(items.values(), key=lambda item: item["occurred_at"], reverse=True)[:LARGE_TRADE_MAX_ITEMS])
    return payload


def get_large_trade_activity(symbol: str, market: str = "spot", *, session_started_at=None) -> dict:
    """Read the shared collected snapshot; this HTTP path never fetches Binance."""
    from .agent_features.whale_activity.service import get_activity

    return get_activity(symbol, market, session_started_at=session_started_at)

# --- supported coins ----------------------------------------------------
# `symbol` is the Binance pair the builder uses, so clicking a coin can prefill it.
COINS: dict[str, dict] = {
    "PEPE": {
        "name": "페페",
        "symbol": "PEPEUSDT",
        "source": "blockscout",
        "contract": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "base_url": "https://eth.blockscout.com/api",
        "ttl": 600.0,  # 10 min
    },
    "WETH": {
        "name": "이더(WETH)",
        "symbol": "ETHUSDT",
        "source": "blockscout",
        "contract": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "base_url": "https://eth.blockscout.com/api",
        "ttl": 600.0,
    },
    "XRP": {
        "name": "리플",
        "symbol": "XRPUSDT",
        "source": "xrpscan",
        # Rich list is refreshed ~daily upstream; polling faster is pure waste.
        "ttl": 21600.0,  # 6 h
    },
}

ONCHAIN_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ONCHAIN_BLOCKSCOUT_MAX_RESPONSE_BYTES = 512 * 1024


def _onchain_top_n() -> int:
    try:
        return max(1, min(100, int(os.environ.get("WHALE_TOP_N", "50"))))
    except (TypeError, ValueError, OverflowError):
        return 50


def _onchain_timeout_seconds() -> float:
    try:
        value = float(os.environ.get("WHALE_HTTP_TIMEOUT", "12"))
    except (TypeError, ValueError, OverflowError):
        return 12.0
    return max(1.0, min(12.0, value)) if math.isfinite(value) else 12.0


TOP_N = _onchain_top_n()  # Legacy helper compatibility; new observations read settings dynamically.
HTTP_TIMEOUT = _onchain_timeout_seconds()


def onchain_configuration() -> dict:
    """Safe Prefect configuration metadata, excluding API keys and wallet lists."""
    return {
        "provider": "public_onchain_holders", "coins": list(COINS),
        "top_n": _onchain_top_n(),
        "refresh_seconds": {coin: int(cfg["ttl"]) for coin, cfg in COINS.items()},
        "timeout_seconds": _onchain_timeout_seconds(),
        "max_response_bytes": ONCHAIN_MAX_RESPONSE_BYTES,
        "api_calls_per_fetch": 1, "ai_calls": 0,
        "xrp_upstream_refresh": "nightly", "eth_scope": "Ethereum WETH contract only; not native ETH",
    }


def supported_coin_for_symbol(symbol: str) -> str | None:
    symbol = str(symbol or "").strip().upper()
    quote = next((quote for quote in ("USDT", "USDC") if symbol.endswith(quote)), None)
    base = symbol[:-len(quote)] if quote else ""
    return {"PEPE": "PEPE", "1000PEPE": "PEPE", "WETH": "WETH", "ETH": "WETH", "XRP": "XRP"}.get(base)

# Best-known non-trader addresses (null/burn, big CEX wallets, the main PEPE AMM
# pool). PARTIAL by nature — extend via WHALE_EXCLUDE_ADDRESSES (comma-separated).
_DENYLIST_BASE = {
    "0x0000000000000000000000000000000000000000",  # null
    "0x000000000000000000000000000000000000dead",  # burn
    "0xf977814e90da44bfa03b6295a0616a897441acec",  # Binance (cold)
    "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance 14
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549",  # Binance 15
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",  # Binance 16
    "0x9696f59e4d72e237be84ffd425dcad154bf96976",  # Binance 18
    "0xa43fe16908251ee70ef74718545e4fe6c5ccec9f",  # PEPE/WETH Uniswap V2 pool
    # Labels published in https://docs.xrpscan.com/api-documentation/balance/balances
    "rMQ98K56yXJbDGv49ZSmW51sLn94Xe1mu1",  # Ripple 29
    "rKveEyR1SrkWbJX214xcfH43ZsoGMb3PEv",  # Ripple 39
    "rEy8TFcrAPvhpKrwyrscNYyqBGUkE9hKaJ",  # Binance 4
    "rBEc94rUFfLfTDwwGN7rQGBHc883c2QHhx",  # Uphold 4
}


def _denylist() -> set[str]:
    extra = os.environ.get("WHALE_EXCLUDE_ADDRESSES", "")
    out = set(_DENYLIST_BASE)
    for a in extra.split(","):
        a = a.strip()
        a = a.lower() if a.lower().startswith("0x") else a
        if a:
            out.add(a)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- pure helpers (unit-tested; no I/O) ---------------------------------
def filter_holders(holders: list[dict], contract: Optional[str] = None) -> tuple[list[dict], int]:
    """Drop known non-trader addresses. Returns (kept, excluded_count)."""
    deny = _denylist()
    if contract:
        deny.add(contract.lower())
    kept = [h for h in holders if
            (h["wallet"].lower() if h["wallet"].lower().startswith("0x") else h["wallet"]) not in deny]
    return kept, len(holders) - len(kept)


def _to_int(v) -> Optional[int]:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def diff_holders(prev: dict[str, str], holders: list[dict]) -> dict:
    """Compare current balances against the previous observation.

    ``prev`` maps wallet -> balance string. Wallets absent from ``prev`` are new
    entrants and counted as neither buy nor sell (no baseline to compare).
    """
    buys = sells = new = 0
    for h in holders:
        before = prev.get(h["wallet"])
        if before is None:
            new += 1
            continue
        a, b = _to_int(before), _to_int(h["balance"])
        if a is None or b is None:
            continue
        if b > a:
            buys += 1
        elif b < a:
            sells += 1
    return {"buys": buys, "sells": sells, "new": new, "net": buys - sells}


def mood(net: int, buys: int, sells: int) -> str:
    """Short GGparrot-tone read-out. Reference flavour only."""
    if buys == 0 and sells == 0:
        return "조용합니다 😴"
    if net >= 3:
        return "고래들이 담는 중 🐋"
    if net <= -3:
        return "고래들이 내다파는 중 🩸"
    return "눈치싸움 중 🤔"


# --- fetchers -----------------------------------------------------------
class OnchainSourceError(RuntimeError):
    """Safe, typed metadata; upstream bodies, request URLs and keys never escape."""

    def __init__(self, code: str, *, http_status: int | None = None,
                 retry_after_seconds: int | None = None, elapsed_ms: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_sec = retry_after_seconds
        self.elapsed_ms = elapsed_ms
        super().__init__(f"On-chain holders: {code}" +
                         (f" (HTTP {http_status})" if http_status is not None else ""))


def _request_onchain_json(url: str, *, params: dict | None = None, max_bytes: int) -> tuple[object, int]:
    timeout = _onchain_timeout_seconds()
    started = time.monotonic()
    status = None
    try:
        # Disable redirects so a credential-bearing request cannot follow a new host.
        with get_http_client().stream("GET", url, params=params, timeout=timeout, follow_redirects=False) as response:
            status = response.status_code
            if status in (429, 418):
                raise OnchainSourceError("rate_limited", http_status=status,
                    retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After"),
                        default=300, max_seconds=86_400))
            if not 200 <= status < 300:
                raise OnchainSourceError("http_error", http_status=status)
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise OnchainSourceError("response_too_large", http_status=status)
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > max_bytes:
                    raise OnchainSourceError("response_too_large", http_status=status)
                if time.monotonic() - started > timeout:
                    raise OnchainSourceError("timeout", http_status=status)
                body.extend(chunk)
            try:
                return json.loads(body), status
            except (TypeError, ValueError, UnicodeDecodeError, RecursionError):
                raise OnchainSourceError("invalid_response", http_status=status) from None
    except httpx.TimeoutException:
        raise OnchainSourceError("timeout", http_status=status) from None
    except httpx.RequestError:
        raise OnchainSourceError("network_error", http_status=status) from None


def _holder_balance(value) -> str:
    # Integers remain exact beyond 2**53; never use float or substitute a zero.
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,256}", value):
        return str(int(value))
    raise ValueError("invalid holder balance")


def _parse_holder_rows(rows, *, source: str, status: int) -> list[dict]:
    if not isinstance(rows, list):
        raise OnchainSourceError("invalid_response", http_status=status)
    if not rows:
        raise OnchainSourceError("empty_response", http_status=status)
    holders, seen = [], set()
    ethereum = source == "blockscout"
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid holder row")
            wallet = row.get("address" if ethereum else "account")
            if not isinstance(wallet, str):
                raise ValueError("invalid holder address")
            pattern = r"0x[a-fA-F0-9]{40}" if ethereum else r"r[1-9A-HJ-NP-Za-km-z]{24,34}"
            if not re.fullmatch(pattern, wallet):
                raise ValueError("invalid holder address")
            wallet = wallet.lower() if ethereum else wallet
            if wallet in seen:
                raise ValueError("duplicate holder address")
            seen.add(wallet)
            holders.append({"wallet": wallet, "balance": _holder_balance(row.get("value" if ethereum else "balance"))})
    except (ValueError, TypeError, OverflowError):
        raise OnchainSourceError("invalid_response", http_status=status) from None
    return sorted(holders, key=lambda item: (-int(item["balance"]), item["wallet"]))


def _blockscout_params(cfg: dict, limit: int) -> dict:
    return {
        "module": "token",
        "action": "getTokenHolders",
        "contractaddress": cfg["contract"],
        "page": 1,
        "offset": limit,
    }


def _fetch_blockscout_rows(cfg: dict, limit: int) -> tuple[list[dict], int]:
    params = _blockscout_params(cfg, limit)
    key = os.environ.get("BLOCKSCOUT_API_KEY", "")
    if key:
        params["apikey"] = key
    payload, status = _request_onchain_json(cfg["base_url"].rstrip("/") + "/", params=params,
        max_bytes=min(ONCHAIN_MAX_RESPONSE_BYTES, ONCHAIN_BLOCKSCOUT_MAX_RESPONSE_BYTES))
    if not isinstance(payload, dict) or payload.get("status") != "1" or payload.get("message") != "OK":
        raise OnchainSourceError("upstream_error", http_status=status)
    return _parse_holder_rows(payload.get("result"), source="blockscout", status=status), status


def _fetch_blockscout(cfg: dict, limit: int) -> list[dict]:
    return _fetch_blockscout_rows(cfg, limit)[0][:limit]


def _fetch_xrpscan_rows() -> tuple[list[dict], int]:
    payload, status = _request_onchain_json("https://api.xrpscan.com/api/v1/balances", max_bytes=ONCHAIN_MAX_RESPONSE_BYTES)
    return _parse_holder_rows(payload, source="xrpscan", status=status), status


def _fetch_xrpscan(limit: int) -> list[dict]:
    return _fetch_xrpscan_rows()[0][:limit]


def fetch_holder_observation(coin: str) -> dict:
    """Collect one validated observation without DB access, local cache, or AI calls."""
    coin = str(coin or "").strip().upper()
    if coin not in COINS:
        raise ValueError("unsupported on-chain coin")
    cfg, limit, started = COINS[coin], _onchain_top_n(), time.monotonic()
    try:
        if cfg["source"] == "blockscout":
            rows, status = _fetch_blockscout_rows(cfg, limit)
            # Construct the public link independently of the credential-bearing request.
            source_url = str(httpx.URL("https://eth.blockscout.com/api/", params=_blockscout_params(cfg, limit)))
            source_label = "Blockscout · Ethereum 토큰 상위 보유 주소"
        else:
            rows, status = _fetch_xrpscan_rows()
            source_url = "https://api.xrpscan.com/api/v1/balances"
            source_label = "XRPScan · XRP 상위 보유 주소"
        holders, excluded = filter_holders(rows[:limit], cfg.get("contract"))
        return {
            "coin": coin, "source": cfg["source"], "source_label": source_label,
            "source_url": source_url, "observed_at": _now_iso(), "holders": holders,
            "tracked_count": len(holders), "excluded_count": excluded, "fetched_count": len(rows),
            "daily_source": cfg["source"] == "xrpscan", "http_status": status,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "scope": ("Ethereum WETH 계약 보유량입니다. 네이티브 ETH 전체 보유량이 아닙니다." if coin == "WETH"
                      else "XRP 상위 계좌 잔고 표본이며, 원본은 매일 밤 갱신됩니다." if coin == "XRP"
                      else "Ethereum PEPE 계약 상위 보유 주소 표본입니다."),
        }
    except OnchainSourceError as exc:
        exc.elapsed_ms = round((time.monotonic() - started) * 1000)
        raise


def _fetch(coin: str, cfg: dict) -> list[dict]:
    if cfg["source"] == "blockscout":
        return _fetch_blockscout(cfg, TOP_N)
    if cfg["source"] == "xrpscan":
        return _fetch_xrpscan(TOP_N)
    return []


def get_whale_activity() -> dict:
    """Read the shared on-chain snapshots without provider calls or DB writes."""
    from .agent_features.whale_activity.onchain_service import get_all_activity
    return get_all_activity()
