"""FastAPI app: macro create/fetch, backtest, gallery, share card.

No exchange order APIs. Only the public Binance klines endpoint is used, for
historical data. Every returned result represents a PAST SIMULATION.
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

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
from . import feargreed as feargreed_mod
from . import hangang as hangang_mod
from . import hotcoins as hotcoins_mod
from . import kimchi as kimchi_mod
from . import news as news_mod
from . import board as board_mod
from . import leaderboard as leaderboard_mod
from . import optimize as optimize_mod
from . import paper as paper_mod
from . import ai_explain as ai_explain_mod
from . import auth as auth_mod
from . import points as points_mod
from . import account as account_mod
from . import challenge as challenge_mod
from . import runner as runner_mod
from . import user_macros as user_macros_mod
from fastapi import Depends
from .db import User
# [차후 도입] 고래 동향 — app/whales.py 는 그대로 두고 라우트만 꺼둡니다.
# from . import whales as whales_mod
from .card import render_card
from .security import hash_password
from .data import NoSpotDataError, average_daily_funding_pct, get_klines, resolve_period
from .marketdata import fetch_klines_for_macro
from .db import MacroRow, get_session, init_db
from .engine import BacktestResult, Macro, Period, human_summary
from .engine.backtest import run_backtest
from .engine import portfolio as portfolio_mod
from .engine.explain import explain_result
from .engine.summary import _coin
from .realtrade import build_bundle

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Coin Macro Backtest & Share (Simulation only)", lifespan=lifespan)

# Ensure tables exist even when the app is imported without the lifespan running
# (e.g. TestClient constructed without a context manager).
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev: Vite on :5173; demo-scope only
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- helpers ------------------------------------------------------------
def _period_label(period: Period) -> str:
    labels = {"1y": "최근 1년", "6m": "최근 6개월", "3m": "최근 3개월"}
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


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


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
    symbol: str
    position_side: str = "long"
    leverage: int = 1
    market: str = ""  # spot | futures | "" (서버가 방향/레버리지로 결정)
    testnet: bool = True
    human_summary: str = ""
    # 실행 중인 매크로 원문(선택) — 마이페이지 실시간 차트에 전략 보조지표를 그리는
    # 데 쓴다. 거래소 키/시크릿은 포함되지 않는다. 예전 실행기는 보내지 않는다.
    macro: Optional[dict] = None


class RunnerHeartbeatRequest(BaseModel):
    session_id: int
    in_position: bool = False
    last_price: float = 0.0
    entry_price: float = 0.0
    position_qty: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pct: float = 0.0
    note: str = ""


class RunnerStoppedRequest(BaseModel):
    session_id: int
    status: str = "stopped"  # stopped | error
    note: str = ""


class RunnerStopRequest(BaseModel):
    mode: str  # stop_only | close_and_stop


class RunnerLaunchTicketCreateRequest(BaseModel):
    user_macro_id: int
    testnet: Literal[True] = True


class RunnerLaunchTicketClaimRequest(BaseModel):
    ticket: str


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
    username: str
    text: str


# --- endpoints ----------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "disclaimer": "past simulation only; no live trading"}


# --- auth / account ----------------------------------------------------
@app.post("/api/auth/signup")
def auth_signup(req: SignupRequest) -> dict:
    """Create an account (email/username/password) and grant starter points."""
    return auth_mod.signup(req.email, req.username, req.password)


@app.post("/api/auth/login")
def auth_login(req: LoginRequest) -> dict:
    return auth_mod.login(req.email, req.password)


@app.post("/api/auth/forgot")
def auth_forgot(req: ForgotRequest) -> dict:
    """Email a password-reset link (no-op delivery until email is configured)."""
    return auth_mod.request_password_reset(req.email)


@app.post("/api/auth/reset")
def auth_reset(req: ResetRequest) -> dict:
    return auth_mod.reset_password(req.token, req.password)


@app.get("/api/auth/me")
def auth_me(user: User = Depends(auth_mod.current_user)) -> dict:
    """Current account (from the Bearer token), including the points balance."""
    return {"user": auth_mod.user_view(user)}


@app.get("/api/me/dashboard")
def me_dashboard(user: User = Depends(auth_mod.current_user)) -> dict:
    """My-page rollup: profile+tier, created/purchased macros, sales, ledger, 내 글."""
    d = account_mod.dashboard(user)
    d["my_posts"] = board_mod.my_posts(user.id)
    return d


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
        "result": result.model_dump(),
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


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict:
    macro = req.macro
    if req.period_override is not None:
        macro = macro.model_copy(update={"period": req.period_override})
    try:
        result, per_symbol, source, period_label = _run_any(macro)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "result": result.model_dump(),
        "per_symbol": per_symbol,  # [] for single-symbol; portfolio breakdown otherwise
        "human_summary": human_summary(macro),
        "data_source": source,
        "period_label": period_label,
        # 껄무새 해설: 규칙기반(무료·결정론)으로 항상 동봉. AI 심화층이 나중에 같은
        # 스키마로 이 자리를 덮어써도 프론트는 그대로 렌더링됨.
        "explanation": explain_result(macro, result).model_dump(),
        "disclaimer": "past simulation only; not real trading",
    }


# AI 심화 해설 캐시: 백테스트가 결정론이라 (매크로 → 결과 → AI 텍스트)도 결정론.
# 같은 매크로 재클릭은 LLM 재호출 없이 즉시 반환한다.
_ai_explain_cache: dict[str, dict] = {}
_AI_EXPLAIN_CACHE_MAX = 500


@app.post("/api/explain/ai")
def explain_ai(req: ExplainAiRequest) -> dict:
    """On-demand AI 원인 분석 using the server Anthropic key. Always returns a valid
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

    # Deterministic backtest -> macro fully determines the AI text, so cache
    # successful results and skip the LLM on repeats. Never cache failures.
    cache_key = macro.model_dump_json()
    hit = _ai_explain_cache.get(cache_key)
    if hit is not None:
        return {"explanation": hit, "ai_available": True, "cached": True}

    try:
        enriched = ai_explain_mod.generate(macro, result, per_symbol=per_symbol or None)
    except ai_explain_mod.AiError as exc:
        base = explain_result(macro, result).model_dump()
        return {"explanation": base, "ai_available": True, "ai_error": exc.user_message}
    except Exception:
        base = explain_result(macro, result).model_dump()
        return {"explanation": base, "ai_available": True, "ai_error": "AI 호출에 실패했어요."}

    payload = enriched.model_dump()
    if len(_ai_explain_cache) >= _AI_EXPLAIN_CACHE_MAX:
        _ai_explain_cache.clear()
    _ai_explain_cache[cache_key] = payload
    return {"explanation": payload, "ai_available": True}


