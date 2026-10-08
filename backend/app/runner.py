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
import math
import os
import re
import secrets
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, update
from sqlmodel import Session, select

from . import macro_signing
from . import notifications as notifications_mod
from . import runner_engine  # runner_engine 은 runner 를 함수 안에서 늦게 import 한다 — 순환 없음
from .db import RunnerCommand, RunnerKey, RunnerLaunchTicket, RunSession, RunSessionEvent, User, UserMacro, get_session
from .engine import Macro, RuleType
from .exchanges import is_domestic, normalize_exchange, validate_symbol

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
# 실행 이벤트 로그 — 세션당 보관 상한과 한 번의 보고에 받는 상한.
EVENT_CAP = int(os.environ.get("RUNNER_EVENT_CAP", "500"))
EVENT_BATCH_MAX = 100
EVENT_MESSAGE_MAX = 300
_EVENT_KINDS = {"start", "info", "signal", "order", "fill", "error", "stop", "warn"}
# 평가손익 알림: 마지막 알림 기준 이만큼 움직였거나(누적), 한 heartbeat 사이 이만큼 급변하면 알린다(%p).
PNL_ALERT_STEP_PCT = 2.0
PNL_ALERT_JUMP_PCT = 1.0
# 이 종류의 실행 이벤트는 헤더 알림(에이전트)으로도 간다. start·stop 은 세션 알림이 따로 있고 info 는 로그일 뿐.
_NOTIFY_EVENT_LABELS = {"signal": "신호", "order": "주문", "fill": "체결", "error": "오류"}

# 서버 신호 프로토콜(v8+): 실행기는 판단하지 않고 heartbeat 응답의 명령만 실행한다.
SIGNAL_MIN_VERSION = os.environ.get("RUNNER_SIGNAL_MIN_VERSION", "8").strip() or "8"


def supports_signals(version: str) -> bool:
    """실행기가 서버 신호 프로토콜(v8+)을 쓰는가. 숫자 아닌 값·빈 값은 미지원."""
    v = (version or "").strip()
    return v.isascii() and v.isdigit() and len(v) <= 6 and int(v) >= int(SIGNAL_MIN_VERSION)


# 국내 거래소(업비트·빗썸) 주문은 v10 실행기부터 낼 줄 안다. 지표형 신호(v8)와는 다른 능력이라 따로 센다 —
# v8·v9 는 신호는 받아도 원화 마켓 주문 어댑터가 없어, 열어 두면 바이낸스 키로 KRW 종목을 주문하려 든다.
DOMESTIC_MIN_VERSION = os.environ.get("RUNNER_DOMESTIC_MIN_VERSION", "10").strip() or "10"
DOMESTIC_REQUIRED_DETAIL = "국내 거래소 매크로는 실행기 v10 이상이 필요해요. 실행기를 업데이트해 주세요."


def supports_domestic(version: str) -> bool:
    """실행기가 업비트·빗썸 주문을 낼 줄 아는가. 숫자 아닌 값·빈 값은 미지원."""
    v = (version or "").strip()
    return v.isascii() and v.isdigit() and len(v) <= 6 and int(v) >= int(DOMESTIC_MIN_VERSION)


SIGNAL_REQUIRED_DETAIL = "지표형 매크로는 실행기 v8 이상이 필요해요. 실행기를 업데이트해 주세요."
MACRO_REQUIRED_DETAIL = "실행기 v8 은 매크로 설정을 함께 보내야 해요."
# 실행기로는 아직 돌릴 수 없는 매크로 유형(모든 실행기 버전). C(적립식)는 서버가 3초 틱을 세어 분할 매수하는
# 규칙이라 봉·명령 모델과 맞지 않고, K(SAR)는 롱↔숏 전환(flip-to-short)을 실행기가 표현할 수 없다.
# v7 도 C 를 로컬에서 잘못(무조건 진입) 돌렸으므로 버전과 무관하게 막는다.
RUNNER_UNSUPPORTED_RULES = frozenset({RuleType.C, RuleType.K})
UNSUPPORTED_RULE_DETAIL = "실행기는 아직 이 매크로 유형(적립식·SAR)을 지원하지 않아요."
# 실행기는 macro["symbol"] 하나만 읽는다 — 여러 종목 매크로를 넘기면 첫 종목에 전액이 들어가고,
# 사용자가 백테스트·모의로 확인한 분산과 실제로 돌아가는 것이 달라진다. 어느 버전으로도 열지 않는다.
PORTFOLIO_UNSUPPORTED_DETAIL = "실행기는 아직 여러 종목 포트폴리오 매크로를 지원하지 않아요. 종목 하나로 나눠 실행해 주세요."
# 서버(runner_engine)가 세션 note 에 쓰는 마커 — 실행기 heartbeat 의 note 가 덮어쓰면 안 된다.
_SERVER_NOTE_MARKERS = (runner_engine.EXIT_FAIL_NOTE, runner_engine.LOOP_ERROR_NOTE, runner_engine.START_FAIL_NOTE)


def needs_signals(macro: Optional[Macro]) -> bool:
    """A/B(익절·손절 재진입, 지정가 밴드)는 실행기 로컬 로직이 맞다. 그 외는 서버 신호가 필요하다.
    매크로를 안 보낸 구버전(v6)은 판단할 수 없으므로 '필요'로 본다."""
    return macro is None or macro.rule_type not in (RuleType.A, RuleType.B)


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


def _active_account(db, user_id: int) -> User:
    # Serialize new runner starts/key creation with account withdrawal.
    if db.get_bind().dialect.name == "sqlite" and not db.in_transaction():
        from sqlalchemy import text as sql_text
        db.exec(sql_text("BEGIN IMMEDIATE"))
    account = db.exec(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).first()
    if account is None or account.is_deleted:
        raise HTTPException(status_code=401, detail="계정을 찾을 수 없어요.")
    return account


