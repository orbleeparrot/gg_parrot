"""Bounded, credential-safe activity event contract, independent of writers.

Only explicit facts are kept. Legacy text is not parsed into severity, prices,
identifiers or sub-second timestamps. This module performs no I/O.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

SEVERITIES = frozenset({"DEBUG", "INFO", "WARN", "ERROR", "UNKNOWN"})
EVENT_KINDS = frozenset({"start", "info", "signal", "order", "fill", "error", "stop", "warn", "news", "news_error"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_SECRET = re.compile(
    r"(?i)((?:authorization|x-runner-key|api[_-]?key|secret|password|access[_-]?token|receipt[_-]?token|token)"
    r"[\"']?\s*[:=]\s*)(?:[\"']?(?:bearer\s+)?)[^\s,;\"']+"
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_PROVIDER_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ggp_[A-Za-z0-9_-]{20,})\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_DSN = re.compile(r"\b(?:postgres(?:ql)?|mysql|redis)(?:\+[A-Za-z0-9_-]+)?://[^\s\"']+", re.I)
_TEXT_FIELDS = {
    "asset_symbol": 40, "collection_status": 40, "translation_status": 40,
    "outcome": 64, "reason": 500, "action": 24, "status": 40,
    "command_status": 40, "fee_currency": 24, "error_code": 100,
    "error_type": 100, "error": 500, "ack_source": 40, "verification": 100,
    "runner_version": 24, "macro_origin": 40, "macro_digest": 64,
    "source": 80, "query": 200, "stage": 40, "condition": 120,
    "indicator": 64, "decision": 100, "mode": 24, "market": 24,
    "exchange": 24, "symbol": 40,
    "order_status": 40, "raw_status": 40, "quote_currency": 24,
}
_TIMESTAMP_FIELDS = frozenset({"collected_at", "last_success_at", "last_heartbeat_at", "response_issued_at"})
_ID_FIELDS = frozenset({"command_id", "client_order_id", "exchange_order_id", "request_id",
    "snapshot_id", "collection_run_id", "flow_run_id", "task_run_id", "article_id", "batch_id"})
_INTEGER_FIELDS = frozenset({"duration_ms", "attempt", "attempts", "max_attempts", "command_seq",
    "expires_ms", "next_collection_ms", "count", "pending_count", "cursor", "fetched_count", "deduped_count",
    "filtered_count", "stored_count", "translated_count", "failed_count"})
_NUMBER_FIELDS = frozenset({"signal_price", "notional_frac", "qty_frac", "executed_qty", "fill_price",
    "quantity", "price", "fee", "observed_value", "threshold", "realized_pnl", "acquired_qty",
    "requested_qty", "requested_notional"})
_BOOL_FIELDS = frozenset({"cache_hit", "reused", "matched", "in_position", "position_uncertain",
    "position_mismatch", "server_in_position", "runner_in_position", "retryable", "terminal", "fees_known",
    "attempted", "reduce_only", "closing"})
_SOURCE_RESULT_FIELDS = frozenset({"source", "status", "stage", "http_status", "count", "fetched_count",
    "duration_ms", "cache_hit", "retry_at", "error", "attempted", "error_code", "error_type", "retryable"})


def safe_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parts = urlsplit(value[:1500])
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
                (".local", ".internal", ".localhost", ".localdomain")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            # Public FQDNs only: single-label hosts and numeric shorthand may
            # resolve into private infrastructure through the browser.
            if "." not in host or all(part.isdigit() for part in host.split(".")):
                return ""
        if _PROVIDER_KEY.search(parts.path) or _JWT.search(parts.path):
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:500]
    except (TypeError, ValueError):
        return ""


def safe_text(value, maximum: int = 300) -> str:
    text = " ".join(str(value or "").split())
    text = _SECRET.sub(r"\1[redacted]", text)
    text = _PROVIDER_KEY.sub("[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JWT.sub("[redacted]", text)
    text = _DSN.sub("[connection]", text)
    return _URL.sub(lambda match: safe_url(match.group()), text)[:maximum]


def safe_identifier(value, maximum: int = 120) -> str:
    if not isinstance(value, str) or len(value) > maximum or not _IDENTIFIER.fullmatch(value):
        return ""
    # Credentials must not enter indexed identifiers either.
    return value if safe_text(value, maximum) == value else ""


def _integer(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        parsed = int(value)
        return parsed if 0 <= parsed <= 2**63 - 1 else None
    except (ValueError, TypeError, OverflowError):
        return None


def safe_timestamp(value) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or not 2000 <= parsed.year <= 2100:
            return ""
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def safe_details(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    result = {key: safe_text(raw[key], bound) for key, bound in _TEXT_FIELDS.items()
              if isinstance(raw.get(key), str)}
    for key in _ID_FIELDS:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1:
            result[key] = value
        elif isinstance(value, str):
            result[key] = safe_text(value, 120)
    for key in _INTEGER_FIELDS:
        if key in raw and (value := _integer(raw[key])) is not None:
            result[key] = min(value, 10000) if key.endswith("count") or key == "count" else value
    for key in _NUMBER_FIELDS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) <= 1e18 and math.isfinite(value):
            result[key] = value
    for key in _BOOL_FIELDS:
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    for key in _TIMESTAMP_FIELDS:
        if (value := safe_timestamp(raw.get(key))):
            result[key] = value
    if "http_status" in raw and (value := _integer(raw["http_status"])) is not None and 100 <= value <= 599:
        result["http_status"] = value
    # Retry time remains an explicit upstream value; no inferred backoff.
    if isinstance(raw.get("retry_at"), str):
        if (value := safe_timestamp(raw["retry_at"])):
            result["retry_at"] = value
    elif "retry_at" in raw and (value := _integer(raw["retry_at"])) is not None:
        result["retry_at"] = value
    if isinstance(raw.get("source_counts"), dict):
        result["source_counts"] = {safe_text(source, 40): min(value, 10000)
            for source, count in list(raw["source_counts"].items())[:6]
            if isinstance(source, str) and safe_text(source, 40) and (value := _integer(count)) is not None}
    if isinstance(raw.get("examples"), list):
        result["examples"] = [{"title": safe_text(item.get("title"), 120),
            "source": safe_text(item.get("source"), 40), "url": safe_url(item.get("url"))}
            for item in raw["examples"][:3] if isinstance(item, dict)]
    if isinstance(raw.get("source_results"), list):
        # Project only leaf source fields before reusing the validator, so a
        # malicious nested source_results cannot recurse or expand the budget.
        result["source_results"] = [safe_details({key: item[key] for key in _SOURCE_RESULT_FIELDS if key in item})
            for item in raw["source_results"][:8] if isinstance(item, dict)]
        for item in result["source_results"]:
            if "error" in item:
                item["error"] = item["error"][:200]
    return result


def normalize_event(event, *, component: str | None = None) -> dict:
    """Return model-ready fields; caller supplies owner/session/reception time.

    A component argument forces writer provenance instead of trusting uploads.
    Caller remains responsible for deciding which event kinds/keys it permits.
    """
    raw = event if isinstance(event, dict) else {}
    details = raw.get("details")
    if not isinstance(details, dict):
        try:
            serialized = raw.get("details_json") or "{}"
            details = serialized if isinstance(serialized, dict) else (
                json.loads(serialized) if isinstance(serialized, str) and len(serialized) <= 16384 else {})
        except (ValueError, TypeError):
            details = {}
    severity = str(raw.get("severity") or "UNKNOWN").upper()
    kind = raw.get("kind") if isinstance(raw.get("kind"), str) and raw.get("kind") in EVENT_KINDS else "info"
    return {
        "ts": safe_text(raw.get("ts"), 40), "kind": kind, "message": safe_text(raw.get("message")),
        "event_key": safe_identifier(raw.get("event_key")) or None,
        "severity": severity if severity in SEVERITIES else "UNKNOWN",
        "component": safe_identifier(component if component is not None else raw.get("component"), 48),
        "event_name": safe_identifier(raw.get("event_name"), 80),
        "operation_id": safe_identifier(raw.get("operation_id"), 120),
        "details_json": json.dumps(safe_details(details), ensure_ascii=False, sort_keys=True, allow_nan=False),
    }
