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

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlmodel import select

from .db import RunnerKey, RunSession, User, get_session

_KST = timezone(timedelta(hours=9))

# heartbeat 주기(실행기가 서버에 상태를 올리고 종료명령을 받아가는 간격, 초).
POLL_SECONDS = 5
# 이 시간 이상 heartbeat 가 없으면 마이페이지에서 '연결 끊김'으로 표시한다.
STALE_SECONDS = 30
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


def get_or_create_key(user_id: int) -> dict:
    """계정의 회원 키를 반환(없으면 생성). 계정당 1개."""
    with get_session() as db:
        row = db.exec(select(RunnerKey).where(RunnerKey.user_id == user_id)).first()
        if row is None:
            row = RunnerKey(user_id=user_id, key=_new_key(), created_at=_now_iso())
            db.add(row)
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


# --- 실행기용: 세션 시작/하트비트/종료확정 -----------------------------
def start_session(user: User, payload: dict) -> dict:
    """실행기가 매크로 구동을 시작할 때 세션을 만든다. session_id 를 돌려준다."""
    side = str(payload.get("position_side", "long")).lower()
    leverage = max(1, int(payload.get("leverage", 1) or 1))
    market = str(payload.get("market", "")).lower()
    if market not in ("spot", "futures"):
        market = "futures" if (side == "short" or leverage > 1) else "spot"
    # 실행 중인 매크로 원문 — 마이페이지 실시간 차트에 전략 보조지표를 그리는 데 쓴다.
    # 예전 실행기는 보내지 않으므로 없으면 빈 문자열로 둔다(차트는 평단선만 그림).
    macro_json = ""
    macro = payload.get("macro")
    if isinstance(macro, dict):
        try:
            dumped = json.dumps(macro, ensure_ascii=False)
            if len(dumped) <= 20000:  # 방어적 상한(정상 매크로는 ~1KB)
                macro_json = dumped
        except (TypeError, ValueError):
            macro_json = ""
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
            macro_json=macro_json,
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
    macro = None
    if getattr(row, "macro_json", ""):
        try:
            macro = json.loads(row.macro_json)
        except (TypeError, ValueError):
            macro = None
    return {
        "session_id": row.id,
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
