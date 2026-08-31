"""매크로 실행기(로컬 exe) 연동 백엔드.

흐름
----
1. 회원은 마이페이지에서 **껄무새 회원 키**(계정당 1개)를 발급받아 실행기에 입력한다.
2. 실행기가 매크로를 돌리기 시작하면 ``start`` 로 :class:`RunSession` 을 만든다.
3. 실행기는 몇 초마다 ``heartbeat`` 로 실시간 상태(현재가/포지션/손익)를 올리고,
   응답의 ``action`` 으로 마이페이지가 요청한 종료 명령을 받아간다.
4. 마이페이지의 종료 버튼은 ``request_stop`` 으로 ``stop_mode`` 플래그만 세운다.
   - ``stop_only``      → 매크로만 종료(열린 포지션은 그대로 둠)
   - ``close_and_stop`` → 보유 포지션을 청산한 뒤 종료
5. 실행기가 명령을 수행한 뒤 ``mark_stopped`` 로 종료를 확정 보고한다.

보안: 거래소 API 키/시크릿은 서버로 오지 않는다. 이 모듈이 다루는 건 회원 키와
구동 상태(요약·시세·손익)뿐이다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlmodel import select

from .db import RunnerKey, RunnerLaunchTicket, RunSession, User, UserMacro, get_session
from .engine import Macro

_KST = timezone(timedelta(hours=9))

# heartbeat 주기(실행기가 서버에 상태를 올리고 종료명령을 받아가는 간격, 초).
POLL_SECONDS = 5
# 이 시간 이상 heartbeat 가 없으면 마이페이지에서 '연결 끊김'으로 표시한다.
STALE_SECONDS = 30
# A websocket normally wakes on each persisted runner update. This periodic
# snapshot also catches changes made by another process/instance or a missed
# in-process notification.
SESSION_STREAM_RESYNC_SECONDS = max(
    5, min(int(os.environ.get("RUNNER_SESSION_WS_RESYNC_SECONDS", "15")), 60)
)
# Web -> local runner handoff tickets are deliberately short-lived bearer
# credentials. Only their SHA-256 digest is persisted.
LAUNCH_TICKET_TTL_SECONDS = 120
_LAUNCH_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# 활성으로 취급하는 종료 명령 값.
_STOP_MODES = {"stop_only", "close_and_stop"}


class _SessionStreamHub:
    """Small per-process fan-out hub for account session snapshots.

    Runner endpoints are synchronous FastAPI handlers and websocket handlers
    are asynchronous, so notifications cross thread/event-loop boundaries via
    ``loop.call_soon_threadsafe``. Payloads are not cached in memory: every wake
    reads an authoritative snapshot from the database.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 0
        self._subscribers: dict[int, dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    def subscribe(self, user_id: int) -> tuple[int, asyncio.Event]:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        with self._lock:
            self._next_id += 1
            subscription_id = self._next_id
            self._subscribers.setdefault(user_id, {})[subscription_id] = (loop, event)
        return subscription_id, event

    def unsubscribe(self, user_id: int, subscription_id: int) -> None:
        with self._lock:
            account = self._subscribers.get(user_id)
            if account is None:
                return
            account.pop(subscription_id, None)
            if not account:
                self._subscribers.pop(user_id, None)

    def notify(self, user_id: int) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(user_id, {}).values())
        for loop, event in subscribers:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # The websocket loop closed between snapshotting and notifying.
                # Its finally block will remove the stale subscription.
                continue


_SESSION_STREAM_HUB = _SessionStreamHub()


def subscribe_session_stream(user_id: int) -> tuple[int, asyncio.Event]:
    return _SESSION_STREAM_HUB.subscribe(user_id)


def unsubscribe_session_stream(user_id: int, subscription_id: int) -> None:
    _SESSION_STREAM_HUB.unsubscribe(user_id, subscription_id)