def _get_or_create_key_row(db, user_id: int) -> RunnerKey:
    _active_account(db, user_id)
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
        _active_account(db, user_id)
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


def user_for_key(key: str, *, db: Session | None = None) -> User:
    """실행기가 보낸 회원 키를 계정으로 해석한다(실패 시 401)."""
    key = (key or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="회원 키가 없어요. 마이페이지에서 키를 확인하세요.")
    with (nullcontext(db) if db is not None else get_session()) as db:
        row = db.exec(select(RunnerKey).where(RunnerKey.key == key)).first()
        if row is None:
            raise HTTPException(status_code=401, detail="유효하지 않은 회원 키예요. 마이페이지에서 다시 확인하세요.")
        user = db.get(User, row.user_id)
        if user is None or user.is_deleted:
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


# 독립 봇 압축 묶음(bot.py + run.bat)은 바이낸스 전용이다 — bot.py 에 국내 주문 코드가 없다.
# 매크로 파일(.ggm.json)은 국내도 내려받을 수 있다(실행기 v10+ 가 돌린다).
# 같은 문구를 realtrade.build_bundle 도 쓴다(순환 import 를 피해 글자로 둔 사본 — test_domestic_macro_file 이 둘을 견준다).
DOMESTIC_BUNDLE_DETAIL = "독립 봇 압축파일은 바이낸스 전용이에요. 업비트·빗썸은 매크로 파일(.ggm.json)을 내려받아 매크로 실행기에 넣어 주세요."


def _require_supported_exchange(macro: Macro, runner_version: str) -> None:
    """국내 거래소 매크로는 국내 주문을 낼 줄 아는 실행기(v10+)에만 내준다. 바이낸스는 버전을 묻지 않는다.

    실행기 버전을 아는 자리(세션 시작·티켓 청구)에서만 부른다 — 티켓 발급 때는 실행기가 아직 말하지 않았다.
    """
    if is_domestic(macro.exchange) and not supports_domestic(runner_version):
        raise _ticket_error(426, DOMESTIC_REQUIRED_DETAIL)


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
        _active_account(db, user_id)
        macro_row = db.get(UserMacro, user_macro_id)
        if macro_row is None or macro_row.user_id != user_id:
            raise _ticket_error(404, "내 매크로를 찾을 수 없어요.")

        try:
            macro = Macro.model_validate_json(macro_row.macro_json)
        except (TypeError, ValueError):
            raise _ticket_error(422, "저장된 매크로 형식이 올바르지 않아요.")
        # 거래소는 여기서 막지 않는다 — 발급 시점엔 실행기 버전을 모른다(실행기는 청구할 때 처음 자기 버전을 말한다).
        # 구버전 실행기에는 청구 시점에 426 으로 돌려준다(claim_launch_ticket).
        # 여러 종목은 버전과 무관하게 못 돌리므로 여기서 막는다 — 실행기는 청구 실패의 사유 문구를 버리므로
        # (티켓·키 유출 방지) 발급 시점에 막아야 사용자가 브라우저에서 이유를 읽는다. 청구 경로의 같은 게이트는
        # 발급 뒤 매크로가 여러 종목으로 바뀌는 경우를 받는다.
        if macro.is_portfolio():
            raise _ticket_error(422, PORTFOLIO_UNSUPPORTED_DETAIL)

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