@app.post("/api/optimize")
def optimize(req: OptimizeRequest) -> dict:
    """Sweep take-profit × stop-loss and return a scored grid (자동 최적화).

    Past-fit only: the response flags the overfitting risk and the UI must show
    it. Refuses symbols with no real spot data (422) rather than fabricating.
    """
    try:
        return optimize_mod.optimize_tp_sl(req.macro, req.tp_values, req.sl_values)
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

    Globally cached per (symbol, interval, limit, market) for a few seconds, so
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


@app.get("/api/hot-coins")
def hot_coins(limit: int = 10) -> dict:
    """'오늘의 경주마' — surging + actively-traded USDT coins (Binance 24h).

    Globally cached: the exchange is hit at most once per cache window regardless
    of client count. Reference indicator only — never a trading signal.
    """
    return hotcoins_mod.get_hot_coins(limit)


@app.get("/api/news/market")
def news_market() -> dict:
    """'오늘의 코인동향' — 시장·규제 전반 뉴스 헤드라인 + AI 중립 개요.

    Google News RSS(무료) 기반. KST 하루 1회만 요약해 캐시(정보 제공용, 자문 아님).
    """
    return news_mod.get_market_news()


@app.get("/api/news/coin/{symbol}")
def news_coin(symbol: str) -> dict:
    """'경주마 동향' — 특정 코인의 최신 뉴스 헤드라인(요약 목록). 캐시."""
    return news_mod.get_coin_news(symbol)


# [차후 도입] '고래 동향' — 온체인 상위 지갑 매수/매도 흐름 (참고 지표).
# 상위 보유자 목록에 거래소·컨트랙트 지갑이 섞여 신호 신뢰도가 낮아 일단 보류.
# 주소 라벨링을 보강한 뒤 아래 라우트와 App.jsx 의 <WhaleBanner /> 를 함께 되살리면 됩니다.
# 로직/테스트는 app/whales.py, tests/test_whales.py 에 그대로 남아 있습니다.
#
# @app.get("/api/whale-activity")
# def whale_activity() -> dict:
#     """Server-cached per coin; degrades to stale/omitted so the page never breaks."""
#     return whales_mod.get_whale_activity()


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
    return {"entry": entry, "disclaimer": "paper (simulated) trading; reference only"}