def notify_sessions_changed(user_id: int) -> None:
    _SESSION_STREAM_HUB.notify(user_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _kst_label(iso: str) -> str:
    dt = _parse_iso(iso)
    return dt.astimezone(_KST).strftime("%m/%d %H:%M:%S") if dt else ""


# --- 회원 키 발급 -------------------------------------------------------
def _new_key() -> str:
    # 사람이 실행기에 입력하므로 헷갈리는 문자를 피한 URL-safe 토큰.
    return "ggp_" + secrets.token_urlsafe(24)


def _get_or_create_key_row(db, user_id: int) -> RunnerKey:
    row = db.exec(select(RunnerKey).where(RunnerKey.user_id == user_id)).first()
    if row is None:
        row = RunnerKey(user_id=user_id, key=_new_key(), created_at=_now_iso())
        db.add(row)
        db.flush()
    return row


def get_or_create_key(user_id: int) -> dict:
    """계정의 회원 키를 반환(없으면 생성). 계정당 1개."""
    with get_session() as db:
        row = _get_or_create_key_row(db, user_id)
        db.commit()
        db.refresh(row)
        return {"key": row.key, "created_at": row.created_at}


def regenerate_key(user_id: int) -> dict:
    """새 키를 발급하고 기존 키를 무효화한다(실행기에 재입력 필요)."""
    with get_session() as db:
        row = db.exec(select(RunnerKey).where(RunnerKey.user_id == user_id)).first()
        if row is None:
            row = RunnerKey(user_id=user_id, key=_new_key(), created_at=_now_iso())
            db.add(row)
        else:
            row.key = _new_key()
            row.created_at = _now_iso()
            db.add(row)
        db.commit()
        db.refresh(row)
        return {"key": row.key, "created_at": row.created_at}


def user_for_key(key: str) -> User:
    """실행기가 보낸 회원 키를 계정으로 해석한다(실패 시 401)."""
    key = (key or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="회원 키가 없어요. 마이페이지에서 키를 확인하세요.")
    with get_session() as db:
        row = db.exec(select(RunnerKey).where(RunnerKey.key == key)).first()
        if row is None:
            raise HTTPException(status_code=401, detail="유효하지 않은 회원 키예요. 마이페이지에서 다시 확인하세요.")
        user = db.get(User, row.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="계정을 찾을 수 없어요.")
        return user


# --- Web -> local runner one-time launch handoff -------------------------
def _ticket_digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("ascii")).hexdigest()


def _ticket_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Cache-Control": "no-store"},
    )


def create_launch_ticket(
    user_id: int,
    user_macro_id: int,
    testnet: bool = True,
    launch_environment: str = "production",
) -> dict:
    """Create a 120-second, single-use runner launch ticket for an owned macro."""
    if testnet is not True:
        raise _ticket_error(422, "빠른 실행 연결은 테스트넷으로만 시작할 수 있어요.")
    if launch_environment not in {"production", "local"}:
        raise _ticket_error(422, "지원하지 않는 실행기 연결 환경이에요.")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=LAUNCH_TICKET_TTL_SECONDS)
    raw_ticket = secrets.token_urlsafe(32)
    with get_session() as db:
        macro_row = db.get(UserMacro, user_macro_id)
        if macro_row is None or macro_row.user_id != user_id:
            raise _ticket_error(404, "내 매크로를 찾을 수 없어요.")

        row = RunnerLaunchTicket(
            user_id=user_id,
            user_macro_id=user_macro_id,
            token_hash=_ticket_digest(raw_ticket),
            testnet=True,
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_ms=int(expires.timestamp() * 1000),
            claimed_at="",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "launch_id": row.id,
            "launch_url": (
                "ggparrot://launch/?v=2"
                f"&env={launch_environment}&ticket={raw_ticket}"
            ),
            "expires_at": row.expires_at,
            "status": "ready",
        }


