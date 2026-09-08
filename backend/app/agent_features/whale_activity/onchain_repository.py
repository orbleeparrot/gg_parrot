"""Fenced shared storage for top-holder balances, without provider requests.

Only consecutive intersecting holders are comparable. Entering/leaving the top
holder sample and transferring tokens must never be presented as trading fills.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from ... import db as db_mod
from ...db import OnchainHolderState, get_session

INTERVAL_SECONDS = {"PEPE": 600, "WETH": 600, "XRP": 21_600}
SOURCES = {"PEPE": "blockscout", "WETH": "blockscout", "XRP": "xrpscan"}
LEASE_MS = 60_000
_MAX_PAYLOAD_BYTES = 128_000
_SAFE_ERRORS = {"429", "418", "400", "403", "404", "451", "500", "502", "503",
                "timeout", "network_error", "invalid_response", "collector_error",
                "rate_limited", "http_error", "storage_error", "response_too_large",
                "upstream_error", "empty_response"}
_RATE_ERRORS = {"429", "418", "rate_limited"}
_METADATA_FIELDS = ("coin", "source", "source_label", "source_url", "observed_at",
                    "tracked_count", "excluded_count", "fetched_count", "daily_source",
                    "http_status", "elapsed_ms", "scope")
_EVENT_FIELDS = ("id", "coin", "occurred_at", "previous_observed_at", "increased_count",
                 "decreased_count", "compared_count", "tracked_count", "source", "source_label",
                 "source_url", "scope", "daily_source")


def _millis(value):
    return int(time.time() * 1000) if value is None else int(value)


def _coin(value):
    normalized = str(value or "").strip().upper()
    return normalized if normalized in SOURCES else None


def _iso_millis(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return 0


def _decode(value, default):
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _encode(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Onchain observation exceeds storage limit")
    return encoded


def _ensure_row(db, *, state_key, coin, source):
    dialect = db.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise RuntimeError("Unsupported onchain collection database")
    db.exec(insert(OnchainHolderState).values(state_key=state_key, coin=coin, source=source)
            .on_conflict_do_nothing(index_elements=["state_key"]))


def _lock_provider(db, source):
    key = f"provider:{source}"
    _ensure_row(db, state_key=key, coin="", source=source)
    return db.exec(select(OnchainHolderState).where(OnchainHolderState.state_key == key).with_for_update()).one()


def _owned(row, token, millis):
    return bool(token and row and row.claim_token == token and millis - LEASE_MS < row.claimed_ms <= millis)


def _holders(values):
    if not isinstance(values, list) or not 0 <= len(values) <= 100:
        raise ValueError("Invalid onchain holder sample")
    result = {}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("Invalid onchain holder row")
        wallet, balance = item.get("wallet"), item.get("balance")
        if (not isinstance(wallet, str) or not 1 <= len(wallet) <= 100 or not wallet.isascii()
                or any(character.isspace() or ord(character) < 33 for character in wallet)
                or wallet in result or not isinstance(balance, str)
                or not re.fullmatch(r"[0-9]{1,256}", balance)):
            raise ValueError("Invalid onchain holder identity or exact balance")
        result[wallet] = str(int(balance))
    return result


def _metadata(coin, payload, millis):
    if not isinstance(payload, dict) or payload.get("coin") != coin or payload.get("source") != SOURCES[coin]:
        raise ValueError("Invalid onchain observation identity")
    observed = _iso_millis(payload.get("observed_at"))
    if not millis - LEASE_MS <= observed <= millis + 5000:
        raise ValueError("Invalid onchain observation timestamp")
    try:
        url = urlsplit(str(payload.get("source_url", "")))
        if (url.scheme != "https" or url.username or url.password or url.fragment
                or url.hostname != ("api.xrpscan.com" if coin == "XRP" else "eth.blockscout.com")
                or any(key.lower() in {"apikey", "api_key", "key", "token"} for key, _ in parse_qsl(url.query))):
            raise ValueError("Invalid public onchain source URL")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid public onchain source URL") from exc
    result = {key: payload.get(key) for key in _METADATA_FIELDS}
    for key in ("source_label", "scope", "source_url", "observed_at"):
        if not isinstance(result[key], str) or len(result[key]) > 2048:
            raise ValueError("Invalid onchain source metadata")
    for key in ("tracked_count", "excluded_count", "fetched_count", "elapsed_ms"):
        if type(result[key]) is not int or not 0 <= result[key] <= 1_000_000:
            raise ValueError("Invalid onchain source count")
    if result["http_status"] != 200 or type(result["daily_source"]) is not bool or result["daily_source"] != (coin == "XRP"):
        raise ValueError("Invalid onchain source status")
    return result


def _recent_items(values, coin, millis):
    unique = {}
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        identity = item.get("id")
        if (not isinstance(identity, str) or not re.fullmatch(rf"onchain:{coin}:[0-9]+", identity)
                or not millis - 2*INTERVAL_SECONDS[coin]*1000 <= _iso_millis(item.get("occurred_at")) <= millis + 5000):
            continue
        unique[identity] = {key: item[key] for key in _EVENT_FIELDS if key in item}
    return sorted(unique.values(), key=lambda item: (_iso_millis(item["occurred_at"]), item["id"]), reverse=True)[:20]


def discover_coins(*, now_ms=None, db: Session | None = None) -> list[str]:
    """Discover the three configured sources, independent of active macros."""
    if db is None:
        with get_session() as owned:
            return discover_coins(now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    keys = set(SOURCES) | {f"provider:{source}" for source in SOURCES.values()}
    rows = {row.state_key: row for row in db.exec(select(OnchainHolderState).where(OnchainHolderState.state_key.in_(keys))).all()}
    due = [coin for coin in SOURCES
           if not ((row := rows.get(coin)) and (row.next_collection_ms > millis
                   or (row.claim_token and row.claimed_ms > millis-LEASE_MS)))
           if not ((provider := rows.get(f"provider:{SOURCES[coin]}")) and provider.next_collection_ms > millis)]
    return sorted(due, key=lambda coin: (rows[coin].last_attempt_ms if coin in rows else 0, coin))


def claim_collection(coin: str, *, now_ms=None, db: Session | None = None) -> str | None:
    coin = _coin(coin)
    if coin is None:
        return None
    if db is None:
        with get_session() as owned:
            return claim_collection(coin, now_ms=now_ms, db=owned)
    if ((os.environ.get("DATABASE_URL", "").strip() or db_mod._DATABASE_URL)
            and db.get_bind().dialect.name != "postgresql"):
        raise RuntimeError("Configured shared database is unavailable for onchain collection claims")
    millis = _millis(now_ms)
    provider = _lock_provider(db, SOURCES[coin])
    if provider.next_collection_ms > millis:
        db.commit()
        return None
    _ensure_row(db, state_key=coin, coin=coin, source=SOURCES[coin])
    token = uuid.uuid4().hex
    result = db.exec(update(OnchainHolderState).where(
        OnchainHolderState.state_key == coin, OnchainHolderState.next_collection_ms <= millis,
        or_(OnchainHolderState.claim_token == "", OnchainHolderState.claimed_ms <= millis-LEASE_MS),
    ).values(claim_token=token, claimed_ms=millis, last_attempt_ms=millis, collection_status="collecting"))
    claimed = result.rowcount == 1
    db.commit()
    return token if claimed else None


def store_result(coin: str, token: str, payload: dict, *, now_ms=None, db: Session | None = None) -> bool:
    coin = _coin(coin)
    if coin is None or not token:
        return False
    if db is None:
        with get_session() as owned:
            return store_result(coin, token, payload, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    row = db.exec(select(OnchainHolderState).where(OnchainHolderState.state_key == coin).with_for_update()).first()
    if not _owned(row, token, millis):
        db.commit()
        return False
    metadata = _metadata(coin, payload, millis)
    current = _holders(payload.get("holders"))
    if (metadata["tracked_count"] != len(current)
            or metadata["tracked_count"] + metadata["excluded_count"] > metadata["fetched_count"]
            or metadata["fetched_count"] == 0
            or (not current and not metadata["excluded_count"])):
        raise ValueError("Onchain counts do not match holder sample")
    previous = _decode(row.holders_json, {})
    old_payload = _decode(row.payload_json, {})
    compared = current.keys() & previous.keys()
    baseline = (not compared or not row.last_success_ms
                or millis-row.last_success_ms > 3*INTERVAL_SECONDS[coin]*1000
                or old_payload.get("scope") != metadata["scope"]
                or old_payload.get("source") != metadata["source"])
    sequence = row.observation_seq + 1
    items = [] if baseline else _recent_items(old_payload.get("items"), coin, millis)
    if not baseline:
        increased = sum(int(current[wallet]) > int(previous[wallet]) for wallet in compared)
        decreased = sum(int(current[wallet]) < int(previous[wallet]) for wallet in compared)
        if increased or decreased:
            change = {key: metadata[key] for key in ("coin", "source", "source_label", "source_url", "scope", "daily_source")}
            change.update(id=f"onchain:{coin}:{sequence}", occurred_at=metadata["observed_at"],
                          previous_observed_at=old_payload.get("observed_at"), increased_count=increased,
                          decreased_count=decreased, compared_count=len(compared), tracked_count=len(current))
            items = _recent_items([change, *items], coin, millis)
    public = {**metadata, "status": "baseline" if baseline else "ready", "items": items}
    result = db.exec(update(OnchainHolderState).where(
        OnchainHolderState.state_key == coin, OnchainHolderState.claim_token == token,
        OnchainHolderState.claimed_ms > millis-LEASE_MS,
    ).values(holders_json=_encode(current), payload_json=_encode(public), observation_seq=sequence,
             last_success_ms=millis, next_collection_ms=millis+INTERVAL_SECONDS[coin]*1000,
             claim_token="", claimed_ms=0, consecutive_failures=0, error_code="", collection_status=public["status"]))
    stored = result.rowcount == 1
    db.commit()
    return stored


def record_failure(coin: str, token: str, *, error_code="collector_error", delay_seconds=None,
                   now_ms=None, db: Session | None = None) -> bool:
    coin = _coin(coin)
    if coin is None or not token:
        return False
    if db is None:
        with get_session() as owned:
            return record_failure(coin, token, error_code=error_code, delay_seconds=delay_seconds, now_ms=now_ms, db=owned)
    millis = _millis(now_ms)
    provider = _lock_provider(db, SOURCES[coin])
    row = db.exec(select(OnchainHolderState).where(OnchainHolderState.state_key == coin).with_for_update()).first()
    if not _owned(row, token, millis):
        db.commit()
        return False
    code = str(error_code).lower()
    code = code if code in _SAFE_ERRORS else "collector_error"
    failures = row.consecutive_failures+1
    delay_ms = min(3_600_000, 60_000*(2**min(failures-1, 6)))
    if delay_seconds is not None:
        try:
            delay_ms = max(delay_ms, min(86_400_000, max(0, int(float(delay_seconds)*1000))))
        except (ValueError, TypeError, OverflowError):
            pass
    if code == "418":
        delay_ms = max(delay_ms, 300_000)
    retry_at = millis+delay_ms
    status = "rate_limited" if code in _RATE_ERRORS else "error"
    result = db.exec(update(OnchainHolderState).where(
        OnchainHolderState.state_key == coin, OnchainHolderState.claim_token == token,
        OnchainHolderState.claimed_ms > millis-LEASE_MS,
    ).values(next_collection_ms=retry_at, claim_token="", claimed_ms=0, consecutive_failures=failures,
             error_code=code, collection_status=status))
    stored = result.rowcount == 1
    if stored and code in _RATE_ERRORS:
        provider.next_collection_ms = max(provider.next_collection_ms, retry_at)
        provider.last_attempt_ms = millis
        provider.collection_status, provider.error_code = status, code
        db.add(provider)
    db.commit()
    return stored


def read_snapshot(coin: str, *, now_ms=None, db: Session | None = None) -> dict | None:
    """Read public metadata and stable change events only, without side effects."""
    coin = _coin(coin)
    if coin is None:
        return None
    if db is None:
        with get_session() as owned:
            return read_snapshot(coin, now_ms=now_ms, db=owned)
    row = db.get(OnchainHolderState, coin)
    if row is None:
        return None
    millis = _millis(now_ms)
    payload = _decode(row.payload_json, {})
    public = {key: payload[key] for key in _METADATA_FIELDS if key in payload}
    public.update(coin=coin, source=SOURCES[coin], status=payload.get("status", "unavailable"),
                  items=_recent_items(payload.get("items"), coin, millis))
    return {**public, "collection_status": row.collection_status,
            "last_success_ms": row.last_success_ms, "last_attempt_ms": row.last_attempt_ms,
            "next_collection_ms": row.next_collection_ms, "error_code": row.error_code,
            "stale": bool(row.last_success_ms and (millis-row.last_success_ms >= 2*INTERVAL_SECONDS[coin]*1000
                           or row.collection_status in {"error", "rate_limited"}))}
