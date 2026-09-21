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
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlmodel import Session, select

from .data import ensure_spot_available, get_klines, get_ticker_price_cached
from .db import PaperSession, PaperTrade, get_session
from .engine import Macro, RuleType
from .engine.stepper import make_sim

POLL_SECONDS = float(os.environ.get("PAPER_POLL_SECONDS", "3"))
REPLAY_SECONDS = float(os.environ.get("PAPER_REPLAY_SECONDS", "0.4"))
REPLAY_HOURS = int(os.environ.get("PAPER_REPLAY_HOURS", "6"))
CHECKPOINT_SECONDS = max(1.0, float(os.environ.get("PAPER_CHECKPOINT_SECONDS", "10")))
_RECENT_CAP = 200


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


class _Leg:
    """One symbol of a session: its own sim and its share of the capital."""

    __slots__ = (
        "symbol", "sim", "initial", "last_price", "equity", "ret",
        "liquidations", "liquidated_loss", "replay_prices",
    )

    def __init__(self, symbol: str, sim, initial: float):
        self.symbol = symbol
        self.sim = sim
        self.initial = initial
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.replay_prices: List[float] = []

    def view(self) -> dict:
        return {
            "symbol": self.symbol,
            "virtual_balance": round(self.initial, 2),
            "current_equity": round(self.equity, 2),
            "current_return": round(self.ret, 4),
            "last_price": round(self.last_price, 4),
            "liquidations": self.liquidations,
        }


class _Runner:
    def __init__(
        self,
        session_id: int,
        sim,
        symbol: str,
        mode: str,
        initial: float,
        *,
        legs: Optional[List[_Leg]] = None,
    ):
        self.session_id = session_id
        # Single-symbol callers pass (sim, symbol, initial); a portfolio passes
        # its legs and `initial` is the total. The first leg is the primary
        # symbol so every existing single-symbol accessor keeps working.
        self.legs: List[_Leg] = legs if legs else [_Leg(symbol, sim, initial)]
        self.sim = self.legs[0].sim
        self.symbol = self.legs[0].symbol
        self.mode = mode
        self.initial = initial
        self.stop_flag = False
        self.task: Optional[asyncio.Task] = None
        self.last_price = 0.0
        self.equity = initial
        self.ret = 0.0
        self.status = "running"
        self.recent: List[dict] = []
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.last_checkpoint_monotonic = time.monotonic()
        self.finalize_lock: Optional[asyncio.Lock] = None
        self.finalized = False
        self.inflight_persist: Optional[asyncio.Task] = None
        # 리더보드 행 상태용 체결 요약 — _note_fill 이 채우고 _state_view 가 읽는다.
        self.trade_count = 0
        self.last_fill: Optional[dict] = None
        self.last_entry_return: Optional[float] = None

    @property
    def replay_prices(self) -> List[float]:
        return self.legs[0].replay_prices

    @replay_prices.setter
    def replay_prices(self, prices: List[float]) -> None:
        self.legs[0].replay_prices = prices

    @property
    def symbols(self) -> List[str]:
        return [leg.symbol for leg in self.legs]

    def is_portfolio(self) -> bool:
        return len(self.legs) > 1

    def leg_for(self, symbol: Optional[str]) -> _Leg:
        if symbol is None:
            return self.legs[0]
        for leg in self.legs:
            if leg.symbol == symbol:
                return leg
        raise KeyError(symbol)


_running: Dict[int, _Runner] = {}


def _session_initial(macro: Macro) -> float:
    if macro.rule_type is RuleType.C:
        return 1_000_000.0
    return float(macro.initial_capital or 1_000_000.0)


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
    legs: List[_Leg] = []
    for sym in symbols:
        leg_macro = macro.for_symbol(sym, per_leg) if len(symbols) > 1 else macro
        legs.append(_Leg(sym, make_sim(leg_macro, initial_capital=per_leg), per_leg))

    session_id = await asyncio.to_thread(_create_session, macro, symbols[0], mode, initial)

    runner = _Runner(session_id, legs[0].sim, symbols[0], mode, initial, legs=legs)
    if mode == "replay":
        for leg in runner.legs:
            leg.replay_prices = await asyncio.to_thread(_load_replay_prices, leg.symbol)

    _running[session_id] = runner
    runner.task = asyncio.create_task(_run_loop(runner))

    return {
        "session_id": session_id,
        "symbol": symbols[0],
        "symbols": symbols,
        "mode": mode,
        "virtual_balance": initial,
        "status": "running",
    }


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