def launch_ticket_status(user_id: int, launch_id: int) -> dict:
    """Return only the lifecycle state of one ticket owned by the account."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_session() as db:
        row = db.get(RunnerLaunchTicket, launch_id)
        if row is None or row.user_id != user_id:
            raise _ticket_error(404, "실행 연결 요청을 찾을 수 없어요.")
        if row.claimed_at:
            status = "claimed"
        elif row.expires_ms <= now_ms:
            status = "expired"
        else:
            status = "ready"
        return {
            "launch_id": row.id,
            "expires_at": row.expires_at,
            "status": status,
        }


def claim_launch_ticket(ticket: str) -> dict:
    """Atomically consume a launch ticket and return its local-runner payload."""
    raw_ticket = (ticket or "").strip()
    if not _LAUNCH_TICKET_RE.fullmatch(raw_ticket):
        raise _ticket_error(404, "유효한 실행 연결 요청을 찾을 수 없어요.")

    digest = _ticket_digest(raw_ticket)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    claimed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_session() as db:
        candidate = db.exec(
            select(RunnerLaunchTicket).where(RunnerLaunchTicket.token_hash == digest)
        ).first()
        if candidate is None:
            raise _ticket_error(404, "유효한 실행 연결 요청을 찾을 수 없어요.")
        if candidate.claimed_at:
            raise _ticket_error(409, "이미 사용한 실행 연결 요청이에요.")
        if candidate.expires_ms <= now_ms:
            raise _ticket_error(410, "실행 연결 요청이 만료됐어요. 웹에서 다시 시도해 주세요.")

        macro_row = db.get(UserMacro, candidate.user_macro_id)
        if macro_row is None or macro_row.user_id != candidate.user_id:
            raise _ticket_error(404, "연결할 내 매크로를 찾을 수 없어요.")
        try:
            macro = Macro.model_validate_json(macro_row.macro_json)
        except (TypeError, ValueError):
            raise _ticket_error(422, "저장된 매크로 형식이 올바르지 않아요.")

        # The conditional UPDATE is the single-use boundary. Concurrent claims
        # can both read the row above, but only one can change claimed_at.
        result = db.exec(
            update(RunnerLaunchTicket)
            .where(
                RunnerLaunchTicket.id == candidate.id,
                RunnerLaunchTicket.claimed_at == "",
                RunnerLaunchTicket.expires_ms > now_ms,
            )
            .values(claimed_at=claimed_at)
        )
        if result.rowcount != 1:
            db.rollback()
            current = db.get(RunnerLaunchTicket, candidate.id)
            if current is not None and current.claimed_at:
                raise _ticket_error(409, "이미 사용한 실행 연결 요청이에요.")
            if current is not None and current.expires_ms <= now_ms:
                raise _ticket_error(410, "실행 연결 요청이 만료됐어요. 웹에서 다시 시도해 주세요.")
            raise _ticket_error(409, "실행 연결 요청을 사용할 수 없어요.")

        runner_key = _get_or_create_key_row(db, candidate.user_id)
        db.commit()
        return {
            "launch_id": candidate.id,
            "macro": macro.model_dump(mode="json"),
            "user_macro_id": macro_row.id,
            "name": macro_row.name,
            "symbol": macro_row.symbol,
            "runner_key": runner_key.key,
            "testnet": True,
        }


# --- 실행기용: 세션 시작/하트비트/종료확정 -----------------------------
def start_session(user: User, payload: dict) -> dict:
    """실행기가 매크로 구동을 시작할 때 세션을 만든다. session_id 를 돌려준다."""
    symbol = str(payload.get("symbol", "")).upper()
    side = str(payload.get("position_side", "long")).lower()
    leverage = max(1, int(payload.get("leverage", 1) or 1))
    market = str(payload.get("market", "")).lower()
    summary = str(payload.get("human_summary", ""))[:300]
    if market not in ("spot", "futures"):
        market = "futures" if (side == "short" or leverage > 1) else "spot"
    # 실행 중인 매크로 원문 — 마이페이지 실시간 차트에 전략 보조지표를 그리는 데 쓴다.
    # 예전 실행기는 보내지 않으므로 없으면 빈 문자열로 둔다(차트는 평단선만 그림).
    macro_json = ""
    macro = payload.get("macro")
    normalized_macro: Optional[Macro] = None
    if isinstance(macro, dict):
        try:
            normalized_macro = Macro.model_validate(macro)
            dumped = normalized_macro.model_dump_json()
            if len(dumped) <= 20000:  # 방어적 상한(정상 매크로는 ~1KB)
                macro_json = dumped
        except (TypeError, ValueError):
            macro_json = ""
            normalized_macro = None

    raw_user_macro_id = payload.get("user_macro_id")
    user_macro_id: Optional[int] = None
    if raw_user_macro_id is not None:
        try:
            user_macro_id = int(raw_user_macro_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="내 매크로 ID가 올바르지 않아요.")
        if user_macro_id <= 0:
            raise HTTPException(status_code=422, detail="내 매크로 ID가 올바르지 않아요.")
        if normalized_macro is None:
            raise HTTPException(
                status_code=422,
                detail="내 매크로 세션에는 정규화된 매크로 설정이 필요해요.",
            )

    now = _now_iso()
    with get_session() as db:
        if user_macro_id is not None:
            stored_row = db.get(UserMacro, user_macro_id)
            if stored_row is None or stored_row.user_id != user.id:
                # Do not reveal whether another account owns the requested id.
                raise HTTPException(status_code=404, detail="내 매크로를 찾을 수 없어요.")
            try:
                stored_macro = Macro.model_validate_json(stored_row.macro_json)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="저장된 매크로 형식이 올바르지 않아요.")
            if (
                normalized_macro is None
                or normalized_macro.model_dump(mode="json")
                != stored_macro.model_dump(mode="json")
            ):
                raise HTTPException(
                    status_code=409,
                    detail="실행기가 보낸 매크로가 선택한 내 매크로와 일치하지 않아요.",
                )
            # An ID-bound session has one authoritative identity: the stored,
            # normalized UserMacro. Do not let redundant runner fields drift
            # from the macro selected by the signed-in user.
            symbol = stored_macro.symbol
            side = stored_macro.position_side.value
            leverage = stored_macro.leverage
            market = stored_macro.resolved_market()
            summary = stored_row.human_summary[:300]

        row = RunSession(
            user_id=user.id,
            user_macro_id=user_macro_id,
            symbol=symbol,
            position_side=side,
            leverage=leverage,
            market=market,
            testnet=bool(payload.get("testnet", True)),
            human_summary=summary,
            macro_json=macro_json,
            status="running",
            stop_mode="",
            started_at=now,
            last_heartbeat_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        result = {"session_id": row.id, "poll_seconds": POLL_SECONDS}
    notify_sessions_changed(user.id)
    return result


def heartbeat(user: User, session_id: int, snapshot: dict) -> dict:
    """실시간 스냅샷을 저장하고, 마이페이지가 요청한 종료 명령을 돌려준다.

    응답 ``action`` : "continue" | "stop_only" | "close_and_stop".
    이미 서버에서 세션이 사라졌거나 종료됐다면 실행기도 멈추도록 "stop_only" 를 준다.
    """
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user.id:
            # 세션이 없어졌으면 실행기가 안전하게 멈추도록 종료 지시.
            return {"action": "stop_only", "reason": "세션을 찾을 수 없어요."}
        if row.status != "running":
            return {"action": row.stop_mode or "stop_only", "reason": "이미 종료 처리된 세션이에요."}

        row.last_price = float(snapshot.get("last_price", row.last_price) or 0.0)
        row.in_position = bool(snapshot.get("in_position", False))
        row.entry_price = float(snapshot.get("entry_price", 0.0) or 0.0)
        row.position_qty = float(snapshot.get("position_qty", 0.0) or 0.0)
        row.realized_pnl = float(snapshot.get("realized_pnl", 0.0) or 0.0)
        row.unrealized_pct = float(snapshot.get("unrealized_pct", 0.0) or 0.0)
        if snapshot.get("note"):
            row.note = str(snapshot["note"])[:200]
        row.last_heartbeat_at = _now_iso()
        action = row.stop_mode if row.stop_mode in _STOP_MODES else "continue"
        db.add(row)
        db.commit()
    notify_sessions_changed(user.id)
    return {"action": action}


def mark_stopped(user: User, session_id: int, status: str = "stopped", note: str = "") -> dict:
    """실행기가 종료(또는 오류 종료)를 확정 보고한다."""
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        row.status = "error" if status == "error" else "stopped"
        if note:
            row.note = str(note)[:200]
        row.stopped_at = _now_iso()
        row.in_position = bool(row.in_position and status != "stopped")  # 청산됐으면 False 로 남김
        db.add(row)
        db.commit()
    notify_sessions_changed(user.id)
    return {"ok": True}


# --- 마이페이지용: 목록 조회 / 종료 요청 -------------------------------
def _is_connected(row: RunSession) -> bool:
    """실행기가 STALE_SECONDS 안에 heartbeat 를 보냈는가."""
    hb = _parse_iso(row.last_heartbeat_at)
    if hb is None:
        return False
    return (datetime.now(timezone.utc) - hb).total_seconds() <= STALE_SECONDS


def _session_view(row: RunSession) -> dict:
    connected = _is_connected(row)
    # 실행기가 종료 명령을 받아 정리 중인 상태(플래그는 섰지만 아직 확정 보고 전).
    stopping = row.status == "running" and row.stop_mode in _STOP_MODES
    macro = None
    if getattr(row, "macro_json", ""):
        try:
            macro = json.loads(row.macro_json)
        except (TypeError, ValueError):
            macro = None
    return {
        "session_id": row.id,
        "user_macro_id": getattr(row, "user_macro_id", None),
        "symbol": row.symbol,
        "position_side": row.position_side,
        "leverage": row.leverage,
        "market": row.market,
        "testnet": row.testnet,
        "human_summary": row.human_summary,
        "macro": macro,
        "status": row.status,
        "stopping": stopping,
        "stop_mode": row.stop_mode,
        "connected": connected,
        "in_position": row.in_position,
        "last_price": row.last_price,
        "entry_price": row.entry_price,
        "position_qty": row.position_qty,
        "realized_pnl": row.realized_pnl,
        "unrealized_pct": row.unrealized_pct,
        "note": row.note,
        "started_kst": _kst_label(row.started_at),
        "heartbeat_kst": _kst_label(row.last_heartbeat_at),
        "stopped_kst": _kst_label(row.stopped_at or ""),
    }


def list_sessions(user_id: int, limit: int = 20, db=None) -> dict:
    """All active sessions plus up to ``limit`` recently ended sessions."""
    recent_limit = max(0, min(int(limit), 100))
    filters = [RunSession.status == "running"]
    if recent_limit:
        recent_ids = (
            select(RunSession.id)
            .where(RunSession.user_id == user_id, RunSession.status != "running")
            .order_by(RunSession.id.desc())
            .limit(recent_limit)
        )
        filters.append(RunSession.id.in_(recent_ids))
    session_scope = nullcontext(db) if db is not None else get_session()
    with session_scope as db:
        rows = db.exec(
            select(RunSession)
            .where(RunSession.user_id == user_id, or_(*filters))
            .order_by(RunSession.id.desc())
        ).all()
    active_rows = [row for row in rows if row.status == "running"]
    recent_rows = [row for row in rows if row.status != "running"]
    active = [_session_view(r) for r in active_rows]
    recent = [_session_view(r) for r in recent_rows]
    return {"active": active, "recent": recent, "poll_seconds": POLL_SECONDS}


def get_owned_session(user_id: int, session_id: int, db=None) -> dict:
    """Return one authoritative session view without revealing other users' IDs."""
    session_scope = nullcontext(db) if db is not None else get_session()
    with session_scope as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        return _session_view(row)


