"""FastAPI app: macro create/fetch, backtest, gallery, share card.

No exchange order APIs. Only the public Binance klines endpoint is used, for
historical data. Every returned result represents a PAST SIMULATION.
"""
from __future__ import annotations

import hashlib

import asyncio
import logging
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from pydantic import BaseModel, Field
from sqlmodel import Session, select

# Load backend/.env (gitignored) for local dev so secrets like GEMINI_API_KEY are
# available before any module reads os.environ. No-op in prod (Render injects env
# vars) and when python-dotenv isn't installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from . import chart as chart_mod
from . import chat as chat_mod
from . import rooms as rooms_mod
from . import feargreed as feargreed_mod
from . import hangang as hangang_mod
from . import hotcoins as hotcoins_mod
from . import http_runtime as http_runtime_mod
from . import kimchi as kimchi_mod
from . import news as news_mod
from . import public_news as public_news_mod
from . import board as board_mod
from . import leaderboard as leaderboard_mod
from . import leaderboard_runtime
from . import optimize as optimize_mod
from . import optimize_runtime as optimize_runtime_mod
from . import paper as paper_mod
from . import runner_engine as runner_engine_mod
from . import ai_explain as ai_explain_mod
from . import ai_runtime as ai_runtime_mod
from . import community_summaries as community_summaries_mod
from . import auth as auth_mod
from . import avatars as avatars_mod
from . import profile as profile_mod
from . import points as points_mod
from . import quests as quests_mod
from . import ask as ask_mod
from . import notifications as notifications_mod
from . import notification_stream
from . import admin as admin_mod
from . import members as members_mod
from . import macro_events
from . import account as account_mod
from . import challenge as challenge_mod
from . import runner as runner_mod
from . import macro_signing as macro_signing_mod
from . import user_macros as user_macros_mod
from .agent_features.position_news.router import router as position_news_router
from .agent_features.position_news import runtime as position_news_runtime
from .agent_features.whale_activity import runtime as whale_activity_runtime
from . import observability
from .observability import observe_application, router as observability_router
from fastapi import Depends
from .db import User
from . import whales as whales_mod
from .card import render_card
from .security import hash_password
from .http_cache import public_news_response
from .data import NoSpotDataError, average_daily_funding_pct, get_klines, resolve_period
from .data import symbols as symbols_mod
from .data.binance import backtest_limits
from .marketdata import fetch_klines_for_macro
from . import marketdata as marketdata_mod
from .db import MacroRow, get_session, init_db, request_session
from .engine import BacktestResult, Macro, Period, compact_backtest_result, human_summary
from .engine.backtest import run_backtest
from .engine import portfolio as portfolio_mod
from .engine.explain import explain_result
from .engine.summary import _coin
from .realtrade import build_bundle

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 재배포로 끊긴 페이퍼 세션을 되살린다 — 실패해도 서버는 떠야 하므로 로그만 남긴다.
    try:
        await paper_mod.resume_running_sessions()
    except Exception:
        logging.getLogger(__name__).exception("paper session resume failed at startup")
    # 실행기(v8+) 세션의 서버 측 전략 드라이버 — 요청 스레드가 루프로 작업을 넘길 수 있게 먼저 루프를 등록하고,
    # 재배포로 끊긴 running 세션을 state_json 에서 되살린다. 역시 실패해도 서버는 뜬다.
    runner_engine_mod.install(asyncio.get_running_loop())
    try:
        await runner_engine_mod.resume_running_runner_sessions()
    except Exception:
        logging.getLogger(__name__).exception("runner engine resume failed at startup")
    notification_stream.start()  # Postgres 일 때 LISTEN — 다른 프로세스의 알림도 SSE 로 밀어 준다
    community_summaries_mod.start()
    leaderboard_runtime.start()
    public_news_mod.start()
    position_news_runtime.start()
    whale_activity_runtime.start()
    try:
        yield
    finally:
        await notification_stream.stop()
        stopped = await asyncio.gather(
            leaderboard_runtime.stop(), public_news_mod.stop(),
            whale_activity_runtime.stop(), position_news_runtime.stop(),
            return_exceptions=True,
        )
        for result in stopped:
            if isinstance(result, BaseException):
                logging.getLogger(__name__).error("Background worker shutdown failed: %s", type(result).__name__)
        try:
            # 실행기 세션 드라이버를 먼저 멈춘다 — 롤링 배포에서 새 프로세스와 겹쳐 같은 명령을 두 번 남기지 않게.
            await runner_engine_mod.shutdown_drivers()
        except Exception:
            logging.getLogger(__name__).exception("runner engine shutdown failed")
        try:
            await paper_mod.shutdown_running_sessions()
        finally:
            try:
                optimize_runtime_mod.shutdown()
            finally:
                try:
                    await asyncio.to_thread(community_summaries_mod.shutdown)
                finally:
                    try:
                        ai_runtime_mod.close_ai_runtime()
                    finally:
                        from .cache_runtime import close_cache_runtime
                        await asyncio.to_thread(close_cache_runtime)
                        http_runtime_mod.close_http_runtime()


app = FastAPI(title="Coin Macro Backtest & Share (Simulation only)", lifespan=lifespan)
app.include_router(position_news_router)
app.include_router(observability_router)

# Schema initialization belongs to lifespan, before serving requests. Importing
# route definitions must not run DDL against a database used by live macros.

# 응답 압축. 캔들 JSON은 같은 모양의 숫자 문자열이 300줄 반복이라 압축이 아주
# 잘 든다 — /api/candles 실측 29,387 B -> 7,083 B (4.1배). 이게 빠져 있어서
# 무료 대역폭 5 GB 를 태웠다. compresslevel 은 9 대신 6: 비율은 거의 같은데
# CPU 를 훨씬 덜 쓴다(무료 인스턴스라 CPU 가 더 아깝다).
#
# CORS와 observability는 모든 라우트가 등록된 뒤 FastAPI 전체를 감싼다.
# 그래야 Starlette의 최외곽 ServerErrorMiddleware가 만든 500도 두 헤더를 지난다.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


# --- helpers ------------------------------------------------------------
def _period_label(period: Period) -> str:
    labels = {"1y": "최근 1년", "6m": "최근 6개월", "3m": "최근 3개월", "1m": "최근 1개월", "1w": "최근 1주", "1d": "최근 1일"}
    if period.preset and period.preset != "custom":
        return labels.get(period.preset, period.preset)
    return f"{period.start} ~ {period.end}"


def _make_slug(macro: Macro) -> str:
    coin = _coin(macro.symbol).lower()
    p = macro.params
    descs = {
        "A": lambda: f"{p.get('take_profit_pct', 'x')}pct",
        "B": lambda: "band",
        "C": lambda: f"dca{p.get('interval_days', 'x')}d",
        "D": lambda: f"grid{p.get('grid_count', 'x')}",
        "E": lambda: f"trail{p.get('trail_percent', 'x')}",
        "F": lambda: f"rsi{p.get('rsi_period', 'x')}",
        "G": lambda: f"bb{p.get('bb_period', 'x')}",
        "H": lambda: f"safety{p.get('max_safety_orders', 'x')}",
        "I": lambda: f"vbk{p.get('k', 'x')}",
        "J": lambda: f"ma{p.get('fast_period', 'x')}x{p.get('slow_period', 'x')}",
    }
    desc = descs.get(macro.rule_type.value, lambda: macro.rule_type.value.lower())()
    side = macro.position_side.value
    return f"{coin}-{desc}-{side}-{uuid.uuid4().hex[:4]}"


def _run_any(macro: Macro) -> tuple[BacktestResult, list, str, str]:
    """Run a macro; returns (result, per_symbol, source, period_label).

    Single-symbol => per_symbol == []. Portfolio (macro.symbols len>1) => the
    same rule runs on each symbol with capital split evenly, and the aggregated
    portfolio result is returned alongside a per-symbol breakdown.
    """
    start_ms, end_ms = resolve_period(macro.period.preset, macro.period.start, macro.period.end)
    label = _period_label(macro.period)

    if macro.is_portfolio():
        syms = macro.all_symbols()
        base = macro.initial_capital
        per_cap = (base / len(syms)) if base else None
        results: list = []
        source = ""
        for sym in syms:
            leg = macro.for_symbol(sym, per_cap)
            df, source = fetch_klines_for_macro(leg, start_ms, end_ms)
            results.append((sym, run_backtest(leg, df)))
        agg, per_symbol = portfolio_mod.aggregate(results, candle_interval=macro.candle_interval)
        return agg, per_symbol, source, label

    # Single symbol: no synthetic fallback; missing data raises NoSpotDataError.
    df, source = fetch_klines_for_macro(macro, start_ms, end_ms)
    return run_backtest(macro, df), [], source, label


def _run_for_macro(macro: Macro) -> tuple[BacktestResult, str, str]:
    result, _per, source, label = _run_any(macro)
    return result, source, label


def _row_to_macro(row: MacroRow) -> Macro:
    return Macro.model_validate_json(row.macro_json)


# --- request/response models -------------------------------------------
class BacktestRequest(BaseModel):
    macro: Macro
    period_override: Optional[Period] = None


class PaperStartRequest(BaseModel):
    macro: Macro
    symbol: Optional[str] = None
    mode: str = "live"  # live | replay


class OptimizeRequest(BaseModel):
    macro: Macro
    tp_values: Optional[List[float]] = None
    sl_values: Optional[List[float]] = None


class SignupRequest(BaseModel):
    email: str
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str  # Google Identity Services 가 준 ID 토큰(JWT)


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class DeleteAccountRequest(BaseModel):
    confirmation: str
    password: str = ""
    credential: str = ""


class ExplainAiRequest(BaseModel):
    macro: Macro
    period_override: Optional[Period] = None


class BundleRequest(BaseModel):
    macro: Macro


class UserMacroSaveRequest(BaseModel):
    macro: Macro
    name: str = ""