def _tick(
    runner: _Runner,
    price: float,
    ts: Optional[datetime] = None,
    symbol: Optional[str] = None,
):
    """Advance one leg's simulation in memory and return a fill, if one occurred.

    ``symbol`` picks the leg (default: the primary symbol). Session totals are
    re-summed over every leg after each step.
    """
    leg = runner.leg_for(symbol)
    leg.last_price = price
    fill = leg.sim.step(price, ts)
    leg.equity = leg.sim.equity(price)
    leg.ret = (leg.equity - leg.initial) / leg.initial * 100.0
    leg.liquidations = getattr(leg.sim, "liquidations", 0)
    leg.liquidated_loss = getattr(leg.sim, "liquidated_loss", 0.0)
    _aggregate(runner)
    return fill


def _aggregate(runner: _Runner) -> None:
    runner.last_price = runner.legs[0].last_price
    runner.equity = sum(leg.equity for leg in runner.legs)
    runner.ret = (runner.equity - runner.initial) / runner.initial * 100.0
    runner.liquidations = sum(leg.liquidations for leg in runner.legs)
    runner.liquidated_loss = sum(leg.liquidated_loss for leg in runner.legs)


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
        fill = _tick(runner, price, ts, symbol=leg.symbol)
        if fill is not None:
            fills.append((leg.symbol, fill))
            _note_fill(runner, fill, leg.symbol)
    if fills:
        for symbol, fill in fills:
            await _checkpoint(runner, fill=fill, symbol=symbol)
    elif ticked:
        await _checkpoint(runner)


_EXIT_SIDES = {"sell", "cover"}


def sim_state(sim) -> dict:
    """state() 가 없는 시뮬레이터(테스트 더미 등)에는 '포지션 없음' 기본값."""
    getter = getattr(sim, "state", None)
    if getter is None:
        return {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    return getter()


def _note_fill(runner: _Runner, fill, symbol: str) -> None:
    """체결 하나를 러너의 상태 요약에 반영 — 횟수·마지막 체결·익절/손절 구분."""
    runner.trade_count += 1
    kind = ""
    if fill.side in _EXIT_SIDES:
        kind = "exit" if runner.last_entry_return is None else ("tp" if fill.return_pct > runner.last_entry_return else "sl")
    else:
        runner.last_entry_return = float(fill.return_pct)
    runner.last_fill = {"ms": _now_ms(), "side": fill.side, "return": round(float(fill.return_pct), 4), "kind": kind, "symbol": symbol}


def _state_view(runner: _Runner) -> dict:
    """리더보드 행 상태(포지션·체결 요약) — 체크포인트마다 state_json 으로 저장된다."""
    legs = []
    cooldowns = []
    for leg in runner.legs:
        st = sim_state(leg.sim)
        legs.append({"symbol": leg.symbol, "qty": round(st["qty"], 8), "dir": st["dir"],
                     "entry_price": round(st["entry_price"], 4), "last_price": round(leg.last_price, 4),
                     "in_position": bool(st["in_position"])})
        if st["cooldown_until_ms"] is not None:
            cooldowns.append(int(st["cooldown_until_ms"]))
    last = runner.last_fill or {}
    return {
        "in_position": any(l["in_position"] for l in legs),
        "halted_today": any(sim_state(leg.sim)["halted_today"] for leg in runner.legs),
        "cooldown_until_ms": max(cooldowns) if cooldowns else None,
        "trade_count": runner.trade_count,
        "last_fill_ms": last.get("ms"), "last_fill_side": last.get("side", ""),
        "last_fill_return": last.get("return"), "last_fill_kind": last.get("kind", ""),
        "last_price": round(runner.last_price, 4), "checkpoint_ms": _now_ms(), "legs": legs,
    }


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
