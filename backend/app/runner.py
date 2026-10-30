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
import logging
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
from . import runner_settings
from .db import (AgentCommentaryState, AgentUtterance, RunnerCommand, RunnerKey, RunnerLaunchTicket,
                 RunnerSettingChange, RunSession, RunSessionEvent, User, UserMacro, get_session)
from engine import Macro, RuleType
from engine.version import ENGINE_VERSION, meets_engine_version, required_engine_version
from engine.exchanges import is_domestic, normalize_exchange, validate_symbol

_KST = timezone(timedelta(hours=9))
_log = logging.getLogger(__name__)

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
# 평가손익 알림은 구간(±3 · 5 · 10 · 15 · 20 · 30 · 50%)을 같은 쪽으로 처음 넘을 때 한 번 — 2026-10-17 전에는
# 2%p 이동 · 1%p 급변마다 알려, 한 포지션에서 "급락 · 평단 210 → 현재가 210" 같은 알림이 열 번 넘게 쌓였다.
PNL_ALERT_BANDS = (3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0)
# 이 종류의 실행 이벤트는 헤더 알림(에이전트)으로도 간다. start·stop 은 세션 알림이 따로 있고 info 는 로그일 뿐.
# 종 알림은 체결과 오류만 — 신호 · 주문 단계까지 알리면 한 번 사는 데 알림이 네 개(서버 신호 · 실행기 신호 ·
# 주문 · 체결) 쌓였다. 단계 기록은 활동 로그에 그대로 남는다.
_NOTIFY_EVENT_LABELS = {"fill": "체결", "error": "오류"}

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


# 바이낸스 연결 도우미(테스트넷 · 데모 · 실거래 키 안내)는 실행기 v19 부터 있다. 웹 빠른 실행은 바이낸스 매크로도
# '실행기에서 연결 · 실행' 한 화면으로 보내므로, 그 화면을 따라 그릴 도우미가 있는 실행기에만 티켓을 내준다.
# 운영에 공개된 실행기가 이 버전에 닿기 전에는 켜지 않는다 — 서버를 먼저 배포해도 지금 흐름(웹 4단계)이 그대로다.
BINANCE_HELPER_MIN_VERSION = os.environ.get("RUNNER_BINANCE_HELPER_MIN_VERSION", "19").strip() or "19"
BINANCE_HELPER_REQUIRED_DETAIL = "바이낸스 빠른 실행은 실행기 v19 이상이 필요해요(연결 도우미). 실행기를 업데이트해 주세요."


def _version_at_least(version: str, minimum: str) -> bool:
    v, m = (version or "").strip(), (minimum or "").strip()
    return v.isascii() and v.isdigit() and len(v) <= 6 and m.isdigit() and int(v) >= int(m)


def binance_helper_active() -> bool:
    """운영에 공개된 실행기(RUNNER_DOWNLOAD_URL)가 바이낸스 연결 도우미를 가졌는가."""
    from .runner_release import resolve_runner_release
    return _version_at_least(resolve_runner_release(os.environ)["version"], BINANCE_HELPER_MIN_VERSION)


SIGNAL_REQUIRED_DETAIL = "지표형 매크로는 실행기 v8 이상이 필요해요. 실행기를 업데이트해 주세요."
MACRO_REQUIRED_DETAIL = "실행기 v8 은 매크로 설정을 함께 보내야 해요."
# **서버가 판단할 때** 실행기로 돌릴 수 없는 매크로 유형. C(적립식)는 서버가 3초 틱을 세어 분할 매수하는
# 규칙이라 봉·명령 모델과 맞지 않고, K(SAR)는 롱↔숏 전환(flip-to-short)을 명령으로 표현할 수 없다.
# v7 도 C 를 로컬에서 잘못(무조건 진입) 돌렸으므로 버전과 무관하게 막았다.
#
# **실행기가 스스로 판단하면 둘 다 사라지는 제약이다.** 실행기는 자기 시계로 '며칠마다' 를 세고,
# 방향 전환도 자기 장부 안에서 한다 — 명령 모델을 거치지 않으므로 표현할 수 없던 것이 없다.
# 그래서 로컬 판단 세션에서는 막지 않는다(`unsupported_rule` 참고).
SERVER_DRIVEN_UNSUPPORTED_RULES = frozenset({RuleType.C, RuleType.K})
# 옛 이름 — 서버가 판단하는 경우의 뜻으로 남긴다.
RUNNER_UNSUPPORTED_RULES = SERVER_DRIVEN_UNSUPPORTED_RULES
UNSUPPORTED_RULE_DETAIL = "실행기는 아직 이 매크로 유형(적립식·SAR)을 지원하지 않아요."


def unsupported_rule(macro: Macro, decides_locally: bool) -> bool:
    """이 매크로를 열면 안 되는가.

    실행기가 스스로 판단하는 세션이면 열한 유형을 다 돌린다 — C·K 를 막던 이유가
    서버 명령 모델에 있었고, 그 모델을 거치지 않기 때문이다.
    """
    if decides_locally:
        return False
    return macro.rule_type in SERVER_DRIVEN_UNSUPPORTED_RULES
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


# 실행기가 스스로 판단하는 길을 서버가 열어 두는가. 꺼 두면 실행기가 요청해도 서버가 판단한다 —
# 거래 흐름을 바꾸는 전환이라 코드가 다 들어간 뒤에도 운영에서 한 번 더 켜야 한다.
LOCAL_DECISIONS_ENABLED = os.environ.get("RUNNER_LOCAL_DECISIONS", "").strip().lower() in ("1", "true", "yes")


def wants_local_decisions(payload: dict) -> bool:
    """실행기가 '내가 판단하겠다' 고 말했는가. 서버 스위치가 꺼져 있으면 무엇을 말하든 거짓."""
    return LOCAL_DECISIONS_ENABLED and str((payload or {}).get("decides") or "").strip().lower() == "local"


ENGINE_REQUIRED_DETAIL = (
    "이 매크로는 더 새로운 판단 엔진이 필요해요. 실행기를 업데이트해 주세요."
)


def _require_engine_version(macro: Macro, engine_version: str) -> None:
    """실행기가 **스스로 판단**해도 백테스트와 같은 답이 나오는 세대인지 본다.

    실행기가 엔진 세대를 밝히지 않으면(지금 배포된 전부) 통과시킨다 — 판단을 서버가
    하므로 그 실행기의 엔진은 결과에 영향이 없다. 실행기가 판단을 넘겨받으면서
    세대를 싣고 오고, 그때부터 이 비교가 일을 한다.

    늦게 사는 것보다 **다르게 사는 것**이 나쁘다. 늦은 건 사용자 눈에 보이지만,
    옛 엔진이 다른 자리에서 사고판 건 보이지 않는다.
    """
    if not meets_engine_version(engine_version, macro):
        raise _ticket_error(
            426,
            f"{ENGINE_REQUIRED_DETAIL}"
            f"(필요 {required_engine_version(macro)} · 지금 {str(engine_version or '').strip() or '알 수 없음'})",
        )


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
        progress = getattr(row, "setup_progress", "")
        if status == "claimed" and progress:
            try:
                payload["progress"] = json.loads(progress)
                payload["progress_at"] = getattr(row, "setup_progress_at", "")
            except ValueError:
                pass
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
    if is_domestic(macro.exchange):
        return DOMESTIC_MIN_VERSION
    if normalize_exchange(macro.exchange) == "binance" and binance_helper_active():
        return str(max(int(BINANCE_HELPER_MIN_VERSION), int(general_min) if str(general_min).isdigit() else 0))
    return general_min


# 실행기 → 웹 가이드 진행 단계. 청구된 티켓에, 그 계정의 회원 키로만, 청구 뒤 이 시간 안에만 쓴다.
SETUP_PROGRESS_WINDOW_SECONDS = 2 * 60 * 60