def launch_ticket_status(user_id: int, launch_id: int, *, min_runner_version: str = "") -> dict:
    """Return only the lifecycle state of one ticket owned by the account.

    ``rejected`` means an outdated runner answered the launch: the ticket is still
    usable, but the web should tell the user to update instead of waiting.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_session() as db:
        row = db.get(RunnerLaunchTicket, launch_id)
        if row is None or row.user_id != user_id:
            raise _ticket_error(404, "실행 연결 요청을 찾을 수 없어요.")
        rejected_version = getattr(row, "rejected_version", "")
        if row.claimed_at:
            status = "claimed"
        elif row.expires_ms <= now_ms:
            status = "expired"
        elif getattr(row, "rejected_at", ""):
            status = "rejected"
        else:
            status = "ready"
        payload = {
            "launch_id": row.id,
            "expires_at": row.expires_at,
            "status": status,
        }
        if status == "rejected":
            payload["runner_version"] = rejected_version
            payload["min_runner_version"] = _required_version_for(db, row.user_macro_id, min_runner_version)
        return payload


def _required_version_for(db, user_macro_id: int, general_min: str) -> str:
    """이 티켓의 매크로가 요구하는 실행기 버전. 국내 거래소 매크로는 일반 최소 버전이 아니라 v10 이다.

    웹이 "v9이에요. 웹 연결은 v6부터 돼요" 처럼 스스로 모순된 안내를 하지 않게, 숫자는 서버가 매크로를 보고 정한다.
    매크로를 못 읽으면 일반 최소 버전으로 둔다(상태 조회가 그 이유로 실패하면 안 된다).
    """
    macro_row = db.get(UserMacro, user_macro_id)
    if macro_row is None:
        return general_min
    try:
        macro = Macro.model_validate_json(macro_row.macro_json)
    except (TypeError, ValueError):
        return general_min
    return DOMESTIC_MIN_VERSION if is_domestic(macro.exchange) else general_min


def mark_launch_ticket_rejected(ticket: str, runner_version: str) -> None:
    """Record that an outdated runner answered this ticket. Best effort, never raises.

    The ticket stays claimable by an up-to-date runner; only the web-visible state
    changes so the wizard can explain the situation immediately.
    """
    raw_ticket = (ticket or "").strip()
    if not _LAUNCH_TICKET_RE.fullmatch(raw_ticket):
        return
    digest = _ticket_digest(raw_ticket)
    now = datetime.now(timezone.utc)
    try:
        with get_session() as db:
            row = db.exec(
                select(RunnerLaunchTicket).where(RunnerLaunchTicket.token_hash == digest)
            ).first()
            if row is None or row.claimed_at or row.expires_ms <= int(now.timestamp() * 1000):
                return
            row.rejected_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            row.rejected_version = str(runner_version or "")[:12]
            db.add(row)
            db.commit()
    except Exception:
        return


def claim_launch_ticket(ticket: str, runner_version: str = "") -> dict:
    """Atomically consume a launch ticket and return its local-runner payload.

    ``runner_version`` 은 최소 버전 검사를 통과한 값이다. 여기서는 매크로 종류를 보고 한 번 더 가른다 —
    지표형 매크로는 서버 신호(v8+)가 필요하므로 구버전에는 티켓을 내주지 않는다(티켓은 살려 둔다).
    """
    raw_ticket = (ticket or "").strip()
    if not _LAUNCH_TICKET_RE.fullmatch(raw_ticket):
        raise _ticket_error(404, "유효한 실행 연결 요청을 찾을 수 없어요.")

    digest = _ticket_digest(raw_ticket)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    claimed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text as sql_text
            db.exec(sql_text("BEGIN IMMEDIATE"))
        candidate = db.exec(
            select(RunnerLaunchTicket).where(RunnerLaunchTicket.token_hash == digest)
        ).first()
        if candidate is None:
            raise _ticket_error(404, "유효한 실행 연결 요청을 찾을 수 없어요.")
        # Account first, then ticket: use the same lock order as withdrawal.
        _active_account(db, candidate.user_id)
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
        if macro.rule_type in RUNNER_UNSUPPORTED_RULES:
            # 실행기가 못 돌리는 유형은 버전과 무관하게 거절 — 업데이트로 풀리는 문제가 아니므로 버전 게이트(426)보다
            # 먼저 보고, '거절(업데이트 필요)' 표시도 하지 않고 티켓도 소비하지 않는다(잠금만 푼다).
            db.rollback()
            raise _ticket_error(422, UNSUPPORTED_RULE_DETAIL)
        if macro.is_portfolio():
            # 못 돌리는 유형과 같은 취급 — 업데이트로 풀리는 문제가 아니므로 거절 표시도, 티켓 소비도 하지 않는다.
            db.rollback()
            raise _ticket_error(422, PORTFOLIO_UNSUPPORTED_DETAIL)
        try:
            _require_supported_exchange(macro, runner_version)
        except HTTPException:
            # 구버전 실행기 + 국내 매크로: 지표형과 같은 취급 — 거절만 남기고 티켓은 소비하지 않는다(업데이트하면 청구 가능).
            db.rollback()
            mark_launch_ticket_rejected(raw_ticket, runner_version)
            raise
        if not supports_signals(runner_version) and needs_signals(macro):
            # 구버전 실행기 + 지표형 매크로: 거절 사실만 남기고(웹이 상태 조회로 알아챔) 티켓은 소비하지 않는다.
            # mark_launch_ticket_rejected 는 자기 세션을 여니 BEGIN IMMEDIATE 잠금을 먼저 푼다.
            db.rollback()
            mark_launch_ticket_rejected(raw_ticket, runner_version)
            raise _ticket_error(426, SIGNAL_REQUIRED_DETAIL)

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


# --- 실행 모드 ---------------------------------------------------------
# 실행기가 고른 모드. 모르는 값은 빈 문자열로 둔다 — 억지로 어느 모드라고 적으면 연습과 실전이
# 기록에서 뒤섞인다. 빈 값은 '모드를 말하지 않은 실행기' 이고, 그 세션은 testnet 플래그로만 읽는다.
RUN_MODES = ("mock", "testnet", "live")
_MODE_LABELS = {"mock": "모의", "testnet": "테스트넷", "live": "메인넷(실거래)"}


def _run_mode(raw) -> str:
    mode = str(raw or "").strip().lower()
    return mode if mode in RUN_MODES else ""


def environment_label(row: RunSession) -> str:
    """세션 기록 · 알림에 쓰는 실행 환경 이름.

    실전 여부는 testnet 플래그가 정한다(언제나 그랬다). 연습 세션의 이름만 모드가 가른다 —
    바이낸스 모의는 테스트넷에도 주문을 보내지 않으므로 '테스트넷' 이 아니다.
    """
    if not row.testnet:
        return "메인넷(실거래)"
    return _MODE_LABELS.get(_run_mode(getattr(row, "mode", "")), "테스트넷")


# --- 실행기용: 세션 시작/하트비트/종료확정 -----------------------------
def start_session(user: User, payload: dict) -> dict:
    """실행기가 매크로 구동을 시작할 때 세션을 만든다. session_id 를 돌려준다."""
    symbol = str(payload.get("symbol", "")).upper()
    # 거래소는 이 요청이 처음으로 실어 오는 값이다 — 예전엔 아무도 보내지 않아 모든 세션이 바이낸스로 기록됐다.
    # 세션 행에는 거래소 칸이 없고 매크로 원문(macro_json)이 그 역할을 한다. 여기서는 말이 서로 맞는지만 본다:
    # 모르는 이름 · 종목과 안 맞는 거래소 · 매크로와 다른 거래소는 버전과 무관하게 잘못된 요청(422)이다.
    raw_macro = payload.get("macro")
    try:
        payload_exchange = normalize_exchange(payload.get("exchange") or "binance")
        if symbol:
            validate_symbol(symbol, payload_exchange)
    except ValueError as exc:
        raise _ticket_error(422, str(exc))
    if isinstance(raw_macro, dict) and str(raw_macro.get("exchange") or "binance").strip().lower() != payload_exchange:
        raise _ticket_error(422, "시작 요청의 거래소가 매크로의 거래소와 달라요.")
    side = str(payload.get("position_side", "long")).lower()
    leverage = max(1, int(payload.get("leverage", 1) or 1))
    market = str(payload.get("market", "")).lower()
    summary = str(payload.get("human_summary", ""))[:300]
    # 시작 요청에 실린 실행기 버전. 예전 실행기는 보내지 않는다(빈 문자열).
    runner_version = str(payload.get("runner_version") or "").strip()[:12]
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

    # 버전 게이트(2026-09-22 결정): v8+ 는 서버가 전략을 돌리므로 매크로가 필수(422).
    # v8 미만은 A/B 만 로컬 판단으로 돌릴 수 있고, 지표형이거나 매크로를 안 보내 판별 불가면 426.
    signal_runner = supports_signals(runner_version)
    if normalized_macro is None and is_domestic(payload_exchange) and not supports_domestic(runner_version):
        # 매크로를 못 읽었어도(보내지 않았거나 깨짐) 요청이 국내 거래소를 말하면 구버전에는 열지 않는다 —
        # 아래 '매크로를 함께 보내라(422)' 보다 먼저여야 v8·v9 실행기가 해야 할 일(업데이트)을 듣는다.
        raise _ticket_error(426, DOMESTIC_REQUIRED_DETAIL)
    if signal_runner and normalized_macro is None:
        raise HTTPException(status_code=422, detail=MACRO_REQUIRED_DETAIL)
    if normalized_macro is not None and normalized_macro.rule_type in RUNNER_UNSUPPORTED_RULES:
        # 적립식(C)·SAR(K)는 어느 실행기 버전으로도 돌리지 않는다(위 주석 참고) — 업데이트 안내(426)보다 먼저 알린다.
        raise HTTPException(status_code=422, detail=UNSUPPORTED_RULE_DETAIL)
    if normalized_macro is not None and normalized_macro.is_portfolio():
        # 여러 종목은 어느 실행기 버전으로도 돌리지 않는다(위 상수 주석 참고) — 업데이트 안내(426)보다 먼저 알린다.
        raise HTTPException(status_code=422, detail=PORTFOLIO_UNSUPPORTED_DETAIL)
    # 매크로를 읽은 경우의 국내 거래소 게이트 — 업데이트로 풀리는 문제라 위 '못 돌리는 유형(422)' 뒤, 지표형 게이트 앞에 둔다
    # (청구 경로와 같은 순서).
    if normalized_macro is not None:
        _require_supported_exchange(normalized_macro, runner_version)
    if not signal_runner and needs_signals(normalized_macro):
        raise HTTPException(status_code=426, detail=SIGNAL_REQUIRED_DETAIL)

    raw_user_macro_id = payload.get("user_macro_id")
    user_macro_id: Optional[int] = None
    if raw_user_macro_id is not None:
        try:
            user_macro_id = int(raw_user_macro_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="내 매크로 ID가 올바르지 않아요.")
        if user_macro_id <= 0:
            raise HTTPException(status_code=422, detail="내 매크로 ID가 올바르지 않아요.")
        # 매크로 본문이 없는 경우는 위 버전 게이트가 이미 걸렀다(v8+ 는 422, 그 미만은 426).

    now = _now_iso()
    # 매크로 출처: 티켓 경로면 web, 파일이면 동봉된 서명을 검증해 원본/수정본을 가른다.
    macro_sig = payload.get("macro_sig")
    macro_origin = macro_signing.classify_origin(
        normalized_macro, user_macro_id=user_macro_id, sig=macro_sig if isinstance(macro_sig, dict) else None
    )
    macro_digest = macro_signing.digest(normalized_macro) if normalized_macro is not None else ""
    with get_session() as db:
        _active_account(db, user.id)
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
            mode=_run_mode(payload.get("mode")),
            human_summary=summary,
            macro_json=macro_json,
            status="running",
            stop_mode="",
            started_at=now,
            last_heartbeat_at=now,
            runner_version=runner_version,
            macro_origin=macro_origin,
            macro_digest=macro_digest,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        # 첫 이벤트에 지문을 남긴다 — 문의가 오면 이 줄만 봐도 무엇을 돌렸는지 안다.
        _append_events(db, row, [{
            "ts": now,
            "kind": "start",
            "message": (
                f"실행 시작 · 실행기 v{runner_version or '?'} · {environment_label(row)}"
                f" · 매크로 {macro_signing.ORIGIN_LABELS.get(macro_origin, macro_origin)}"
                + (f" · 지문 {macro_digest}" if macro_digest else "")
            ),
        }])
        # 헤더 알림(에이전트) — 어느 매크로가 어디서 돌기 시작했는지.
        notifications_mod.notify(
            db, user.id, "agent", f"{symbol} 매크로 실행 시작",
            environment_label(row) + (f" · {summary}" if summary else ""),
            "/agents", session_id=row.id,
            data={"event": "start", "symbol": symbol, "testnet": row.testnet, "mode": row.mode},
        )
        db.commit()
        result = {
            "session_id": row.id,
            "poll_seconds": POLL_SECONDS,
            "macro_origin": macro_origin,
            "macro_origin_label": macro_signing.ORIGIN_LABELS.get(macro_origin, macro_origin),
            "macro_digest": macro_digest,
        }
    notify_sessions_changed(user.id)
    if signal_runner:
        # v8+: 서버 측 전략 드라이버를 이벤트 루프에 올린다(웜업·구독·틱 루프는 runner_engine 이 맡는다).
        runner_engine.schedule_start(result["session_id"])
    from .agent_features.position_news.runtime import request_collection
    request_collection()
    from .agent_features.whale_activity.runtime import request_collection as request_whale_collection
    request_whale_collection()
    return result


def heartbeat(user: User, session_id: int, snapshot: dict, *, db: Session | None = None) -> dict:
    """실시간 스냅샷을 저장하고, 마이페이지가 요청한 종료 명령을 돌려준다.

    응답 ``action`` : "continue" | "stop_only" | "close_and_stop".
    이미 서버에서 세션이 사라졌거나 종료됐다면 실행기도 멈추도록 "stop_only" 를 준다.

    v8+ 실행기에는 ``commands`` 로 서버 전략이 낸 미실행 주문 명령을 함께 주고, 요청의 ``acks`` 로
    직전 명령의 실행 결과를 받는다. 구버전에는 항상 빈 리스트(무해).
    """
    events = snapshot.pop("events", None)
    acks = snapshot.pop("acks", None)
    # Capture before commit: a shared authenticated User expires on commit;
    # reading user.id afterwards would start a second transaction/checkout.
    user_id = user.id
    with (nullcontext(db) if db is not None else get_session()) as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            # 세션이 없어졌으면 실행기가 안전하게 멈추도록 종료 지시.
            return {"action": "stop_only", "reason": "세션을 찾을 수 없어요.", "commands": []}
        if row.status != "running":
            return {"action": row.stop_mode or "stop_only", "reason": "이미 종료 처리된 세션이에요.", "commands": []}
        _append_events(db, row, events)

        previous_pct, previously_in_position = row.unrealized_pct, row.in_position
        row.last_price = float(snapshot.get("last_price", row.last_price) or 0.0)
        row.in_position = bool(snapshot.get("in_position", False))
        row.position_uncertain = bool(snapshot.get("position_uncertain", row.position_uncertain))
        row.entry_price = float(snapshot.get("entry_price", 0.0) or 0.0)
        row.position_qty = float(snapshot.get("position_qty", 0.0) or 0.0)
        _track_invested(row)
        _track_invested(row)
        row.realized_pnl = float(snapshot.get("realized_pnl", 0.0) or 0.0)
        row.unrealized_pct = float(snapshot.get("unrealized_pct", 0.0) or 0.0)
        # 실행기는 매 heartbeat 에 note(기본 "") 를 보낸다 — 서버가 쓴 마커(청산 실패·루프 오류)는 덮어쓰지 않는다.
        # 마커는 runner_engine 이 청산 성공 ack 등에서 스스로 지운다.
        if "note" in snapshot and row.note not in _SERVER_NOTE_MARKERS:
            row.note = str(snapshot["note"])[:200]
        row.last_heartbeat_at = _now_iso()
        _alert_pnl_move(db, row, previous_pct, previously_in_position)
        action = row.stop_mode if row.stop_mode in _STOP_MODES else "continue"
        commands: list = []
        if supports_signals(row.runner_version):
            # ack 반영 → 만료 정리 후 남은 명령 → 서버 전략과 실행기 포지션 유무 대조(경고만).
            now_ms = runner_engine._now_ms()
            runner_engine.apply_acks(db, row, acks or [], now_ms)
            commands = runner_engine.pending_commands(db, row, now_ms)
            if not row.position_uncertain:
                # 실행기 스스로 포지션을 모르는 상태면 대조할 근거가 없다.
                runner_engine.check_position_mismatch(db, row, row.in_position)
            if action != "continue":
                # 종료 중인 세션엔 매매 명령을 주지 않는다(명령 행은 그대로 — 만료로 정리된다).
                commands = []
        db.add(row)
        db.commit()
    notify_sessions_changed(user_id)
    return {"action": action, "commands": commands}


def _alert_pnl_move(db, row: RunSession, previous_pct: float, previously_in_position: bool) -> bool:
    """평가손익이 크게 움직이면 에이전트 알림을 남긴다(커밋은 호출자).

    기준(``pnl_alert_pct``)은 마지막으로 알린 시점의 값이고 포지션이 새로 열리면 0 이다. 기준에서
    ``PNL_ALERT_STEP_PCT`` 이상 움직였거나 직전 heartbeat 보다 ``PNL_ALERT_JUMP_PCT`` 이상 급변했을 때
    한 번 알리고 기준을 옮긴다 — 경계에서 왔다 갔다 해도 같은 알림이 반복되지 않는다.
    """
    if not row.in_position:
        row.pnl_alert_pct = 0.0
        return False
    if not previously_in_position:
        row.pnl_alert_pct = 0.0
    pct = float(row.unrealized_pct or 0.0)
    moved = pct - float(row.pnl_alert_pct or 0.0)
    jump = pct - float(previous_pct or 0.0) if previously_in_position else 0.0
    sudden = abs(jump) >= PNL_ALERT_JUMP_PCT
    if abs(moved) < PNL_ALERT_STEP_PCT and not sudden:
        return False
    delta = jump if sudden else moved
    direction = "up" if delta > 0 else "down"
    if sudden:
        change = f"{jump:+.2f}%p 급{'등' if jump > 0 else '락'}"
    else:
        change = f"마지막 알림보다 {moved:+.2f}%p {'상승' if moved > 0 else '하락'}"
    body = f"{change} · 평단 {row.entry_price:,.2f} → 현재가 {row.last_price:,.2f}"
    notifications_mod.notify(
        db, row.user_id, "agent", f"{row.symbol} 평가손익 {pct:+.2f}%", body, "/agents",
        session_id=row.id,
        data={"event": "pnl", "symbol": row.symbol, "pct": round(pct, 2), "delta": round(delta, 2),
              "direction": direction, "sudden": sudden, "entry_price": row.entry_price, "last_price": row.last_price},
    )
    row.pnl_alert_pct = pct
    return True


def mark_stopped(
    user: User,
    session_id: int,
    status: str = "stopped",
    note: str = "",
    *,
    snapshot: dict | None = None,
    events: list | None = None,
) -> dict:
    """실행기가 종료(또는 오류 종료)를 확정 보고한다."""
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        already_final = row.status != "running"
        _append_events(db, row, events)
        if not already_final and row.in_position:
            # 청산 후 종료는 아래에서 현재 포지션 값을 0 으로 지운다 — 결과 화면용으로 마지막 포지션을 남긴다.
            row.final_entry_price = row.entry_price
            row.final_position_qty = row.position_qty
            row.final_unrealized_pct = row.unrealized_pct
        row.status = "error" if status == "error" else "stopped"
        if note:
            row.note = str(note)[:200]
        row.stopped_at = _now_iso()
        # A stopped process may still have an open exchange position. Legacy
        # runners omit the final snapshot; preserve their last known position.
        if snapshot is not None:
            row.in_position = bool(snapshot.get("in_position", row.in_position))
            row.position_uncertain = bool(snapshot.get("position_uncertain", row.position_uncertain))
            for field in ("last_price", "entry_price", "position_qty", "realized_pnl", "unrealized_pct"):
                if field in snapshot:
                    value = float(snapshot[field] or 0)
                    if not math.isfinite(value):
                        raise HTTPException(status_code=422, detail="최종 포지션 값이 올바르지 않아요.")
                    setattr(row, field, value)
            _track_invested(row)  # 종료 직전 스냅샷의 포지션도 투입금에 반영
        elif status == "stopped" and note in {"청산 완료 후 종료", "포지션 없이 종료"}:
            # v5 explicitly reports these outcomes without a final snapshot.
            row.in_position = False
            row.position_qty = row.entry_price = row.unrealized_pct = 0.0
        if row.position_uncertain or (row.stop_mode == "close_and_stop" and row.in_position):
            row.status = "error"
            row.note = note or "청산 완료를 확인하지 못했어요. 거래소에서 포지션을 확인하세요."
        db.add(row)
        # 같은 종료 보고를 재전송해도 종료 이벤트는 한 번만 남긴다.
        if not already_final:
            _append_events(db, row, [{
                "ts": row.stopped_at,
                "kind": "stop",
                "message": ("오류 종료" if row.status == "error" else "종료")
                + (f" · {row.note}" if row.note else "")
                + f" · 누적 실현손익 {row.realized_pnl:+.2f} USDT"
                + (" · 포지션 보유 중" if row.in_position else ""),
            }])
            notifications_mod.notify(
                db, user.id, "agent",
                f"{row.symbol} 매크로 {'오류 종료' if row.status == 'error' else '실행 종료'}",
                (f"{row.note} · " if row.note else "")
                + f"누적 실현손익 {row.realized_pnl:+.2f} USDT"
                + (" · 포지션 보유 중" if row.in_position else ""),
                "/agents", session_id=row.id,
                data={"event": "error" if row.status == "error" else "stop",
                      "symbol": row.symbol, "realized_pnl": row.realized_pnl},
            )
        db.commit()
        if not already_final:
            # 기록이 하나 늘어난 순간에만 정리한다 — 몇 초마다 부르는 목록 조회는 쿼리 한 번으로 둔다.
            try:
                prune_ended_sessions(user.id, db)
            except Exception:
                db.rollback()
    notify_sessions_changed(user.id)
    # v8+ 라면 서버 측 드라이버도 내린다(구버전·이미 없는 세션이면 runner_engine 이 무시한다).
    runner_engine.schedule_stop(session_id)
    return {"ok": True}


# --- 실행 이벤트 로그 ------------------------------------------------------
def _append_events(db, row: RunSession, events) -> int:
    """실행기가 보낸 이벤트를 세션에 붙인다(정제·상한 적용). 커밋은 호출자가 한다."""
    if not isinstance(events, list) or not events:
        return 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    added = 0
    notified = False
    for item in events[:EVENT_BATCH_MAX]:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        kind = str(item.get("kind") or "info").strip().lower()
        if kind not in _EVENT_KINDS:
            kind = "info"
        ts = str(item.get("ts") or "").strip()[:32] or _now_iso()
        db.add(RunSessionEvent(
            session_id=row.id,
            user_id=row.user_id,
            ts=ts,
            kind=kind,
            message=message[:EVENT_MESSAGE_MAX],
            created_ms=now_ms + added,  # 같은 배치 안 순서 보존
        ))
        added += 1
        if kind in _NOTIFY_EVENT_LABELS:
            notifications_mod.notify(
                db, row.user_id, "agent", f"{row.symbol} 매크로 · {_NOTIFY_EVENT_LABELS[kind]}",
                message, "/agents", session_id=row.id,
                data={"event": kind, "symbol": row.symbol}, trim=False,
            )
            notified = True
    if added:
        db.flush()
        _trim_events(db, row.id)
        if notified:
            notifications_mod.trim_user(db, row.user_id)
    return added


def _trim_events(db, session_id: int) -> None:
    from sqlalchemy import func

    total = int(db.exec(
        select(func.count(RunSessionEvent.id)).where(RunSessionEvent.session_id == session_id)
    ).one())
    overflow = total - EVENT_CAP
    if overflow <= 0:
        return
    oldest = db.exec(
        select(RunSessionEvent)
        .where(RunSessionEvent.session_id == session_id)
        .order_by(RunSessionEvent.id.asc())
        .limit(overflow)
    ).all()
    for event in oldest:
        db.delete(event)


def _event_view(e: RunSessionEvent) -> dict:
    return {
        "id": e.id,
        "ts": e.ts,
        "ts_kst": _kst_label(e.ts),
        "kind": e.kind,
        "message": e.message,
    }


def list_events(user_id: int, session_id: int, limit: int = 300) -> dict:
    """내 세션의 실행 로그(최신순)."""
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        rows = db.exec(
            select(RunSessionEvent)
            .where(RunSessionEvent.session_id == session_id)
            .order_by(RunSessionEvent.id.desc())
            .limit(max(1, min(int(limit), EVENT_CAP)))
        ).all()
        return {
            "session_id": session_id,
            "status": row.status,
            "macro_origin": getattr(row, "macro_origin", "") or "",
            "macro_origin_label": macro_signing.ORIGIN_LABELS.get(getattr(row, "macro_origin", "") or "", ""),
            "macro_digest": getattr(row, "macro_digest", "") or "",
            "events": [_event_view(e) for e in rows],
        }


# --- 마이페이지용: 목록 조회 / 종료 요청 -------------------------------
def _is_connected(row: RunSession) -> bool:
    """실행기가 STALE_SECONDS 안에 heartbeat 를 보냈는가."""
    hb = _parse_iso(row.last_heartbeat_at)
    if hb is None:
        return False
    return (datetime.now(timezone.utc) - hb).total_seconds() <= STALE_SECONDS


def _track_invested(row: RunSession) -> None:
    """투입금 = 세션 동안 실제로 들어간 최대 금액(수량×진입가). 줄어들지 않는다(부분 청산해도 분모는 그대로)."""
    notional = float(row.position_qty or 0.0) * float(row.entry_price or 0.0)
    if notional > float(getattr(row, "invested_usdt", 0.0) or 0.0):
        row.invested_usdt = notional


def session_returns(row: RunSession) -> dict:
    """투입금 대비 수익률. 평가손익(USDT)은 실행기가 보낸 진입가 대비 %를 현재 보유 금액에 곱해 되계산한다."""
    invested = float(getattr(row, "invested_usdt", 0.0) or 0.0)
    held = float(row.position_qty or 0.0) * float(row.entry_price or 0.0) if row.in_position else 0.0
    unrealized = held * float(row.unrealized_pct or 0.0) / 100.0
    realized = float(row.realized_pnl or 0.0)
    if invested <= 0:
        return {"invested_usdt": 0.0, "unrealized_usdt": unrealized, "total_return_pct": None, "final_return_pct": None}
    return {
        "invested_usdt": round(invested, 4),
        "unrealized_usdt": round(unrealized, 4),
        "total_return_pct": round((realized + unrealized) / invested * 100.0, 4),
        "final_return_pct": round(realized / invested * 100.0, 4),
    }


def _track_invested(row: RunSession) -> None:
    """투입금 = 수량×진입가의 세션 최대값. 포지션을 키우면 올라가고, 줄이거나 닫아도 내려가지 않는다."""
    if row.in_position and row.position_qty > 0 and row.entry_price > 0:
        row.invested_usdt = max(float(getattr(row, "invested_usdt", 0.0) or 0.0), row.position_qty * row.entry_price)


def total_return_pct(row: RunSession) -> Optional[float]:
    """투입금 대비 총수익률(%) = (누적 실현손익 + 지금 포지션의 평가손익) / 투입금. 투입금이 0 이면 None."""
    invested = float(getattr(row, "invested_usdt", 0.0) or 0.0)
    if invested <= 0:
        return None
    unrealized = 0.0
    if row.in_position and row.position_qty > 0 and row.entry_price > 0:
        unrealized = row.position_qty * row.entry_price * float(row.unrealized_pct or 0.0) / 100.0
    return round((float(row.realized_pnl or 0.0) + unrealized) / invested * 100.0, 4)


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
        # 연습 세션의 이름은 모드가 가른다(모의 ≠ 테스트넷). 모드를 보내지 않은 옛 세션은 빈 문자열.
        "mode": _run_mode(getattr(row, "mode", "")),
        "human_summary": row.human_summary,
        "macro": macro,
        "status": row.status,
        "stopping": stopping,
        "stop_mode": row.stop_mode,
        "runner_version": getattr(row, "runner_version", ""),
        "macro_origin": getattr(row, "macro_origin", "") or "",
        "macro_origin_label": macro_signing.ORIGIN_LABELS.get(getattr(row, "macro_origin", "") or "", ""),
        "macro_digest": getattr(row, "macro_digest", "") or "",
        "connected": connected,
        "in_position": row.in_position,
        "position_uncertain": row.position_uncertain,
        "last_price": row.last_price,
        "entry_price": row.entry_price,
        "position_qty": row.position_qty,
        "invested_usdt": float(getattr(row, "invested_usdt", 0.0) or 0.0),
        "return_pct": total_return_pct(row),
        "realized_pnl": row.realized_pnl,
        "unrealized_pct": row.unrealized_pct,
        "final_entry_price": getattr(row, "final_entry_price", 0.0) or 0.0,
        "final_position_qty": getattr(row, "final_position_qty", 0.0) or 0.0,
        "final_unrealized_pct": getattr(row, "final_unrealized_pct", 0.0) or 0.0,
        **session_returns(row),
        "note": row.note,
        "started_at": row.started_at,
        "stopped_at": row.stopped_at or "",
        "pinned": bool(getattr(row, "pinned", False)),
        "started_kst": _kst_label(row.started_at),
        "heartbeat_kst": _kst_label(row.last_heartbeat_at),
        # UTC instant lets the UI prove a retry log was followed by a successful
        # report; never substitute the browser's mount/render timestamp.
        "last_heartbeat_at": row.last_heartbeat_at,
        "stopped_kst": _kst_label(row.stopped_at or ""),
    }


# 종료 기록 보관 — 계정마다 보관하지 않은 종료 세션은 최근 30건·30일까지만 남긴다(2026-10-02).
# 사용자가 '보관'한 기록은 정리에서 빠지되 10건까지. 정리할 때 세션의 이벤트·실행 명령도 같이 지운다.
# 정리는 세션이 끝날 때(mark_stopped)·보관을 풀 때, 그리고 하루 한 번 전체 계정(maybe_prune_all_ended_sessions).
# 목록은 정리와 무관하게 기간이 지난 기록을 바로 뺀다.
HISTORY_KEEP = 30
HISTORY_DAYS = 30
PIN_LIMIT = 10


def _ended_at(row: RunSession) -> Optional[datetime]:
    raw = row.stopped_at or row.last_heartbeat_at or row.started_at or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _delete_session_rows(db, session_ids: list[int]) -> None:
    from sqlalchemy import delete as sql_delete

    if not session_ids:
        return
    db.exec(sql_delete(RunSessionEvent).where(RunSessionEvent.session_id.in_(session_ids)))
    db.exec(sql_delete(RunnerCommand).where(RunnerCommand.session_id.in_(session_ids)))
    db.exec(sql_delete(RunSession).where(RunSession.id.in_(session_ids)))


def prune_ended_sessions(user_id: int, db, *, now: Optional[datetime] = None) -> int:
    """보관하지 않은 종료 기록을 최근 HISTORY_KEEP 건 · HISTORY_DAYS 일만 남기고 지운다. 지운 개수를 돌려준다."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    rows = db.exec(
        select(RunSession)
        .where(RunSession.user_id == user_id, RunSession.status != "running", RunSession.pinned == False)  # noqa: E712
        .order_by(RunSession.id.desc())
    ).all()
    doomed = []
    for index, row in enumerate(rows):
        ended = _ended_at(row)
        if index >= HISTORY_KEEP or (ended is not None and ended < cutoff):
            doomed.append(row.id)
    _delete_session_rows(db, doomed)
    if doomed:
        db.commit()
    return len(doomed)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_DAILY_PRUNE_MS = 24 * 3_600_000