def request_stop(user_id: int, session_id: int, mode: str) -> dict:
    """마이페이지 종료 버튼: stop_mode 플래그만 세운다.

    실제 종료는 실행기가 다음 heartbeat 에서 이 값을 받아 수행한다(최대 POLL_SECONDS
    지연). ``mode`` 는 "stop_only"(매크로만) 또는 "close_and_stop"(청산 후 종료).
    """
    if mode not in _STOP_MODES:
        raise HTTPException(status_code=400, detail="종료 방식이 올바르지 않아요.")
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        if row.status != "running":
            return {"ok": True, "already_stopped": True}
        row.stop_mode = mode
        db.add(row)
        db.commit()
    notify_sessions_changed(user_id)
    return {"ok": True, "mode": mode, "note": "실행기가 곧 반영해요(최대 몇 초 지연)."}


def delete_session(user_id: int, session_id: int) -> dict:
    """세션 기록을 목록에서 지운다.

    살아 있는 실행을 지우면 실행기는 계속 도는데 화면에서만 사라져 원격 종료할
    수단이 없어진다. 그래서 heartbeat 가 아직 도착하는 running 세션은 거부하고
    먼저 종료를 요청하게 한다. 응답이 끊긴(STALE_SECONDS 초과) running 세션과
    종료·오류로 끝난 세션은 지울 수 있다 — 화면에서 '응답대기'·'오류'로 보이는
    바로 그 항목들이다.
    """
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        if row.status == "running" and _is_connected(row):
            raise HTTPException(
                status_code=409,
                detail="실행기가 아직 응답 중이에요. 먼저 종료한 뒤 목록에서 지울 수 있어요.",
            )
        db.delete(row)
        db.commit()
    notify_sessions_changed(user_id)
    return {"ok": True, "deleted_session_id": session_id}
