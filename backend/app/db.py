"""SQLite persistence for macros (SQLModel)."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import BigInteger, event

# epoch 밀리초를 담는 컬럼은 반드시 BIGINT 여야 한다. SQLite 의 INTEGER 는
# 가변 길이(최대 8바이트)라 그냥 들어가지만, Postgres 의 INTEGER 는 정확히
# 32비트(최대 2,147,483,647)라 1.7e12 인 밀리초가 800배 초과로 터진다
# (psycopg.errors.NumericValueOutOfRange). SQLite 로만 개발하면 안 보이고
# Supabase 로 옮기는 순간 리더보드·채팅·게시판이 전부 500 이 된다.
from sqlmodel import Field, Session, SQLModel, create_engine

from .observability import record_timing

# Engine selection: DATABASE_URL (Supabase/Postgres) in prod, local SQLite otherwise.
# This lets us develop & test on SQLite and run durable Postgres in deployment
# without any code change — only the env var differs. Accounts, points and the
# point ledger MUST live on the durable store (Render's free disk is ephemeral).
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def _sqlite_engine():
    # 테스트는 SQLITE_PATH 로 임시 파일을 지정해 개발용 app.db 와도 분리한다.
    path = os.environ.get("SQLITE_PATH", "").strip() or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "app.db"
    )
    return create_engine(f"sqlite:///{path}", echo=False)


def _build_engine():
    url = _DATABASE_URL
    if url:
        # Normalize to the psycopg (v3) driver SQLAlchemy expects.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgresql+psycopg://"):
            # pool_pre_ping recycles connections Supabase drops when idle.
            connect_timeout = max(
                1,
                int(os.environ.get("DATABASE_CONNECT_TIMEOUT_SECONDS", "10")),
            )
            statement_timeout_ms = max(
                1_000,
                int(os.environ.get("DATABASE_STATEMENT_TIMEOUT_MS", "30000")),
            )
            return create_engine(
                url,
                echo=False,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": connect_timeout,
                    "options": f"-c statement_timeout={statement_timeout_ms}",
                },
            )
        # Wrong value (e.g. the https project URL was pasted instead of the
        # Postgres connection string). Don't crash the whole app — fall back to
        # SQLite and warn loudly so the misconfig is obvious in the logs.
        scheme = _DATABASE_URL.split("://", 1)[0]
        print(
            f"[db] DATABASE_URL scheme '{scheme}' is not Postgres; falling back to "
            "SQLite (ephemeral). Set it to the Supabase Session-pooler connection "
            "string (postgresql://...).",
            flush=True,
        )
    return _sqlite_engine()


_engine = _build_engine()


def before_cursor_execute(conn, _cursor, _statement, _parameters, _context, _executemany):
    conn.info.setdefault("ggp_query_started", []).append(time.perf_counter())


def after_cursor_execute(conn, _cursor, _statement, _parameters, _context, _executemany):
    starts = conn.info.get("ggp_query_started") or []
    if not starts:
        return
    started = starts.pop()
    record_timing("sql", (time.perf_counter() - started) * 1000.0)


def handle_query_error(exception_context):
    conn = exception_context.connection
    starts = conn.info.get("ggp_query_started") if conn is not None else None
    if starts:
        started = starts.pop()
        record_timing("sql", (time.perf_counter() - started) * 1000.0)


event.listen(_engine, "before_cursor_execute", before_cursor_execute)
event.listen(_engine, "after_cursor_execute", after_cursor_execute)
event.listen(_engine, "handle_error", handle_query_error)


class TracedSession(Session):
    """Session whose public DB operations include checkout and transaction RTT.

    Cursor events above intentionally measure only SQL execution. These outer
    operations also cover pool checkout/pre-ping plus commit, rollback, and
    connection release. Nested calls (for example ``commit`` -> ``flush``) are
    folded into one span so totals are not double counted.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ggp_trace_depth = 0

    @contextmanager
    def _timed_db_operation(self):
        outermost = self._ggp_trace_depth == 0
        started = time.perf_counter() if outermost else 0.0
        self._ggp_trace_depth += 1
        try:
            yield
        finally:
            self._ggp_trace_depth -= 1
            if outermost:
                record_timing("db", (time.perf_counter() - started) * 1000.0)

    def exec(self, *args, **kwargs):
        with self._timed_db_operation():
            return super().exec(*args, **kwargs)

    def execute(self, *args, **kwargs):
        with self._timed_db_operation():
            return super().execute(*args, **kwargs)

    def get(self, *args, **kwargs):
        with self._timed_db_operation():
            return super().get(*args, **kwargs)

    def connection(self, *args, **kwargs):
        with self._timed_db_operation():
            return super().connection(*args, **kwargs)

    def flush(self, *args, **kwargs) -> None:
        with self._timed_db_operation():
            super().flush(*args, **kwargs)

    def refresh(self, *args, **kwargs) -> None:
        with self._timed_db_operation():
            super().refresh(*args, **kwargs)

    def commit(self) -> None:
        with self._timed_db_operation():
            super().commit()

    def rollback(self) -> None:
        if not self.in_transaction():
            super().rollback()
            return
        with self._timed_db_operation():
            super().rollback()

    def close(self) -> None:
        # A second close is a SQLAlchemy-supported no-op and should not create a
        # synthetic DB span. An active transaction means close will roll it back
        # and release its pooled connection, which is real DB lifecycle work.
        if not self.in_transaction():
            super().close()
            return
        with self._timed_db_operation():
            super().close()