# --- 매크로 실행기(로컬 exe) 연동 모델 ---------------------------------
class RunnerStartRequest(BaseModel):
    # Stable ID of the signed-in account's saved macro. None keeps old runners
    # compatible; when supplied, the backend verifies ownership and exact
    # normalized macro equality before creating the session.
    user_macro_id: Optional[int] = None
    symbol: str
    position_side: str = "long"
    leverage: int = 1
    market: str = ""  # spot | futures | "" (서버가 방향/레버리지로 결정)
    testnet: bool = True
    human_summary: str = ""
    # 실행 중인 매크로 원문(선택) — 마이페이지 실시간 차트에 전략 보조지표를 그리는
    # 데 쓴다. 거래소 키/시크릿은 포함되지 않는다. 예전 실행기는 보내지 않는다.
    macro: Optional[dict] = None
    # 실행기 버전. v7 부터 보낸다. 있고 최소 버전 미만이면 세션을 만들지 않는다.
    runner_version: str = ""
    # 매크로 파일에 동봉된 서명(`_sig`). 실행기 v7+ 가 파일을 열면 그대로 올린다.
    # 서버가 검증해 세션 출처(원본/수정본)를 남긴다. 티켓 경로·옛 실행기는 None.
    macro_sig: Optional[dict] = None
    macro_source: str = ""  # web | file | "" (실행기가 스스로 밝히는 값, 참고용)


class RunnerHeartbeatRequest(BaseModel):
    session_id: int
    position_uncertain: bool = False
    in_position: bool = False
    last_price: float = 0.0
    entry_price: float = 0.0
    position_qty: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pct: float = 0.0
    note: str = ""
    # 실행기 창 로그를 서버에 쌓는다(v7+). [{ts, kind, message}] — 한 번에 100개까지.
    events: list[dict] = []
    # v8: 직전 heartbeat 로 받은 명령의 실행 결과 [{command_id, ok, executed_qty, fill_price, error}]
    acks: list[dict] = []


class RunnerStoppedRequest(BaseModel):
    session_id: int
    status: str = "stopped"  # stopped | error
    note: str = ""
    snapshot: Optional[dict] = None
    events: list[dict] = []


class RunnerStopRequest(BaseModel):
    mode: str  # stop_only | close_and_stop


class RunnerLaunchTicketCreateRequest(BaseModel):
    user_macro_id: int
    testnet: Literal[True] = True


class RunnerLaunchTicketClaimRequest(BaseModel):
    ticket: str
    runner_version: str = ""


class LeaderboardRegisterRequest(BaseModel):
    macro: Macro
    username: str  # display id (required)
    password: str  # edit-ownership proof (required; stored hashed only)
    user_id: str = "anon"
    mode: str = "live"  # live | replay


class LeaderboardEditRequest(BaseModel):
    macro: Macro
    password: str
    mode: str = "live"


class VoteRequest(BaseModel):
    user_id: str
    value: int  # +1 like | -1 dislike


class ChatPostRequest(BaseModel):
    text: str
    room_id: Optional[int] = None


class ChatReadRequest(BaseModel):
    last_seen_id: int
    room_id: Optional[int] = None


class RoomCreateRequest(BaseModel):
    title: str
    capacity: int
    entry_fee: int = 0
    consent: bool = False


# --- endpoints ----------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": os.environ.get("RENDER_GIT_COMMIT", "local"),
            "disclaimer": "past simulation only; no live trading"}


# --- auth / account ----------------------------------------------------
@app.post("/api/auth/signup")
def auth_signup(req: SignupRequest) -> dict:
    """Create an account (email/username/password) and grant starter points."""
    return auth_mod.signup(req.email, req.username, req.password)


@app.post("/api/auth/login")
def auth_login(req: LoginRequest) -> dict:
    return auth_mod.login(req.email, req.password)


@app.get("/api/auth/google/config")
def auth_google_config() -> dict:
    """프런트가 구글 버튼을 켤지·어떤 client_id 로 초기화할지 알려준다(런타임)."""
    return {"enabled": auth_mod.google_enabled(), "client_id": auth_mod.GOOGLE_CLIENT_ID}


@app.post("/api/auth/google")
def auth_google(req: GoogleAuthRequest) -> dict:
    """구글 간편 로그인/회원가입 — ID 토큰 검증 후 세션 토큰 발급."""
    return auth_mod.google_auth(req.credential)


@app.post("/api/auth/forgot")
def auth_forgot(req: ForgotRequest) -> dict:
    """Email a password-reset link (no-op delivery until email is configured)."""
    return auth_mod.request_password_reset(req.email)


@app.post("/api/auth/reset")
def auth_reset(req: ResetRequest) -> dict:
    return auth_mod.reset_password(req.token, req.password)