@app.get("/api/challenge/today")
async def challenge_today() -> dict:
    """Today's AI challenge (lazily generated once per KST day): symbol + 🤖 name."""
    return await challenge_mod.get_today()


@app.get("/api/leaderboard")
def leaderboard_list(user_id: str = "", account: Optional[User] = Depends(auth_mod.optional_user)) -> dict:
    return leaderboard_mod.list_entries(
        viewer_id=user_id, viewer_user_id=account.id if account else None
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
    return leaderboard_mod.vote(entry_id, req.user_id, req.value)


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
        paper_mod.stop_session(old.paper_session_id)
    entry = leaderboard_mod.update_entry(
        entry_id,
        symbol=macro.symbol,
        macro_json=macro.model_dump_json(),
        human_summary=human_summary(macro),
        paper_session_id=info["session_id"],
    )
    return {"entry": entry}


@app.delete("/api/leaderboard/{entry_id}")
def leaderboard_delete(entry_id: int, account: User = Depends(auth_mod.current_user)) -> dict:
    """Delete one of my own (account-owned) leaderboard entries."""
    entry = leaderboard_mod.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="엔트리를 찾을 수 없습니다.")
    if entry.owner_user_id != account.id:
        raise HTTPException(status_code=403, detail="내가 등록한 매크로만 삭제할 수 있어요.")
    sid = leaderboard_mod.delete_entry(entry_id)
    if sid:
        paper_mod.stop_session(sid)
    return {"ok": True}


# --- leaderboard chat (daily KST board) ---------------------------------
@app.get("/api/chat")
def chat_list() -> dict:
    return chat_mod.list_messages()


@app.post("/api/chat")
def chat_post(req: ChatPostRequest, request: Request) -> dict:
    ip = request.client.host if request.client else "unknown"
    try:
        msg = chat_mod.add_message(req.username, req.text, ip)
    except chat_mod.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": msg}


