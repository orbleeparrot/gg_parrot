"""'고래 동향' — on-chain top-holder flow (reference indicator only, NOT a signal).

[차후 도입 / 현재 비활성화]
    상위 보유자 목록에 거래소·컨트랙트·브리지 지갑이 많이 섞여 있어 신호 신뢰도가
    낮다고 판단, 노출을 보류했습니다. ``_DENYLIST_BASE`` 주소 라벨링을 충분히
    보강한 뒤 ``main.py`` 의 /api/whale-activity 라우트와 프론트의 <WhaleBanner />
    를 함께 되살리면 그대로 동작합니다. 순수 로직은 tests/test_whales.py 로 계속
    검증되고 있어 방치되어 썩지 않습니다.


Ported from the standalone `coin_active` collector, but self-contained: GGparrot
fetches the top holders itself and diffs them against the previous observation
stored in SQLite, so there is no dependency on an external daemon or Supabase.

    balance up   vs. previous observation -> "buy"
    balance down vs. previous observation -> "sell"

IMPORTANT CAVEATS (surfaced in the UI, do not remove):
  * Top-holder lists are full of exchange hot wallets, bridges, AMM pools and
    contracts. Their balance moves are ordinary user deposits/withdrawals, NOT a
    whale trading. ``_DENYLIST`` filters the best-known ones but is PARTIAL —
    treat the output as a rough curiosity, never a trading signal.
  * The delta window is "since the last observation", which is request-driven
    (the server only refreshes once per cache window), so it is irregular. The
    payload carries ``since`` / ``window_minutes`` so the UI can say so.
  * XRP's rich list only refreshes about once a day upstream, so it gets a much
    longer cache window and will usually report no change.
"""
from __future__ import annotations

import os
import math
import threading
from copy import deepcopy
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

import httpx
from sqlmodel import select

from .db import WhaleHolderBalance, WhaleObservation, get_session
from .http_runtime import SingleFlightGroup, get_http_client


# Public large fills are observable for any supported trading pair. They do
# not identify wallet owners or imply a change in a named investor's holdings.
_large_trade_cache = {}
_large_trade_lock = threading.Lock()
_large_trade_flights = SingleFlightGroup()


def _fetch_aggregate_trades(symbol, market):
    futures = market == "futures"
    base = os.environ.get("BINANCE_FAPI_BASE", "https://fapi.binance.com") if futures else os.environ.get("BINANCE_API_BASE", "https://api.binance.com")
    path = "/fapi/v1/aggTrades" if futures else "/api/v3/aggTrades"
    response = get_http_client().get(base.rstrip("/") + path, params={"symbol": symbol, "limit": 500}, timeout=8)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("invalid aggregate trades response")
    return rows


def get_large_trade_activity(symbol: str, market: str = "spot") -> dict:
    from .news import canonical_market_symbol
    symbol = canonical_market_symbol(symbol)
    market = "futures" if market == "futures" else "spot"
    quote = next((value for value in ("USDT", "USDC") if symbol.endswith(value) and len(symbol) > len(value)), "")
    threshold = max(1000, float(os.environ.get("AGENT_LARGE_TRADE_MIN_QUOTE", "100000")))
    base = {
        "feature_key": "whale_activity", "symbol": symbol, "market": market,
        "status": "unavailable", "items": [], "stale": False,
        "threshold_quote": threshold, "quote_asset": quote, "sampled_trades": 0,
        "refresh_seconds": 30,
        "disclaimer": "최근 최대 500건 중 10분 이내 대규모 체결 표본입니다. 전체 거래나 특정 고래의 보유량을 뜻하지 않습니다.",
    }
    if not symbol or not quote:
        return base
    key = (symbol, market)
    def load():
        now = time.time()
        with _large_trade_lock:
            hit = _large_trade_cache.get(key)
        if hit and hit[1] > now:
            return deepcopy(hit[0])
        payload = {**base, "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                   "window_start": datetime.fromtimestamp(now - 600, timezone.utc).isoformat()}
        try:
            rows = _fetch_aggregate_trades(symbol, market)
            items = {}
            for row in rows:
                try:
                    price, quantity = float(row["p"]), float(row["q"])
                    notional = price * quantity
                    timestamp = int(row["T"])
                    if (not math.isfinite(notional) or price <= 0 or quantity <= 0
                        or notional < threshold or not (now - 600) * 1000 <= timestamp <= (now + 5) * 1000
                        or not isinstance(row.get("m"), bool)):
                        continue
                    identity = f"{market}:{symbol}:{int(row['a'])}"
                    items[identity] = {"id": identity, "price": price, "quantity": quantity,
                        "notional": notional, "side": "sell" if row["m"] else "buy",
                        "occurred_at": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()}
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
            payload.update(status="ready" if items else "empty", sampled_trades=len(rows),
                           items=sorted(items.values(), key=lambda item: item["occurred_at"], reverse=True)[:30])
        except Exception:
            if hit and now - hit[2] < 120:
                payload = {**hit[0], "stale": True, "status": "unavailable"}
        with _large_trade_lock:
            _large_trade_cache[key] = (payload, now + 30, hit[2] if payload["stale"] else now)
            while len(_large_trade_cache) > 512:
                _large_trade_cache.pop(next(iter(_large_trade_cache)))
        return deepcopy(payload)
    return _large_trade_flights.run(key, load)[0]

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

TOP_N = int(os.environ.get("WHALE_TOP_N", "50"))
HTTP_TIMEOUT = float(os.environ.get("WHALE_HTTP_TIMEOUT", "12"))

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
}


