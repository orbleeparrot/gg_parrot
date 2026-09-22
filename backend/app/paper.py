"""Paper (simulated) trading manager.

Drives the SAME execution machine as the backtest (``engine.stepper``), but fed
by live ticks instead of historical candles. NO real orders, NO account, NO API
keys — only public price data is read. Every session is flagged simulated.

Assumptions / choices (see README):
  * Real-time source: REST polling of the public ticker (simplest, robust),
    interval from ``PAPER_POLL_SECONDS`` (default 3s).
  * ``demo_replay`` mode fast-forwards recent 1m candles so trades reliably
    stream during a talk even if the live market is flat / offline (synthetic
    intraday fallback when candles are unavailable).
  * Single-process assumption: running sessions live in memory + SQLite. Fine
    for a demo; a multi-worker deploy would need a shared store.
  * Multi-symbol (portfolio) macros mirror the backtest: the SAME rule runs on
    every symbol as an independent leg with the capital split evenly, and the
    session's equity/return is the sum over legs. Fills are tagged with the
    leg's symbol so one log shows every coin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlmodel import Session, select

from .data import ensure_spot_available, get_klines, get_ticker_price_cached
from .db import PaperSession, PaperTrade, get_session
from .engine import Macro, RuleType
from .engine.candle_feed import feed  # 모듈 이름으로 참조 — 테스트가 monkeypatch 한다
from .engine.driver import Leg, StrategyDriver, sim_state  # noqa: F401 (sim_state 재export)
from .engine.stepper import make_sim

# 시뮬레이션 코어(레그·틱·체결 요약·상태)는 engine.driver 로 옮겼다. 여기서는 DB·체크포인트·
# 리더보드 관심사만 남기고, 옛 이름들은 테스트 호환용 별칭·래퍼로 유지한다.
_Leg = Leg

POLL_SECONDS = float(os.environ.get("PAPER_POLL_SECONDS", "3"))
WARMUP_CANDLES = 500  # MA slow_period ≤ 400 을 덮는다
REPLAY_SECONDS = float(os.environ.get("PAPER_REPLAY_SECONDS", "0.4"))
REPLAY_HOURS = int(os.environ.get("PAPER_REPLAY_HOURS", "6"))
CHECKPOINT_SECONDS = max(1.0, float(os.environ.get("PAPER_CHECKPOINT_SECONDS", "10")))
_RECENT_CAP = 200
log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _synthetic_intraday(symbol: str, n: int = 360) -> List[float]:
    """Deterministic intraday walk with ~1-2% swings (offline replay fallback)."""
    seed = sum(ord(ch) for ch in symbol.upper())
    base = 100.0 + (seed % 500)
    out: List[float] = []
    price = base
    for i in range(n):
        wave = math.sin((i + seed) / 7.0) * 0.010 + math.sin((i + seed) / 2.3) * 0.006
        price *= 1.0 + wave
        out.append(round(price, 4))
    return out


class _Runner:
    def __init__(
        self,
        session_id: int,
        sim,
        symbol: str,
        mode: str,
        initial: float,
        *,
        legs: Optional[List[Leg]] = None,
    ):
        self.session_id = session_id
        # Single-symbol callers pass (sim, symbol, initial); a portfolio passes
        # its legs and `initial` is the total. The first leg is the primary
        # symbol so every existing single-symbol accessor keeps working.
        # 시뮬레이션 코어는 드라이버가 들고, 러너는 세션 수명(루프·체크포인트·종료)만 챙긴다.
        self.driver = StrategyDriver(legs if legs else [Leg(symbol, sim, initial)], initial)
        self.mode = mode
        self.initial = initial
        self.stop_flag = False
        self.subs: list = []  # 마감봉 피드 구독 — 캔들형만 채워진다(_attach_feed/_detach_feed)
        self.task: Optional[asyncio.Task] = None
        self.status = "running"
        self.recent: List[dict] = []
        self.last_checkpoint_monotonic = time.monotonic()
        self.finalize_lock: Optional[asyncio.Lock] = None
        self.finalized = False
        self.inflight_persist: Optional[asyncio.Task] = None

    # 드라이버 위임 — 테스트·상태 뷰가 옛 이름으로 읽고, 일부는 대입도 한다.
    legs = property(lambda self: self.driver.legs)
    sim = property(lambda self: self.driver.sim)
    symbol = property(lambda self: self.driver.symbol)
    symbols = property(lambda self: self.driver.symbols)
    last_price = property(lambda self: self.driver.last_price)
    equity = property(lambda self: self.driver.equity)
    ret = property(lambda self: self.driver.ret)
    liquidations = property(lambda self: self.driver.liquidations)
    liquidated_loss = property(lambda self: self.driver.liquidated_loss)
    trade_count = property(lambda self: self.driver.trade_count, lambda self, v: setattr(self.driver, "trade_count", v))
    last_fill = property(lambda self: self.driver.last_fill, lambda self, v: setattr(self.driver, "last_fill", v))
    entry_returns = property(lambda self: self.driver.entry_returns, lambda self, v: setattr(self.driver, "entry_returns", v))

    @property
    def replay_prices(self) -> List[float]:
        return self.legs[0].replay_prices

    @replay_prices.setter
    def replay_prices(self, prices: List[float]) -> None:
        self.legs[0].replay_prices = prices

    def is_portfolio(self) -> bool:
        return self.driver.is_portfolio()

    def leg_for(self, symbol: Optional[str]) -> Leg:
        return self.driver.leg_for(symbol)


_running: Dict[int, _Runner] = {}


def _session_initial(macro: Macro) -> float:
    if macro.rule_type is RuleType.C:
        return 1_000_000.0
    return float(macro.initial_capital or 1_000_000.0)


# --- 마감봉 피드 ----------------------------------------------------------
async def _attach_feed(runner: _Runner) -> None:
    """캔들형 세션: 과거 마감봉으로 웜업하고 새 마감봉을 구독한다. 틱형은 아무것도 하지 않는다."""
    keys = runner.driver.candle_keys()
    if not keys:
        return
    history: Dict[str, list] = {}
    for symbol, interval, market in keys:
        try:
            history[symbol] = await feed.history(symbol, interval, market, WARMUP_CANDLES)
        except Exception:
            log.exception("paper %s: warmup history failed for %s — starting cold", runner.session_id, symbol)
    runner.driver.warmup(history)

    async def on_candle(symbol: str, candle) -> None:
        if runner.stop_flag:
            return
        runner.driver.push_candle(symbol, candle)

    subs = []
    for symbol, interval, market in keys:
        hist = history.get(symbol)
        since_t = hist[-1][0] if hist else None  # Candle.t — 인덱스로 읽어 테스트 더미(tuple)도 받는다
        subs.append(feed.subscribe(symbol, interval, market, on_candle, since_t=since_t))
    runner.subs = subs


def _detach_feed(runner: _Runner) -> None:
    for sub in getattr(runner, "subs", []):
        try:
            feed.unsubscribe(sub)
        except Exception:
            log.exception("paper %s: unsubscribe failed", runner.session_id)
    runner.subs = []


async def _attach_feed_for_resume(runner: _Runner, info: dict) -> None:
    """복구는 웜업 → 복구 순서다: 웜업이 장부를 비우므로 체크포인트 상태를 그 뒤에 얹는다."""
    await _attach_feed(runner)
    if runner.driver.candle_keys():
        runner.driver.restore(
            info["state"],
            leg_equity={leg.get("symbol"): float(leg.get("current_equity") or 0.0) for leg in info["legs"]},
            total_equity=float(info["current_equity"] or 0.0),
        )


# --- lifecycle ----------------------------------------------------------
async def start_session(macro: Macro, symbol: Optional[str], mode: str) -> dict:
    # A portfolio macro runs every symbol (like the backtest); a single-symbol
    # macro may be pointed at an explicit symbol by the caller.
    if macro.is_portfolio():
        symbols = macro.all_symbols()
    else:
        symbols = [(symbol or macro.symbol).upper()]
    # Refuse futures-only / delisted symbols: no real spot data -> no paper
    # session (raises NoSpotDataError -> 422 at the endpoint). Never run on a
    # synthetic fallback here.
    for sym in symbols:
        await asyncio.to_thread(ensure_spot_available, sym)
    initial = _session_initial(macro)
    per_leg = initial / len(symbols)
    legs: List[Leg] = []
    for sym in symbols:
        leg_macro = macro.for_symbol(sym, per_leg) if len(symbols) > 1 else macro
        legs.append(Leg(sym, make_sim(leg_macro, initial_capital=per_leg), per_leg))

    session_id = await asyncio.to_thread(_create_session, macro, symbols[0], mode, initial)

    runner = _Runner(session_id, legs[0].sim, symbols[0], mode, initial, legs=legs)
    runner.driver.macro = macro  # 캔들 피드 구독 키(종목·간격·시장)를 드라이버가 매크로에서 읽는다
    if mode == "replay":
        for leg in runner.legs:
            leg.replay_prices = await asyncio.to_thread(_load_replay_prices, leg.symbol)
    if mode == "live":
        await _attach_feed(runner)

    _running[session_id] = runner
    _spawn_loop(runner)

    return {
        "session_id": session_id,
        "symbol": symbols[0],
        "symbols": symbols,
        "mode": mode,
        "virtual_balance": initial,
        "status": "running",
    }


def _spawn_loop(runner: _Runner) -> None:
    runner.task = asyncio.create_task(_run_loop(runner))


# --- 재기동 복구 ----------------------------------------------------------
# Render 재배포는 프로세스를 강제로 끊어 종료 훅(shutdown_running_sessions)이 못 돌 수 있다.
# 그러면 DB 에는 running 인데 루프는 없는 세션이 남고, 리더보드 수익률은 마지막 체크포인트에
# 박제된다(2026-09-21 프로덕션 8행 전부). 기동 시 running 세션을 되살려 자산·포지션·체결 요약을
# 이어 간다. 리플레이 세션은 되살릴 가격 창이 없으니 닫는다.
def _load_resumable() -> tuple[list[dict], list[int]]:
    """running 세션을 (되살릴 것, 닫을 것) 으로 나눠 필요한 값만 dict 로 꺼낸다 — 워커 스레드용."""
    revive: list[dict] = []
    close: list[int] = []
    with get_session() as db:
        rows = db.exec(select(PaperSession).where(PaperSession.status == "running")).all()
        for row in rows:
            if row.mode != "live" or not row.macro_json:
                close.append(int(row.id))
                continue
            trades = db.exec(
                select(PaperTrade).where(PaperTrade.session_id == row.id).order_by(PaperTrade.id.asc())
            ).all()
            revive.append({
                "id": int(row.id),
                "symbol": row.symbol,
                "macro_json": row.macro_json,
                "virtual_balance": float(row.virtual_balance or 0.0),
                "current_equity": float(row.current_equity or 0.0),
                "legs": _stored_legs(row),
                "state": parse_state(getattr(row, "state_json", "")),
                "trades": [
                    {"symbol": t.symbol, "side": t.side, "return_at_trade": float(t.return_at_trade), "ts": t.ts}
                    for t in trades
                ],
            })
        for sid in close:
            row = db.get(PaperSession, sid)
            if row is not None and row.status == "running":
                row.status = "stopped"
                row.stopped_at = _now_iso()
                db.add(row)
        if close:
            db.commit()
    return revive, close


def _rebuild_runner(info: dict) -> _Runner:
    """한 세션의 러너를 DB 값으로 다시 만든다(루프는 띄우지 않는다)."""
    macro = Macro.model_validate_json(info["macro_json"])
    symbols = macro.all_symbols() if macro.is_portfolio() else [str(info["symbol"] or macro.symbol).upper()]
    initial = info["virtual_balance"] or _session_initial(macro)
    per_leg = initial / len(symbols)
    leg_equity = {leg.get("symbol"): float(leg.get("current_equity") or 0.0) for leg in info["legs"]}
    legs: List[Leg] = []
    for sym in symbols:
        leg_macro = macro.for_symbol(sym, per_leg) if len(symbols) > 1 else macro
        legs.append(Leg(sym, make_sim(leg_macro, initial_capital=per_leg), per_leg))
    runner = _Runner(info["id"], legs[0].sim, symbols[0], "live", initial, legs=legs)
    runner.driver.macro = macro
    # 자산·포지션은 체크포인트 상태로, 체결 요약은 DB 체결 행으로 되살린다.
    runner.driver.restore(info["state"], leg_equity=leg_equity, total_equity=float(info["current_equity"] or 0.0))
    runner.driver.restore_fills(info["trades"])
    return runner


async def resume_running_sessions() -> int:
    """기동 시 running 세션을 되살린다. 되살린 수를 돌려준다; 실패한 세션은 건너뛰고 로그만 남긴다."""
    revive, closed = await asyncio.to_thread(_load_resumable)
    if closed:
        log.info("paper resume: closed %d non-resumable running session(s)", len(closed))
    count = 0
    for info in revive:
        if info["id"] in _running:
            continue
        try:
            runner = _rebuild_runner(info)
        except Exception:
            log.exception("paper resume: session %s could not be rebuilt; leaving it as is", info["id"])
            continue
        await _attach_feed_for_resume(runner, info)
        _running[info["id"]] = runner
        _spawn_loop(runner)
        count += 1
    if count:
        log.info("paper resume: revived %d session(s)", count)
    return count


def _create_session(macro: Macro, symbol: str, mode: str, initial: float) -> int:
    """Create the durable row in a worker thread, never on the event loop."""
    with get_session() as db:
        row = PaperSession(
            macro_id=macro.macro_id or "adhoc",
            symbol=symbol,
            mode=mode,
            status="running",
            started_at=_now_iso(),
            virtual_balance=initial,
            current_equity=initial,
            current_return=0.0,
            macro_json=macro.model_dump_json(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)


def _load_replay_prices(symbol: str) -> List[float]:
    end = _now_ms()
    start = end - REPLAY_HOURS * 3600 * 1000
    try:
        df, _ = get_klines(symbol, start, end, interval="1m")
        prices = [float(x) for x in df["close"].tolist()]
    except Exception:
        prices = []
    if len(prices) < 30:
        prices = _synthetic_intraday(symbol)
    return prices


async def _run_loop(runner: _Runner) -> None:
    try:
        if runner.mode == "replay":
            # Replay of recent 1m candles: advance a virtual clock across
            # REPLAY_HOURS so time-based rules (max holding / cooldown / daily
            # loss) actually fire during the fast-forward instead of being
            # pinned to (near-constant) wall-clock time.
            n = max(len(leg.replay_prices) for leg in runner.legs)
            base = datetime.now(timezone.utc) - timedelta(hours=REPLAY_HOURS)
            per = (REPLAY_HOURS * 3600.0) / max(1, n)
            for i in range(n):
                if runner.stop_flag:
                    break
                ts = base + timedelta(seconds=i * per)
                prices = [
                    leg.replay_prices[i] if i < len(leg.replay_prices) else None
                    for leg in runner.legs
                ]
                await _tick_and_checkpoint(runner, prices, ts)
                await asyncio.sleep(REPLAY_SECONDS)
            await _finalize_async(runner)  # replay exhausted -> auto stop
        else:
            while not runner.stop_flag:
                # Cached per-symbol: concurrent sessions on the same coin share one fetch.
                prices = await asyncio.gather(
                    *(asyncio.to_thread(get_ticker_price_cached, leg.symbol) for leg in runner.legs)
                )
                await _tick_and_checkpoint(runner, list(prices), datetime.now(timezone.utc))
                await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        # A transient price/DB failure must not leave a durable session marked
        # running forever after its loop has already died.
        await asyncio.shield(_finalize_async(runner))


# --- 드라이버 위임 래퍼(테스트 호환) ------------------------------------------
def _tick(
    runner: _Runner,
    price: float,
    ts: Optional[datetime] = None,
    symbol: Optional[str] = None,
):
    """한 레그를 한 틱 진행하고 체결이 있으면 돌려준다 — 체결 요약(_note_fill)까지 드라이버가 센다."""
    return runner.driver.tick(price, ts, symbol)


def _aggregate(runner: _Runner) -> None:
    runner.driver.aggregate()


def _note_fill(runner: _Runner, fill, symbol: str) -> None:
    """체결 하나를 상태 요약에 반영 — 횟수·마지막 체결·익절/손절 구분."""
    runner.driver.note_fill(fill, symbol)


def _state_view(runner: _Runner) -> dict:
    """리더보드 행 상태(포지션·체결 요약) — 체크포인트마다 state_json 으로 저장된다."""
    return runner.driver.state()


async def _tick_and_checkpoint(
    runner: _Runner, prices: List[Optional[float]], ts: datetime
) -> None:
    """Step every leg that has a price this round, then persist.

    Fills are durable immediately (one write per fill, tagged with its leg's
    symbol); a round without fills only refreshes the coalesced snapshot.
    """
    fills: List[tuple] = []
    ticked = False
    for leg, price in zip(runner.legs, prices):
        if price is None:
            continue
        ticked = True
        fill = _tick(runner, price, ts, symbol=leg.symbol)  # 체결 요약은 driver.tick 이 이미 셌다
        if fill is not None:
            fills.append((leg.symbol, fill))
    if fills:
        for symbol, fill in fills:
            await _checkpoint(runner, fill=fill, symbol=symbol)
    elif ticked:
        await _checkpoint(runner)


def parse_state(text) -> dict:
    """state_json 문자열 → dict. 비었거나 깨졌으면 {} (구 행·마이그레이션 전 행)."""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot(runner: _Runner) -> dict:
    return {
        "session_id": runner.session_id,
        "equity": round(runner.equity, 4),
        "return": round(runner.ret, 4),
        "liquidations": runner.liquidations,
        "liquidated_loss": round(runner.liquidated_loss, 4),
        # Per-symbol breakdown; persisted so a stopped portfolio session still
        # shows what each coin did.
        "legs": [leg.view() for leg in runner.legs] if runner.is_portfolio() else [],
        "state": _state_view(runner),
    }


def _fill_payload(fill, symbol: str = "") -> Optional[dict]:
    if fill is None:
        return None
    return {
        "ts": _now_iso(),
        "symbol": symbol,
        "side": fill.side,
        "price": round(fill.price, 4),
        "qty": round(fill.qty, 8),
        "return_at_trade": round(fill.return_pct, 4),
    }


def _persist_checkpoint(snapshot: dict, fill: Optional[dict]) -> Optional[dict]:
    """Persist one coalesced state snapshot and optional fill in one transaction."""
    with get_session() as db:
        row = db.get(PaperSession, snapshot["session_id"])
        if row is None or row.status != "running":
            return None
        row.current_equity = snapshot["equity"]
        row.current_return = snapshot["return"]
        row.liquidations = snapshot["liquidations"]
        row.liquidated_loss = snapshot["liquidated_loss"]
        if snapshot.get("legs"):
            row.legs_json = json.dumps(snapshot["legs"])
        row.state_json = json.dumps(snapshot.get("state") or {})
        db.add(row)
        if fill:
            trade = PaperTrade(
                session_id=snapshot["session_id"],
                **fill,
            )
            db.add(trade)
        db.commit()
        if fill:
            db.refresh(trade)
            return _trade_view(trade)
    return None


def _trade_view(t: PaperTrade) -> dict:
    return {
        "id": t.id,
        "ts": t.ts,
        "symbol": getattr(t, "symbol", "") or "",
        "side": t.side,
        "price": t.price,
        "qty": t.qty,
        "return_at_trade": t.return_at_trade,
    }


async def _checkpoint(
    runner: _Runner,
    *,
    fill=None,
    symbol: Optional[str] = None,
    force: bool = False,
    now: Optional[float] = None,
) -> None:
    """Write at a bounded cadence; fills are durable immediately."""
    current = time.monotonic() if now is None else now
    if not force and fill is None:
        if current - runner.last_checkpoint_monotonic < CHECKPOINT_SECONDS:
            return

    write_task = asyncio.create_task(
        asyncio.to_thread(
            _persist_checkpoint,
            _snapshot(runner),
            _fill_payload(fill, symbol or runner.symbol),
        )
    )
    runner.inflight_persist = write_task
    try:
        # Cancelling the paper loop must not abandon a DB thread mid-commit.
        trade = await asyncio.shield(write_task)
    finally:
        if write_task.done() and runner.inflight_persist is write_task:
            runner.inflight_persist = None
    runner.last_checkpoint_monotonic = current
    if trade:
        runner.recent.insert(0, trade)
        del runner.recent[_RECENT_CAP:]


def _persist_finalize(snapshot: dict) -> None:
    with get_session() as db:
        row = db.get(PaperSession, snapshot["session_id"])
        if row:
            row.status = "stopped"
            row.stopped_at = _now_iso()
            row.current_equity = snapshot["equity"]
            row.current_return = snapshot["return"]
            row.liquidations = snapshot["liquidations"]
            row.liquidated_loss = snapshot["liquidated_loss"]
            if snapshot.get("legs"):
                row.legs_json = json.dumps(snapshot["legs"])
            row.state_json = json.dumps(snapshot.get("state") or {})
            db.add(row)
            db.commit()


async def _finalize_async(runner: _Runner) -> None:
    if runner.finalize_lock is None:
        runner.finalize_lock = asyncio.Lock()
    async with runner.finalize_lock:
        if runner.finalized:
            return
        _detach_feed(runner)
        pending = runner.inflight_persist
        if pending is not None:
            try:
                await asyncio.shield(pending)
            except Exception:
                # A failed checkpoint must not prevent the terminal status from
                # being persisted by the independent finalize transaction.
                pass
            finally:
                if pending.done() and runner.inflight_persist is pending:
                    runner.inflight_persist = None
        runner.status = "stopped"
        await asyncio.to_thread(_persist_finalize, _snapshot(runner))
        runner.finalized = True


def _stop_persisted_session(session_id: int) -> bool:
    with get_session() as db:
        row = db.get(PaperSession, session_id)
        if not row:
            return False
        if row.status == "running":
            row.status = "stopped"
            row.stopped_at = _now_iso()
            db.add(row)
            db.commit()
    return True


async def stop_session(session_id: int) -> dict:
    runner = _running.pop(session_id, None)
    if runner:
        runner.stop_flag = True
        if runner.task:
            runner.task.cancel()
            try:
                await runner.task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The terminal write below is independent and still needs to
                # run when the polling/checkpoint task failed first.
                pass
        await _finalize_async(runner)
        return {"session_id": session_id, "status": "stopped"}

    found = await asyncio.to_thread(_stop_persisted_session, session_id)
    if not found:
        return {"error": "not found"}
    return {"session_id": session_id, "status": "stopped"}


async def shutdown_running_sessions() -> None:
    """Quiesce every in-process paper loop and durably finalize its session.

    Unlike the interactive stop endpoint, process shutdown does not cancel the
    loop: an ``asyncio.to_thread`` price/checkpoint operation cannot itself be
    interrupted. Setting the flag and awaiting the task drains that in-flight
    work before shared HTTP and AI runtimes are closed by the app lifespan.
    """
    runners = list(_running.values())
    if not runners:
        return

    for runner in runners:
        runner.stop_flag = True
        _detach_feed(runner)

    tasks = [
        runner.task
        for runner in runners
        if runner.task is not None and runner.task is not asyncio.current_task()
    ]
    if tasks:
        # A loop failure still needs the independent terminal write below.
        await asyncio.gather(*tasks, return_exceptions=True)

    finalize_errors = []
    for runner in runners:
        try:
            await _finalize_async(runner)
        except Exception as error:
            finalize_errors.append(error)
        finally:
            if _running.get(runner.session_id) is runner:
                _running.pop(runner.session_id, None)

    if finalize_errors:
        raise RuntimeError(
            f"failed to finalize {len(finalize_errors)} paper session(s) during shutdown"
        ) from finalize_errors[0]


def get_status(session_id: int) -> Optional[dict]:
    runner = _running.get(session_id)
    if runner:
        return {
            "session_id": session_id,
            "symbol": runner.symbol,
            "symbols": runner.symbols,
            "legs": [leg.view() for leg in runner.legs] if runner.is_portfolio() else [],
            "mode": runner.mode,
            "status": runner.status,
            "virtual_balance": round(runner.initial, 2),
            "current_equity": round(runner.equity, 2),
            "current_return": round(runner.ret, 4),
            "last_price": round(runner.last_price, 4),
            "liquidations": runner.liquidations,
            "liquidated_loss": round(runner.liquidated_loss, 2),
            "trades": runner.recent[:30],
            "state": _state_view(runner),
        }

    with get_session() as db:
        row = db.get(PaperSession, session_id)
        if not row:
            return None
        trades = db.exec(
            select(PaperTrade)
            .where(PaperTrade.session_id == session_id)
            .order_by(PaperTrade.id.desc())
            .limit(30)
        ).all()
    legs = _stored_legs(row)
    return {
        "session_id": session_id,
        "symbol": row.symbol,
        "symbols": [leg["symbol"] for leg in legs] or [row.symbol],
        "legs": legs,
        "mode": row.mode,
        "status": row.status,
        "virtual_balance": round(row.virtual_balance, 2),
        "current_equity": round(row.current_equity, 2),
        "current_return": round(row.current_return, 4),
        "last_price": 0.0,
        "liquidations": getattr(row, "liquidations", 0) or 0,
        "liquidated_loss": round(getattr(row, "liquidated_loss", 0.0) or 0.0, 2),
        "trades": [_trade_view(t) for t in trades],
        "state": parse_state(getattr(row, "state_json", "")),
    }


def _stored_legs(row: PaperSession) -> List[dict]:
    raw = getattr(row, "legs_json", "") or ""
    if not raw:
        return []
    try:
        legs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return legs if isinstance(legs, list) else []


def get_statuses(session_ids: List[int], *, db: Optional[Session] = None) -> Dict[int, dict]:
    """Return lightweight statuses with at most one DB query for cache misses.

    Live in-process runners stay memory-only. Stopped or other-process sessions
    are loaded in one ``IN`` query instead of the leaderboard opening one
    connection and issuing one query per row.
    """
    ids = list(dict.fromkeys(int(value) for value in session_ids if value is not None))
    if not ids:
        return {}

    statuses: Dict[int, dict] = {}
    missing: List[int] = []
    for session_id in ids:
        runner = _running.get(session_id)
        if runner is None:
            missing.append(session_id)
            continue
        statuses[session_id] = {
            "session_id": session_id,
            "symbol": runner.symbol,
            "mode": runner.mode,
            "status": runner.status,
            "virtual_balance": round(runner.initial, 2),
            "current_equity": round(runner.equity, 2),
            "current_return": round(runner.ret, 4),
            "last_price": round(runner.last_price, 4),
            "liquidations": runner.liquidations,
            "liquidated_loss": round(runner.liquidated_loss, 2),
        }

    if not missing:
        return statuses

    if db is None:
        with get_session() as owned:
            rows = owned.exec(select(PaperSession).where(PaperSession.id.in_(missing))).all()
    else:
        rows = db.exec(select(PaperSession).where(PaperSession.id.in_(missing))).all()
    for row in rows:
        statuses[row.id] = {
            "session_id": row.id,
            "symbol": row.symbol,
            "mode": row.mode,
            "status": row.status,
            "virtual_balance": round(row.virtual_balance, 2),
            "current_equity": round(row.current_equity, 2),
            "current_return": round(row.current_return, 4),
            "last_price": 0.0,
            "liquidations": getattr(row, "liquidations", 0) or 0,
            "liquidated_loss": round(getattr(row, "liquidated_loss", 0.0) or 0.0, 2),
        }
    return statuses


def get_trades(session_id: int) -> List[dict]:
    with get_session() as db:
        trades = db.exec(
            select(PaperTrade)
            .where(PaperTrade.session_id == session_id)
            .order_by(PaperTrade.id.desc())
        ).all()
    return [_trade_view(t) for t in trades]