_last_daily_prune_ms = 0
_daily_prune_lock = threading.Lock()


def prune_all_ended_sessions(db, *, now: Optional[datetime] = None) -> int:
    """모든 계정의 종료 기록을 정리한다 — 기간(30일)이 지난 것은 한 번에, 30건을 넘긴 계정은 계정별로."""
    now = now or datetime.now(timezone.utc)
    cutoff = _iso_utc(now - timedelta(days=HISTORY_DAYS))
    expired = db.exec(select(RunSession.id).where(
        RunSession.status != "running", RunSession.pinned == False,  # noqa: E712
        func.coalesce(RunSession.stopped_at, RunSession.started_at) < cutoff,
    )).all()
    _delete_session_rows(db, list(expired))
    db.commit()
    removed = len(expired)
    over = db.exec(
        select(RunSession.user_id)
        .where(RunSession.status != "running", RunSession.pinned == False)  # noqa: E712
        .group_by(RunSession.user_id)
        .having(func.count(RunSession.id) > HISTORY_KEEP)
    ).all()
    for user_id in over:
        removed += prune_ended_sessions(user_id, db, now=now)
    return removed


def maybe_prune_all_ended_sessions(db) -> int:
    """하루에 한 번만 전체 정리를 돈다(별도 스케줄러 없이, 방문 비콘 정리와 같은 방식). 실패해도 요청은 성공."""
    global _last_daily_prune_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with _daily_prune_lock:
        if now_ms - _last_daily_prune_ms < _DAILY_PRUNE_MS:
            return 0
        _last_daily_prune_ms = now_ms
    try:
        return prune_all_ended_sessions(db)
    except Exception:
        db.rollback()
        return 0


