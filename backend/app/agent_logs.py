"""Owner-only session history and bounded signed news-response receipts.

News GETs stay read-only. A receipt means the browser received a server response,
not that a human read it or that news authorized an order. Stable server keys
deduplicate multiple tabs/retries; no provider requests are made by this module.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
import time

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import func, or_
from sqlmodel import Session, select

from . import auth, runner
from .db import RunSession, RunSessionEvent, get_session
from .agent_event_schema import (
    SEVERITIES, safe_details as _safe_details, safe_identifier,
    safe_text as _safe_text, safe_url as _safe_url,
)

logger = logging.getLogger(__name__)
RECEIPT_TTL_SECONDS = 600
TOKEN_MAX_CHARS = 12000
RECEIPT_MAX_BODY_BYTES = 16_384
_RECEIPT_PATH = re.compile(r"^/api/me/agents/sessions/\d+/news-receipts/?$")
_DOMAIN = b"gg-parrot-agent-news-receipt-v1:"
_NEWS_KINDS = ("news", "news_error")
_CATEGORIES = {
    "trade": ("order", "fill"),
    "execution": ("start", "stop", "info", "signal", "warn"),
    "news": _NEWS_KINDS,
    "error": ("error", "news_error"),
}
_STATUSES = frozenset({"ready", "pending", "empty", "error", "partial", "processing", "collecting", "stopped"})


class NewsReceiptBodyLimit:
    """Bound only receipt JSON before FastAPI parses it or authenticates it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not _RECEIPT_PATH.fullmatch(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length < 0 or length > RECEIPT_MAX_BODY_BYTES:
                await JSONResponse({"detail": "뉴스 수신 확인 요청의 크기가 올바르지 않아요."},
                    status_code=413 if length > RECEIPT_MAX_BODY_BYTES else 400,
                    headers={"Cache-Control": "private, no-store"})(scope, receive, send)
                return
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > RECEIPT_MAX_BODY_BYTES:
                    raise HTTPException(413, "뉴스 수신 확인 요청이 너무 커요.",
                                        headers={"Cache-Control": "private, no-store"})
            return message

        await self.app(scope, bounded_receive, send)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _count(value, maximum: int = 10000) -> int:
    try:
        return max(0, min(maximum, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _timestamp(value) -> str:
    """Only timezone-aware bounded ISO timestamps are suitable for display."""
    try:
        parsed = datetime.fromisoformat(str(value or "")[:40].replace("Z", "+00:00"))
        if parsed.tzinfo is None or not 2000 <= parsed.year <= 2100:
            return ""
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _event_view(row: RunSessionEvent) -> dict:
    received_at = _iso(row.created_ms) if row.created_ms > 0 else ""
    occurred_at = _timestamp(row.ts)
    occurrence = occurred_at or received_at
    try:
        serialized = row.details_json or "{}"
        details = _safe_details(json.loads(serialized)) if len(serialized) <= 16384 else {}
    except (TypeError, ValueError):
        details = {}
    category = "news" if row.kind in _NEWS_KINDS else "trade" if row.kind in _CATEGORIES["trade"] else (
        "error" if row.kind == "error" else "execution")
    return {
        "id": row.id, "kind": row.kind, "category": category,
        "source": "news_receipt" if row.kind in _NEWS_KINDS else "execution",
        "source_label": "뉴스 응답 수신 확인" if row.kind in _NEWS_KINDS else "실행 기록",
        "ts": occurrence,
        "ts_kst": datetime.fromisoformat(occurrence).astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M:%S")
                  if occurrence else "",
        "received_at": received_at,
        "occurred_at": occurred_at,
        "time_basis": "event" if occurred_at else "server_reception",
        "message": _safe_text(row.message), "details": details,
        "severity": row.severity if row.severity in SEVERITIES else "UNKNOWN",
        "component": safe_identifier(row.component, 48),
        "event_name": safe_identifier(row.event_name, 80),
        "operation_id": safe_identifier(row.operation_id, 120),
        "event_key": safe_identifier(row.event_key, 120) or None,
    }


def _session_context(session: RunSession) -> dict:
    exchange = ""
    try:
        macro = json.loads(session.macro_json or "{}")
        if isinstance(macro, dict) and macro.get("exchange") in {"binance", "upbit", "bithumb"}:
            exchange = macro["exchange"]
    except (ValueError, TypeError):
        pass
    return {"session_id": session.id, "symbol": _safe_text(session.symbol, 40), "exchange": exchange,
            "mode": session.mode if session.mode in {"mock", "testnet", "live"} else "",
            "market": _safe_text(session.market, 24), "runner_version": _safe_text(session.runner_version, 24),
            "macro_digest": _safe_text(session.macro_digest, 64)}


def list_logs(user_id: int, session_id: int, *, category: str = "all",
              before_id: int | None = None, limit: int = 50, q: str = "", severity: str = "",
              component: str = "", event_name: str = "", operation_id: str = "",
              since_ms: int | None = None, until_ms: int | None = None,
              context_id: int | None = None, db: Session | None = None) -> dict:
    if db is None:
        with get_session() as owned:
            return list_logs(user_id, session_id, category=category, before_id=before_id, limit=limit,
                q=q, severity=severity, component=component, event_name=event_name, operation_id=operation_id,
                since_ms=since_ms, until_ms=until_ms, context_id=context_id, db=owned)
    if not 1 <= session_id <= 2**63 - 1:
        raise HTTPException(404, "세션을 찾을 수 없어요.", headers={"Cache-Control": "private, no-store"})
    session = db.get(RunSession, session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(404, "세션을 찾을 수 없어요.", headers={"Cache-Control": "private, no-store"})
    if (category not in {"all", *_CATEGORIES} or not isinstance(limit, int) or limit < 1
            or not isinstance(q, str) or len(q) > 200 or severity not in {"", *SEVERITIES}
            or any(value and value != safe_identifier(value, bound)
                   for value, bound in ((component, 48), (event_name, 80), (operation_id, 120)))
            or any(value is not None and (not isinstance(value, int) or not 1 <= value <= 2**63 - 1)
                   for value in (before_id, context_id))
            or any(value is not None and (not isinstance(value, int) or not 0 <= value <= 2**63 - 1)
                   for value in (since_ms, until_ms))
            or (since_ms is not None and until_ms is not None and since_ms > until_ms)):
        raise HTTPException(422, "로그 조회 조건이 올바르지 않아요.", headers={"Cache-Control": "private, no-store"})
    bound = min(100, int(limit))
    statement = select(RunSessionEvent).where(RunSessionEvent.session_id == session_id,
                                             RunSessionEvent.user_id == user_id)
    context_window = None
    if context_id is not None:
        # An anchor must itself belong to this owner/session. Neighbors ignore
        # filters, otherwise the failure's preceding cause could disappear.
        anchor = db.exec(statement.where(RunSessionEvent.id == context_id).limit(1)).first()
        if anchor is None:
            raise HTTPException(404, "로그를 찾을 수 없어요.", headers={"Cache-Control": "private, no-store"})
        older_bound = (bound - 1) // 2
        older = db.exec(statement.where(RunSessionEvent.id < context_id)
                        .order_by(RunSessionEvent.id.desc()).limit(older_bound)).all() if older_bound else []
        newer = db.exec(statement.where(RunSessionEvent.id > context_id)
                        .order_by(RunSessionEvent.id.asc()).limit(bound - len(older) - 1)).all()
        # At a window edge, fill the remainder from the other side. Still at
        # most four bounded index/keyset queries, never a full history scan.
        remaining = bound - len(older) - len(newer) - 1
        if remaining:
            edge = older[-1].id if older else context_id
            older += db.exec(statement.where(RunSessionEvent.id < edge)
                             .order_by(RunSessionEvent.id.desc()).limit(remaining)).all()
        rows = list(reversed(newer)) + [anchor] + older
        has_more = False
        context_window = {"anchor_id": context_id, "oldest_id": rows[-1].id,
                          "newest_id": rows[0].id, "filters_ignored": True}
    else:
        if category != "all":
            statement = statement.where(RunSessionEvent.kind.in_(_CATEGORIES[category]))
        if before_id is not None:
            statement = statement.where(RunSessionEvent.id < before_id)
        if severity:
            statement = statement.where(or_(RunSessionEvent.severity.not_in(SEVERITIES - {"UNKNOWN"}),
                                            RunSessionEvent.severity.is_(None))) if severity == "UNKNOWN" else (
                statement.where(RunSessionEvent.severity == severity))
        for column, value in ((RunSessionEvent.component, component), (RunSessionEvent.event_name, event_name),
                              (RunSessionEvent.operation_id, operation_id)):
            if value:
                statement = statement.where(column == value)
        if since_ms is not None:
            statement = statement.where(RunSessionEvent.created_ms >= since_ms)
        if until_ms is not None:
            statement = statement.where(RunSessionEvent.created_ms <= until_ms)
        if q:
            # %/_/backslash are literal user input, never SQL wildcards. Bind
            # parameters prevent injection; the session event cap bounds scan.
            pattern = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            statement = statement.where(or_(*(func.lower(column).like(pattern, escape="\\")
                for column in (RunSessionEvent.message, RunSessionEvent.kind,
                               RunSessionEvent.event_name, RunSessionEvent.operation_id))))
        rows = db.exec(statement.order_by(RunSessionEvent.id.desc()).limit(bound + 1)).all()
        has_more = len(rows) > bound
        rows = rows[:bound]
    items = [_event_view(row) for row in rows]
    if context_id is not None:
        for item in items:
            item["context_anchor"] = item["id"] == context_id
    return {
        "session_id": session_id, "status": session.status, "items": items,
        "has_more": has_more, "next_before_id": rows[-1].id if has_more else None,
        "context": _session_context(session), "context_window": context_window,
        "query": {"q": _safe_text(q, 200), "severity": severity, "component": component,
                  "event_name": event_name, "operation_id": operation_id, "since_ms": since_ms,
                  "until_ms": until_ms, "context_id": context_id},
        "retention": {"event_cap": runner.EVENT_CAP, "news_cap": runner.news_event_cap(),
                      "history_days": runner.HISTORY_DAYS, "history_keep": runner.HISTORY_KEEP,
                      "pin_limit": runner.PIN_LIMIT},
    }


def make_news_receipt(user_id: int, session: dict, payload: dict) -> dict:
    """Issue a short-lived bounded capability with a request-time-free identity.

    No writes/claims/translation occur. Ready empty delta polls carry no receipt;
    failures, pending translation and initial collection states are observable.
    """
    absent = {"receipt_token": None, "receipt_key": None}
    if session.get("status") != "running":
        return absent
    items = [item for item in (payload.get("items") or [])[:100] if isinstance(item, dict)]
    collection = payload.get("collection") or {}
    translation = payload.get("translation") or {}
    collection = collection if isinstance(collection, dict) else {}
    translation = translation if isinstance(translation, dict) else {}
    status = str(collection.get("status") or "pending")
    status = status if status in _STATUSES else "pending"
    translation_status = str(translation.get("status") or "ready")
    translation_status = translation_status if translation_status in _STATUSES else "pending"
    pending_count = _count(translation.get("pending_count"))
    if not items and status == "ready" and not pending_count and translation_status == "ready":
        return absent
    asset = _safe_text((payload.get("context") or {}).get("asset_symbol"), 24)
    details = _safe_details({
        "asset_symbol": asset, "count": len(items), "collection_status": status,
        "translation_status": translation_status, "pending_count": pending_count,
        "source_counts": dict(Counter(_safe_text(item.get("source"), 40) or "출처 미상" for item in items)),
        "examples": items[:3], **({"cursor": payload["cursor"]} if payload.get("cursor") is not None else {}),
        **({"snapshot_id": payload["snapshot_id"]} if payload.get("snapshot_id") is not None else {}),
        **{key: collection[key] for key in (
            "collection_run_id", "flow_run_id", "task_run_id", "cache_hit", "reused", "collected_at",
            "source_results",
            "last_success_at", "next_collection_ms", "duration_ms", "attempt", "max_attempts", "retry_at", "retryable", "terminal",
            "fetched_count", "deduped_count", "filtered_count", "stored_count", "error_code", "error_type",
            "http_status", "request_id") if key in collection},
    })
    failed = status == "error" or translation_status == "error"
    if items:
        message = f"{asset or '선택 종목'} 뉴스 {len(items)}건 · 에이전트 화면 응답 수신 확인"
        if failed:
            message += " · 최근 뉴스 처리 실패, 기존 기사 유지"
    elif failed:
        message = "뉴스 처리 실패 상태 수신 · 기존 기사 유지"
    elif pending_count:
        message = f"뉴스 번역 대기 {pending_count}건 상태 수신 · 완료된 기사부터 제공"
    elif status == "empty":
        message = "관련 기사를 아직 찾지 못한 상태 수신"
    else:
        message = "뉴스 수집 대기·처리 상태 수신"
    identity = {
        "asset": asset, "cursor": payload.get("cursor"), "snapshot_id": str(payload.get("snapshot_id") or "")[:100],
        "items": sorted((str(item.get("id") or "")[:100], _safe_text(item.get("title"), 120)) for item in items),
        "collection_status": status, "translation_status": translation_status, "pending_count": pending_count,
        # Reattempts are meaningful only when unavailable; freshness/age/clock
        # and successful per-poll timestamps must not produce duplicate writes.
        "attempt": str(collection.get("last_attempt_at") or collection.get("last_attempt_ms") or "")[:40]
                   if status in {"error", "empty"} else "",
    }
    key = "news:" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    reference = collection.get("collection_run_id")
    operation_id = safe_identifier(f"collection:{reference}") if reference is not None else (
        safe_identifier(f"snapshot:{payload['snapshot_id']}") if payload.get("snapshot_id") is not None else "")
    level = "ERROR" if failed else "WARN" if (pending_count or status != "ready" or translation_status != "ready") else "INFO"
    now = _now_ms()
    claim = {"v": 1, "uid": user_id, "sid": session["session_id"], "iat": now,
             "exp": now + RECEIPT_TTL_SECONDS * 1000, "key": key,
             "kind": "news_error" if failed else "news", "message": _safe_text(message), "details": details,
             "severity": level, "operation_id": operation_id}
    raw = json.dumps(claim, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    signature = hmac.new(auth.SECRET_KEY.encode(), _DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
    token = encoded + "." + signature
    if len(token) > TOKEN_MAX_CHARS:
        return absent
    return {"receipt_token": token, "receipt_key": key}


def _decode_receipt(token: str, user_id: int, session_id: int) -> dict:
    invalid = HTTPException(422, "뉴스 수신 확인이 유효하지 않아요. 뉴스를 다시 확인해 주세요.")
    if not isinstance(token, str) or not 1 <= len(token) <= TOKEN_MAX_CHARS:
        raise invalid
    try:
        encoded, given = token.split(".")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[a-f0-9]{64}", given):
            raise ValueError("format")
        signature = hmac.new(auth.SECRET_KEY.encode(), _DOMAIN + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(given, signature):
            raise ValueError("signature")
        claim = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        now = _now_ms()
        if (not isinstance(claim, dict) or claim.get("v") != 1 or claim.get("uid") != user_id
                or claim.get("sid") != session_id or claim.get("kind") not in _NEWS_KINDS
                or not re.fullmatch(r"news:[a-f0-9]{64}", str(claim.get("key") or ""))
                or not isinstance(claim.get("iat"), int) or not isinstance(claim.get("exp"), int)
                or not claim["iat"] <= now < claim["exp"]
                or claim["exp"] - claim["iat"] != RECEIPT_TTL_SECONDS * 1000):
            raise ValueError("claim")
        return claim
    except (TypeError, ValueError, UnicodeError, KeyError):
        raise invalid from None


def record_news_receipt(user_id: int, session_id: int, token: str, *, db: Session | None = None) -> dict:
    if db is None:
        with get_session() as owned:
            return record_news_receipt(user_id, session_id, token, db=owned)
    # Check owner first, keeping the same non-disclosing 404 as every session API.
    if not 1 <= session_id <= 2**63 - 1:
        raise HTTPException(404, "세션을 찾을 수 없어요.")
    session = db.exec(select(RunSession).where(RunSession.id == session_id).with_for_update()).first()
    if session is None or session.user_id != user_id:
        raise HTTPException(404, "세션을 찾을 수 없어요.")
    if session.status != "running":
        raise HTTPException(409, "종료된 세션에는 새 뉴스 수신 기록을 추가하지 않아요.")
    claim = _decode_receipt(token, user_id, session_id)
    now = _now_ms()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    try:
        statement = insert(RunSessionEvent).values(
            # The server observes receipt arrival, not the client's actual
            # network receive time. Claim iat is response issuance, kept apart.
            session_id=session_id, user_id=user_id, ts=_iso(now), kind=claim["kind"],
            message=_safe_text(claim["message"]), created_ms=now, event_key=claim["key"],
            details_json=json.dumps(_safe_details({**(claim.get("details") if isinstance(claim.get("details"), dict) else {}),
                                                   "response_issued_at": _iso(claim["iat"])}),
                                    ensure_ascii=False, sort_keys=True),
            severity=claim.get("severity") if claim.get("severity") in SEVERITIES else "UNKNOWN",
            component="news_delivery", event_name="news.response_received",
            operation_id=safe_identifier(claim.get("operation_id"), 120),
        ).on_conflict_do_nothing(index_elements=[RunSessionEvent.session_id, RunSessionEvent.event_key]).returning(RunSessionEvent.id)
        event_id = db.exec(statement).scalar_one_or_none()
        recorded = event_id is not None
        if recorded:
            runner._trim_events(db, session_id)
        else:
            event_id = db.exec(select(RunSessionEvent.id).where(RunSessionEvent.session_id == session_id,
                               RunSessionEvent.event_key == claim["key"])).first()
        db.commit()
    except Exception:
        db.rollback()
        # Never include signed tokens, raw provider errors or credentials.
        logger.warning("Agent news receipt storage unavailable (session=%s)", session_id)
        raise HTTPException(503, "뉴스는 표시됐지만 수신 기록 저장을 완료하지 못했어요.") from None
    return {"ok": True, "recorded": recorded, "event_id": event_id}
