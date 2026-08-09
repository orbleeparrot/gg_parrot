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

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import select

from .db import RunnerKey, RunnerLaunchTicket, RunSession, User, UserMacro, get_session
from .engine import Macro

_KST = timezone(timedelta(hours=9))

# heartbeat 주기(실행기가 서버에 상태를 올리고 종료명령을 받아가는 간격, 초).
POLL_SECONDS = 5
# 이 시간 이상 heartbeat 가 없으면 마이페이지에서 '연결 끊김'으로 표시한다.
STALE_SECONDS = 30
# Web -> local runner handoff tickets are deliberately short-lived bearer
# credentials. Only their SHA-256 digest is persisted.
LAUNCH_TICKET_TTL_SECONDS = 120
_LAUNCH_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# 활성으로 취급하는 종료 명령 값.
_STOP_MODES = {"stop_only", "close_and_stop"}


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


def create_launch_ticket(user_id: int, user_macro_id: int, testnet: bool = True) -> dict:
    """Create a 120-second, single-use runner launch ticket for an owned macro."""
    if testnet is not True:
        raise _ticket_error(422, "빠른 실행 연결은 테스트넷으로만 시작할 수 있어요.")

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
            "launch_url": f"ggparrot://launch?v=1&ticket={raw_ticket}",
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
    side = str(payload.get("position_side", "long")).lower()
    leverage = max(1, int(payload.get("leverage", 1) or 1))
    market = str(payload.get("market", "")).lower()
    if market not in ("spot", "futures"):
        market = "futures" if (side == "short" or leverage > 1) else "spot"
    now = _now_iso()
    with get_session() as db:
        row = RunSession(
            user_id=user.id,
            symbol=str(payload.get("symbol", "")).upper(),
            position_side=side,
            leverage=leverage,
            market=market,
            testnet=bool(payload.get("testnet", True)),
            human_summary=str(payload.get("human_summary", ""))[:300],
            status="running",
            stop_mode="",
            started_at=now,
            last_heartbeat_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"session_id": row.id, "poll_seconds": POLL_SECONDS}


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
        return {"ok": True}


# --- 마이페이지용: 목록 조회 / 종료 요청 -------------------------------
def _session_view(row: RunSession) -> dict:
    hb = _parse_iso(row.last_heartbeat_at)
    connected = False
    if hb is not None:
        connected = (datetime.now(timezone.utc) - hb).total_seconds() <= STALE_SECONDS
    # 실행기가 종료 명령을 받아 정리 중인 상태(플래그는 섰지만 아직 확정 보고 전).
    stopping = row.status == "running" and row.stop_mode in _STOP_MODES
    return {
        "session_id": row.id,
        "symbol": row.symbol,
        "position_side": row.position_side,
        "leverage": row.leverage,
        "market": row.market,
        "testnet": row.testnet,
        "human_summary": row.human_summary,
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


def list_sessions(user_id: int, limit: int = 20) -> dict:
    """활성(running) 세션 + 최근 종료 세션. 활성 세션을 위로 정렬한다."""
    with get_session() as db:
        rows = db.exec(
            select(RunSession)
            .where(RunSession.user_id == user_id)
            .order_by(RunSession.id.desc())
            .limit(limit)
        ).all()
    active = [_session_view(r) for r in rows if r.status == "running"]
    recent = [_session_view(r) for r in rows if r.status != "running"]
    return {"active": active, "recent": recent, "poll_seconds": POLL_SECONDS}


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
    return {"ok": True, "mode": mode, "note": "실행기가 곧 반영해요(최대 몇 초 지연)."}