def list_sessions(user_id: int, limit: int = HISTORY_KEEP + PIN_LIMIT, db=None) -> dict:
    """All active sessions plus the retained ended sessions (최근 30건 + 보관 10건까지)."""
    recent_limit = max(0, min(int(limit), 100))
    filters = [RunSession.status == "running"]
    if recent_limit:
        # 기간이 지난 기록은 정리가 아직 돌지 않았어도 화면에서 바로 뺀다(보관한 기록은 예외).
        cutoff = _iso_utc(datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS))
        recent_ids = (
            select(RunSession.id)
            .where(
                RunSession.user_id == user_id, RunSession.status != "running",
                or_(RunSession.pinned == True, func.coalesce(RunSession.stopped_at, RunSession.started_at) >= cutoff),  # noqa: E712
            )
            .order_by(RunSession.pinned.desc(), RunSession.id.desc())
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
    return {
        "active": active, "recent": recent, "poll_seconds": POLL_SECONDS,
        "history_policy": {"keep": HISTORY_KEEP, "days": HISTORY_DAYS, "pin_limit": PIN_LIMIT},
    }


def set_pinned(user_id: int, session_id: int, pinned: bool) -> dict:
    """종료 기록 보관 켜기/끄기. 실행 중 세션은 정리 대상이 아니라 보관할 필요가 없다."""
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        if pinned and row.status == "running":
            raise HTTPException(status_code=409, detail="실행 중인 에이전트는 종료된 뒤에 보관할 수 있어요.")
        if pinned and not row.pinned:
            count = len(db.exec(select(RunSession.id).where(
                RunSession.user_id == user_id, RunSession.pinned == True)).all())  # noqa: E712
            if count >= PIN_LIMIT:
                raise HTTPException(
                    status_code=409,
                    detail=f"보관은 {PIN_LIMIT}건까지예요. 다른 기록의 보관을 먼저 풀어 주세요.",
                )
        row.pinned = bool(pinned)
        db.add(row)
        db.commit()
        if not pinned:
            prune_ended_sessions(user_id, db)  # 보관을 풀어 30건·30일 밖이 된 기록은 바로 정리
    notify_sessions_changed(user_id)
    return {"ok": True, "session_id": session_id, "pinned": bool(pinned)}


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
        # 이벤트·실행 명령도 같이 지운다(예전엔 실행 명령이 남아 쌓였다).
        _delete_session_rows(db, [session_id])
        db.commit()
    notify_sessions_changed(user_id)
    return {"ok": True, "deleted_session_id": session_id}