@app.get("/api/auth/me")
def auth_me(
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """Current account (from the Bearer token), including the points balance."""
    return {"user": auth_mod.user_view(user, db=db)}


@app.post("/api/me/avatar")
def upload_avatar(
    image: UploadFile = File(...),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    # Bounded read and raster decoding run in FastAPI's worker thread.
    try:
        data = image.file.read(avatars_mod.MAX_IMAGE_BYTES + 1)
        if len(data) > avatars_mod.MAX_IMAGE_BYTES:
            raise HTTPException(413, "프로필 사진은 2MB 이하만 올릴 수 있어요.")
        normalized = avatars_mod.normalize_image(data)
        avatars_mod.set_avatar(int(user.id), normalized)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(401, str(exc)) from exc
    finally:
        image.file.close()
    return {"user": auth_mod.user_view(user)}


@app.delete("/api/me/avatar")
def delete_avatar(user: User = Depends(auth_mod.current_user)) -> dict:
    try:
        avatars_mod.set_avatar(int(user.id), None)
    except LookupError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"user": auth_mod.user_view(user)}


@app.get("/api/avatars/{user_id}")
def avatar_image(user_id: int, request: Request, v: str | None = None) -> Response:
    avatar = avatars_mod.get_avatar(user_id, version=v)
    if avatar is None:
        raise HTTPException(404, "프로필 사진이 없어요.", headers={"Cache-Control": "no-store"})
    etag = f'"{avatar.version}"'
    headers = {
        "Cache-Control": "public, max-age=300, must-revalidate",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=avatar.image_data, media_type="image/webp", headers=headers)


@app.patch("/api/me/profile")
def update_profile(
    username: str = Form(...), bio: str = Form(""),
    remove_avatar: bool = Form(False), image: UploadFile | None = File(None),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    normalized = None
    if image is not None:
        try:
            raw = image.file.read(avatars_mod.MAX_IMAGE_BYTES + 1)
            if len(raw) > avatars_mod.MAX_IMAGE_BYTES:
                raise HTTPException(413, "프로필 사진은 2MB 이하만 올릴 수 있어요.")
            normalized = avatars_mod.normalize_image(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            image.file.close()
    return profile_mod.update_profile(user.id, username, bio, image_data=normalized, remove_avatar=remove_avatar)


@app.post("/api/me/password")
def change_password(req: ChangePasswordRequest, user: User = Depends(auth_mod.current_user)) -> dict:
    result = profile_mod.change_password(user.id, req.current_password, req.new_password)
    runner_mod.notify_sessions_changed(user.id)
    return result


@app.delete("/api/me/account")
def delete_account(req: DeleteAccountRequest, user: User = Depends(auth_mod.current_user)) -> dict:
    result = profile_mod.delete_account(user.id, req.confirmation, req.password, req.credential)
    runner_mod.notify_sessions_changed(user.id)
    return result


@app.get("/api/me/dashboard")
def me_dashboard(
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """My-page rollup: profile+tier, created/purchased macros, sales, ledger, 내 글."""
    d = account_mod.dashboard(user, db=db)
    d["my_posts"] = board_mod.my_posts(user.id, db=db)
    d["quests"] = quests_mod.today(db, user)
    return d


# ── 껄무새에게 물어볼까? — 카드 답변으로 백테스트 상위 3개 조합. 로그인 필수, 하루 한도, 고지 동의. ──
@app.get("/api/ask/status")
def ask_status(
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    return ask_mod.status(db, account)


@app.post("/api/ask/consent")
def ask_consent(
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    return ask_mod.give_consent(db, account)


@app.post("/api/ask/extra")
def ask_extra(
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """무료 횟수를 다 쓴 뒤 포인트로 1회 추가(하루 상한 있음)."""
    try:
        return ask_mod.buy_extra(db, account)
    except ask_mod.AskError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.post("/api/ask/macros")
def ask_macros(
    req: ask_mod.AskRequest,
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    try:
        return ask_mod.run_ask(db, account, req, lambda macro: _run_any(macro)[0])
    except ask_mod.AskError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.post("/api/ask/candidates")
def ask_candidates_route(
    req: ask_mod.CandidatesRequest,
    account: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """카드 답변으로 종목 후보를 낸다 — 하루 횟수는 여기서만 차감된다."""
    try:
        return ask_mod.run_candidates(db, account, req)
    except ask_mod.AskError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.get("/api/me/quests")
def me_quests(
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """오늘(KST)의 일일 퀘스트와 완료 여부·오늘 번 포인트."""
    return quests_mod.today(db, user)


# 알림(헤더 종 아이콘) ---------------------------------------------------
class NotificationReadIn(BaseModel):
    ids: list[int] = []
    all: bool = False


@app.get("/api/me/notifications")
def me_notifications(
    limit: int = Query(default=30, ge=1, le=notifications_mod.PAGE_MAX),
    before: Optional[int] = Query(default=None, ge=1),
    after: Optional[int] = Query(default=None, ge=0),
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """알림 목록(개인 알림 + 최근 공지, 최신순) 한 페이지와 안 읽은 수. ``after`` 는 그 id 뒤에 온 것만(오래된 순)."""
    return notifications_mod.list_for(db, user, limit=limit, before_ms=before, after_id=after)


@app.get("/api/me/notifications/unread")
def me_notifications_unread(
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """헤더 배지용 — 안 읽은 수와 가장 최근 알림 id(폴링 모드가 새 알림을 알아채 토스트로 띄우는 기준)."""
    return {
        "unread": notifications_mod.unread_count(db, user),
        "latest_id": notifications_mod.latest_id_for(db, user.id),
    }


@app.post("/api/me/notifications/read")
def me_notifications_read(
    req: NotificationReadIn,
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """읽음 처리 — ids 로 몇 개만, 또는 all 로 전부. 남은 안 읽은 수를 돌려준다."""
    return {"unread": notifications_mod.mark_read(db, user, ids=req.ids, everything=req.all)}


@app.post("/api/me/notifications/stream-token")
def me_notifications_stream_token(
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    """알림 SSE 전용 단기 토큰 — EventSource 는 Authorization 헤더를 못 붙인다."""
    response.headers["Cache-Control"] = "no-store"
    return auth_mod.make_stream_token(user.id, auth_mod.NOTIFICATION_STREAM_PURPOSE)


@app.get("/api/me/notifications/stream")
async def me_notifications_stream(token: str = Query(default="", max_length=2048)) -> StreamingResponse:
    """안 읽은 알림 수를 SSE 로 밀어 준다(event: unread). 브라우저의 폴링은 폴백으로 남는다."""
    try:
        user_id = await asyncio.to_thread(auth_mod.decode_stream_token, token, auth_mod.NOTIFICATION_STREAM_PURPOSE)
    except HTTPException:
        raise HTTPException(status_code=401, detail="알림 스트림 인증이 만료됐거나 유효하지 않아요.")
    return StreamingResponse(
        notification_stream.event_stream(user_id, token),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# --- 비콘(익명 write 경로) — 입력 상한 · body 상한 · 방문자별 한도 ---------------------------------------
# 문자열 상한은 프론트(lib/visit.js)가 자르는 길이 = admin.record_visit 가 저장하는 길이. 넘으면 조용히 자르지 않고 422 —
# 정상 브라우저는 절대 넘지 않으니 넘는 건 스크립트다.
class VisitIn(BaseModel):
    kind: Literal["view", "event"] = "view"  # view(화면 진입) | event(행동: backtest 등)
    path: str = Field("/", max_length=120)  # view 면 경로, event 면 행동 이름
    view_key: str = Field("", max_length=64)  # 브라우저가 만든 페이지뷰 id — 재전송 무시·떠날 때 체류시간 갱신용
    session_key: str = Field("", max_length=64)  # 30분 무활동이면 브라우저가 새로 만든다
    referrer: str = Field("", max_length=300)
    utm_source: str = Field("", max_length=60)
    visitor: str = Field("", max_length=80)  # 브라우저 익명 id(서버는 해시만 저장)
    is_new: bool = False
    is_landing: bool = False
    screen_w: int = 0


class VisitLeaveIn(BaseModel):
    view_key: str = Field("", max_length=64)
    dwell_ms: int = 0


class ImpressionsIn(BaseModel):
    entry_ids: list[int] = Field(default_factory=list, max_length=macro_events.MAX_IMPRESSION_IDS)


# 비콘은 익명·고빈도라 IP 별로 막는다(관측 RUM 과 같은 슬라이딩 창). 페이지뷰마다 진입 1 + 떠남 1.
_visit_limiter = observability.SlidingWindowRateLimiter(limit=240, window_seconds=60.0, max_keys=5000)
_leave_limiter = observability.SlidingWindowRateLimiter(limit=240, window_seconds=60.0, max_keys=5000)
_impressions_limiter = observability.SlidingWindowRateLimiter(limit=60, window_seconds=60.0, max_keys=5000)
_open_limiter = observability.SlidingWindowRateLimiter(limit=120, window_seconds=60.0, max_keys=5000)


def _client_ip(request: Request) -> str:
    """실제 클라이언트 IP — 프록시(Vercel rewrite → Render) 뒤라 client.host 는 라우터 주소다. 첫 X-Forwarded-For 홉을 우선한다."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _enforce_beacon_rate_limit(limiter: observability.SlidingWindowRateLimiter, request: Request) -> None:
    retry_after = limiter.retry_after(_client_ip(request) or "anon")
    if retry_after:
        raise HTTPException(status_code=429, detail="잠시 후 다시 시도해 주세요.", headers={"Retry-After": str(retry_after)})


BEACON_MAX_BODY_BYTES = 4_096  # 관측 RUM(RUM_MAX_BODY_BYTES)과 같은 상한. 비콘 body 는 커야 수백 바이트다.
_BEACON_PATH_RE = re.compile(r"^/api/(visit|visit/leave|leaderboard/impressions|leaderboard/\d+/open)/?$")


class _BeaconBodyLimit:
    """비콘 경로의 요청 body 를 BEACON_MAX_BODY_BYTES 로 막는 ASGI 미들웨어.

    FastAPI 는 의존성보다 먼저 body 를 다 읽고 JSON 으로 푼다(fastapi.routing: request.body() → solve_dependencies). 그래서
    스키마의 max_length 나 라우트 안의 검사는 5 MB body 가 이미 파싱된 뒤에야 돈다. 여기서는 Content-Length 가 크면 읽기
    전에 413, 선언이 없거나(chunked) 거짓이면 받은 바이트를 세다가 넘는 순간 HTTPException(413) — FastAPI 는 body 를 읽는
    중 미들웨어가 낸 HTTPException 을 그대로 다시 던지고 ExceptionMiddleware 가 응답으로 바꾼다.
    """

    def __init__(self, app, *, max_bytes: int = BEACON_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not _BEACON_PATH_RE.match(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = -1
            if size < 0:
                await JSONResponse({"detail": "Content-Length 가 올바르지 않아요."}, status_code=400)(scope, receive, send)
                return
            if size > self.max_bytes:
                await JSONResponse({"detail": "비콘 body 가 너무 커요."}, status_code=413)(scope, receive, send)
                return

        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail="비콘 body 가 너무 커요.")
            return message

        await self.app(scope, bounded_receive, send)


app.add_middleware(_BeaconBodyLimit)


@app.post("/api/visit", status_code=204)
def visit_record(
    req: VisitIn,
    request: Request,
    account: Optional[User] = Depends(auth_mod.optional_user_in_session),
    db: Session = Depends(request_session),
) -> Response:
    """화면 진입(view) 또는 행동(event) 한 건을 남긴다(관리자 대시보드의 사용자 지표). 회원이면 회원 id 도 같이."""
    _enforce_beacon_rate_limit(_visit_limiter, request)
    admin_mod.record_visit(
        db, kind=req.kind, path=req.path, view_key=req.view_key, session_key=req.session_key, referrer=req.referrer,
        utm_source=req.utm_source, visitor=req.visitor, is_new=req.is_new, is_landing=req.is_landing, screen_w=req.screen_w,
        user_id=account.id if account else None, secret=auth_mod.SECRET_KEY,
    )
    admin_mod.maybe_prune_visits(db)  # 90일 지난 행은 하루 한 번 정리
    return Response(status_code=204)


@app.post("/api/visit/leave", status_code=204)
def visit_leave(
    req: VisitLeaveIn,
    request: Request,
    db: Session = Depends(request_session),
) -> Response:
    """페이지를 떠날 때(sendBeacon) 체류시간 — 같은 view_key 행에 max(기존, 값), 상한 6시간."""
    _enforce_beacon_rate_limit(_leave_limiter, request)
    admin_mod.record_leave(db, view_key=req.view_key, dwell_ms=req.dwell_ms)
    return Response(status_code=204)


@app.post("/api/leaderboard/impressions", status_code=204)
def leaderboard_impressions(
    req: ImpressionsIn,
    request: Request,
    db: Session = Depends(request_session),
) -> Response:
    """목록에 보인 엔트리들의 노출 +1(세션당 엔트리 1회는 브라우저가 지킨다). 100개 넘게 보내면 스키마가 422."""
    _enforce_beacon_rate_limit(_impressions_limiter, request)
    macro_events.record_impressions(db, req.entry_ids)
    return Response(status_code=204)


@app.post("/api/leaderboard/{entry_id}/open", status_code=204)
def leaderboard_open(
    entry_id: int,
    request: Request,
    db: Session = Depends(request_session),
) -> Response:
    """열람 +1 — 매크로 행에서 빌더로 가져오기·빠른 실행·언락 중 하나를 눌렀을 때."""
    _enforce_beacon_rate_limit(_open_limiter, request)
    macro_events.record_open(db, entry_id)
    return Response(status_code=204)


@app.get("/api/admin/users")
def admin_users(
    days: int = Query(default=30, ge=7, le=90),
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 대시보드 — 사용자 지표(활성 사용자·세션·채널·페이지·기기, 최근 days 일)."""
    return admin_mod.users_report(db, days=days)


@app.get("/api/admin/signups")
def admin_signups(
    days: int = Query(default=30, ge=7, le=90),
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 대시보드 — 가입·전환 퍼널·코호트 리텐션·가입 방법."""
    return admin_mod.signups_report(db, days=days)


@app.get("/api/admin/macros")
def admin_macros(
    days: int = Query(default=30, ge=7, le=90),
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 대시보드 — 매크로 등록·노출·열람·언락·매출과 실행 세션."""
    return admin_mod.macros_report(db, days=days)


class MemberMessageIn(BaseModel):
    title: str = Field(default="", max_length=members_mod.TITLE_MAX)
    body: str = Field(default="", max_length=members_mod.BODY_MAX)
    link: str = Field(default="", max_length=members_mod.LINK_MAX)


class MemberBlockIn(BaseModel):
    blocked: bool = True
    reason: str = Field(default="", max_length=members_mod.REASON_MAX)


class MemberRemoveIn(BaseModel):
    reason: str = Field(default="", max_length=members_mod.REASON_MAX)


@app.get("/api/admin/members")
def admin_members(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=members_mod.PAGE_SIZE_DEFAULT, ge=10, le=members_mod.PAGE_SIZE_MAX),
    q: str = Query(default="", max_length=members_mod.QUERY_MAX),
    status: str = Query(default="all"),
    sort: str = Query(default="recent"),
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """회원 목록(+페이징·검색·상태 필터). 이메일은 마스킹해서 내려간다."""
    return members_mod.list_members(db, page=page, page_size=page_size, q=q, status=status, sort=sort)


@app.post("/api/admin/members/{user_id}/message")
def admin_member_message(
    user_id: int,
    req: MemberMessageIn,
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """그 회원의 알림창으로 관리자 메시지를 보낸다."""
    return members_mod.send_message(db, admin, user_id, title=req.title, body=req.body, link=req.link)


@app.post("/api/admin/members/{user_id}/reset-link")
def admin_member_reset_link(
    user_id: int,
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """비밀번호 재설정 링크(30분) — 관리자가 본인에게 직접 전해 준다. 비밀번호 원문은 어디에도 없다."""
    return members_mod.make_reset_link(db, admin, user_id)


@app.post("/api/admin/members/{user_id}/block")
def admin_member_block(
    user_id: int,
    req: MemberBlockIn,
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """차단·해제 — 채팅·게시글·댓글 쓰기만 막는다(로그인·열람은 그대로)."""
    return members_mod.set_blocked(db, admin, user_id, blocked=req.blocked, reason=req.reason)


@app.post("/api/admin/members/{user_id}/remove")
def admin_member_remove(
    user_id: int,
    req: MemberRemoveIn,
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 탈퇴 — 계정을 지우고 같은 이메일 재가입을 막는다. 되돌릴 수 없다."""
    return members_mod.remove_member(db, admin, user_id, reason=req.reason)


@app.get("/api/admin/news")
def admin_news(
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 대시보드 — 뉴스 수집(크롤링) 현황."""
    return admin_mod.news_report(db)


@app.get("/api/admin/costs")
def admin_costs(
    months: int = Query(default=6, ge=1, le=24),
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 대시보드 — 월별 비용(Gemini 토큰 추정 + 고정액)."""
    return admin_mod.costs_report(db, months=months)


class AdminNotificationIn(BaseModel):
    title: str
    body: str = ""
    link: str = ""
    username: str = ""  # 받을 회원 아이디. 비우면 전체 공지


@app.post("/api/admin/notifications")
def admin_notification_send(
    req: AdminNotificationIn,
    admin: User = Depends(auth_mod.require_admin),
    db: Session = Depends(request_session),
) -> dict:
    """관리자 메시지(한 회원) 또는 공지사항(전체)을 보낸다. User.is_admin 계정만."""
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="제목을 입력해 주세요.")
    target = None
    username = req.username.strip()
    if username:
        target = db.exec(
            select(User).where(User.username == username, User.is_deleted.is_(False))
        ).first()
        if target is None:
            raise HTTPException(status_code=404, detail="받을 회원을 찾을 수 없어요.")
    row = notifications_mod.notify(
        db, target.id if target is not None else None, "admin" if target is not None else "notice",
        title, req.body, req.link, data={"from": admin.username},
    )
    db.commit()
    return {
        "message": notifications_mod.view(row, read=False),
        "recipient": target.username if target is not None else "all",
    }


@app.get("/api/me/macros")
def me_macros(user: User = Depends(auth_mod.current_user)) -> dict:
    """Stable macro snapshots owned by the logged-in account."""
    return user_macros_mod.list_macros(user.id)


@app.get("/api/me/macros/{macro_id}")
def me_macro_get(macro_id: int, user: User = Depends(auth_mod.current_user)) -> dict:
    return user_macros_mod.get_macro(user.id, macro_id)


@app.post("/api/me/macros")
def me_macro_save(req: UserMacroSaveRequest, user: User = Depends(auth_mod.current_user)) -> dict:
    """Validate and save an uploaded/builder macro into the account library."""
    return {"item": user_macros_mod.save_upload(user.id, req.macro, req.name)}


@app.post("/api/me/macros/from-leaderboard/{entry_id}")
def me_macro_from_leaderboard(
    entry_id: int,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    return {"item": user_macros_mod.save_from_leaderboard(user.id, entry_id)}


@app.post("/api/macros")
def create_macro(
    macro: Macro,
    account: Optional[User] = Depends(auth_mod.optional_user),
) -> dict:
    """Store a macro, generate share_slug, and snapshot a representative backtest."""
    macro.macro_id = str(uuid.uuid4())
    macro.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = human_summary(macro)

    try:
        result, source, period_label = _run_for_macro(macro)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # data/period problems shouldn't block saving
        raise HTTPException(status_code=400, detail=f"backtest failed: {exc}")

    with get_session() as session:
        # ensure unique slug
        for _ in range(5):
            slug = _make_slug(macro)
            if not session.exec(select(MacroRow).where(MacroRow.share_slug == slug)).first():
                break
        macro.share_slug = slug
        row = MacroRow(
            macro_id=macro.macro_id,
            share_slug=slug,
            symbol=macro.symbol,
            rule_type=macro.rule_type.value,
            position_side=macro.position_side.value,
            macro_json=macro.model_dump_json(),
            human_summary=summary,
            created_at=macro.created_at,
            rep_return_pct=result.final_return_pct,
            rep_win_pct=result.win_rate_pct,
            rep_mdd_pct=result.mdd_pct,
            rep_trades=result.total_trades,
            rep_source=source,
            rep_period_label=period_label,
            rep_leverage=macro.leverage,
        )
        session.add(row)
        session.commit()

    user_macro = None
    if account is not None:
        user_macro = user_macros_mod.save_snapshot(
            account.id,
            macro,
            source_type="builder",
            source_ref=slug,
            created_at=macro.created_at,
        )

    return {
        "macro": macro.model_dump(mode="json"),
        "share_slug": slug,
        "user_macro": user_macro,
        "human_summary": summary,
        "result": compact_backtest_result(result).model_dump(),
        "explanation": explain_result(macro, result).model_dump(),
        "data_source": source,
    }


@app.get("/api/macros/{slug}")
def get_macro(slug: str) -> dict:
    with get_session() as session:
        row = session.exec(select(MacroRow).where(MacroRow.share_slug == slug)).first()
    if not row:
        raise HTTPException(status_code=404, detail="macro not found")
    macro = _row_to_macro(row)
    return {
        "macro": macro.model_dump(mode="json"),
        "share_slug": row.share_slug,
        "human_summary": row.human_summary,
    }


@app.get("/api/backtest/limits")
def get_backtest_limits() -> dict:
    return backtest_limits()


@app.post("/api/backtest")
def backtest(
    req: BacktestRequest,
    account: Optional[User] = Depends(auth_mod.optional_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    macro = req.macro
    if req.period_override is not None:
        macro = macro.model_copy(update={"period": req.period_override})
    try:
        result, per_symbol, source, period_label = _run_any(macro)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 일일 퀘스트: 로그인 계정이 성공한 백테스트를 돌리면 하루 한 번 보상.
    quest = quests_mod.complete(db, account, "backtest_run")
    return {
        "quest": quest,
        "result": compact_backtest_result(result).model_dump(),
        "per_symbol": per_symbol,  # [] for single-symbol; portfolio breakdown otherwise
        "human_summary": human_summary(macro),
        "data_source": source,
        "period_label": period_label,
        # 껄무새 해설: 규칙기반(무료·결정론)으로 항상 동봉. AI 심화층이 나중에 같은
        # 스키마로 이 자리를 덮어써도 프론트는 그대로 렌더링됨.
        "explanation": explain_result(macro, result).model_dump(),
        "disclaimer": "past simulation only; not real trading",
    }


@app.post("/api/explain/ai")
def explain_ai(req: ExplainAiRequest) -> dict:
    """On-demand AI 원인 분석 using the server Gemini key. Always returns a valid
    ``explanation``: on any AI failure it falls back to the rule-based one (same
    schema) and reports ``ai_error`` so the UI can hint why."""
    macro = req.macro
    if req.period_override is not None:
        macro = macro.model_copy(update={"period": req.period_override})

    try:
        result, per_symbol, _, _ = _run_any(macro)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not ai_explain_mod.ai_available():
        return {"explanation": explain_result(macro, result).model_dump(), "ai_available": False}

    try:
        enriched, runtime_state = ai_explain_mod.generate_with_cache_status(
            macro,
            result,
            per_symbol=per_symbol or None,
        )
    except ai_explain_mod.AiError as exc:
        base = explain_result(macro, result).model_dump()
        return {"explanation": base, "ai_available": True, "ai_error": exc.user_message}
    except Exception:
        base = explain_result(macro, result).model_dump()
        return {"explanation": base, "ai_available": True, "ai_error": "AI 호출에 실패했어요."}

    payload = enriched.model_dump()
    response = {"explanation": payload, "ai_available": True}
    if runtime_state != "loaded":
        response["cached"] = True
    return response


@app.post("/api/optimize")
def optimize(req: OptimizeRequest) -> dict:
    """Sweep take-profit × stop-loss and return a scored grid (자동 최적화).

    Past-fit only: the response flags the overfitting risk and the UI must show
    it. Refuses symbols with no real spot data (422) rather than fabricating.
    """
    try:
        prepared = optimize_mod.prepare_optimization(req.macro, req.tp_values, req.sl_values)
        return optimize_runtime_mod.run(prepared)
    except optimize_runtime_mod.OptimizeBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "3"})
    except optimize_runtime_mod.OptimizeTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/funding-rate")
def funding_rate(
    symbol: str,
    preset: str = "1y",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict:
    """Average *daily* USDT-M funding cost (%) for a symbol over the period.

    Reference for prefilling the backtest funding fee with a realistic number.
    ``available`` is False (and the pct null) when the symbol has no perp market
    or the funding API is unreachable — the UI keeps the user's manual value.
    """
    try:
        start_ms, end_ms = resolve_period(preset, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    avg = average_daily_funding_pct(symbol.upper(), start_ms, end_ms)
    return {
        "symbol": symbol.upper(),
        "avg_daily_funding_pct": avg,
        "available": avg is not None,
        "note": "3 settlements/day, mean absolute rate; reference only",
    }


@app.get("/api/kimchi-premium")
def kimchi_premium(symbol: str = "BTC") -> dict:
    """Aggregate upbit(KRW) vs binance(USDT)×USDKRW into the kimchi premium.

    Reference indicator only — never a trading signal. Degrades gracefully if
    the FX API is down (fallback rate flagged via ``fx_is_fallback``).
    """
    return kimchi_mod.get_premium(symbol)


@app.get("/api/usdkrw")
def usdkrw() -> dict:
    """Approximate USD->KRW rate for showing KRW alongside USDT amounts.

    Reference only — reuses the kimchi FX source (free API + fallback constant).
    Amounts in this app are denominated in USDT; the returned rate lets the UI
    render a rough KRW figure next to them for convenience.
    """
    return kimchi_mod.get_usdkrw()


@app.get("/api/hangang-temp")
def hangang_temp() -> dict:
    """'한강 수온' — proxy + server-cache the public Hangang temperature API.

    Fun reference widget (GGparrot tone). Server-cached so the upstream is hit at
    most once per window regardless of client count; degrades gracefully (stale
    cache or ok:false) so the page never breaks on an upstream failure.
    """
    return hangang_mod.get_temp()


@app.get("/api/fear-greed")
def fear_greed() -> dict:
    """Crypto Fear & Greed index — MARKET-WIDE sentiment (reference only).

    Server-cached proxy of Alternative.me. One 0~100 gauge for the whole crypto
    market (BTC-centric), not per-coin; the UI labels it as such. Degrades to a
    stale copy so the banner never breaks the page.
    """
    return feargreed_mod.get_fear_greed()


@app.get("/api/candles")
def candles(
    symbol: str,
    interval: str = chart_mod.DEFAULT_INTERVAL,
    limit: int = 120,
    market: str = "spot",
) -> dict:
    """Recent OHLC candles for the live chart (public market data only).

    Globally cached per (symbol, interval, market) and sliced to the requested limit, so
    many viewers collapse into at most one upstream call per window. The last
    candle is the in-progress bar (``closed: false``) and is never persisted to
    the shared kline cache, so it can't leak into a backtest.
    """
    try:
        return chart_mod.get_candles(symbol, interval=interval, limit=limit, market=market)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/candles/live")
def live_candles(
    symbol: str,
    interval: str = chart_mod.DEFAULT_INTERVAL,
    market: str = "spot",
) -> dict:
    """Latest two public candles for the chart's moving live edge."""
    try:
        return chart_mod.get_live_candles(symbol, interval=interval, market=market)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/prices")
def prices(symbols: str = Query(default="", max_length=700)) -> dict:
    """공개 일괄 시세 — 리더보드 보유 중 행의 미실현 수익률용.

    전 종목 시세를 한 번에 받아 2초 캐시 — 요청당 상류 호출 최대 1회.
    """
    wanted = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    if not wanted or len(wanted) > marketdata_mod.MAX_PRICE_SYMBOLS or any(not marketdata_mod.SYMBOL_RE.match(s) for s in wanted):
        raise HTTPException(422, "종목 형식이 잘못됐어요.")
    return {"prices": marketdata_mod.batch_prices(wanted), "ms": int(time.time() * 1000)}


@app.get("/api/hot-coins")
def hot_coins(limit: int = 10) -> dict:
    """'오늘의 경주마' — surging + actively-traded USDT coins (Binance 24h).

    Globally cached: the exchange is hit at most once per cache window regardless
    of client count. Reference indicator only — never a trading signal.
    """
    return hotcoins_mod.get_hot_coins(limit)


@app.get("/api/news/market")
def news_market(request: Request) -> Response:
    """백그라운드에서 준비한 시장 기사와 일별 요약을 DB에서 조회한다."""
    try:
        return public_news_response(request, public_news_mod.get_market_news())
    except news_mod.NewsTranslationBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except news_mod.NewsTranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except news_mod.NewsFetchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/news/coin/{symbol}")
def news_coin(symbol: str, request: Request) -> Response:
    """준비된 코인 기사를 조회한다. 미수집 상태도 외부 호출 없이 반환한다."""
    try:
        return public_news_response(request, public_news_mod.get_coin_news(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except news_mod.NewsTranslationBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except news_mod.NewsTranslationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except news_mod.NewsFetchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/whale-activity")
def whale_activity(response: Response) -> dict:
    """Public, read-only shared holder observations; site requests never collect."""
    response.headers["Cache-Control"] = "public, max-age=2"
    return whales_mod.get_whale_activity()


@app.get("/api/gallery")
def gallery(limit: int = 50) -> dict:
    with get_session() as session:
        rows = session.exec(
            select(MacroRow).order_by(MacroRow.rep_return_pct.desc()).limit(limit)
        ).all()
    items = [
        {
            "share_slug": r.share_slug,
            "symbol": r.symbol,
            "rule_type": r.rule_type,
            "position_side": r.position_side,
            "human_summary": r.human_summary,
            "return_pct": r.rep_return_pct,
            "win_pct": r.rep_win_pct,
            "mdd_pct": r.rep_mdd_pct,
            "trades": r.rep_trades,
            "period_label": r.rep_period_label,
            "leverage": getattr(r, "rep_leverage", 1) or 1,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return {"items": items, "note": "all returns are backtest (simulated), not live"}


# --- 오늘의 리더보드 (daily KST paper-return board) ---------------------
# Simple in-memory rate limit for failed edit-password attempts: (entry_id, ip).
_edit_fails: dict[tuple[int, str], list[float]] = {}
_EDIT_MAX_FAILS = 5
_EDIT_WINDOW = 60.0


def _edit_rate_check(entry_id: int, ip: str) -> None:
    import time

    key = (entry_id, ip)
    now = time.time()
    hist = [t for t in _edit_fails.get(key, []) if now - t < _EDIT_WINDOW]
    if len(hist) >= _EDIT_MAX_FAILS:
        raise HTTPException(status_code=429, detail="비밀번호 시도가 너무 많습니다. 잠시 후 다시 시도하세요.")
    _edit_fails[key] = hist


def _edit_rate_fail(entry_id: int, ip: str) -> None:
    import time

    key = (entry_id, ip)
    _edit_fails.setdefault(key, []).append(time.time())


@app.post("/api/leaderboard/register")
async def leaderboard_register(
    req: LeaderboardRegisterRequest,
    account: Optional[User] = Depends(auth_mod.optional_user),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Register a macro: start its paper session and add it to today's board.

    If logged in, the entry is owned by that account (it earns the creator share
    when others unlock it). Anonymous registration still works (legacy: display id
    + password) and stays fully visible/free. Rejects symbols with no spot data.
    """
    macro = req.macro
    if account is not None:
        owner_user_id = account.id
        username = account.username
        password_hash = ""  # account-owned; edited via the account, not a password
    else:
        # A Bearer token was sent but no account resolved -> the session is stale
        # (expired, or the account no longer exists). Tell the user to re-login
        # instead of demanding an id/password they don't have.
        if authorization and authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="세션이 만료됐어요. 다시 로그인해 주세요.")
        if not req.username.strip() or not req.password:
            raise HTTPException(status_code=400, detail="아이디와 비밀번호를 모두 입력하세요.")
        owner_user_id = None
        username = req.username
        password_hash = hash_password(req.password)

    mode = "replay" if req.mode == "replay" else "live"
    try:
        info = await paper_mod.start_session(macro, macro.symbol, mode)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    entry = leaderboard_mod.create_entry(
        user_id=req.user_id,
        username=username,
        password_hash=password_hash,
        owner_user_id=owner_user_id,
        symbol=macro.symbol,
        macro_json=macro.model_dump_json(),
        human_summary=human_summary(macro),
        paper_session_id=info["session_id"],
    )
    if account is not None:
        user_macros_mod.save_snapshot(
            account.id,
            macro,
            source_type="created",
            source_ref=str(entry["id"]),
            created_at=entry.get("created_at", ""),
        )
        # 헤더 알림 — 등록은 이미 끝났으니 알림 저장 실패가 응답을 막지 않는다(notify_now).
        notifications_mod.notify_now(
            account.id, "macro_registered", f"{macro.symbol} 매크로를 리더보드에 등록했어요",
            "오늘 보드에서 순위와 언락 수익을 확인해요.", "/leaderboard",
            data={"entry_id": entry["id"], "symbol": macro.symbol},
        )
    return {"entry": entry, "disclaimer": "paper (simulated) trading; reference only"}


@app.get("/api/challenge/today")
def challenge_today(db: Session = Depends(request_session)) -> dict:
    """Read the completed daily challenge without starting background work."""
    return challenge_mod.get_today(db=db)


@app.get("/api/leaderboard")
def leaderboard_list(
    user_id: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    snapshot_id: str = Query(default="", max_length=64),
    entry_id: Optional[int] = Query(default=None, ge=1),
    account: Optional[User] = Depends(auth_mod.optional_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """Read a completed public ranking and current private access state."""
    return leaderboard_mod.list_entries(
        viewer_id=user_id, viewer_user_id=account.id if account else None,
        db=db, page=page, page_size=page_size, snapshot_id=snapshot_id, entry_id=entry_id,
    )


@app.post("/api/leaderboard/{entry_id}/unlock")
def leaderboard_unlock(entry_id: int, account: User = Depends(auth_mod.current_user)) -> dict:
    """Spend points to reveal+copy an entry's macro; 70% goes to its creator."""
    try:
        result = leaderboard_mod.unlock_entry(account, entry_id)
        macro_data = result.get("entry", {}).get("macro")
        if macro_data:
            result["user_macro"] = user_macros_mod.save_snapshot(
                account.id,
                Macro.model_validate(macro_data),
                source_type="leaderboard",
                source_ref=str(entry_id),
            )
        return result
    except leaderboard_mod.UnlockError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    except points_mod.InsufficientPoints as exc:
        raise HTTPException(status_code=402, detail=str(exc))


@app.post("/api/leaderboard/{entry_id}/vote")
def leaderboard_vote(entry_id: int, req: VoteRequest) -> dict:
    try:
        return leaderboard_mod.vote(entry_id, req.user_id, req.value)
    except leaderboard_mod.UnlockError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@app.post("/api/leaderboard/{entry_id}/edit")
async def leaderboard_edit(
    entry_id: int,
    req: LeaderboardEditRequest,
    request: Request,
    account: Optional[User] = Depends(auth_mod.optional_user),
) -> dict:
    """Edit an entry's macro. Account-owned entries authorize via the logged-in
    owner (no password); legacy anonymous entries verify the edit password
    (rate-limited per entry+IP). Restarts the paper session on success."""
    old = leaderboard_mod.get_entry(entry_id)
    if old is None:
        raise HTTPException(status_code=404, detail="엔트리를 찾을 수 없습니다.")

    is_account_owner = (
        old.owner_user_id is not None and account is not None and account.id == old.owner_user_id
    )
    if not is_account_owner:
        if old.owner_user_id is not None:
            raise HTTPException(status_code=403, detail="내가 등록한 매크로만 수정할 수 있어요.")
        ip = request.client.host if request.client else "unknown"
        _edit_rate_check(entry_id, ip)
        if not leaderboard_mod.verify_owner(entry_id, req.password):
            _edit_rate_fail(entry_id, ip)
            raise HTTPException(status_code=403, detail="비밀번호가 일치하지 않습니다.")

    macro = req.macro
    mode = "replay" if req.mode == "replay" else "live"
    try:
        info = await paper_mod.start_session(macro, macro.symbol, mode)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if old.paper_session_id:
        await paper_mod.stop_session(old.paper_session_id)
    entry = leaderboard_mod.update_entry(
        entry_id,
        symbol=macro.symbol,
        macro_json=macro.model_dump_json(),
        human_summary=human_summary(macro),
        paper_session_id=info["session_id"],
    )
    return {"entry": entry}


@app.delete("/api/leaderboard/{entry_id}")
async def leaderboard_delete(entry_id: int, account: User = Depends(auth_mod.current_user)) -> dict:
    """Delete one of my own (account-owned) leaderboard entries."""
    entry = leaderboard_mod.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="엔트리를 찾을 수 없습니다.")
    if entry.owner_user_id != account.id:
        raise HTTPException(status_code=403, detail="내가 등록한 매크로만 삭제할 수 있어요.")
    sid = leaderboard_mod.delete_entry(entry_id)
    if sid:
        await paper_mod.stop_session(sid)
    return {"ok": True}


# --- leaderboard chat (daily KST board) ---------------------------------
@app.get("/api/chat")
def chat_list(
    room_id: Optional[int] = Query(default=None, ge=1),
    before_id: Optional[int] = Query(default=None, ge=1, le=2**63 - 1),
    seen_id: Optional[int] = Query(default=None, ge=0),
    after_id: Optional[int] = Query(default=None, ge=0, le=2**63 - 1),
    metadata_only: bool = False,
    message_ids: Optional[str] = Query(default=None, max_length=4200, pattern=r"^\d+(,\d+)*$"),
    account: Optional[User] = Depends(auth_mod.optional_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    ids = list(dict.fromkeys(int(value) for value in message_ids.split(","))) if message_ids else None
    if ids is not None and (len(ids) > chat_mod.MAX_LIST or any(value < 1 or value > 2**63 - 1 for value in ids)):
        raise HTTPException(422, "메시지는 한 번에 200개까지 조회할 수 있어요.")
    if sum((before_id is not None, after_id is not None, metadata_only, ids is not None)) > 1:
        raise HTTPException(422, "메시지 조회 방식을 하나만 선택해 주세요.")
    try:
        return chat_mod.list_messages(account, room_id=room_id, before_id=before_id, seen_id=seen_id,
                                      after_id=after_id, metadata_only=metadata_only, message_ids=ids, db=db)
    except rooms_mod.RoomError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/api/chat")
def chat_post(req: ChatPostRequest, account: User = Depends(auth_mod.current_user_in_session),
              db: Session = Depends(request_session)) -> dict:
    try:
        msg = chat_mod.add_message(account, req.text, room_id=req.room_id, db=db)
    except rooms_mod.RoomError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except chat_mod.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": msg}


@app.put("/api/chat/read")
def chat_read(req: ChatReadRequest, account: User = Depends(auth_mod.current_user_in_session),
              db: Session = Depends(request_session)) -> dict:
    try:
        return chat_mod.mark_read(account, req.last_seen_id, room_id=req.room_id, db=db)
    except rooms_mod.RoomError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


# --- 전략방 ---------------------------------------------------------------
def _room_http(exc: Exception) -> HTTPException:
    if isinstance(exc, rooms_mod.RoomError):
        return HTTPException(exc.status, exc.message)
    if isinstance(exc, points_mod.InsufficientPoints):
        return HTTPException(402, str(exc))
    raise exc


@app.get("/api/rooms")
def rooms_list(account: User = Depends(auth_mod.current_user_in_session),
               db: Session = Depends(request_session)) -> dict:
    return rooms_mod.list_rooms(db, account)


@app.post("/api/rooms")
def rooms_create(req: RoomCreateRequest, account: User = Depends(auth_mod.current_user_in_session),
                 db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.create_room(db, account, title=req.title, capacity=req.capacity,
                                     entry_fee=req.entry_fee, consent=req.consent)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)


@app.post("/api/rooms/{room_id}/join")
def rooms_join(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
               db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.join_room(db, account, room_id)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)


@app.delete("/api/rooms/{room_id}/leave")
def rooms_leave(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
                db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.leave_room(db, account, room_id)
    except rooms_mod.RoomError as exc:
        raise _room_http(exc)


@app.post("/api/rooms/{room_id}/extend")
def rooms_extend(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
                 db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.extend_room(db, account, room_id)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)


@app.post("/api/admin/rooms/{room_id}/close")
def admin_room_close(room_id: int, admin: User = Depends(auth_mod.require_admin),
                     db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.close_room_by_admin(db, room_id)
    except rooms_mod.RoomError as exc:
        raise _room_http(exc)


# --- 껄무새 게시판 -------------------------------------------------------
@app.post("/api/board/posts")
async def board_create(
    title: str = Form(...),
    body: str = Form(""),
    body_format: str = Form("text"),
    images: list[UploadFile] = File(default=[]),
    image: Optional[UploadFile] = File(default=None),
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """글 작성 — 로그인 계정만. 사진(jpg/png, 각 2MB 이하)은 `images` 로 여러 장, 옛 클라이언트의 `image` 한 장도 받는다.
    `body_format=html` 이면 편집기 HTML(새 사진은 `data-key="new:N"` 자리)로 받아 정제해 저장한다."""
    uploads = [*(images or []), *([image] if image is not None else [])]
    uploads = [up for up in uploads if up is not None and (up.filename or "")]
    if len(uploads) > board_mod.MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"사진은 {board_mod.MAX_IMAGES}장까지 붙일 수 있어요.")
    validated: list[tuple[bytes, str]] = []
    for up in uploads:
        data = await up.read()
        try:
            validated.append(board_mod.validate_image(data, up.content_type))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await run_in_threadpool(board_mod.create_post, user, title, body, validated, body_format=body_format, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/board/posts/{post_id}")
async def board_update(
    post_id: int,
    title: str = Form(...),
    body: str = Form(""),
    body_format: str = Form("text"),
    keep_image_ids: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    """글 수정 — 작성자만. `keep_image_ids` 는 남길 사진 id 를 쉼표로(옛 한 장은 0), `images` 는 새로 붙일 사진."""
    try:
        keep = [int(part) for part in keep_image_ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="남길 사진 목록이 올바르지 않아요.")
    uploads = [up for up in (images or []) if up is not None and (up.filename or "")]
    validated: list[tuple[bytes, str]] = []
    for up in uploads:
        data = await up.read()
        try:
            validated.append(board_mod.validate_image(data, up.content_type))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        view = await run_in_threadpool(board_mod.update_post, post_id, user, title, body, keep, validated, body_format=body_format, db=db)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if view is None:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
    return view


@app.get("/api/board/posts")
def board_list(
    page: int = 1,
    size: int = board_mod.PAGE_SIZE_DEFAULT,
    sort: str = "new",
    q: str = "",
    field: str = "all",
) -> dict:
    """목록 — sort: new|likes|views|comments, q: 검색어, field: all(제목+내용)|title|author."""
    return board_mod.list_posts(page, size, sort=sort, q=q, field=field)


def _board_view_key(request: Request) -> str:
    """조회수용 방문자 키 — IP(프록시 뒤면 첫 X-Forwarded-For) + UA 해시. 저장하지 않고 메모리에서 30분만 기억한다."""
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    return hashlib.sha1(f"{ip}|{ua}".encode()).hexdigest()[:16]


@app.get("/api/board/posts/{post_id}")
def board_detail(post_id: int, request: Request, account: Optional[User] = Depends(auth_mod.optional_user_in_session), db: Session = Depends(request_session)) -> dict:
    view = board_mod.get_post(post_id, viewer_id=account.id if account else None, view_key=_board_view_key(request), db=db)
    if view is None:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
    return view


class PostVoteRequest(BaseModel):
    value: int


@app.post("/api/board/posts/{post_id}/vote")
def board_vote(post_id: int, req: PostVoteRequest, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    """추천(+1)/비추천(-1) — 로그인 계정당 한 표, 같은 표를 다시 누르면 취소."""
    result = board_mod.vote_post(post_id, user.id, req.value, db=db)
    if result is None:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
    return result


@app.delete("/api/board/posts/{post_id}")
def board_delete(post_id: int, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    if not board_mod.delete_post(post_id, user.id, db=db):
        raise HTTPException(status_code=403, detail="본인이 쓴 글만 삭제할 수 있어요.")
    return {"ok": True}


@app.get("/api/board/posts/{post_id}/image")
def board_image(post_id: int) -> Response:
    got = board_mod.get_image(post_id)
    if got is None:
        raise HTTPException(status_code=404, detail="이미지가 없어요.")
    data, mime = got
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/board/posts/{post_id}/images/{image_id}")
def board_post_image(post_id: int, image_id: int) -> Response:
    got = board_mod.get_post_image(post_id, image_id)
    if got is None:
        raise HTTPException(status_code=404, detail="이미지가 없어요.")
    data, mime = got
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


class CommentIn(BaseModel):
    text: str
    parent_id: Optional[int] = None


@app.post("/api/board/posts/{post_id}/comments")
def board_comment_add(post_id: int, req: CommentIn, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    """댓글·답글 — 로그인 계정만, 닉네임은 계정 이름. parent_id 가 있으면 그 댓글의 답글."""
    try:
        comment = board_mod.add_comment(post_id, user, req.text, parent_id=req.parent_id, db=db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except board_mod.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    # 일일 퀘스트: 짧은 댓글로 채우는 걸 막기 위해 글자 수 하한을 둔다.
    quest = None
    if len(req.text.strip()) >= quests_mod.COMMENT_MIN_CHARS:
        quest = quests_mod.complete(db, user, "board_comment")
    return {"comment": comment, "quest": quest}


class CommentEditIn(BaseModel):
    text: str


@app.put("/api/board/comments/{comment_id}")
def board_comment_edit(comment_id: int, req: CommentEditIn, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    try:
        view = board_mod.edit_comment(comment_id, user, req.text, db=db)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if view is None:
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
    return {"comment": view}


class ReportIn(BaseModel):
    target_type: str
    target_id: int
    reason: str
    detail: str = ""


@app.post("/api/board/reports")
def board_report(req: ReportIn, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    """글·댓글·채팅 신고 — 로그인 계정당 대상 하나에 한 번."""
    try:
        return board_mod.report(req.target_type, req.target_id, user, req.reason, req.detail, db=db)
    except board_mod.AlreadyReported as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/board/comments/{comment_id}")
def board_comment_delete(comment_id: int, user: User = Depends(auth_mod.current_user_in_session), db: Session = Depends(request_session)) -> dict:
    if not board_mod.delete_comment(comment_id, user, db=db):
        raise HTTPException(status_code=403, detail="본인이 쓴 댓글만 지울 수 있어요.")
    return {"ok": True}


@app.get("/api/symbols")
def symbols(response: Response) -> dict:
    """Tradable Binance USDT symbols (spot + USDT-M perpetual) for the builder's search — only these can be added."""
    try:
        data = symbols_mod.list_symbols()
        response.headers["Cache-Control"] = (
            "public, max-age=5, s-maxage=5" if data.get("stale") or not data.get("items")
            else "public, max-age=300, s-maxage=300"
        )
        return data
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"종목 목록을 불러오지 못했어요: {type(exc).__name__}")


@app.get("/api/coin-logo/{base}.png")
def coin_logo(base: str) -> Response:
    """Same-origin copy of Binance's public coin logo so the share card can be captured as an image."""
    png = symbols_mod.coin_logo_png(base)
    if png is None:
        raise HTTPException(status_code=404, detail="logo not found")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/card/{slug}.png")
def card(slug: str) -> Response:
    with get_session() as session:
        row = session.exec(select(MacroRow).where(MacroRow.share_slug == slug)).first()
    if not row:
        raise HTTPException(status_code=404, detail="macro not found")
    frontend_base = os.environ.get("FRONTEND_BASE", "http://localhost:5173")
    png = render_card(
        symbol=row.symbol,
        human_summary=row.human_summary,
        period_label=row.rep_period_label,
        return_pct=row.rep_return_pct,
        win_pct=row.rep_win_pct,
        mdd_pct=row.rep_mdd_pct,
        trades=row.rep_trades,
        share_url=f"{frontend_base}/s/{slug}",
        data_source=row.rep_source,
        leverage=getattr(row, "rep_leverage", 1) or 1,
    )
    return Response(content=png, media_type="image/png")


# --- paper (simulated) trading -----------------------------------------
@app.post("/api/paper/start")
async def paper_start(
    req: PaperStartRequest,
    account: Optional[User] = Depends(auth_mod.optional_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    mode = "replay" if req.mode == "replay" else "live"
    try:
        info = await paper_mod.start_session(req.macro, req.symbol, mode)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    info["disclaimer"] = "paper (simulated) trading; no real orders, no API keys"
    # 일일 퀘스트: 로그인 계정이 페이퍼 세션을 시작하면 하루 한 번 보상.
    info["quest"] = await run_in_threadpool(quests_mod.complete, db, account, "paper_start")
    return info


@app.post("/api/paper/{session_id}/stop")
async def paper_stop(session_id: int) -> dict:
    return await paper_mod.stop_session(session_id)


@app.get("/api/paper/{session_id}")
def paper_status(session_id: int) -> dict:
    status = paper_mod.get_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="paper session not found")
    status["disclaimer"] = "paper (simulated) trading; no real orders"
    return status


@app.get("/api/paper/{session_id}/trades")
def paper_trades(session_id: int) -> dict:
    return {"trades": paper_mod.get_trades(session_id)}


# --- real-trade executable bundle (real orders; default testnet/fake funds) -----------
@app.post("/api/realtrade/bundle")
def realtrade_bundle(req: BundleRequest) -> Response:
    data = build_bundle(req.macro)
    filename = f"realtrade-bot-{req.macro.rule_type.value}-{req.macro.position_side.value}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- 매크로 실행기용 매크로 파일 (macro.json 하나만) -------------------
@app.post("/api/realtrade/macro-file")
def realtrade_macro_file(req: BundleRequest) -> Response:
    """매크로 실행기(exe)에 넣을 정규화된 macro.json 을 반환한다.

    실행기가 엔진을 내장하므로 bot.py/run.bat 없이 이 설정 파일 하나만 내려받아
    실행기에 넣으면 된다(human_summary 동봉).
    """
    macro = req.macro
    payload = macro.model_dump(mode="json")
    payload["human_summary"] = human_summary(macro)
    # 서명 동봉 — 실행기가 시작할 때 같이 올리면 서버가 "원본 그대로인지" 판별한다.
    # 파일을 손으로 고쳐 돌리면 세션에 '수정된 파일'로 남아 문의 대응이 가능해진다.
    payload["_sig"] = macro_signing_mod.sign(macro)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = f"macro-{macro.rule_type.value}-{macro.position_side.value}.ggm.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ======================================================================
#  매크로 실행기(로컬 exe) ↔ 서버 연동
#  - 실행기용 엔드포인트: 회원 키(X-Runner-Key 헤더)로 인증
#  - 마이페이지용 엔드포인트: 로그인 계정(JWT)로 인증
# ======================================================================
def _runner_user(x_runner_key: Optional[str] = Header(default=None)) -> User:
    """X-Runner-Key 헤더의 회원 키를 계정으로 해석하는 의존성."""
    return runner_mod.user_for_key(x_runner_key or "")


# 실행기용 -------------------------------------------------------------
@app.post("/api/runner/launch-tickets/claim")
def runner_launch_ticket_claim(
    req: RunnerLaunchTicketClaimRequest,
    response: Response,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    current = req.runner_version.strip()
    if not _runner_version_supported(current):
        # 거절 사실을 티켓에 남긴다 — 이 426 은 실행기 창에만 가고, 웹은 상태 조회로 알아챈다.
        runner_mod.mark_launch_ticket_rejected(req.ticket, current)
        raise HTTPException(
            status_code=426,
            detail=f"실행기 v{_RUNNER_MIN_VERSION or '6'} 이상으로 업데이트해 주세요.",
            headers={"Cache-Control": "no-store"},
        )
    return runner_mod.claim_launch_ticket(req.ticket, runner_version=current)


def _runner_version_supported(current: str) -> bool:
    """실행기가 최소 버전 이상인가. 숫자 아닌 값은 미지원으로 본다."""
    required = _RUNNER_MIN_VERSION or "6"
    try:
        return (
            current.isascii()
            and current.isdigit()
            and required.isascii()
            and required.isdigit()
            and len(current) <= 6
            and len(required) <= 6
            and int(current) >= int(required)
        )
    except ValueError:
        return False


@app.post("/api/runner/start")
def runner_start(req: RunnerStartRequest, user: User = Depends(_runner_user)) -> dict:
    # 버전을 보낸 실행기(v7+)만 검사한다. 안 보내는 v6 이하는 아직 막지 않는다 —
    # 배포된 v6 도 버전을 안 실어 보내므로, 빈 값 거절은 v7 exe 가 나간 뒤에 켠다.
    version = req.runner_version.strip()
    if version and not _runner_version_supported(version):
        raise HTTPException(
            status_code=426,
            detail=f"실행기 v{_RUNNER_MIN_VERSION or '6'} 이상으로 업데이트해 주세요.",
        )
    return runner_mod.start_session(user, req.model_dump())


@app.post("/api/runner/heartbeat")
def runner_heartbeat(req: RunnerHeartbeatRequest, user: User = Depends(_runner_user)) -> dict:
    snap = req.model_dump()
    return runner_mod.heartbeat(user, snap.pop("session_id"), snap)


@app.post("/api/runner/stopped")
def runner_stopped(req: RunnerStoppedRequest, user: User = Depends(_runner_user)) -> dict:
    return runner_mod.mark_stopped(
        user, req.session_id, req.status, req.note, snapshot=req.snapshot, events=req.events
    )


# 마이페이지용 ---------------------------------------------------------
def _runner_launch_environment(request: Request) -> str:
    """Map only loopback web origins to the runner's fixed local API target."""
    host = (request.url.hostname or "").strip().lower()
    return "local" if host in {"localhost", "127.0.0.1", "::1"} else "production"


@app.post("/api/me/runner/launch-tickets")
def runner_launch_ticket_create(
    req: RunnerLaunchTicketCreateRequest,
    request: Request,
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return runner_mod.create_launch_ticket(
        user.id,
        req.user_macro_id,
        req.testnet,
        _runner_launch_environment(request),
    )


@app.get("/api/me/runner/launch-tickets/{launch_id}")
def runner_launch_ticket_get(
    launch_id: int,
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return runner_mod.launch_ticket_status(user.id, launch_id, min_runner_version=_RUNNER_MIN_VERSION)


@app.get("/api/me/runner/key")
def runner_key_get(user: User = Depends(auth_mod.current_user)) -> dict:
    return runner_mod.get_or_create_key(user.id)


@app.post("/api/me/runner/key/regenerate")
def runner_key_regen(user: User = Depends(auth_mod.current_user)) -> dict:
    return runner_mod.regenerate_key(user.id)


@app.get("/api/me/runner/sessions")
def runner_sessions(
    user: User = Depends(auth_mod.current_user_in_session),
    db: Session = Depends(request_session),
) -> dict:
    return runner_mod.list_sessions(user.id, db=db)


@app.post("/api/me/runner/sessions/stream-token")
def runner_sessions_stream_token(
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    """Exchange a normal login session for a short-lived WS-only token."""
    response.headers["Cache-Control"] = "no-store"
    return auth_mod.make_runner_session_stream_token(user.id)


@app.websocket("/api/me/runner/sessions/stream")
async def runner_sessions_stream(websocket: WebSocket) -> None:
    """Push authoritative runner-session snapshots to one signed-in account.

    The browser supplies two websocket subprotocols because the WebSocket API
    cannot attach a Bearer header:
    ``ggparrot.sessions.v1`` and ``ggp-auth.<short-lived-purpose-JWT>``.
    Only the non-secret version protocol is echoed in the handshake.
    """
    protocols = list(websocket.scope.get("subprotocols") or [])
    version_protocol = "ggparrot.sessions.v1"
    auth_values = [p[len("ggp-auth."):] for p in protocols if p.startswith("ggp-auth.")]
    if version_protocol not in protocols or len(auth_values) != 1 or not auth_values[0]:
        await websocket.close(code=4401, reason="실시간 세션 연결 인증이 필요해요.")
        return
    try:
        user_id = auth_mod.decode_runner_session_stream_token(auth_values[0])
    except HTTPException:
        await websocket.close(code=4401, reason="실시간 세션 연결 인증이 유효하지 않아요.")
        return
    if await asyncio.to_thread(auth_mod.get_user_by_id, user_id) is None:
        await websocket.close(code=4401, reason="계정을 찾을 수 없어요.")
        return

    subscription_id, changed = runner_mod.subscribe_session_stream(user_id)
    try:
        await websocket.accept(subprotocol=version_protocol)
        await websocket.send_json(
            {
                "type": "sessions.snapshot",
                "reason": "initial",
                "data": await asyncio.to_thread(runner_mod.list_sessions, user_id),
            }
        )
        while True:
            try:
                await asyncio.wait_for(
                    changed.wait(),
                    timeout=runner_mod.SESSION_STREAM_RESYNC_SECONDS,
                )
                reason = "update"
                changed.clear()
            except asyncio.TimeoutError:
                reason = "resync"
            try:
                await asyncio.to_thread(auth_mod.decode_runner_session_stream_token, auth_values[0], check_expiry=False)
            except HTTPException:
                await websocket.close(code=4401, reason="계정 인증이 변경됐어요. 다시 로그인해 주세요.")
                break
            await websocket.send_json(
                {
                    "type": "sessions.snapshot",
                    "reason": reason,
                    "data": await asyncio.to_thread(runner_mod.list_sessions, user_id),
                }
            )
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        runner_mod.unsubscribe_session_stream(user_id, subscription_id)


@app.post("/api/me/runner/sessions/{session_id}/request-stop")
def runner_request_stop(
    session_id: int, req: RunnerStopRequest, user: User = Depends(auth_mod.current_user)
) -> dict:
    return runner_mod.request_stop(user.id, session_id, req.mode)


@app.get("/api/me/runner/sessions/{session_id}/events")
def runner_session_events(
    session_id: int, limit: int = 300, user: User = Depends(auth_mod.current_user)
) -> dict:
    """세션의 실행 로그(최신순) — 실행기가 heartbeat 로 올린 신호·주문·체결·오류."""
    return runner_mod.list_events(user.id, session_id, limit=limit)


@app.delete("/api/me/runner/sessions/{session_id}")
def runner_delete_session(
    session_id: int, user: User = Depends(auth_mod.current_user)
) -> dict:
    """목록에서 세션을 지운다(응답이 끊겼거나 이미 끝난 세션만)."""
    return runner_mod.delete_session(user.id, session_id)


# 실행기(exe) 배포 파일 다운로드 -----------------------------------------
# 빌드한 exe 를 RUNNER_EXE_PATH 에 두면 서비스에서 바로 내려받게 한다. 없으면
# 다운로드 페이지가 '준비 중' 으로 표시된다(available:false).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_RUNNER_EXE_NAME = "ggparrot-runner.exe"
_RUNNER_EXE_PATH = os.environ.get("RUNNER_EXE_PATH") or os.path.join(
    _REPO_ROOT, "runner", "dist", _RUNNER_EXE_NAME
)


# v6 adds confirmed fills and authoritative final position reporting.
_RUNNER_V6_URL = "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v6/ggparrot-runner.exe"
_RUNNER_DOWNLOAD_URL = os.environ.get("RUNNER_DOWNLOAD_URL", "").strip() or _RUNNER_V6_URL
# Upgrade stale official release configuration after the immutable v6 asset is published.
if _RUNNER_DOWNLOAD_URL in {
    _RUNNER_V6_URL.replace("runner-v6", f"runner-v{version}") for version in range(1, 6)
}:
    _RUNNER_DOWNLOAD_URL = _RUNNER_V6_URL
_RUNNER_SUPPORT_DEFAULT = "true" if _RUNNER_DOWNLOAD_URL == _RUNNER_V6_URL else "false"
_RUNNER_SUPPORTS_LAUNCH = os.environ.get(
    "RUNNER_SUPPORTS_LAUNCH", _RUNNER_SUPPORT_DEFAULT
).strip().lower() in {"1", "true", "yes"}
_RUNNER_LAUNCH_SCHEME = "ggparrot" if _RUNNER_SUPPORTS_LAUNCH else ""
_RUNNER_MIN_VERSION = (
    os.environ.get("RUNNER_MIN_VERSION", "6").strip() or "6"
) if _RUNNER_SUPPORTS_LAUNCH else ""
_RUNNER_EXE_VERSION = os.environ.get("RUNNER_EXE_VERSION", "").strip()
if _RUNNER_DOWNLOAD_URL == _RUNNER_V6_URL:
    _RUNNER_EXE_VERSION = "6"
    if _RUNNER_SUPPORTS_LAUNCH:
        _RUNNER_MIN_VERSION = "6"


def _runner_launch_capabilities() -> dict:
    return {
        "supports_launch": _RUNNER_SUPPORTS_LAUNCH,
        "launch_scheme": _RUNNER_LAUNCH_SCHEME,
        "min_runner_version": _RUNNER_MIN_VERSION,
    }


@app.get("/api/runner/download/info")
def runner_download_info() -> dict:
    """실행기 파일의 준비 여부/크기/버전/외부링크. 다운로드 페이지가 버튼 상태를 정한다."""
    if _RUNNER_DOWNLOAD_URL:
        return {
            "available": True,
            "filename": _RUNNER_EXE_NAME,
            "size": 0,  # 외부 링크라 크기 미상
            "version": _RUNNER_EXE_VERSION,
            "url": _RUNNER_DOWNLOAD_URL,
            **_runner_launch_capabilities(),
        }
    exists = os.path.isfile(_RUNNER_EXE_PATH)
    return {
        "available": exists,
        "filename": _RUNNER_EXE_NAME,
        "size": os.path.getsize(_RUNNER_EXE_PATH) if exists else 0,
        "version": _RUNNER_EXE_VERSION,
        "url": "",
        **_runner_launch_capabilities(),
    }


@app.get("/api/runner/download")
def runner_download():
    # 외부 링크가 설정돼 있으면 그리로 리다이렉트(GitHub Releases 등).
    if _RUNNER_DOWNLOAD_URL:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(_RUNNER_DOWNLOAD_URL)
    if not os.path.isfile(_RUNNER_EXE_PATH):
        raise HTTPException(status_code=404, detail="실행기 파일이 아직 준비되지 않았어요.")
    from fastapi.responses import FileResponse

    return FileResponse(
        _RUNNER_EXE_PATH, media_type="application/octet-stream", filename=_RUNNER_EXE_NAME
    )


# --- serve built frontend if present (production single-process) --------
# Dev flow is Vite (:5173) + uvicorn (:8000). If the SPA has been built,
# also serve it here with an index.html fallback so deep links (/s/:slug,
# /gallery) work on refresh.
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")
if os.path.isdir(_DIST):
    from fastapi.responses import FileResponse
    from fastapi import Request

    _ASSETS = os.path.join(_DIST, "assets")
    if os.path.isdir(_ASSETS):
        app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, request: Request):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        candidate = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))


# Export the complete ASGI stack. Keeping the FastAPI instance separately is
# useful for introspection while the public ``app`` is what Uvicorn/TestClient
# must run so uncaught framework errors retain both CORS and trace headers.
api_app = app
app = observe_application(
    api_app,
    allow_origins=["*"],  # dev: Vite on :5173; demo-scope only
    allow_methods=["*"],
    allow_headers=["*"],
)