def _is_sqlite() -> bool:
    return _engine.dialect.name == "sqlite"


class MacroRow(SQLModel, table=True):
    """One shared macro plus a representative backtest snapshot for the gallery."""

    id: Optional[int] = Field(default=None, primary_key=True)
    macro_id: str = Field(index=True, unique=True)
    share_slug: str = Field(index=True, unique=True)
    symbol: str
    rule_type: str
    position_side: str
    macro_json: str  # full normalized macro JSON
    human_summary: str
    created_at: str

    # Representative backtest snapshot (over the macro's own period) for gallery/card.
    rep_return_pct: float = 0.0
    rep_win_pct: float = 0.0
    rep_mdd_pct: float = 0.0
    rep_trades: int = 0
    rep_source: str = ""
    rep_period_label: str = ""
    rep_leverage: int = 1  # macro leverage (for the gallery high-risk badge)


class PaperSession(SQLModel, table=True):
    """A live/replay paper-trading session (simulated fills, no real orders)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    macro_id: str = Field(index=True)
    symbol: str
    mode: str = "live"  # live | replay
    status: str = "running"  # running | stopped
    started_at: str
    stopped_at: Optional[str] = None
    virtual_balance: float = 0.0  # initial capital
    current_equity: float = 0.0
    current_return: float = 0.0
    # isolated-margin liquidation stats (leverage > 1); 0 for spot macros
    liquidations: int = 0
    liquidated_loss: float = 0.0
    macro_json: str = ""


class LeaderboardEntry(SQLModel, table=True):
    """One user's macro entered into the daily (KST) paper-return leaderboard."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)  # anonymous localStorage id (voting/identity)
    nickname: str
    username: str = ""  # display id chosen at register time (v7)
    password_hash: str = ""  # PBKDF2 hash for edit ownership; never returned (v7)
    # Marketplace: the registered account that owns this entry and receives the
    # creator share when others unlock it. None for legacy anonymous entries.
    owner_user_id: Optional[int] = Field(default=None, index=True)
    # Daily AI challenge bots: free/visible (owner None) but flagged so the UI
    # can mark them 🤖 and users know who to beat.
    is_ai: bool = Field(default=False)
    symbol: str
    macro_json: str
    human_summary: str
    paper_session_id: Optional[int] = None
    created_at: str  # UTC ISO
    created_ms: int = Field(index=True, sa_type=BigInteger)  # epoch ms
    # 매일 KST 00:00 초기화 때 상위 N등은 다음 날 보드로 이월된다(leaderboard.py).
    # 이월은 created_ms 를 그날 자정으로 밀어 오늘 필터에 다시 걸리게 하는 방식이라,
    # 원래 등록 시각은 first_created_ms 에 한 번만 보관한다(이월 전 None).
    streak_days: int = Field(default=1)  # 보드에 연속으로 남은 일수(1 = 오늘 등록)
    first_created_ms: Optional[int] = Field(default=None, sa_type=BigInteger)


