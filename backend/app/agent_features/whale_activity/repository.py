"""Short durable transactions for shared public Binance trade observations.

No function contacts a provider. Collection requires a committed fenced claim;
failed database operations never authorize uncoordinated fallback requests.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, or_, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from ... import db as db_mod
from ...db import RunSession, WhaleTradeState, get_session

INTERVAL_MS = 30_000
STALE_MS = 120_000
LEASE_MS = 60_000
ACTIVE_SESSION_MS = 30_000
_MAX_PAYLOAD_BYTES = 128_000
_WINDOW_MS = 600_000
_MAX_BACKOFF_MS = 3_600_000
_SAFE_ERRORS = {
    "429": "Provider rate limit", "418": "Provider temporarily blocked requests",
    "400": "Invalid provider request", "404": "Unsupported provider pair",
    "451": "Provider unavailable in this region", "500": "Provider unavailable",
    "502": "Provider unavailable", "503": "Provider unavailable",
    "timeout": "Provider request timed out", "network_error": "Provider connection failed",
    "invalid_response": "Invalid provider response", "collector_error": "Collection failed",
    "rate_limited": "Provider rate limit", "http_error": "Provider request failed",
    "storage_error": "Observation could not be stored",
}


def collection_interval_seconds() -> int:
    return INTERVAL_MS // 1000


def stale_seconds() -> int:
    return STALE_MS // 1000


def _millis(now_ms):
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def _iso_millis(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000) if parsed.tzinfo is None else int(parsed.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return 0


def _pair(symbol, market):
    symbol = str(symbol or "").strip().upper()
    market = str(market or "").strip().lower()
    if (market not in {"spot", "futures"} or not re.fullmatch(r"[A-Z0-9]{1,21}", symbol)
            or not any(symbol.endswith(quote) and len(symbol) > len(quote) for quote in ("USDT", "USDC"))):
        return None
    return symbol, market


def _key(symbol, market):
    return f"{market}:{symbol}"


def _provider_key(market):
    return f"provider:binance:{market}"


def _ensure_row(db, *, state_key, symbol, market):
    dialect = db.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise RuntimeError("Unsupported whale collection database")
    db.exec(insert(WhaleTradeState).values(state_key=state_key, symbol=symbol, market=market)
            .on_conflict_do_nothing(index_elements=["state_key"]))


def _lock_provider(db, market):
    key = _provider_key(market)
    _ensure_row(db, state_key=key, symbol="", market=market)
    return db.exec(select(WhaleTradeState).where(WhaleTradeState.state_key == key).with_for_update()).one()


def _owned(row, token, millis):
    return bool(token and row and row.claim_token == token and millis - LEASE_MS < row.claimed_ms <= millis)


def _decoded(row):
    if not row or not row.payload_json:
        return None
    try:
        value = json.loads(row.payload_json)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _recent_items(items, *, symbol, market, threshold, millis):
    unique = {}
    prefix = _key(symbol, market) + ":"
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            notional = float(item.get("notional", 0))
            timestamp = _iso_millis(item.get("occurred_at"))
            identity = str(item.get("id") or "")
            if (not math.isfinite(notional) or notional < threshold or not identity.startswith(prefix)
                    or not re.fullmatch(r"[0-9]{1,30}", identity[len(prefix):])
                    or not millis - _WINDOW_MS <= timestamp <= millis + 5000):
                continue
            unique[identity] = item
        except (ValueError, TypeError, OverflowError):
            continue
    return sorted(unique.values(), key=lambda item: (_iso_millis(item["occurred_at"]), item["id"]), reverse=True)[:30]


def discover_pairs(*, now_ms=None, due_only=False, bootstrap_only=False, db: Session | None = None) -> list[dict]:
    """Only live running sessions select work; positions/testnet do not partition it."""
    if db is None:
        with get_session() as owned:
            return discover_pairs(now_ms=now_ms, due_only=due_only, bootstrap_only=bootstrap_only, db=owned)
    millis = _millis(now_ms)
    cutoff = datetime.fromtimestamp((millis - ACTIVE_SESSION_MS) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.exec(select(RunSession.symbol, RunSession.market, RunSession.last_heartbeat_at)
                   .where(RunSession.status == "running", RunSession.last_heartbeat_at >= cutoff)).all()
    pairs = {pair for row in rows
             if millis - ACTIVE_SESSION_MS <= _iso_millis(row.last_heartbeat_at) <= millis + 30_000
             if (pair := _pair(row.symbol, row.market))}
    states = {}
    if pairs:
        keys = {_key(*pair) for pair in pairs}
        if due_only:
            keys |= {_provider_key(market) for _, market in pairs}
        states = {row.state_key: row for row in db.exec(select(WhaleTradeState).where(WhaleTradeState.state_key.in_(keys))).all()}
    if due_only:
        pairs = {pair for pair in pairs
                 if not ((state := states.get(_key(*pair))) and
                         (state.next_collection_ms > millis or (state.claim_token and state.claimed_ms > millis-LEASE_MS)))
                 if not ((provider := states.get(_provider_key(pair[1]))) and provider.next_collection_ms > millis)}
    if bootstrap_only:
        # The web recovery loop yields to a recently active worker using the
        # same state batch, instead of one extra snapshot query for every pair.
        pairs = {pair for pair in pairs
                 if not ((state := states.get(_key(*pair))) and
                         max(state.last_attempt_ms, state.last_success_ms) > millis-STALE_MS)}
    # A bounded collector cycle advances through assets instead of repeatedly
    # spending its whole deadline on the same first few alphabetic pairs.
    def priority(pair):
        state = states.get(_key(*pair))
        return (state.last_attempt_ms if state else 0, pair[1], pair[0])
    return [{"symbol": symbol, "market": market} for symbol, market in sorted(pairs, key=priority)]


def provider_cooldown_remaining(market: str, *, now_ms=None, db: Session | None = None) -> int:
    """Remaining shared provider backoff in milliseconds (zero for no cooldown)."""
    if market not in {"spot", "futures"}:
        return 0
    if db is None:
        with get_session() as owned:
            return provider_cooldown_remaining(market, now_ms=now_ms, db=owned)
    state = db.get(WhaleTradeState, _provider_key(market))
    return max(0, state.next_collection_ms - _millis(now_ms)) if state else 0


def claim_collection(symbol: str, market: str, *, now_ms=None, db: Session | None = None) -> str | None:
    pair = _pair(symbol, market)
    if pair is None:
        return None
    symbol, market = pair
    if db is None:
        with get_session() as owned:
            return claim_collection(symbol, market, now_ms=now_ms, db=owned)
    if ((os.environ.get("DATABASE_URL", "").strip() or db_mod._DATABASE_URL)
            and db.get_bind().dialect.name != "postgresql"):
        raise RuntimeError("Configured shared database is unavailable for whale collection claims")
    millis = _millis(now_ms)
    # Lock provider before pair everywhere that changes both: a 429 is observed
    # before the next pair can obtain permission to call this same provider.
    provider = _lock_provider(db, market)
    if provider.next_collection_ms > millis:
        db.commit()
        return None
    key, token = _key(symbol, market), uuid.uuid4().hex
    _ensure_row(db, state_key=key, symbol=symbol, market=market)
    result = db.exec(update(WhaleTradeState).where(
        WhaleTradeState.state_key == key, WhaleTradeState.next_collection_ms <= millis,
        or_(WhaleTradeState.claim_token == "", WhaleTradeState.claimed_ms <= millis - LEASE_MS),
    ).values(claim_token=token, claimed_ms=millis, last_attempt_ms=millis, collection_status="collecting"))
    claimed = result.rowcount == 1
    db.commit()
    return token if claimed else None


def store_result(symbol: str, market: str, token: str, payload: dict, *, now_ms=None, db: Session | None = None) -> bool:
    pair = _pair(symbol, market)
    if pair is None or not token:
        return False
    symbol, market = pair
    if (not isinstance(payload, dict) or payload.get("status") not in {"ready", "empty"}
            or payload.get("symbol") != symbol or payload.get("market") != market
            or not isinstance(payload.get("items"), list)):
        raise ValueError("Invalid whale observation payload")
    threshold = float(payload.get("threshold_quote", 100_000))
    if not math.isfinite(threshold) or threshold < 1000:
        raise ValueError("Invalid whale observation threshold")
    if db is None:
        with get_session() as owned:
            return store_result(symbol, market, token, payload, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    row = db.exec(select(WhaleTradeState).where(WhaleTradeState.state_key == _key(symbol, market)).with_for_update()).first()
    if not _owned(row, token, millis):
        return False
    previous = _decoded(row) or {}
    items = _recent_items([*(previous.get("items") or []), *payload["items"]],
                          symbol=symbol, market=market, threshold=threshold, millis=millis)
    merged = {**payload, "items": items, "status": "ready" if items else "empty", "stale": False}
    encoded = json.dumps(merged, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Whale observation payload exceeds storage limit")
    result = db.exec(update(WhaleTradeState).where(
        WhaleTradeState.state_key == row.state_key, WhaleTradeState.claim_token == token,
        WhaleTradeState.claimed_ms > millis - LEASE_MS,
    ).values(payload_json=encoded, last_success_ms=millis, next_collection_ms=millis+INTERVAL_MS,
             claim_token="", claimed_ms=0, consecutive_failures=0, last_error="", error_code="", collection_status="ready"))
    stored = result.rowcount == 1
    db.commit()
    return stored


def record_failure(symbol: str, market: str, token: str, *, error="", error_code="collector_error",
                   delay_seconds=None, now_ms=None, db: Session | None = None) -> bool:
    pair = _pair(symbol, market)
    if pair is None or not token:
        return False
    symbol, market = pair
    if db is None:
        with get_session() as owned:
            return record_failure(symbol, market, token, error=error, error_code=error_code,
                                  delay_seconds=delay_seconds, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    provider = _lock_provider(db, market)
    row = db.exec(select(WhaleTradeState).where(WhaleTradeState.state_key == _key(symbol, market)).with_for_update()).first()
    if not _owned(row, token, millis):
        db.commit()
        return False
    code = str(error_code).lower()
    code = code if code in _SAFE_ERRORS else "collector_error"
    failures = row.consecutive_failures+1
    delay_ms = min(_MAX_BACKOFF_MS, INTERVAL_MS * (2 ** min(failures-1, 7)))
    if delay_seconds is not None:
        try:
            delay_ms = max(delay_ms, min(86_400_000, max(0, int(float(delay_seconds) * 1000))))
        except (TypeError, ValueError, OverflowError):
            pass
    if code in {"429", "418", "rate_limited"}:
        delay_ms = max(delay_ms, 300_000 if code == "418" else 60_000)
    retry_at = millis+delay_ms
    status = "rate_limited" if code in {"429", "418", "rate_limited"} else "error"
    result = db.exec(update(WhaleTradeState).where(
        WhaleTradeState.state_key == row.state_key, WhaleTradeState.claim_token == token,
        WhaleTradeState.claimed_ms > millis - LEASE_MS,
    ).values(next_collection_ms=retry_at, claim_token="", claimed_ms=0, consecutive_failures=failures,
             last_error=_SAFE_ERRORS[code], error_code=code, collection_status=status))
    stored = result.rowcount == 1
    if stored and code in {"429", "418", "rate_limited"}:
        provider.next_collection_ms = max(provider.next_collection_ms, retry_at)
        provider.last_attempt_ms = millis
        provider.collection_status = status
        provider.last_error, provider.error_code = _SAFE_ERRORS[code], code
        db.add(provider)
    db.commit()
    return stored


def read_snapshot(symbol: str, market: str, *, now_ms=None, db: Session | None = None) -> dict | None:
    """Read retained data plus freshness only; never collect, claim, or prune."""
    pair = _pair(symbol, market)
    if pair is None:
        return None
    symbol, market = pair
    if db is None:
        with get_session() as owned:
            return read_snapshot(symbol, market, now_ms=now_ms, db=owned)
    row = db.get(WhaleTradeState, _key(symbol, market))
    if row is None:
        return None
    millis = _millis(now_ms)
    payload = _decoded(row)
    if payload is None:
        payload = {"feature_key": "whale_activity", "symbol": symbol, "market": market, "items": [], "status": "unavailable"}
    else:
        items = _recent_items(payload.get("items") or [], symbol=symbol, market=market,
                              threshold=float(payload.get("threshold_quote", 100_000)), millis=millis)
        payload = {**payload, "items": items, "status": "ready" if items else "empty"}
    return {**payload, "collection_status": row.collection_status,
            "last_success_ms": row.last_success_ms, "last_attempt_ms": row.last_attempt_ms,
            "next_collection_ms": row.next_collection_ms, "error_code": row.error_code,
            "stale": bool(row.last_success_ms and (millis-row.last_success_ms >= STALE_MS
                           or row.collection_status in {"error", "rate_limited"}))}


def prune_inactive_states(*, retention_days=7, limit=500, now_ms=None, db: Session | None = None) -> int:
    """Bounded scheduled maintenance; active pairs and current claims survive."""
    if db is None:
        with get_session() as owned:
            return prune_inactive_states(retention_days=retention_days, limit=limit, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    cutoff = millis-max(1, int(retention_days))*86_400_000
    keep = {_key(pair["symbol"], pair["market"]) for pair in discover_pairs(now_ms=millis, db=db)}
    predicates = [WhaleTradeState.symbol != "", WhaleTradeState.last_attempt_ms < cutoff,
                  WhaleTradeState.last_success_ms < cutoff,
                  or_(WhaleTradeState.claim_token == "", WhaleTradeState.claimed_ms <= millis-LEASE_MS)]
    if keep:
        predicates.append(WhaleTradeState.state_key.not_in(keep))
    keys = db.exec(select(WhaleTradeState.state_key).where(*predicates)
                  .order_by(WhaleTradeState.last_attempt_ms).limit(max(1, min(500, int(limit))))).all()
    if not keys:
        return 0
    result = db.exec(delete(WhaleTradeState).where(WhaleTradeState.state_key.in_(keys), *predicates))
    deleted = result.rowcount
    db.commit()
    return deleted