def record_setup_progress(user: User, launch_id: int, progress: dict) -> dict:
    """실행기가 알린 연결 도우미 단계를 티켓에 덮어쓴다(가장 최근 한 건만). 모양은 요청 모델이 이미 걸렀다."""
    now = datetime.now(timezone.utc)
    with get_session() as db:
        row = db.get(RunnerLaunchTicket, launch_id)
        if row is None or row.user_id != user.id or not row.claimed_at:
            raise _ticket_error(404, "실행 연결 요청을 찾을 수 없어요.")
        try:
            claimed = datetime.strptime(row.claimed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise _ticket_error(410, "이 실행 연결은 끝났어요.")
        if now - claimed > timedelta(seconds=SETUP_PROGRESS_WINDOW_SECONDS):
            raise _ticket_error(410, "이 실행 연결은 끝났어요.")
        macro_row = db.get(UserMacro, row.user_macro_id)
        try:
            exchange = Macro.model_validate_json(macro_row.macro_json).exchange if macro_row else ""
        except (TypeError, ValueError):
            exchange = ""
        if progress.get("env") and progress.get("exchange") != "binance":
            raise _ticket_error(422, "국내 거래소 진행 단계에는 바이낸스 환경을 싣지 않아요.")
        if exchange and normalize_exchange(exchange) != progress.get("exchange"):
            # 실행기에서 다른 매크로 파일을 열었다 — 웹 가이드가 엉뚱한 거래소 화면을 그리지 않게 받지 않는다.
            raise _ticket_error(409, "이 실행 연결의 매크로와 거래소가 달라요.")
        row.setup_progress = json.dumps(progress, ensure_ascii=False, separators=(",", ":"))
        row.setup_progress_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.add(row)
        db.commit()
    return {"ok": True}


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


def claim_launch_ticket(
    ticket: str, runner_version: str = "", engine_version: str = "", decides: str = ""
) -> dict:
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
        local = wants_local_decisions({"decides": decides})
        if unsupported_rule(macro, local):
            # 서버가 판단하는 세션에서 못 돌리는 유형은 버전과 무관하게 거절 — 업데이트로 풀리는 문제가
            # 아니므로 버전 게이트(426)보다 먼저 보고, '거절(업데이트 필요)' 표시도 하지 않고 티켓도
            # 소비하지 않는다(잠금만 푼다).
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
        if (normalize_exchange(macro.exchange) == "binance" and binance_helper_active()
                and not _version_at_least(runner_version, BINANCE_HELPER_MIN_VERSION)):
            # 웹 빠른 실행의 바이낸스 화면은 실행기 도우미를 따라 그린다 — 도우미가 없는 실행기는 업데이트를 안내한다.
            # 국내 매크로와 같은 취급: 거절만 남기고 티켓은 소비하지 않는다. 실행기에서 직접 파일을 여는 길은 막지 않는다.
            db.rollback()
            mark_launch_ticket_rejected(raw_ticket, runner_version)
            raise _ticket_error(426, BINANCE_HELPER_REQUIRED_DETAIL)
        try:
            _require_engine_version(macro, engine_version)
        except HTTPException:
            # 거래소 게이트와 같은 취급 — 거절만 남기고 티켓은 소비하지 않는다(업데이트하면 청구 가능).
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
    # 실행기에 묶여 있는 판단 엔진의 세대. 지금 배포된 실행기는 보내지 않는다(빈 문자열).
    engine_version = str(payload.get("engine_version") or "").strip()[:12]
    # 실행기가 스스로 판단하는 세션인가 — 그렇다면 서버는 드라이버를 돌리지 않고 비추기만 한다.
    decides_locally = wants_local_decisions(payload)
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
    if normalized_macro is not None and unsupported_rule(normalized_macro, decides_locally):
        # 적립식(C)·SAR(K)는 **서버가 판단할 때** 돌리지 않는다(위 주석 참고) — 업데이트 안내(426)보다
        # 먼저 알린다. 실행기가 스스로 판단하면 그 제약이 없으므로 열어 준다.
        raise HTTPException(status_code=422, detail=UNSUPPORTED_RULE_DETAIL)
    if normalized_macro is not None and normalized_macro.is_portfolio():
        # 여러 종목은 어느 실행기 버전으로도 돌리지 않는다(위 상수 주석 참고) — 업데이트 안내(426)보다 먼저 알린다.
        raise HTTPException(status_code=422, detail=PORTFOLIO_UNSUPPORTED_DETAIL)
    # 매크로를 읽은 경우의 국내 거래소 게이트 — 업데이트로 풀리는 문제라 위 '못 돌리는 유형(422)' 뒤, 지표형 게이트 앞에 둔다
    # (청구 경로와 같은 순서).
    if normalized_macro is not None:
        _require_supported_exchange(normalized_macro, runner_version)
        # 엔진 세대 게이트 — 거래소와 같은 자리(업데이트로 풀리는 문제)에 둔다. 세대를 밝히지
        # 않는 실행기는 통과한다(판단을 서버가 하므로 그 실행기의 엔진은 결과에 영향이 없다).
        _require_engine_version(normalized_macro, engine_version)
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
            engine_version=engine_version,
            decides_locally=decides_locally,
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
            "severity": "INFO", "event_name": "session.started",
            "event_key": f"session:{row.id}:started", "operation_id": f"session:{row.id}",
            "details": {"status": row.status, "runner_version": row.runner_version,
                        "engine_version": row.engine_version,
                        "macro_origin": row.macro_origin, "macro_digest": row.macro_digest},
            "message": (
                f"실행 시작 · 실행기 v{runner_version or '?'} · {environment_label(row)}"
                f" · 매크로 {macro_signing.ORIGIN_LABELS.get(macro_origin, macro_origin)}"
                + (f" · 지문 {macro_digest}" if macro_digest else "")
            ),
        }], component="server")
        # 헤더 알림(에이전트) — 어느 매크로가 어디서 돌기 시작했는지.
        notifications_mod.notify(
            db, user.id, "agent", f"{symbol} 매크로 실행 시작",
            environment_label(row) + (f" · {summary}" if summary else ""),
            "/agents", session_id=row.id,
            data={"event": "start", "symbol": symbol, "testnet": row.testnet, "mode": row.mode},
            activity={"event_key": f"session:{row.id}:started", "event_name": "session.started",
                      "occurred_at": now},
        )
        db.commit()
        result = {
            "session_id": row.id,
            "poll_seconds": POLL_SECONDS,
            "macro_origin": macro_origin,
            "macro_origin_label": macro_signing.ORIGIN_LABELS.get(macro_origin, macro_origin),
            "macro_digest": macro_digest,
            # 이 세션의 판단을 누가 하는가. 실행기는 이 값을 보고 로컬 엔진을 켠다 —
            # 서버가 거절했는데 실행기가 혼자 판단하면 서버 명령과 겹쳐 두 번 산다.
            "decides": "local" if decides_locally else "server",
        }
    notify_sessions_changed(user.id)
    if signal_runner and not decides_locally:
        # v8+: 서버 측 전략 드라이버를 이벤트 루프에 올린다(웜업·구독·틱 루프는 runner_engine 이 맡는다).
        # 실행기가 스스로 판단하는 세션에는 띄우지 않는다 — 양쪽이 동시에 판단하면 같은 신호로 두 번 산다.
        runner_engine.schedule_start(result["session_id"])
    from .agent_features.position_news.runtime import request_collection
    request_collection()
    from .agent_features.whale_activity.runtime import request_collection as request_whale_collection
    request_whale_collection()
    _wake_commentary()
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
        previous_qty = float(row.position_qty or 0.0)  # 이번 명령을 실행하기 전 보유 수량 — 되돌리기 판단에 쓴다
        row.last_price = float(snapshot.get("last_price", row.last_price) or 0.0)
        row.in_position = bool(snapshot.get("in_position", False))
        row.position_uncertain = bool(snapshot.get("position_uncertain", row.position_uncertain))
        row.entry_price = float(snapshot.get("entry_price", 0.0) or 0.0)
        row.position_qty = float(snapshot.get("position_qty", 0.0) or 0.0)
        _track_invested(row)
        row.realized_pnl = float(snapshot.get("realized_pnl", 0.0) or 0.0)
        row.unrealized_pct = float(snapshot.get("unrealized_pct", 0.0) or 0.0)
        for field in NET_PNL_FIELDS:
            setattr(row, field, _net_value(snapshot.get(field)))
        # 실행기는 매 heartbeat 에 note(기본 "") 를 보낸다 — 서버가 쓴 마커(청산 실패·루프 오류)는 덮어쓰지 않는다.
        # 마커는 runner_engine 이 청산 성공 ack 등에서 스스로 지운다.
        if "note" in snapshot and row.note not in _SERVER_NOTE_MARKERS:
            row.note = str(snapshot["note"])[:200]
        row.last_heartbeat_at = _now_iso()
        _alert_pnl_move(db, row, previous_pct, previously_in_position)
        action = row.stop_mode if row.stop_mode in _STOP_MODES else "continue"
        commands: list = []
        if row.decides_locally:
            # 판단은 실행기가 한다 — 서버는 비추기만. 실행기가 올린 체결과 전략 상태를 기록한다.
            runner_engine.record_local_fills(db, row, snapshot.pop("local_fills", None))
            runner_engine.store_local_state(row, snapshot.pop("strategy_state", None))
        elif supports_signals(row.runner_version):
            # ack 반영 → 만료 → 주문이 안 나간 진입은 서버 전략도 되돌림 → 보낼 명령 → 포지션 유무 대조(경고만).
            now_ms = runner_engine._now_ms()
            commands = runner_engine.exchange_commands(db, row, acks or [], now_ms, previous_qty=previous_qty)
            if not row.position_uncertain:
                # 실행기 스스로 포지션을 모르는 상태면 대조할 근거가 없다.
                runner_engine.check_position_mismatch(db, row, row.in_position)
            if action != "continue":
                # 종료 중인 세션엔 매매 명령을 주지 않는다(명령 행은 그대로 — 만료로 정리된다).
                commands = []
        # 실행기가 "이 설정으로 돌고 있다" 고 보낸 지문 — 증빙에 반영 시각을 적는다.
        confirm_settings(db, row, snapshot.get("settings_digest"))
        # 예약해 둔 설정 변경은 포지션이 빈 뒤에 적용한다 — 위에서 이번 스냅샷의
        # 포지션을 반영한 **뒤**라, 막 청산된 경우에도 이번 heartbeat 에서 바로 적용된다.
        restart = apply_pending_settings(db, row)
        db.add(row)
        accepted_event_keys = _confirmed_event_keys(db, row, events)
        wait_args = _entry_wait_args(row) if action == "continue" else None
        settings = _settings_view(row)
        db.commit()
    if restart:
        runner_engine.schedule_restart(session_id)
    notify_sessions_changed(user_id)
    if any(isinstance(event, dict) and str(event.get("kind") or "").lower() in {"fill", "error"} for event in (events or [])):
        _wake_commentary()  # 체결 · 오류는 껄무새 중계가 바로 말하게
    result = {"action": action, "commands": commands}
    # 실행기가 쓰는 값(투자 금액 · 안전망)만 실어 보낸다. 이 기능을 모르는 옛 실행기는
    # 무시하므로 **설정 변경 요청 자체를 버전으로 막는다**(SETTINGS_MIN_VERSION) —
    # 안 막으면 서버 전략만 바뀌고 실행기 안전망은 옛 값으로 남아 둘이 어긋난다.
    if settings is not None:
        result["settings"] = settings
    # 진입을 기다리는 매크로가 아직 안 샀으면 '무엇을 · 얼마나 · 언제' 를 함께 보낸다(실행기 화면 · 구버전은 무시).
    # 시세(캐시)는 DB 연결을 붙잡지 않도록 커밋 뒤에 읽는다 — 그래서 필요한 값은 커밋 전에 꺼내 둔다.
    if wait_args is not None:
        from . import entry_wait
        wait = entry_wait.for_session(**wait_args)
        if wait:
            result["entry_wait"] = wait
    if any(isinstance(event, dict) and event.get("event_key") for event in (events or [])):
        result["accepted_event_keys"] = accepted_event_keys
    return result