class User(SQLModel, table=True):
    """A registered account. Identity + virtual points wallet.

    Auth is our own (email + PBKDF2 hash + JWT session); Supabase is used purely
    as the Postgres store. ``points_balance`` is the authoritative wallet; every
    change is also appended to :class:`PointLedger` for an auditable history.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    username: str = Field(index=True, unique=True)  # public display id
    password_hash: str  # PBKDF2 (see security.py); never returned
    points_balance: int = Field(default=0)  # virtual points (no cash yet)
    created_at: str


class PointLedger(SQLModel, table=True):
    """Append-only record of every points change (audit trail for the wallet)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    delta: int  # +credit / -debit
    balance_after: int
    reason: str  # signup_grant | unlock_spend | unlock_earn | topup(future)
    ref: str = ""  # e.g. "entry:123"
    created_at: str
    created_ms: int = Field(index=True, sa_type=BigInteger)


class MacroUnlock(SQLModel, table=True):
    """Records that a user paid points to reveal/copy a leaderboard entry's macro."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)  # who unlocked
    entry_id: int = Field(index=True)  # which leaderboard entry
    price: int  # points paid
    created_at: str


class UserMacro(SQLModel, table=True):
    """A normalized macro snapshot owned by one account.

    Leaderboard rows can be edited or deleted after another user unlocks them,
    so quick-run must never keep reading the seller's live row. This table is
    the stable account library used by the runner flow. Exchange credentials
    are deliberately not part of this model.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    name: str = ""
    symbol: str = Field(index=True)
    rule_type: str = ""
    position_side: str = "long"
    macro_json: str
    human_summary: str = ""
    source_type: str = Field(index=True)  # created | leaderboard | upload | builder
    source_ref: str = Field(default="", index=True)
    schema_version: str = "1"
    created_at: str
    updated_at: str


class RunnerKey(SQLModel, table=True):
    """계정당 1개의 '껄무새 회원 키'. 매크로 실행기(로컬 exe)가 이 불투명 토큰으로
    서버에 자신을 인증한다. API 키/시크릿은 절대 서버로 오지 않는다 — 오직 이 키와
    구동 상태만 오간다. 재발급하면 기존 키는 무효가 된다(unique).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, unique=True)  # one key per account
    key: str = Field(index=True, unique=True)  # 불투명 토큰 (예: "ggp_xxxxx")
    created_at: str


class RunnerLaunchTicket(SQLModel, table=True):
    """Short-lived, single-use handoff from the signed-in web app to the runner.

    Only a SHA-256 digest is persisted. The raw bearer ticket exists briefly in
    the create response/``ggparrot://`` URI and is never written to the database.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    user_macro_id: int = Field(index=True)
    token_hash: str = Field(index=True, unique=True)
    testnet: bool = True
    created_at: str
    expires_at: str
    expires_ms: int = Field(index=True, sa_type=BigInteger)
    claimed_at: str = ""