# --- 껄무새 게시판 -------------------------------------------------------
@app.post("/api/board/posts")
async def board_create(
    title: str = Form(...),
    body: str = Form(""),
    image: Optional[UploadFile] = File(default=None),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    """글 작성 — 로그인 계정만. 이미지(jpg/png, 2MB 이하) 1장 선택."""
    image_bytes: Optional[bytes] = None
    image_mime = ""
    if image is not None and (image.filename or ""):
        data = await image.read()
        try:
            image_bytes, image_mime = board_mod.validate_image(data, image.content_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        return board_mod.create_post(user, title, body, image_bytes, image_mime)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/board/posts")
def board_list(page: int = 1, size: int = board_mod.PAGE_SIZE_DEFAULT) -> dict:
    return board_mod.list_posts(page, size)


@app.get("/api/board/posts/{post_id}")
def board_detail(post_id: int) -> dict:
    view = board_mod.get_post(post_id)
    if view is None:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
    return view


@app.delete("/api/board/posts/{post_id}")
def board_delete(post_id: int, user: User = Depends(auth_mod.current_user)) -> dict:
    if not board_mod.delete_post(post_id, user.id):
        raise HTTPException(status_code=403, detail="본인이 쓴 글만 삭제할 수 있어요.")
    return {"ok": True}


@app.get("/api/board/posts/{post_id}/image")
def board_image(post_id: int) -> Response:
    got = board_mod.get_image(post_id)
    if got is None:
        raise HTTPException(status_code=404, detail="이미지가 없어요.")
    data, mime = got
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


class CommentIn(BaseModel):
    username: str
    password: str
    text: str


@app.post("/api/board/posts/{post_id}/comments")
def board_comment_add(post_id: int, req: CommentIn, request: Request) -> dict:
    ip = request.client.host if request.client else "unknown"
    try:
        return {"comment": board_mod.add_comment(post_id, req.username, req.password, req.text, ip)}
    except board_mod.RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class CommentDeleteIn(BaseModel):
    password: str


@app.delete("/api/board/comments/{comment_id}")
def board_comment_delete(comment_id: int, req: CommentDeleteIn) -> dict:
    if not board_mod.delete_comment(comment_id, req.password):
        raise HTTPException(status_code=403, detail="비밀번호가 맞지 않아요.")
    return {"ok": True}


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
async def paper_start(req: PaperStartRequest) -> dict:
    mode = "replay" if req.mode == "replay" else "live"
    try:
        info = await paper_mod.start_session(req.macro, req.symbol, mode)
    except NoSpotDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    info["disclaimer"] = "paper (simulated) trading; no real orders, no API keys"
    return info


@app.post("/api/paper/{session_id}/stop")
def paper_stop(session_id: int) -> dict:
    return paper_mod.stop_session(session_id)


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
    return runner_mod.claim_launch_ticket(req.ticket)


@app.post("/api/runner/start")
def runner_start(req: RunnerStartRequest, user: User = Depends(_runner_user)) -> dict:
    return runner_mod.start_session(user, req.model_dump())


@app.post("/api/runner/heartbeat")
def runner_heartbeat(req: RunnerHeartbeatRequest, user: User = Depends(_runner_user)) -> dict:
    snap = req.model_dump()
    return runner_mod.heartbeat(user, snap.pop("session_id"), snap)


@app.post("/api/runner/stopped")
def runner_stopped(req: RunnerStoppedRequest, user: User = Depends(_runner_user)) -> dict:
    return runner_mod.mark_stopped(user, req.session_id, req.status, req.note)


# 마이페이지용 ---------------------------------------------------------
@app.post("/api/me/runner/launch-tickets")
def runner_launch_ticket_create(
    req: RunnerLaunchTicketCreateRequest,
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return runner_mod.create_launch_ticket(user.id, req.user_macro_id, req.testnet)


@app.get("/api/me/runner/launch-tickets/{launch_id}")
def runner_launch_ticket_get(
    launch_id: int,
    response: Response,
    user: User = Depends(auth_mod.current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return runner_mod.launch_ticket_status(user.id, launch_id)


@app.get("/api/me/runner/key")
def runner_key_get(user: User = Depends(auth_mod.current_user)) -> dict:
    return runner_mod.get_or_create_key(user.id)


@app.post("/api/me/runner/key/regenerate")
def runner_key_regen(user: User = Depends(auth_mod.current_user)) -> dict:
    return runner_mod.regenerate_key(user.id)


@app.get("/api/me/runner/sessions")
def runner_sessions(user: User = Depends(auth_mod.current_user)) -> dict:
    return runner_mod.list_sessions(user.id)


@app.post("/api/me/runner/sessions/{session_id}/request-stop")
def runner_request_stop(
    session_id: int, req: RunnerStopRequest, user: User = Depends(auth_mod.current_user)
) -> dict:
    return runner_mod.request_stop(user.id, session_id, req.mode)


# 실행기(exe) 배포 파일 다운로드 -----------------------------------------
# 빌드한 exe 를 RUNNER_EXE_PATH 에 두면 서비스에서 바로 내려받게 한다. 없으면
# 다운로드 페이지가 '준비 중' 으로 표시된다(available:false).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_RUNNER_EXE_NAME = "ggparrot-runner.exe"
_RUNNER_EXE_PATH = os.environ.get("RUNNER_EXE_PATH") or os.path.join(
    _REPO_ROOT, "runner", "dist", _RUNNER_EXE_NAME
)


# 외부 배포 링크(GitHub Releases 등). 설정하면 서버에 파일을 두지 않아도 이 링크로
# 내려받게 한다. 없으면 서버의 로컬 exe(_RUNNER_EXE_PATH)로 폴백한다.
_RUNNER_DOWNLOAD_URL = os.environ.get("RUNNER_DOWNLOAD_URL", "").strip()
_RUNNER_SUPPORTS_LAUNCH = os.environ.get("RUNNER_SUPPORTS_LAUNCH", "").strip().lower() in {
    "1", "true", "yes",
}
_RUNNER_LAUNCH_SCHEME = "ggparrot" if _RUNNER_SUPPORTS_LAUNCH else ""
_RUNNER_MIN_VERSION = (
    os.environ.get("RUNNER_MIN_VERSION", "2").strip() or "2"
) if _RUNNER_SUPPORTS_LAUNCH else ""


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
            "version": os.environ.get("RUNNER_EXE_VERSION", ""),
            "url": _RUNNER_DOWNLOAD_URL,
            **_runner_launch_capabilities(),
        }
    exists = os.path.isfile(_RUNNER_EXE_PATH)
    return {
        "available": exists,
        "filename": _RUNNER_EXE_NAME,
        "size": os.path.getsize(_RUNNER_EXE_PATH) if exists else 0,
        "version": os.environ.get("RUNNER_EXE_VERSION", ""),
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