NET_PNL_FIELDS = ("unrealized_net", "unrealized_net_pct", "realized_net")


def _net_value(value) -> Optional[float]:
    """실행기가 보낸 수수료 반영 값 — 보내지 않았거나(옛 실행기) 모르거나(null) 숫자가 아니면 None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def position_pnl_pct(row: RunSession) -> tuple[float, bool]:
    """알림 · 웹이 '평가손익'으로 보이는 % 와 수수료 반영 여부.

    실행기(0.20.13+)가 보낸 수수료 반영 값(실행기 화면의 '평가손익 · 지금 정리하면')이 있으면 그 값,
    없으면 평단 대비 가격(수수료 전). 손절 · 익절까지의 거리는 늘 평단 대비 가격(unrealized_pct)으로 잰다.
    """
    net = getattr(row, "unrealized_net_pct", None)
    if net is not None:
        return float(net), True
    return float(row.unrealized_pct or 0.0), False


def _pnl_band(pct: float) -> float:
    hit = max((band for band in PNL_ALERT_BANDS if abs(pct) >= band), default=0.0)
    return math.copysign(hit, pct) if hit else 0.0


def _alert_pnl_move(db, row: RunSession, previous_pct: float, previously_in_position: bool) -> bool:
    """평가손익이 새 구간(±3 · 5 · 10 …%)에 처음 들어가면 에이전트 알림을 남긴다(커밋은 호출자).

    ``pnl_alert_pct`` 에는 마지막으로 알린 구간(부호 포함)을 둔다. 같은 쪽 더 깊은 구간이거나 반대쪽 구간일 때만
    알린다 — 구간 경계에서 왔다 갔다 해도 같은 알림이 반복되지 않는다. 포지션이 새로 열리면 0 부터 다시 센다.
    """
    if not row.in_position:
        row.pnl_alert_pct = 0.0
        return False
    if not previously_in_position:
        row.pnl_alert_pct = 0.0
    pct, fees_included = position_pnl_pct(row)
    band = _pnl_band(pct)
    last = float(row.pnl_alert_pct or 0.0)
    if not band or (last and (band > 0) == (last > 0) and abs(band) <= abs(last)):
        return False
    direction = "up" if band > 0 else "down"
    quote_digits = 0 if str(row.symbol or "").upper().startswith("KRW-") else 2
    body = (f"{'수익' if band > 0 else '손실'}이 {band:+g}%를 넘었어요 · "
            f"평단 {row.entry_price:,.{quote_digits}f} → 현재가 {row.last_price:,.{quote_digits}f}")
    notifications_mod.notify(
        db, row.user_id, "agent", f"{row.symbol} 평가손익 {pct:+.2f}%{'' if fees_included else '(수수료 전)'}", body, "/agents",
        session_id=row.id,
        data={"event": "pnl", "symbol": row.symbol, "pct": round(pct, 2), "band": band, "fees_included": fees_included,
              "direction": direction, "entry_price": row.entry_price, "last_price": row.last_price},
        activity={"event_name": "session.pnl_observed", "details": {"observed_value": pct, "threshold": band,
                  "price": row.last_price, "verification": "server_observation_not_exchange_fill"}},
    )
    row.pnl_alert_pct = band
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
            row.final_unrealized_net = row.unrealized_net
            row.final_unrealized_net_pct = row.unrealized_net_pct
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
            for field in NET_PNL_FIELDS:
                if field in snapshot:
                    setattr(row, field, _net_value(snapshot[field]))
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
                "severity": "ERROR" if row.status == "error" else "INFO",
                "event_name": "session.stopped", "event_key": f"session:{row.id}:stopped",
                "operation_id": f"session:{row.id}",
                "details": {"status": row.status, "reason": row.note,
                            "in_position": row.in_position, "position_uncertain": row.position_uncertain,
                            "realized_pnl": row.realized_pnl, "realized_net": row.realized_net, "ack_source": "runner",
                            "verification": "runner_reported_not_exchange_verified"},
                "message": ("오류 종료" if row.status == "error" else "종료")
                + (f" · {row.note}" if row.note else "")
                + f" · 누적 실현손익 {realized_pnl_text(row)}"
                + (" · 포지션 보유 중" if row.in_position else ""),
            }], component="server")
            notifications_mod.notify(
                db, user.id, "agent",
                f"{row.symbol} 매크로 {'오류 종료' if row.status == 'error' else '실행 종료'}",
                (f"{row.note} · " if row.note else "")
                + f"누적 실현손익 {realized_pnl_text(row)}"
                + (" · 포지션 보유 중" if row.in_position else ""),
                "/agents", session_id=row.id,
                data={"event": "error" if row.status == "error" else "stop",
                      "symbol": row.symbol, "realized_pnl": row.realized_pnl, "realized_net": row.realized_net},
                activity={"event_key": f"session:{row.id}:stopped", "event_name": "session.stopped",
                          "occurred_at": row.stopped_at},
            )
        accepted_event_keys = _confirmed_event_keys(db, row, events)
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
    _wake_commentary()
    result = {"ok": True}
    if any(isinstance(event, dict) and event.get("event_key") for event in (events or [])):
        result["accepted_event_keys"] = accepted_event_keys
    return result


def _wake_commentary() -> None:
    try:
        from . import agent_commentary_runtime
        agent_commentary_runtime.request_cycle()
    except Exception:
        _log.debug("agent commentary wake failed", exc_info=True)


# --- 실행 이벤트 로그 ------------------------------------------------------
def _append_events(db, row: RunSession, events, *, component: str = "runner") -> int:
    """Persist safe immutable events, without making telemetry a trading prerequisite.

    The default is an unverified runner report. Only server-controlled call sites
    may pass ``engine``/``server``. Stable keys deduplicate upload retries in the
    database; old clients omitting a key still retain every emitted row.
    """
    if not isinstance(events, list) or not events:
        return 0
    # begin_nested flushes already-pending trading/session changes first. If that
    # primary write fails, the error must still reach its caller. A subsequent
    # telemetry failure rolls back ONLY this savepoint, never a command ack.
    savepoint = db.begin_nested()
    try:
        with savepoint:
            return _append_event_batch(db, row, events, component=component)
    except Exception:
        # Do not log the rejected payload, exception text, or credentials.
        _log.warning("Runner activity log storage unavailable (session=%s)", row.id)
        return 0


def _append_event_batch(db, row: RunSession, events, *, component: str) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from .agent_event_schema import normalize_event, safe_details

    trusted = component in {"engine", "server", "browser_observation", "news_collector"}
    component = component if trusted else "runner"
    batch = [dict(item) for item in events[:EVENT_BATCH_MAX] if isinstance(item, dict)]
    operation_ids = set()
    if not trusted:
        for item in batch:
            operation = str(item.get("operation_id") or "")[:121]
            if re.fullmatch(r"command:[1-9][0-9]{0,18}", operation):
                operation_ids.add(int(operation.split(":")[1]))
    allowed_commands = set(db.exec(select(RunnerCommand.id).where(
        RunnerCommand.session_id == row.id, RunnerCommand.id.in_(operation_ids),
    )).all()) if operation_ids else set()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    added = 0
    notified = False
    runner_names = {"runner.message", "runner.order_result", "runner.command_received",
                    "runner.order_requested", "runner.order_failed",
                    "runner.command_reported", "runner.command_failed", "runner.connection_lost",
                    "runner.connection_restored", "runner.entry_blocked"}
    for item in batch:
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        kind = str(item.get("kind") or "info").strip().lower()
        if kind not in (_EVENT_KINDS | {"news", "news_error"} if trusted else _EVENT_KINDS):
            kind = "info"
        item["kind"] = kind
        # Missing/invalid event time is unknown; created_ms independently records
        # reception. Do not fabricate an occurrence timestamp for old clients.
        item["ts"] = str(item.get("ts") or "").strip()[:32]
        if not trusted:
            # Client-controlled fields must not impersonate a verified exchange
            # fill or collide with signed server news-receipt identifiers.
            if item.get("event_name") not in runner_names:
                item["event_name"] = "runner.message"
            key = str(item.get("event_key") or "")
            if key and not key.startswith("runner:"):
                item["event_key"] = "runner:" + key
            operation = str(item.get("operation_id") or "")
            # Local risk exits have no server command. Their locally generated
            # request ID can still correlate request/result inside THIS session;
            # it is explicitly a runner report, never an exchange verification.
            local_order = re.fullmatch(r"order:ggp-[a-f0-9]{28}", operation)
            if not local_order and not any(operation == f"command:{cid}" for cid in allowed_commands):
                item["operation_id"] = ""
        normalized = normalize_event(item, component=component)
        if not normalized["message"]:
            continue
        if not trusted:
            details = json.loads(normalized["details_json"] or "{}")
            details.update(ack_source="runner", verification="runner_reported_not_exchange_verified")
            # A client's per-event field must not relabel a mock session as
            # live. For old sessions with no mode, keep only an explicit valid
            # runner claim; never infer mock/testnet/live from the testnet flag.
            if row.mode in {"mock", "testnet", "live"}:
                details["mode"] = row.mode
            elif details.get("mode") not in {"mock", "testnet", "live"}:
                details.pop("mode", None)
            normalized["details_json"] = json.dumps(safe_details(details), ensure_ascii=False, sort_keys=True)
        statement = insert(RunSessionEvent).values(
            session_id=row.id, user_id=row.user_id, created_ms=now_ms, **normalized,
        ).on_conflict_do_nothing(
            index_elements=[RunSessionEvent.session_id, RunSessionEvent.event_key],
        ).returning(RunSessionEvent.id)
        event_id = db.exec(statement).scalar_one_or_none()
        if event_id is None:
            continue
        added += 1
        # The result event itself is the fill notification's origin. The GUI
        # displays that result without emitting a second legacy fill event.
        # Request and command-report stages are useful logs, not extra alerts.
        extra_runner_stage = normalized["event_name"] in {
            "runner.order_requested", "runner.order_failed",
            "runner.command_reported", "runner.command_failed",
        }
        if kind in _NOTIFY_EVENT_LABELS and not extra_runner_stage and not (trusted and item.get("_notify") is False):
            notifications_mod.notify(
                db, row.user_id, "agent", f"{row.symbol} 매크로 · {_NOTIFY_EVENT_LABELS[kind]}",
                normalized["message"], "/agents", session_id=row.id,
                data={"event": kind, "symbol": row.symbol, "activity_event_id": event_id,
                      "activity_event_key": normalized["event_key"]}, trim=False,
            )
            notified = True
    if added:
        db.flush()
        _trim_events(db, row.id)
        if notified:
            notifications_mod.trim_user(db, row.user_id)
    return added


def _confirmed_event_keys(db, row: RunSession, events) -> list[str]:
    """Read durable candidates in the transaction; return ONLY after its commit.

    An HTTP success or command ACK is not an event-storage acknowledgement.
    Legacy keyless messages cannot be acknowledged as reliable deliveries.
    """
    from .agent_event_schema import safe_identifier
    keys = [event.get("event_key") for event in (events or [])[:EVENT_BATCH_MAX]
            if isinstance(event, dict) and isinstance(event.get("event_key"), str)
            and event["event_key"].startswith("runner:") and safe_identifier(event["event_key"])]
    if not keys:
        return []
    stored = set(db.exec(select(RunSessionEvent.event_key).where(
        RunSessionEvent.session_id == row.id, RunSessionEvent.user_id == row.user_id,
        RunSessionEvent.event_key.in_(keys))).all())
    return list(dict.fromkeys(key for key in keys if key in stored))


def record_runner_events(user: User, session_id: int, events: list, *, db: Session | None = None) -> dict:
    """Replay logs only, even for ended runs; never touch position/commands.

    Kept separate from heartbeat so recovery after process restart cannot send
    an empty trading snapshot or move old events onto a newly started session.
    """
    user_id = user.id
    with (nullcontext(db) if db is not None else get_session()) as owned:
        row = owned.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(404, "세션을 찾을 수 없어요.")
        _append_events(owned, row, events)
        confirmed = _confirmed_event_keys(owned, row, events)
        owned.commit()
    return {"ok": True, "accepted_event_keys": confirmed}


def _trim_events(db, session_id: int) -> None:
    from sqlalchemy import func

    # Auxiliary news cannot displace the entire trade history. Apply its smaller
    # cap first; runner-only legacy semantics and the combined cap stay intact.
    news_filter = (RunSessionEvent.session_id == session_id,
                   RunSessionEvent.kind.in_(("news", "news_error")))
    news_total = int(db.exec(select(func.count(RunSessionEvent.id)).where(*news_filter)).one())
    news_overflow = news_total - news_event_cap()
    if news_overflow > 0:
        old_news = db.exec(select(RunSessionEvent).where(*news_filter)
                          .order_by(RunSessionEvent.id.asc()).limit(news_overflow)).all()
        for event in old_news:
            db.delete(event)
        db.flush()
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


def news_event_cap() -> int:
    return max(1, min(100, EVENT_CAP // 5))


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
            .where(RunSessionEvent.session_id == session_id,
                   RunSessionEvent.kind.not_in(("news", "news_error")))
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


def _pnl_parts(row: RunSession) -> tuple[float, float, bool]:
    """(누적 실현손익, 지금 포지션의 평가손익 금액, 수수료 반영 여부).

    실행기(0.20.13+)가 수수료 반영 값을 다 보냈으면 그 값 — 실행기 화면의 숫자와 같다. 하나라도 모르면
    둘 다 수수료 전(실현은 realized_pnl, 평가는 진입가 대비 %를 보유 금액에 곱해 되계산) — 섞으면 어느 쪽 숫자도 아니다.
    """
    holding = bool(row.in_position) and float(row.position_qty or 0.0) > 0 and float(row.entry_price or 0.0) > 0
    realized_net = getattr(row, "realized_net", None)
    unrealized_net = getattr(row, "unrealized_net", None) if holding else 0.0
    if realized_net is not None and unrealized_net is not None:
        return float(realized_net), float(unrealized_net), True
    held = float(row.position_qty or 0.0) * float(row.entry_price or 0.0) if holding else 0.0
    return float(row.realized_pnl or 0.0), held * float(row.unrealized_pct or 0.0) / 100.0, False


def session_returns(row: RunSession) -> dict:
    """투입금 대비 수익률과 평가손익 금액 — 수수료 반영 값이 있으면 그 값(_pnl_parts)."""
    invested = float(getattr(row, "invested_usdt", 0.0) or 0.0)
    realized, unrealized, fees_included = _pnl_parts(row)
    if invested <= 0:
        return {"invested_usdt": 0.0, "unrealized_usdt": unrealized, "total_return_pct": None, "final_return_pct": None,
                "fees_included": fees_included}
    return {
        "invested_usdt": round(invested, 4),
        "unrealized_usdt": round(unrealized, 4),
        "total_return_pct": round((realized + unrealized) / invested * 100.0, 4),
        "final_return_pct": round(realized / invested * 100.0, 4),
        "fees_included": fees_included,
    }


def _track_invested(row: RunSession) -> None:
    """투입금 = 수량×진입가의 세션 최대값. 포지션을 키우면 올라가고, 줄이거나 닫아도 내려가지 않는다."""
    if row.in_position and row.position_qty > 0 and row.entry_price > 0:
        row.invested_usdt = max(float(getattr(row, "invested_usdt", 0.0) or 0.0), row.position_qty * row.entry_price)


def total_return_pct(row: RunSession) -> Optional[float]:
    """투입금 대비 총수익률(%) = (누적 실현손익 + 지금 포지션의 평가손익) / 투입금. 투입금이 0 이면 None.
    수수료 반영 값이 있으면 그 값으로 센다(_pnl_parts)."""
    invested = float(getattr(row, "invested_usdt", 0.0) or 0.0)
    if invested <= 0:
        return None
    realized, unrealized, _ = _pnl_parts(row)
    return round((realized + unrealized) / invested * 100.0, 4)


def realized_pnl_text(row: RunSession) -> str:
    """누적 실현손익을 **그 세션의 돈 단위**로 적는다.

    2026-10-12: 거래소를 안 보고 "USDT" 를 글자로 박아 두어, 업비트·빗썸 세션의 알림과 실행
    로그가 원화 손익을 달러로 찍었다(화면 본문은 처음부터 맞았다 — 틀린 건 이 두 자리뿐).

    RunSession 에는 거래소 칸이 없다. 국내 매크로는 스키마(validate_symbol)가 ``KRW-*`` 형식만
    허용하고 바이낸스 종목은 그 접두사를 쓸 수 없으므로, 종목명으로 가르는 것이 거래소로
    가르는 것과 같다. 자릿수는 화면과 맞춘다(원화 0자리 · USDT 2자리).

    2026-10-18: 실행기(0.20.13+)가 수수료 반영 실현손익을 보내면 그 값(실행기 화면과 같다), 아니면 수수료 전
    값에 그렇다고 붙인다.
    """
    net = getattr(row, "realized_net", None)
    value = float(net) if net is not None else float(row.realized_pnl or 0.0)
    note = "(수수료 전)" if net is None and value else ""
    if str(row.symbol or "").upper().startswith("KRW-"):
        return f"{value:+,.0f} KRW{note}"
    return f"{value:+.2f} USDT{note}"


def _local_levels(row: RunSession) -> Optional[dict]:
    """실행기가 올린 가격선 사본. 로컬 판단 세션에만 있다.

    실행기가 판단하면 **실행기만** 그 선을 안다 — 서버는 봉도 지표도 들고 있지 않다.
    그래서 계산하지 않고 받아 적은 것을 그대로 비춘다. 모양은 engine.live_levels 의
    칸을 포함하고(화면이 쓰던 그대로), 규칙별 선이 더 붙는다.
    """
    if not getattr(row, "decides_locally", False):
        return None
    from .paper import parse_state  # 늦은 import: paper ↔ runner 순환 방지
    state = parse_state(getattr(row, "state_json", "")) or {}
    levels = state.get("levels")
    return levels if isinstance(levels, dict) else None


def _strategy_levels(row: RunSession, macro: Optional[dict]) -> dict:
    """화면이 그릴 가격선 — **계산하는 쪽이 하나**여야 한다.

    화면이 직접 계산하면 규칙 산수가 두 벌이 된다. 실제로 그 일이 있었다: E 눌림목의
    '진입 목표 -3%' 를 화면이 **현재가** 기준으로 그려 가격을 따라 영원히 내려왔다.
    모르면 전부 None 이다 — 추측해서 그리면 거짓이 된다.

    판단하는 쪽이 그 선도 낸다:
    * 서버가 판단하면 서버 엔진이(engine.live_levels) — 지금은 E 만 낸다.
    * 실행기가 판단하면 실행기가 올린 사본을 그대로(열한 방식 전부).
    """
    from engine.live_levels import EMPTY, strategy_levels
    local = _local_levels(row)
    if local is not None:
        return local
    if not macro:
        return dict(EMPTY)
    try:
        parsed = Macro.model_validate(macro)
    except Exception:
        return dict(EMPTY)
    from .paper import parse_state  # 늦은 import: paper ↔ runner 순환 방지
    legs = (parse_state(getattr(row, "state_json", "")) or {}).get("legs") or []
    leg = next((item for item in legs if item.get("symbol") == row.symbol), None) or (legs[0] if legs else {})
    try:
        return strategy_levels(parsed, leg.get("strategy"), float(row.entry_price or 0.0))
    except Exception:
        return dict(EMPTY)


def _entry_wait_args(row: RunSession, macro: Optional[dict] = None, levels: Optional[dict] = None) -> Optional[dict]:
    """진입 대기 계산에 필요한 값만 꺼낸다(세션이 닫힌 뒤에도 쓸 수 있게). 해당 없으면 None."""
    if row.status != "running" or row.in_position:
        return None
    if macro is None:
        try:
            macro = json.loads(row.macro_json) if getattr(row, "macro_json", "") else None
        except (TypeError, ValueError):
            return None
    from . import entry_wait
    if not entry_wait.is_waiting(macro):
        return None
    if levels is None and (macro.get("rule_type") == "E" or getattr(row, "decides_locally", False)):
        levels = _strategy_levels(row, macro)
    return dict(
        session_id=row.id, macro=macro, symbol=row.symbol, market=row.market or "spot", side=row.position_side or "long",
        last_price=float(row.last_price or 0.0), started_at=row.started_at, in_position=bool(row.in_position), levels=levels,
    )


def _entry_wait(row: RunSession, macro: Optional[dict] = None, levels: Optional[dict] = None) -> Optional[dict]:
    """진입 대기 모델(entry_wait.py) — 실행 중 · 포지션 없음 · 기다리는 매크로일 때만. 실패하면 None(화면은 예전처럼)."""
    args = _entry_wait_args(row, macro, levels)
    if args is None:
        return None
    from . import entry_wait
    return entry_wait.for_session(**args)


def _settings_form(row: RunSession, stopping: bool) -> dict:
    """화면이 '설정 바꾸기' 를 그릴 재료. 바꿀 수 없으면 이유를 함께 준다.

    왜 서버가 칸 목록까지 주나: 무엇을 바꿀 수 있는지는 ``runner_settings`` 가 정한다.
    화면이 따로 목록을 들면 두 벌이 되어 어긋난다 — 화면에는 보이는데 서버가 거절하는
    칸이 생기고, 사용자는 그걸 고장으로 읽는다.
    """
    pending = bool(getattr(row, "pending_macro_json", ""))
    out = {"editable": False, "reason": "", "fields": [],
           "changed_count": int(getattr(row, "settings_changed_count", 0) or 0),
           "pending": pending}
    if row.status != "running":
        out["reason"] = "끝난 실행은 설정을 바꿀 수 없어요."
        return out
    if stopping:
        out["reason"] = "종료 중인 실행은 설정을 바꿀 수 없어요."
        return out
    if not supports_settings(row.runner_version or ""):
        out["reason"] = SETTINGS_REQUIRED_DETAIL
        return out
    if not row.macro_json:
        out["reason"] = "이 실행에는 저장된 매크로가 없어 바꿀 수 없어요."
        return out
    try:
        macro = Macro.model_validate_json(row.macro_json)
    except Exception:
        out["reason"] = "이 실행의 매크로를 읽지 못했어요."
        return out
    out["editable"] = True
    out["fields"] = runner_settings.form_view(macro)
    return out


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
    levels = _strategy_levels(row, macro)
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
        # 구동 중 설정 변경(2026-10-30) — 바꿀 수 있는 칸과, 못 바꾸면 그 이유.
        "settings": _settings_form(row, stopping),
        "macro": macro,
        "status": row.status,
        "stopping": stopping,
        "stop_mode": row.stop_mode,
        "runner_version": getattr(row, "runner_version", ""),
        # 실행기에 묶여 있던 판단 엔진 세대. 빈 값은 "판단을 서버에 맡긴 실행기".
        "engine_version": getattr(row, "engine_version", ""),
        # 이 세션의 매수 · 매도를 누가 판단했는가. True 면 화면의 숫자는 **실행기가 올린 사본**
        # 이고, 실행기가 멈추면 숫자도 멈춘다(connected · heartbeat_kst 와 함께 읽어야 한다).
        "decides_locally": bool(getattr(row, "decides_locally", False)),
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
        # 수수료 반영 손익(실행기 0.20.13+, 모르면 null) — 웹 '평가손익' · '실현손익'은 이 값을 먼저 쓴다.
        "unrealized_net": getattr(row, "unrealized_net", None),
        "unrealized_net_pct": getattr(row, "unrealized_net_pct", None),
        "realized_net": getattr(row, "realized_net", None),
        "final_unrealized_net": getattr(row, "final_unrealized_net", None),
        "final_unrealized_net_pct": getattr(row, "final_unrealized_net_pct", None),
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
        # 돌고 있는 전략이 지금 보는 가격선(E 의 진입 목표 · 트레일 발동 · 스탑선).
        "strategy_levels": levels,
        # 진입을 기다리는 매크로의 '무엇을 · 얼마나 · 언제'(entry_wait.py). 실행기 하트비트 응답과 같은 값.
        "entry_wait": _entry_wait(row, macro, levels),
        "stopped_kst": _kst_label(row.stopped_at or ""),
    }


# 종료 기록 보관 — 계정마다 보관하지 않은 종료 세션은 최근 30건·30일까지만 남긴다(2026-10-02).
# 사용자가 '보관'한 기록은 정리에서 빠지되 PIN_LIMIT 건까지(기본 10). 정리할 때 세션의 이벤트·실행 명령도 같이 지운다.
# 정리는 세션이 끝날 때(mark_stopped)·보관을 풀 때와 방문 독립 log_retention_runtime에서 실행한다.
# runtime은 기본 매시간 전체 계정을 작은 배치로 정리하며, maybe_prune_all_ended_sessions는 호환용이다.
# 목록은 정리와 무관하게 기간이 지난 기록을 바로 뺀다.
HISTORY_KEEP = 30
HISTORY_DAYS = 30
# 보관 상한은 RUNNER_PIN_LIMIT 로 바꾼다 — 2026-10-15 파일럿 동안만 운영 100(render.yaml). 끝나면 그 줄을 지워 10 으로 돌린다.
# 화면(AgentHistory)은 목록 응답의 history_policy.pin_limit 를 그대로 쓴다.
def _pin_limit() -> int:
    try:
        value = int(os.environ.get("RUNNER_PIN_LIMIT", "10"))
    except ValueError:
        return 10
    return max(1, min(value, 200))


PIN_LIMIT = _pin_limit()


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
    from . import agent_commentary
    db.exec(sql_delete(RunSessionEvent).where(RunSessionEvent.session_id.in_(session_ids)))
    db.exec(sql_delete(RunnerCommand).where(RunnerCommand.session_id.in_(session_ids)))
    agent_commentary.delete_for_sessions(db, session_ids)
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
_DAILY_PRUNE_RETRY_MS = 60_000
_last_daily_prune_ms = 0
_next_daily_prune_retry_ms = 0
_daily_prune_lock = threading.Lock()


def prune_ended_sessions_batch(db, *, now: Optional[datetime] = None, limit: int = 200) -> dict:
    """Flush one bounded history-maintenance batch; the caller owns commit/rollback.

    Preserve the existing unpinned/non-running 30-record/30-day policy. Select
    scalar IDs with SQL ranking, not ORM session snapshots. Each child table is
    drained by at most 1,000 rows; a parent with remaining children waits for the
    next batch. PostgreSQL row locks exclude concurrent pin/start updates while
    children are removed. No network work or new retention-history rows occur.
    """
    from sqlalchemy import case, delete as sql_delete, exists

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("history batch limit must be a positive integer")
    bound = min(limit, 200)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    eligible = (RunSession.status != "running", RunSession.pinned == False)  # noqa: E712
    ended = func.coalesce(func.nullif(RunSession.stopped_at, ""),
                          func.nullif(RunSession.last_heartbeat_at, ""), RunSession.started_at)
    if db.get_bind().dialect.name == "sqlite":
        # SQLite's date parser handles offsets and invalid strings without a
        # lexical +00:00-vs-Z boundary error; invalid input evaluates to NULL.
        expired = func.julianday(ended) < func.julianday(cutoff.isoformat())
    else:
        # Server-generated timestamps are UTC ISO strings. Normalize those
        # without casting arbitrary historical text, including fractional-second
        # padding so Z/+00:00 and the exact cutoff compare identically. This is
        # PostgreSQL 15-compatible (no pg_input_is_valid dependency). Unknown or
        # non-UTC legacy dates are retained by age, but still obey the count cap.
        date = (
            r"([0-9]{4}-((0[13578]|1[02])-(0[1-9]|[12][0-9]|3[01])|"
            r"(0[469]|11)-(0[1-9]|[12][0-9]|30)|02-(0[1-9]|1[0-9]|2[0-8]))|"
            r"([0-9]{2}(0[48]|[2468][048]|[13579][26])|([02468][048]|[13579][26])00)-02-29)"
        )
        pattern = rf"^{date}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{{1,6}})?(Z|\+00:00)$"
        utc = func.replace(ended, "+00:00", "Z")
        fraction = func.split_part(func.split_part(utc, ".", 2), "Z", 1)
        normalized = func.concat(func.substr(utc, 1, 19), ".", func.rpad(fraction, 6, "0"), "Z")
        canonical = case((ended.like("0000-%"), None), (ended.op("~")(pattern), normalized), else_=None)
        expired = canonical < cutoff.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    ranked = select(
        RunSession.id.label("session_id"), expired.label("expired"),
        func.row_number().over(partition_by=RunSession.user_id, order_by=RunSession.id.desc()).label("position"),
    ).where(*eligible).subquery()
    candidates = select(RunSession.id).join(ranked, ranked.c.session_id == RunSession.id).where(
        *eligible, or_(ranked.c.expired, ranked.c.position > HISTORY_KEEP),
    ).order_by(RunSession.id.asc()).limit(bound + 1).with_for_update(of=RunSession, skip_locked=True)
    selected = list(db.exec(candidates).all())
    targets = selected[:bound]
    if not targets:
        return {"deleted_sessions": 0, "deleted_events": 0, "deleted_commands": 0, "has_more": False}

    counts = {"deleted_events": 0, "deleted_commands": 0}
    # 껄무새 중계 말풍선도 실행 기록과 같은 세는 칸(deleted_events)에 더한다 — 정리 지표의 칸을 늘리지 않는다.
    for model, key in ((RunSessionEvent, "deleted_events"), (RunnerCommand, "deleted_commands"),
                       (AgentUtterance, "deleted_events")):
        child_ids = list(db.exec(select(model.id).where(model.session_id.in_(targets))
                                 .order_by(model.id.asc()).limit(1000)).all())
        if child_ids:
            removed = db.exec(sql_delete(model).where(model.id.in_(child_ids)).returning(model.id)).all()
            counts[key] += len(removed)
    db.exec(sql_delete(AgentCommentaryState).where(AgentCommentaryState.session_id.in_(targets)))
    # Recheck pin/status at the actual parent DELETE, and never orphan residual
    # children. The target row lock also protects these facts on PostgreSQL.
    without_events = ~exists(select(RunSessionEvent.id).where(RunSessionEvent.session_id == RunSession.id))
    without_commands = ~exists(select(RunnerCommand.id).where(RunnerCommand.session_id == RunSession.id))
    without_utterances = ~exists(select(AgentUtterance.id).where(AgentUtterance.session_id == RunSession.id))
    removed = db.exec(sql_delete(RunSession).where(
        RunSession.id.in_(targets), *eligible, without_events, without_commands, without_utterances,
    ).returning(RunSession.id)).all()
    db.flush()
    return {"deleted_sessions": len(removed), **counts,
            "has_more": len(selected) > bound or len(removed) < len(targets)}


def prune_all_ended_sessions(db, *, now: Optional[datetime] = None) -> int:
    """Legacy completion API; runtime maintenance should call one batch instead."""
    removed = 0
    while True:
        result = prune_ended_sessions_batch(db, now=now)
        db.commit()
        removed += result["deleted_sessions"]
        if not result["has_more"]:
            return removed


def maybe_prune_all_ended_sessions(db) -> int:
    """호환용 일일 정리. 웹 정기 작업은 이 helper 대신 bounded batch를 직접 호출한다."""
    global _last_daily_prune_ms, _next_daily_prune_retry_ms
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if not _daily_prune_lock.acquire(blocking=False):
        return 0
    try:
        if now_ms - _last_daily_prune_ms < _DAILY_PRUNE_MS or now_ms < _next_daily_prune_retry_ms:
            return 0
        try:
            removed = prune_all_ended_sessions(db)
        except Exception as error:
            try:
                db.rollback()
            except Exception:
                pass
            _next_daily_prune_retry_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + _DAILY_PRUNE_RETRY_MS
            # Type only: database exceptions may embed URLs, SQL and secrets.
            _log.warning("Runner history cleanup failed (%s); retry after 60 seconds", type(error).__name__)
            return 0
        _last_daily_prune_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        _next_daily_prune_retry_ms = 0
        return removed
    finally:
        _daily_prune_lock.release()


def list_sessions(user_id: int, limit: int = HISTORY_KEEP + PIN_LIMIT, db=None) -> dict:
    """All active sessions plus the retained ended sessions (최근 30건 + 보관 PIN_LIMIT 건까지)."""
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


SETTINGS_MIN_VERSION = os.environ.get("RUNNER_SETTINGS_MIN_VERSION", "22").strip() or "22"
SETTINGS_REQUIRED_DETAIL = (
    "구동 중 설정 변경은 실행기 v22 이상이 필요해요. 실행기를 업데이트해 주세요.")
_APPLY_MODES = ("immediate", "next_entry")
SETTINGS_SOURCES = ("web", "runner")


def supports_settings(version: str) -> bool:
    return _version_at_least(version, SETTINGS_MIN_VERSION)


def _change_row(row: RunSession, before: Macro, after: Macro, *, source: str,
                apply_mode: str) -> RunnerSettingChange:
    """증빙 한 줄. **쓴 뒤로는 ``applied_ms`` 말고 아무것도 바꾸지 않는다.**

    세션이 지워져도 남아야 하므로 세션에서 읽어 올 수 있는 값(종목 · 거래소 · 그때의
    포지션)을 전부 베껴 둔다 — 나중에 세션 행을 조인해서 알아낼 수는 없다.
    """
    now, now_ms = _now_iso(), runner_engine._now_ms()
    fields = runner_settings.changed_fields(before, after)
    return RunnerSettingChange(
        user_id=row.user_id, session_id=row.id, user_macro_id=row.user_macro_id,
        symbol=row.symbol, exchange=getattr(before, "exchange", "") or "",
        source=source, created_at=now, created_ms=now_ms,
        before_json=before.model_dump_json(), after_json=after.model_dump_json(),
        before_digest=macro_signing.digest(before), after_digest=macro_signing.digest(after),
        changed_fields=",".join(fields), summary=runner_settings.summary(before, after),
        apply_mode=apply_mode, in_position=bool(row.in_position),
        position_qty=float(row.position_qty or 0.0), entry_price=float(row.entry_price or 0.0),
        unrealized_pct=float(row.unrealized_pct or 0.0),
        runner_version=row.runner_version or "",
    )


def _activate_macro(row: RunSession, after: Macro) -> None:
    """이 세션이 지금부터 쓰는 매크로를 바꾼다 — 서버 전략이 다음 재시작에서 읽는 값이다.

    ``macro_origin`` 은 **시작 시점**의 출처라 건드리지 않는다(그 사실도 증빙이다).
    대신 지문을 새 것으로 바꾸고 수정 횟수를 올려, 화면이 "구동 중 N번 수정됨" 을 말한다.
    """
    row.macro_json = after.model_dump_json()
    row.macro_digest = macro_signing.digest(after)
    row.settings_changed_count = int(row.settings_changed_count or 0) + 1


def request_settings_change(user_id: int, session_id: int, changes: dict, *,
                            source: str = "web", apply_mode: str = "immediate") -> dict:
    """구동 중인 매크로의 금액·조건을 바꾼다. 실행기 화면도 웹도 이 길 하나만 쓴다.

    실행기가 혼자 바꾸면 서버 전략과 실행기 설정이 갈라지고, 그러면 어느 쪽이 진짜인지
    아무도 모른다. 그래서 **변경은 언제나 서버를 거친다.**

    포지션을 들고 있을 때 ``apply_mode`` 로 갈린다.
    - ``immediate``  — 지금 적용한다. 들고 있는 포지션의 청산 조건도 새 값으로 바뀐다.
    - ``next_entry`` — 예약해 두고, 포지션이 비는 첫 heartbeat 에서 적용한다.
    포지션이 없으면 둘 다 같다(바로 적용).
    """
    if source not in SETTINGS_SOURCES:
        raise HTTPException(status_code=400, detail="변경 요청 출처가 올바르지 않아요.")
    if apply_mode not in _APPLY_MODES:
        raise HTTPException(status_code=400, detail="적용 방식이 올바르지 않아요.")
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        if row.status != "running":
            raise HTTPException(status_code=409, detail="끝난 실행은 설정을 바꿀 수 없어요.")
        if row.stop_mode in _STOP_MODES:
            raise HTTPException(status_code=409, detail="종료 중인 실행은 설정을 바꿀 수 없어요.")
        if not supports_settings(row.runner_version or ""):
            raise _ticket_error(426, SETTINGS_REQUIRED_DETAIL)
        if not row.macro_json:
            raise HTTPException(status_code=409, detail="이 실행에는 저장된 매크로가 없어 바꿀 수 없어요.")
        try:
            before = Macro.model_validate_json(row.macro_json)
        except Exception:
            raise HTTPException(status_code=409, detail="이 실행의 매크로를 읽지 못했어요.")

        after = runner_settings.apply_changes(before, changes)
        # 포지션이 없으면 예약할 이유가 없다 — 예약은 '들고 있는 포지션을 지키려고' 있다.
        mode = "immediate" if not row.in_position else apply_mode
        record = _change_row(row, before, after, source=source, apply_mode=mode)
        db.add(record)
        db.flush()  # 예약이면 세션이 이 줄의 번호를 들고 있어야 한다.
        if mode == "immediate":
            _activate_macro(row, after)
            row.pending_macro_json, row.pending_change_id = "", 0
        else:
            row.pending_macro_json = after.model_dump_json()
            row.pending_change_id = int(record.id or 0)
        db.add(row)
        _append_events(db, row, [{
            "ts": record.created_at, "kind": "info", "severity": "WARN",
            "event_name": "settings.changed",
            "event_key": f"session:{row.id}:settings:{record.id}",
            "operation_id": f"session:{row.id}",
            "details": {"source": source, "apply_mode": mode,
                        "changed_fields": record.changed_fields},
            "message": ("설정 변경 · " + record.summary
                        + (" · 이 포지션 정리된 뒤부터" if mode == "next_entry" else "")),
        }], component="server")
        db.commit()
        change_id, applied = int(record.id or 0), mode == "immediate"
    if applied:
        # 드라이버는 매크로를 메모리에 들고 있다 — 다시 세워야 새 값으로 돈다.
        runner_engine.schedule_restart(session_id)
    notify_sessions_changed(user_id)
    return {"ok": True, "change_id": change_id, "apply_mode": mode, "applied": applied,
            "summary": record.summary,
            "note": ("실행기가 곧 반영해요(최대 몇 초 지연)." if applied
                     else "지금 포지션이 정리되면 적용해요.")}


def confirm_settings(db, row: RunSession, echoed: object) -> None:
    """실행기가 "이 설정으로 돌고 있다" 고 보낸 지문을 받아 기록에 반영 시각을 적는다.

    왜 필요한가: 증빙은 "사용자가 바꿨다" 뿐 아니라 **"실행기가 그걸 받아 돌았다"** 까지
    말할 수 있어야 한다. 바꿨는데 실행기가 못 받은 채 사고가 나면 이야기가 달라진다.

    지금 매크로의 지문과 같을 때만 적는다 — 옛 지문을 늦게 보내온 heartbeat 로 엉뚱한
    줄에 시각이 찍히지 않게.
    """
    digest = str(echoed or "").strip()
    if not digest or digest != (row.macro_digest or ""):
        return
    record = db.exec(
        select(RunnerSettingChange)
        .where(RunnerSettingChange.session_id == row.id,
               RunnerSettingChange.after_digest == digest,
               RunnerSettingChange.applied_ms == 0)
        .order_by(RunnerSettingChange.id.desc())
    ).first()
    if record is None:
        return
    record.applied_ms = runner_engine._now_ms()
    db.add(record)


def _settings_view(row: RunSession) -> Optional[dict]:
    """heartbeat 응답에 실을 설정 — **실행기가 실제로 쓰는 값만**.

    판단은 서버가 하므로 조건 값(지표 기간 · 문턱)은 실행기에 보낼 이유가 없다. 실행기가
    쓰는 것은 주문 크기의 밑이 되는 투자 금액 · 비율과, 로컬 안전망(손절 · 일일 손실 ·
    최대 보유시간)뿐이다(runner-core/src/bot.rs).

    ``digest`` 를 함께 보낸다 — 실행기가 "이미 이 설정이다" 를 알아보고 같은 값을 거듭
    반영하지 않게 한다. 설정을 못 읽으면 None(옛 동작 그대로).
    """
    # 이 기능을 모르는 실행기에는 **보내지 않는다.** 쓰지도 못할 키를 실으면 옛 응답
    # 계약이 달라지고, 설정 변경 요청 자체는 어차피 버전 관문이 막는다.
    if not supports_settings(row.runner_version or "") or not row.macro_json:
        return None
    try:
        macro = Macro.model_validate_json(row.macro_json)
    except Exception:
        return None
    risk = macro.risk
    return {
        "digest": macro_signing.digest(macro),
        "changed_count": int(row.settings_changed_count or 0),
        # 실행기 화면도 같은 칸을 그린다 — 목록을 실행기가 따로 들면 두 벌이 되어
        # 화면에는 보이는데 서버가 거절하는 칸이 생긴다.
        "fields": runner_settings.form_view(macro),
        "initial_capital": float(macro.params.get("initial_capital") or 0.0),
        "invest_ratio": float(risk.invest_ratio),
        "stop_loss_pct": risk.stop_loss_pct,
        "daily_max_loss_pct": risk.daily_max_loss_pct,
        "max_holding_hours": risk.max_holding_hours,
        "cooldown_minutes": risk.cooldown_minutes,
    }


def apply_pending_settings(db, row: RunSession) -> bool:
    """예약된 변경을 적용한다 — 포지션이 비었을 때만. 적용했으면 True.

    heartbeat 가 포지션 상태를 갱신한 **뒤에** 부른다. 그래야 이번 heartbeat 에서 막
    청산된 경우에도 곧바로 적용된다.
    """
    if not row.pending_macro_json or row.in_position:
        return False
    try:
        after = Macro.model_validate_json(row.pending_macro_json)
    except Exception:
        log.exception("runner: session %s pending settings unreadable — dropped", row.id)
        row.pending_macro_json, row.pending_change_id = "", 0
        return False
    _activate_macro(row, after)
    change_id, row.pending_change_id = int(row.pending_change_id or 0), 0
    row.pending_macro_json = ""
    if change_id:
        record = db.get(RunnerSettingChange, change_id)
        if record is not None:
            record.applied_ms = runner_engine._now_ms()
            db.add(record)
    db.add(row)
    return True


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
