"""실행기(실계좌) 세션의 서버 측 전략 드라이버.

실행기 v8 은 스스로 진입/청산을 판단하지 않는다. 세션마다 여기서 StrategyDriver 를 돌려(페이퍼와 같은
엔진·같은 마감봉) Fill 을 RunnerCommand 로 바꿔 두면, 실행기가 heartbeat 응답으로 받아 주문만 넣고
다음 heartbeat 의 acks 로 결과를 보고한다.

수명: runner.start_session → schedule_start → start_driver(웜업·구독·틱 루프) … mark_stopped → schedule_stop.
재기동 시 resume_running_runner_sessions 가 state_json 으로 이어 간다.

이 모듈은 HTTP 를 모른다 — 드라이버 수명·Fill→명령·ack/만료만 맡고, API 는 runner.py 가 붙인다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select

from .data import get_ticker_price_cached  # 모듈 이름으로 참조 — 테스트가 monkeypatch 한다
from .db import RunnerCommand, RunSession, get_session
from .engine import Macro
from .engine.candle_feed import feed  # 모듈 이름으로 참조 — 테스트가 monkeypatch 한다
from .engine.driver import Leg, StrategyDriver
from .engine.stepper import EXIT_SIDES, Fill, make_sim

log = logging.getLogger(__name__)

# 페이퍼와 같은 주기로 시세를 본다(env 도 공유).
POLL_SECONDS = float(os.environ.get("PAPER_POLL_SECONDS", "3"))
CHECKPOINT_SECONDS = max(1.0, float(os.environ.get("PAPER_CHECKPOINT_SECONDS", "10")))
# 이 시간 안에 실행기가 가져가지 않은 명령은 만료 — 늦은 진입 신호를 뒤늦게 실행하지 않는다.
COMMAND_TTL_SECONDS = float(os.environ.get("COMMAND_TTL_SECONDS", "90"))
EXIT_RETRY_MAX = 3  # 청산은 실패해도 이만큼 다시 시도한다(포지션이 남으면 위험)
WARMUP_CANDLES = 500
FALLBACK_CAPITAL = 100.0  # 실행기 MAX_ORDER_USDT 기본과 같다
EXIT_FAIL_NOTE = "청산 실패 — 확인 필요"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Live:
    """돌고 있는 세션 하나 — 드라이버·틱 태스크·마감봉 구독."""

    __slots__ = ("session_id", "driver", "task", "subs", "stop_flag", "last_checkpoint")

    def __init__(self, session_id: int, driver: StrategyDriver) -> None:
        self.session_id = session_id
        self.driver = driver
        self.task: Optional[asyncio.Task] = None
        self.subs: list = []
        self.stop_flag = False
        self.last_checkpoint = 0.0


_drivers: Dict[int, _Live] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None


# --- 스레드 → 루프 -------------------------------------------------------
def install(loop: asyncio.AbstractEventLoop) -> None:
    """lifespan 이 부른다. 이후 schedule_* 가 요청 스레드에서 루프로 작업을 넘길 수 있다."""
    global _loop
    _loop = loop


def _schedule(coro_factory) -> None:
    if _loop is None or _loop.is_closed():
        log.warning("runner engine: no event loop installed; skipping %s", getattr(coro_factory, "__name__", "task"))
        return
    _loop.call_soon_threadsafe(lambda: _loop.create_task(coro_factory()))


def schedule_start(session_id: int) -> None:
    _schedule(lambda: start_driver(session_id))


def schedule_stop(session_id: int) -> None:
    _schedule(lambda: stop_driver(session_id))


# --- 드라이버 ------------------------------------------------------------
def build_driver(macro: Macro, symbol: str) -> StrategyDriver:
    """실행기 세션은 종목 하나 — 초기자본은 매크로 값, 없으면 실행기 기본 주문 한도."""
    initial = float(macro.initial_capital or FALLBACK_CAPITAL)
    sym = (symbol or macro.symbol).upper()
    return StrategyDriver([Leg(sym, make_sim(macro, initial_capital=initial), initial)], initial, macro=macro)


def _load_session(session_id: int) -> Optional[dict]:
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.status != "running" or not row.macro_json:
            return None
        return {"symbol": row.symbol, "macro_json": row.macro_json, "state_json": row.state_json}


async def start_driver(session_id: int) -> bool:
    """DB 의 macro_json 으로 드라이버를 만들고 웜업 → (복구) → 구독 → 틱 루프. 이미 돌면 True."""
    if session_id in _drivers:
        return True
    info = await asyncio.to_thread(_load_session, session_id)
    if info is None:
        return False
    try:
        macro = Macro.model_validate_json(info["macro_json"])
        driver = build_driver(macro, info["symbol"])
    except Exception:
        log.exception("runner engine: session %s macro invalid", session_id)
        return False
    live = _Live(session_id, driver)
    keys = driver.candle_keys()
    history: Dict[str, list] = {}
    for symbol, interval, market in keys:
        try:
            history[symbol] = await feed.history(symbol, interval, market, WARMUP_CANDLES)
        except Exception:
            log.exception("runner engine: warmup failed for %s — starting cold", symbol)
    driver.warmup(history)
    # 복구는 웜업 뒤 — 웜업이 장부를 비운다. 자산은 초기자본 기준(실계좌 손익은 실행기가 안다).
    from .paper import parse_state  # 늦은 import: paper ↔ runner_engine 순환 방지
    state = parse_state(info["state_json"])
    if state:
        driver.restore(state, leg_equity={}, total_equity=driver.initial)
        driver.trade_count = int(state.get("trade_count") or 0)

    async def on_candle(symbol: str, candle) -> None:
        if not live.stop_flag:
            driver.push_candle(symbol, candle)

    subs = []
    for symbol, interval, market in keys:
        hist = history.get(symbol)
        since_t = hist[-1][0] if hist else None  # Candle.t — 인덱스로 읽어 테스트 더미(tuple)도 받는다
        subs.append(feed.subscribe(symbol, interval, market, on_candle, since_t=since_t))
    live.subs = subs
    _drivers[session_id] = live
    live.task = asyncio.get_running_loop().create_task(_run(live))
    return True


async def stop_driver(session_id: int) -> None:
    live = _drivers.pop(session_id, None)
    if live is None:
        return
    live.stop_flag = True
    for sub in live.subs:
        try:
            feed.unsubscribe(sub)
        except Exception:
            log.exception("runner engine: unsubscribe failed")
    live.subs = []
    if live.task is not None and live.task is not asyncio.current_task():
        live.task.cancel()


async def _run(live: _Live) -> None:
    try:
        while not live.stop_flag:
            await _tick_once(live)
            await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("runner engine: session %s loop died", live.session_id)
    finally:
        # 세션이 DB 에서 running 이 아니게 돼 루프가 스스로 멈춘 경우(또는 예외) — 등록·구독을 정리한다.
        # stop_driver 가 먼저 뺐으면 아무것도 안 한다(자기 태스크는 취소하지 않는다).
        if _drivers.get(live.session_id) is live:
            await stop_driver(live.session_id)


async def _tick_once(live: _Live) -> None:
    """시세 한 번 → 틱 → 체결이면 명령 기록 → 주기적 체크포인트."""
    try:
        price = await asyncio.to_thread(get_ticker_price_cached, live.driver.symbol)
    except Exception:
        price = None
    if not price:
        return
    fill = live.driver.tick(float(price), datetime.now(timezone.utc))
    if fill is not None:
        await asyncio.to_thread(_persist_fill, live, fill)
        live.last_checkpoint = time.monotonic()
    elif time.monotonic() - live.last_checkpoint >= CHECKPOINT_SECONDS:
        await asyncio.to_thread(_persist_state, live)
        live.last_checkpoint = time.monotonic()


def _persist_state(live: _Live) -> None:
    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        row.state_json = json.dumps(live.driver.state())
        db.add(row)
        db.commit()


def _persist_fill(live: _Live, fill: Fill) -> None:
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        cmd = insert_command(db, row, command_from_fill(fill, live.driver.initial), _now_ms())
        row.state_json = json.dumps(live.driver.state())
        db.add(row)
        _append_events(db, row, [{"ts": _now_iso(), "kind": "signal",
                                  "message": f"[신호 #{cmd.seq}] {cmd.reason} → {cmd.action.upper()} @ {fill.price:g}"}])
        db.commit()


# --- Fill → 명령 -----------------------------------------------------------
def command_from_fill(fill: Fill, initial: float) -> dict:
    """진입은 초기자본 대비 금액 비율, 청산은 직전 보유 수량 대비 비율(모르면 전량)."""
    if fill.side in EXIT_SIDES:
        frac = 1.0 if fill.qty_before <= 0 else min(1.0, fill.qty / fill.qty_before)
        if frac >= 0.999:
            frac = 1.0
        return {"action": fill.side, "notional_frac": 0.0, "qty_frac": float(frac),
                "signal_price": float(fill.price), "reason": fill.reason or "청산"}
    notional = fill.qty * fill.price
    return {"action": fill.side, "notional_frac": float(notional / initial) if initial > 0 else 0.0, "qty_frac": 0.0,
            "signal_price": float(fill.price), "reason": fill.reason or "진입"}


def insert_command(db, row: RunSession, cmd: dict, now_ms: int) -> RunnerCommand:
    """세션 안에서 seq 를 이어 pending 명령을 넣는다(flush 만, 커밋은 호출자)."""
    last = db.exec(select(RunnerCommand.seq).where(RunnerCommand.session_id == row.id).order_by(RunnerCommand.seq.desc())).first()
    item = RunnerCommand(session_id=row.id, seq=int(last or 0) + 1, action=cmd["action"],
                         notional_frac=cmd["notional_frac"], qty_frac=cmd["qty_frac"], signal_price=cmd["signal_price"],
                         reason=str(cmd["reason"])[:200], status="pending", created_at=_now_iso(), created_ms=now_ms,
                         expires_ms=now_ms + int(COMMAND_TTL_SECONDS * 1000))
    db.add(item)
    db.flush()
    return item


def _view(c: RunnerCommand) -> dict:
    return {"id": c.id, "seq": c.seq, "action": c.action, "notional_frac": c.notional_frac, "qty_frac": c.qty_frac,
            "signal_price": c.signal_price, "reason": c.reason, "expires_ms": c.expires_ms}


def pending_commands(db, row: RunSession, now_ms: int) -> List[dict]:
    """TTL 지난 pending 은 expired 로 바꾸고, 남은 pending 을 seq 순으로 응답용 dict 로 돌려준다."""
    rows = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id, RunnerCommand.status == "pending")
                   .order_by(RunnerCommand.seq.asc())).all()
    out = []
    for c in rows:
        if c.expires_ms <= now_ms:
            c.status = "expired"
            db.add(c)
            continue
        out.append(_view(c))
    return out


def apply_acks(db, row: RunSession, acks: list, now_ms: int) -> None:
    """실행기의 실행 결과 보고. 성공은 acked, 청산 실패는 EXIT_RETRY_MAX 까지 재시도, 진입 실패는 바로 failed."""
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    if not isinstance(acks, list):
        return
    events = []
    for ack in acks[:100]:
        if not isinstance(ack, dict):
            continue
        try:
            cmd = db.get(RunnerCommand, int(ack.get("command_id")))
        except (TypeError, ValueError):
            continue
        if cmd is None or cmd.session_id != row.id or cmd.status != "pending":
            continue
        ok = bool(ack.get("ok"))
        cmd.attempts += 1
        cmd.acked_at = _now_iso()
        cmd.executed_qty = float(ack.get("executed_qty") or 0.0)
        cmd.fill_price = float(ack.get("fill_price") or 0.0)
        cmd.error = str(ack.get("error") or "")[:200]
        if ok:
            cmd.status = "acked"
            events.append({"ts": cmd.acked_at, "kind": "order",
                           "message": f"[주문 #{cmd.seq}] {cmd.action.upper()} 체결 {cmd.executed_qty:g} @ {cmd.fill_price:g} · {cmd.reason}"})
        elif cmd.action in EXIT_SIDES and cmd.attempts < EXIT_RETRY_MAX:
            cmd.status = "pending"
            cmd.expires_ms = now_ms + int(COMMAND_TTL_SECONDS * 1000)
            row.note = EXIT_FAIL_NOTE
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 청산 주문 실패({cmd.attempts}/{EXIT_RETRY_MAX}) — 다시 시도합니다 · {cmd.error}"})
        elif cmd.action in EXIT_SIDES:
            cmd.status = "failed"
            row.note = EXIT_FAIL_NOTE
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 청산 주문이 {EXIT_RETRY_MAX}회 실패했어요 — 거래소에서 포지션을 확인해 주세요 · {cmd.error}"})
        else:
            cmd.status = "failed"
            events.append({"ts": cmd.acked_at, "kind": "error",
                           "message": f"⚠ 진입 실패 — 이번 사이클은 건너뜁니다 · {cmd.error}"})
        db.add(cmd)
    if events:
        db.add(row)
        _append_events(db, row, events)


_MISMATCH_NOTED: set = set()  # session_id — 연속 중복 경고 방지(프로세스 메모리)


def check_position_mismatch(db, row: RunSession, reported_in_position: bool) -> None:
    """서버 전략의 포지션 유무와 실행기 보고가 다르면 한 번 경고. 다시 일치하면 다음 불일치에 또 1회.

    명령이 pending 인 동안은 실행기가 아직 못 따라온 것뿐이라 비교하지 않는다.
    """
    from .paper import parse_state  # 늦은 import: paper ↔ runner_engine 순환 방지
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    state = parse_state(row.state_json)
    if not state:
        return
    pending = db.exec(select(RunnerCommand.id).where(RunnerCommand.session_id == row.id, RunnerCommand.status == "pending")).first()
    if pending is not None:
        return
    expected = bool(state.get("in_position"))
    if expected == bool(reported_in_position):
        _MISMATCH_NOTED.discard(row.id)
        return
    if row.id in _MISMATCH_NOTED:
        return
    _MISMATCH_NOTED.add(row.id)
    _append_events(db, row, [{"ts": _now_iso(), "kind": "warn",
                              "message": ("서버 전략은 포지션 보유 중인데 실행기는 비어 있어요" if expected
                                          else "서버 전략은 비어 있는데 실행기는 포지션을 들고 있어요")
                                         + " — 거래소 포지션을 확인해 주세요."}])


# --- 재기동 복구 -------------------------------------------------------------
def _running_v8_ids() -> List[int]:
    from .runner import supports_signals  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        rows = db.exec(select(RunSession.id, RunSession.runner_version).where(RunSession.status == "running")).all()
    return [int(sid) for sid, ver in rows if supports_signals(str(ver or ""))]


async def resume_running_runner_sessions() -> int:
    """서버 재기동 — running 상태의 v8+ 세션 드라이버를 state_json 에서 되살린다."""
    count = 0
    for sid in await asyncio.to_thread(_running_v8_ids):
        try:
            if await start_driver(sid):
                count += 1
        except Exception:
            log.exception("runner engine resume: session %s failed", sid)
    if count:
        log.info("runner engine resume: revived %d session(s)", count)
    return count
