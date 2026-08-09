"""SQLite persistence for macros (SQLModel)."""
from __future__ import annotations

import os
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine

# Engine selection: DATABASE_URL (Supabase/Postgres) in prod, local SQLite otherwise.
# This lets us develop & test on SQLite and run durable Postgres in deployment
# without any code change — only the env var differs. Accounts, points and the
# point ledger MUST live on the durable store (Render's free disk is ephemeral).
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def _sqlite_engine():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.db")
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
            return create_engine(url, echo=False, pool_pre_ping=True)
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
    created_ms: int = Field(index=True)  # epoch ms, for the KST day-window filter


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
    created_ms: int = Field(index=True)


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
    expires_ms: int = Field(index=True)
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
    # 실행 대상 요약 (마이페이지 표시용)
    symbol: str = ""
    position_side: str = "long"
    leverage: int = 1
    market: str = ""  # spot | futures
    testnet: bool = True  # 실거래 여부: False = 메인넷(실제 자금)
    human_summary: str = ""
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


class DailyChallenge(SQLModel, table=True):
    """One day's AI challenge: the chosen symbol for a KST date (idempotency key)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    date_kst: str = Field(index=True, unique=True)  # YYYY-MM-DD (KST)
    symbol: str
    created_at: str


class ChatMessage(SQLModel, table=True):
    """One leaderboard chat message (daily KST board; reference only)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    text: str
    created_at: str  # UTC ISO
    created_ms: int = Field(index=True)  # epoch ms, for the KST day-window filter


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
    created_ms: int = Field(index=True)


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
    created_ms: int = Field(index=True)


def _migrate() -> None:
    """Add columns introduced after a table was first created (SQLite create_all
    does not ALTER existing tables). Idempotent and safe to run every startup."""
    added = {
        "leaderboardentry": {
            "username": "ALTER TABLE leaderboardentry ADD COLUMN username TEXT DEFAULT ''",
            "password_hash": "ALTER TABLE leaderboardentry ADD COLUMN password_hash TEXT DEFAULT ''",
            "owner_user_id": "ALTER TABLE leaderboardentry ADD COLUMN owner_user_id INTEGER",
            "is_ai": "ALTER TABLE leaderboardentry ADD COLUMN is_ai INTEGER DEFAULT 0",
        },
        "papersession": {
            "liquidations": "ALTER TABLE papersession ADD COLUMN liquidations INTEGER DEFAULT 0",
            "liquidated_loss": "ALTER TABLE papersession ADD COLUMN liquidated_loss FLOAT DEFAULT 0",
        },
        "macrorow": {
            "rep_leverage": "ALTER TABLE macrorow ADD COLUMN rep_leverage INTEGER DEFAULT 1",
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
        conn.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)
    # _migrate uses SQLite PRAGMA and only patches pre-existing SQLite tables.
    # On a fresh Postgres, create_all already builds every table with all columns.
    if _is_sqlite():
        _migrate()


def get_session() -> Session:
    return Session(_engine)