class RunSession(SQLModel, table=True):
    """사용자 PC의 매크로 실행기가 돌리는 '한 번의 실거동' 세션.

    실행기가 start 로 만들고, heartbeat 로 상태를 올리며, 마이페이지의 종료 버튼은
    ``stop_mode`` 플래그만 세운다. 실행기가 다음 heartbeat 에서 그 플래그를 보고
    종료(포지션 유지) 또는 청산 후 종료한 뒤 서버에 확정 보고한다.

    거래소 API 키/시크릿은 이 테이블에 저장되지 않는다(로컬에서만 사용).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    # Stable account-library identity. Nullable for sessions created by legacy
    # runners that only uploaded an anonymous macro JSON snapshot.
    user_macro_id: Optional[int] = Field(default=None, index=True)
    # 실행 대상 요약 (마이페이지 표시용)
    symbol: str = ""
    position_side: str = "long"
    leverage: int = 1
    market: str = ""  # spot | futures
    testnet: bool = True  # 실거래 여부: False = 메인넷(실제 자금)
    human_summary: str = ""
    # 실행 중인 매크로 원문(JSON 문자열). 마이페이지 실시간 차트에 전략 보조지표
    # (볼린저·이동평균·RSI 등)를 빌더와 동일하게 그리기 위해 실행기가 함께 올린다.
    # 예전 실행기가 만든 세션엔 비어 있을 수 있어 프런트는 없으면 평단선만 그린다.
    macro_json: str = ""
    # 수명주기: running -> stopped(정상) | error
    status: str = Field(default="running", index=True)
    # 마이페이지가 요청한 종료 방식: "" (계속) | "stop_only" | "close_and_stop"
    stop_mode: str = ""
    # 마지막 heartbeat 의 실시간 스냅샷
    in_position: bool = False
    last_price: float = 0.0
    entry_price: float = 0.0
    position_qty: float = 0.0
    realized_pnl: float = 0.0  # 누적 실현손익(USDT)
    unrealized_pct: float = 0.0  # 현재 포지션 평가손익(%)
    note: str = ""  # 예: "청산 실패 — 포지션 남음"
    started_at: str
    last_heartbeat_at: str = ""
    stopped_at: Optional[str] = None


class TickerNewsSnapshot(SQLModel, table=True):
    """One immutable, position-independent news analysis for an asset ticker.

    The central collector claims a unique snapshot before invoking AI. API
    requests only read completed rows and apply the caller's long/short mapping
    afterwards, so users never trigger collection or model work themselves.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_key: str = Field(index=True, unique=True)
    asset_symbol: str = Field(index=True)
    coin_name: str = ""
    query: str = ""
    news_json: str = ""
    analysis_json: str = ""
    item_count: int = 0
    processing_status: str = Field(default="pending", index=True)
    analysis_status: str = "pending"
    analysis_source: str = ""
    prompt_version: str = ""
    model: str = ""
    collected_at: str
    collected_ms: int = Field(sa_type=BigInteger, index=True)
    claimed_at: str
    claimed_ms: int = Field(sa_type=BigInteger, index=True)
    claim_token: str = ""
    last_observed_at: str = ""
    last_observed_ms: int = Field(default=0, sa_type=BigInteger)
    last_observation_seq: int = Field(default=0, sa_type=BigInteger)
    analysis_attempts: int = 0
    next_retry_ms: int = Field(default=0, sa_type=BigInteger)
    completed_at: str = ""
    completed_ms: int = Field(default=0, sa_type=BigInteger)


class TickerNewsState(SQLModel, table=True):
    """Mutable collection cursor and latest good snapshot for one asset."""

    asset_symbol: str = Field(primary_key=True)
    latest_snapshot_id: Optional[int] = Field(default=None, index=True)
    observation_seq: int = Field(default=0, sa_type=BigInteger)
    latest_observation_seq: int = Field(default=0, sa_type=BigInteger)
    latest_observed_ms: int = Field(default=0, sa_type=BigInteger)
    collection_status: str = Field(default="pending", index=True)
    last_error: str = ""
    consecutive_failures: int = 0
    last_attempt_at: str = ""
    last_attempt_ms: int = Field(default=0, sa_type=BigInteger)
    last_success_at: str = ""
    last_success_ms: int = Field(default=0, sa_type=BigInteger, index=True)
    updated_at: str = ""


class TickerNewsAiBudget(SQLModel, table=True):
    """Durable namespaced daily model-call budget shared by every instance."""

    budget_date_kst: str = Field(primary_key=True)
    used: int = 0
    updated_at: str = ""


class MarketNewsSummary(SQLModel, table=True):
    """KST 하루치 시장·규제 요약 하나.

    프로세스 메모리에만 두면 Render 재배포·재시작마다 다시 결제한다. 2026-09-04 에는
    배포 네 번으로 일일 예산(3회)이 바닥나 자정까지 요약이 비었다.
    """

    summary_key: str = Field(primary_key=True)
    overview: str = ""
    prompt_version: str = ""
    updated_at: str = ""
    updated_ms: int = Field(default=0, sa_type=BigInteger)


class NewsTitleTranslation(SQLModel, table=True):
    """One shared translation per normalized news title.

    The public endpoints translate only after source items have been deduplicated.
    Keeping that result in Postgres prevents a deploy or a second web instance from
    paying to translate the same headline again.
    """

    title_hash: str = Field(primary_key=True)
    original_title: str
    translated_title: str = ""
    processing_status: str = Field(default="ready", index=True)
    claim_token: str = ""
    claimed_ms: int = Field(default=0, sa_type=BigInteger, index=True)
    updated_at: str = ""
    updated_ms: int = Field(default=0, sa_type=BigInteger, index=True)