def _denylist() -> set[str]:
    extra = os.environ.get("WHALE_EXCLUDE_ADDRESSES", "")
    out = set(_DENYLIST_BASE)
    for a in extra.split(","):
        a = a.strip().lower()
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
    kept = [h for h in holders if h["wallet"].lower() not in deny]
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
def _fetch_blockscout(cfg: dict, limit: int) -> list[dict]:
    params = {
        "module": "token",
        "action": "getTokenHolders",
        "contractaddress": cfg["contract"],
        "page": 1,
        "offset": limit,
    }
    key = os.environ.get("BLOCKSCOUT_API_KEY", "")
    if key:
        params["apikey"] = key
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        r = client.get(cfg["base_url"].rstrip("/") + "/", params=params)
        r.raise_for_status()
        rows = r.json().get("result")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        addr, val = row.get("address"), row.get("value")
        if addr:
            out.append({"wallet": str(addr), "balance": str(val if val is not None else "0")})
    out.sort(key=lambda h: _to_int(h["balance"]) or 0, reverse=True)
    return out[:limit]


def _fetch_xrpscan(limit: int) -> list[dict]:
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        r = client.get("https://api.xrpscan.com/api/v1/balances")
        r.raise_for_status()
        rows = r.json()
    if not isinstance(rows, list):
        return []
    out = []
    for item in rows[:limit]:
        if not isinstance(item, dict):
            continue
        acct, bal = item.get("account"), item.get("balance")
        if acct:
            out.append({"wallet": str(acct), "balance": str(_to_int(bal) or 0)})
    return out


def _fetch(coin: str, cfg: dict) -> list[dict]:
    if cfg["source"] == "blockscout":
        return _fetch_blockscout(cfg, TOP_N)
    if cfg["source"] == "xrpscan":
        return _fetch_xrpscan(TOP_N)
    return []


# --- persistence + refresh ---------------------------------------------
def _load_prev(db, coin: str) -> dict[str, str]:
    rows = db.exec(select(WhaleHolderBalance).where(WhaleHolderBalance.coin == coin)).all()
    return {r.wallet: r.balance_raw for r in rows}


def _store(db, coin: str, holders: list[dict], prev: dict[str, str]) -> None:
    """Upsert current balances (only touching rows that actually changed)."""
    existing = {
        r.wallet: r
        for r in db.exec(select(WhaleHolderBalance).where(WhaleHolderBalance.coin == coin)).all()
    }
    now = _now_iso()
    for h in holders:
        row = existing.get(h["wallet"])
        if row is None:
            db.add(WhaleHolderBalance(coin=coin, wallet=h["wallet"], balance_raw=h["balance"], updated_at=now))
        elif row.balance_raw != h["balance"]:
            row.balance_raw = h["balance"]
            row.updated_at = now
            db.add(row)


def _refresh_coin(coin: str, cfg: dict) -> dict:
    """Fetch, diff against the stored snapshot, persist, and return the summary."""
    holders = _fetch(coin, cfg)
    if not holders:
        raise RuntimeError(f"no holders for {coin}")
    holders, excluded = filter_holders(holders, cfg.get("contract"))

    with get_session() as db:
        prev = _load_prev(db, coin)
        baseline = not prev
        d = diff_holders(prev, holders)
        _store(db, coin, holders, prev)

        obs = db.get(WhaleObservation, coin)
        since = obs.observed_at if obs else None
        now = _now_iso()
        if obs is None:
            obs = WhaleObservation(coin=coin, observed_at=now, buys=d["buys"], sells=d["sells"], tracked=len(holders))
        else:
            obs.observed_at, obs.buys, obs.sells, obs.tracked = now, d["buys"], d["sells"], len(holders)
        db.add(obs)
        db.commit()

    window_min = None
    if since:
        try:
            t0 = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            window_min = round((datetime.now(timezone.utc) - t0).total_seconds() / 60.0, 1)
        except ValueError:
            pass

    return {
        "coin": coin,
        "name": cfg["name"],
        "symbol": cfg["symbol"],
        "tracked": len(holders),
        "excluded": excluded,
        "buys": d["buys"],
        "sells": d["sells"],
        "net": d["net"],
        "baseline": baseline,  # first ever observation: nothing to compare yet
        "since": since,
        "window_minutes": window_min,
        "observed_at": _now_iso(),
        "mood": mood(d["net"], d["buys"], d["sells"]),
        "daily_source": cfg["source"] == "xrpscan",  # upstream only refreshes ~daily
    }


# per-coin cache: coin -> (payload, expires_at)
_cache: dict[str, tuple[dict, float]] = {}


def get_whale_activity() -> dict:
    """Cached on-chain whale flow for every supported coin (never raises)."""
    out: list[dict] = []
    for coin, cfg in COINS.items():
        hit = _cache.get(coin)
        if hit and hit[1] > time.time():
            out.append(hit[0])
            continue
        try:
            payload = _refresh_coin(coin, cfg)
            _cache[coin] = (payload, time.time() + cfg["ttl"])
            out.append(payload)
        except Exception:
            if hit:  # serve stale rather than dropping the coin
                stale = dict(hit[0])
                stale["stale"] = True
                out.append(stale)

    return {
        "ok": bool(out),
        "coins": out,
        "updated_at": _now_iso(),
        "disclaimer": "on-chain reference only; exchange/contract wallets may remain; not a trading signal",
    }