class DailyChallenge(SQLModel, table=True):
    """One day's AI challenge: the chosen symbol for a KST date (idempotency key)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    date_kst: str = Field(index=True, unique=True)  # YYYY-MM-DD (KST)
    symbol: str
    created_at: str
    status: str = "ready"
    claim_token: str = ""
    claimed_ms: int = Field(default=0, sa_type=BigInteger)
    last_error: str = ""


class LeaderboardCarryover(SQLModel, table=True):
    """Idempotency record: one row per KST date whose top-N carry-over already ran."""

    id: Optional[int] = Field(default=None, primary_key=True)
    date_kst: str = Field(index=True, unique=True)  # the day carried INTO (YYYY-MM-DD)
    carried: int = 0  # how many entries survived into that day
    created_at: str


class ChatMessage(SQLModel, table=True):
    """One leaderboard chat message (daily KST board; reference only)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    text: str
    created_at: str  # UTC ISO
    created_ms: int = Field(index=True, sa_type=BigInteger)  # epoch ms


class LeaderboardVote(SQLModel, table=True):
    """One user's like(+1)/dislike(-1) on a leaderboard entry (1 vote per user)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    entry_id: int = Field(index=True)
    user_id: str = Field(index=True)
    value: int  # +1 like | -1 dislike


class PaperTrade(SQLModel, table=True):
    """One simulated fill inside a paper session."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    ts: str
    side: str  # buy | sell | short | cover
    price: float
    qty: float
    return_at_trade: float


class WhaleHolderBalance(SQLModel, table=True):
    """Last observed on-chain balance of one top-holder wallet ('고래 동향').

    Diffed against the next observation to classify a wallet as buying/selling.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    coin: str = Field(index=True)  # PEPE | WETH | XRP
    wallet: str = Field(index=True)
    balance_raw: str
    updated_at: str


class WhaleObservation(SQLModel, table=True):
    """Latest whale-flow summary per coin (also marks when we last observed)."""

    coin: str = Field(primary_key=True)
    observed_at: str
    buys: int = 0
    sells: int = 0
    tracked: int = 0


class BoardPost(SQLModel, table=True):
    """껄무새 게시판 글. 로그인 계정만 작성. 이미지(jpg/png) 1장을 DB에 함께 저장.

    이미지를 DB 바이트로 두는 이유: Render 무료 디스크는 재배포마다 비워지므로
    파일시스템 저장은 유실된다. 별도 스토리지 설정 없이 durable(Postgres)에 담는다.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    author_user_id: int = Field(index=True)  # 작성자 계정 id
    author_name: str = ""  # 작성 시점의 표시 아이디(스냅샷)
    title: str
    body: str = ""
    image_mime: str = ""  # "" | image/jpeg | image/png
    image_data: Optional[bytes] = Field(default=None)  # 원본 바이트(없으면 None)
    created_at: str  # UTC ISO
    created_ms: int = Field(index=True, sa_type=BigInteger)


class BoardComment(SQLModel, table=True):
    """게시글 댓글. 리더보드 채팅처럼 계정 없이 '일회성 아이디+비밀번호'로 단다.

    비밀번호 해시는 본인 삭제 확인에만 쓰고, 절대 응답(view)에 담지 않는다.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    post_id: int = Field(index=True)
    username: str = ""
    password_hash: str = ""  # PBKDF2; 본인 삭제 확인용, 응답에 미포함
    text: str = ""
    created_at: str
    created_ms: int = Field(index=True, sa_type=BigInteger)


def _migrate() -> None:
    """Add columns introduced after a table was first created (SQLite create_all
    does not ALTER existing tables). Idempotent and safe to run every startup."""
    added = {
        "leaderboardentry": {
            "username": "ALTER TABLE leaderboardentry ADD COLUMN username TEXT DEFAULT ''",
            "password_hash": "ALTER TABLE leaderboardentry ADD COLUMN password_hash TEXT DEFAULT ''",
            "owner_user_id": "ALTER TABLE leaderboardentry ADD COLUMN owner_user_id INTEGER",
            "is_ai": "ALTER TABLE leaderboardentry ADD COLUMN is_ai INTEGER DEFAULT 0",
            "streak_days": "ALTER TABLE leaderboardentry ADD COLUMN streak_days INTEGER DEFAULT 1",
            "first_created_ms": "ALTER TABLE leaderboardentry ADD COLUMN first_created_ms INTEGER",
        },
        "papersession": {
            "liquidations": "ALTER TABLE papersession ADD COLUMN liquidations INTEGER DEFAULT 0",
            "liquidated_loss": "ALTER TABLE papersession ADD COLUMN liquidated_loss FLOAT DEFAULT 0",
        },
        "macrorow": {
            "rep_leverage": "ALTER TABLE macrorow ADD COLUMN rep_leverage INTEGER DEFAULT 1",
        },
        "runsession": {
            "macro_json": "ALTER TABLE runsession ADD COLUMN macro_json TEXT DEFAULT ''",
            "user_macro_id": "ALTER TABLE runsession ADD COLUMN user_macro_id INTEGER",
        },
        "tickernewssnapshot": {
            "claim_token": "ALTER TABLE tickernewssnapshot ADD COLUMN claim_token TEXT DEFAULT ''",
            "last_observed_at": "ALTER TABLE tickernewssnapshot ADD COLUMN last_observed_at TEXT DEFAULT ''",
            "last_observed_ms": "ALTER TABLE tickernewssnapshot ADD COLUMN last_observed_ms INTEGER DEFAULT 0",
            "last_observation_seq": "ALTER TABLE tickernewssnapshot ADD COLUMN last_observation_seq INTEGER DEFAULT 0",
            "analysis_attempts": "ALTER TABLE tickernewssnapshot ADD COLUMN analysis_attempts INTEGER DEFAULT 0",
            "next_retry_ms": "ALTER TABLE tickernewssnapshot ADD COLUMN next_retry_ms INTEGER DEFAULT 0",
        },
        "tickernewsstate": {
            "observation_seq": "ALTER TABLE tickernewsstate ADD COLUMN observation_seq INTEGER DEFAULT 0",
            "latest_observation_seq": "ALTER TABLE tickernewsstate ADD COLUMN latest_observation_seq INTEGER DEFAULT 0",
            "latest_observed_ms": "ALTER TABLE tickernewsstate ADD COLUMN latest_observed_ms INTEGER DEFAULT 0",
        },
        "newstitletranslation": {
            "processing_status": "ALTER TABLE newstitletranslation ADD COLUMN processing_status TEXT DEFAULT 'ready'",
            "claim_token": "ALTER TABLE newstitletranslation ADD COLUMN claim_token TEXT DEFAULT ''",
            "claimed_ms": "ALTER TABLE newstitletranslation ADD COLUMN claimed_ms INTEGER DEFAULT 0",
        },
        "dailychallenge": {
            "status": "ALTER TABLE dailychallenge ADD COLUMN status TEXT DEFAULT 'ready'",
            "claim_token": "ALTER TABLE dailychallenge ADD COLUMN claim_token TEXT DEFAULT ''",
            "claimed_ms": "ALTER TABLE dailychallenge ADD COLUMN claimed_ms INTEGER DEFAULT 0",
            "last_error": "ALTER TABLE dailychallenge ADD COLUMN last_error TEXT DEFAULT ''",
        },
    }
    with _engine.connect() as conn:
        for table, cols in added.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table not created yet; create_all made it with all columns
            for col, ddl in cols.items():
                if col not in existing:
                    conn.exec_driver_sql(ddl)
        # ALTER TABLE cannot add an indexed SQLModel field in SQLite. Keep the
        # lookup index explicit for upgraded databases as well as fresh ones.
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_runsession_user_macro_id "
            "ON runsession (user_macro_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_newstitletranslation_processing_status "
            "ON newstitletranslation (processing_status)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_newstitletranslation_claimed_ms "
            "ON newstitletranslation (claimed_ms)"
        )
        conn.commit()


def _migrate_pg() -> None:
    """Postgres: create_all does not ALTER existing tables either, so add columns
    introduced after a table first shipped. `ADD COLUMN IF NOT EXISTS` makes this
    idempotent and safe on every startup."""
    stmts = [
        "ALTER TABLE runsession ADD COLUMN IF NOT EXISTS macro_json TEXT DEFAULT ''",
        "ALTER TABLE runsession ADD COLUMN IF NOT EXISTS user_macro_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_runsession_user_macro_id ON runsession (user_macro_id)",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS claim_token TEXT DEFAULT ''",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS last_observed_at TEXT DEFAULT ''",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS last_observed_ms BIGINT DEFAULT 0",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS last_observation_seq BIGINT DEFAULT 0",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS analysis_attempts INTEGER DEFAULT 0",
        "ALTER TABLE tickernewssnapshot ADD COLUMN IF NOT EXISTS next_retry_ms BIGINT DEFAULT 0",
        "ALTER TABLE tickernewsstate ADD COLUMN IF NOT EXISTS observation_seq BIGINT DEFAULT 0",
        "ALTER TABLE tickernewsstate ADD COLUMN IF NOT EXISTS latest_observation_seq BIGINT DEFAULT 0",
        "ALTER TABLE tickernewsstate ADD COLUMN IF NOT EXISTS latest_observed_ms BIGINT DEFAULT 0",
        "ALTER TABLE leaderboardentry ADD COLUMN IF NOT EXISTS streak_days INTEGER DEFAULT 1",
        "ALTER TABLE leaderboardentry ADD COLUMN IF NOT EXISTS first_created_ms BIGINT",
        "ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS processing_status TEXT DEFAULT 'ready'",
        "ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS claim_token TEXT DEFAULT ''",
        "ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS claimed_ms BIGINT DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS ix_newstitletranslation_processing_status ON newstitletranslation (processing_status)",
        "CREATE INDEX IF NOT EXISTS ix_newstitletranslation_claimed_ms ON newstitletranslation (claimed_ms)",
        "ALTER TABLE dailychallenge ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ready'",
        "ALTER TABLE dailychallenge ADD COLUMN IF NOT EXISTS claim_token TEXT DEFAULT ''",
        "ALTER TABLE dailychallenge ADD COLUMN IF NOT EXISTS claimed_ms BIGINT DEFAULT 0",
        "ALTER TABLE dailychallenge ADD COLUMN IF NOT EXISTS last_error TEXT DEFAULT ''",
        "ALTER TABLE newstitletranslation ENABLE ROW LEVEL SECURITY",
        "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM PUBLIC",
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN "
            "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM anon; "
            "END IF; "
            "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN "
            "REVOKE ALL PRIVILEGES ON TABLE newstitletranslation FROM authenticated; "
            "END IF; "
            "END $$"
        ),
    ]
    bigint_columns = {
        "tickernewssnapshot": (
            "collected_ms",
            "claimed_ms",
            "last_observed_ms",
            "last_observation_seq",
            "next_retry_ms",
            "completed_ms",
        ),
        "tickernewsstate": (
            "observation_seq",
            "latest_observation_seq",
            "latest_observed_ms",
            "last_attempt_ms",
            "last_success_ms",
        ),
    }
    with _engine.connect() as conn:
        for ddl in stmts:
            conn.exec_driver_sql(ddl)
        for table, columns in bigint_columns.items():
            for column in columns:
                row = conn.exec_driver_sql(
                    "SELECT data_type FROM information_schema.columns "
                    f"WHERE table_schema = current_schema() AND table_name = '{table}' "
                    f"AND column_name = '{column}'"
                ).first()
                if row is not None and row[0] != "bigint":
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ALTER COLUMN {column} "
                        f"TYPE BIGINT USING {column}::bigint"
                    )
        conn.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)
    # create_all never ALTERs a pre-existing table, so patch late-added columns on
    # both backends: SQLite via PRAGMA checks, Postgres via ADD COLUMN IF NOT EXISTS.
    if _is_sqlite():
        _migrate()
    else:
        _migrate_pg()


def get_session() -> Session:
    return TracedSession(_engine)


def request_session() -> Iterator[Session]:
    """FastAPI dependency: one SQLAlchemy Session shared within one request."""
    with get_session() as session:
        yield session


def database_dialect() -> str:
    """Expose the configured store type without leaking the engine itself."""
    return str(_engine.dialect.name)
