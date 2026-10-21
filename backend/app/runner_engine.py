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
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlmodel import select

from . import runner_fills
from .data import get_ticker_price_cached  # 모듈 이름으로 참조 — 테스트가 monkeypatch 한다
from .db import RunnerCommand, RunSession, get_session
from .engine import Macro
from .data.binance import _INTERVAL_MS
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
# 같은 세션의 직전 명령과 action·signal_price 가 같고 이 시간 안이면 새로 넣지 않는다 — 롤링 배포로 옛/새 프로세스가
# 같은 세션 드라이버를 잠깐 둘 다 돌릴 때 같은 신호가 두 번 실주문으로 나가는 걸 막는다.
COMMAND_DEDUPE_SECONDS = float(os.environ.get("COMMAND_DEDUPE_SECONDS", "60"))
# 실행기 heartbeat 가 이만큼 끊기면 세션을 서버가 닫고 드라이버를 내린다 — 죽은 실행기의 드라이버가 영영 돌며
# 아무도 실행하지 않을 명령을 쌓지 않게. 마이페이지의 '연결 끊김'(30초)보다 훨씬 길게 잡아 잠깐의 끊김은 견딘다.
STALE_RUNNER_SECONDS = float(os.environ.get("RUNNER_STALE_SECONDS", "3600"))
STALE_RUNNER_NOTE = "실행기 연결 끊김 — 서버 전략을 자동 종료했어요"
WARMUP_CANDLES = 500
FALLBACK_CAPITAL = 100.0  # 실행기 MAX_ORDER_USDT 기본과 같다
EXIT_FAIL_NOTE = "청산 실패 — 확인 필요"
ENTRY_SIDES = ("buy", "short")
# 진입 주문이 나가지 않으면(실패 · 만료) 서버 전략을 그 신호 직전으로 되돌린 뒤, 이만큼 기다렸다가 다시 진입한다.
# 잔고 부족처럼 금방 안 풀리는 실패를 3초마다 실주문으로 두드리지 않게 — 연속으로 되돌릴수록 길게.
# 실제로 체결된 진입이 한 번 나오면 처음부터 다시 센다.
RETRY_BACKOFF_MINUTES = (5, 15, 60)
ROLLED_BACK = "rolled_back"  # RunnerCommand.rollback_state — 이 명령으로 서버 전략을 되돌렸다는 표시
# 같은 heartbeat 에서 함께 되돌린 둘째 명령부터(그리드 칸 여러 개). 연속 실패 횟수(대기 시간)는 heartbeat 단위로 센다.
ROLLED_BACK_ALSO = "rolled_back_also"
# heartbeat 가 루프의 드라이버 되돌리기를 기다리는 최대 시간. 모든 실행기 heartbeat 가 연결 하나(대기 1초)를 나눠 쓰므로
# 길게 쥐면 다른 회원의 heartbeat 가 '혼잡' 으로 밀린다. 루프는 보통 수 ms 안에 받는다 — 못 받으면 다음 heartbeat 에 다시.
ROLLBACK_WAIT_SECONDS = 0.5
ROLLBACK_RETRY_MAX = 5  # 일시적으로 못 되돌린 진입을 다음 heartbeat 들에서 다시 시도하는 횟수
# 세션 실행 방식(RunSession.exec_policy) — 서버 전략 장부를 실행기가 보고한 실제 체결로 확정한다(runner.fill_ledger_policy).
FILL_LEDGER_POLICY = "fill_ledger_v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Live:
    """돌고 있는 세션 하나 — 드라이버·틱 태스크·마감봉 구독."""

    __slots__ = ("session_id", "driver", "task", "subs", "stop_flag", "last_checkpoint", "unsaved", "exchange", "rolled_back",
                 "unsaved_fold")

    def __init__(self, session_id: int, driver: StrategyDriver, exchange: str = "binance") -> None:
        self.session_id = session_id
        self.driver = driver
        # 시세·봉을 어느 거래소에서 읽을지. 세션 행에는 거래소 칸이 없어 매크로에서 가져온다 —
        # 안 넘기면 KRW 종목을 바이낸스에서 찾게 된다.
        self.exchange = exchange
        self.task: Optional[asyncio.Task] = None
        self.subs: list = []
        self.stop_flag = False
        self.last_checkpoint = 0.0
        # DB 저장에 실패한 체결 (fill, 그 시점 state_json, 직전 상태 JSON) — 다음 틱에서 먼저 다시 저장한다.
        # 엔진은 이미 큐에서 뺐으므로 여기 두지 않으면 신호가 사라진다.
        self.unsaved: List[tuple] = []
        # 이 드라이버에 적용한 되돌리기: 명령 id → 그때 취소하기로 한 뒤 명령 id. heartbeat 의 저장이 실패해 실행기가
        # 같은 ack 를 다시 보내면 같은 결과를 다시 쓴다(드라이버는 이미 되돌렸으니 체결 수 검사로 거절하면 안 된다).
        self.rolled_back: Dict[int, List[int]] = {}
        # 체결 기준 장부: 접었는데 저장하지 못한 결과(체결 행 id · 되돌림 표시할 명령 id · state_json · 이벤트) — 다음 틱에 먼저 다시 쓴다.
        self.unsaved_fold: Optional[tuple] = None


_drivers: Dict[int, _Live] = {}
_starting: set = set()  # start_driver 가 첫 await 전에 표시 — 동시 호출이 드라이버를 둘 만들지 않게
_loop: Optional[asyncio.AbstractEventLoop] = None
_scheduled: set = set()  # _schedule 이 만든 태스크의 강한 참조 — GC 로 사라지지 않게(끝나면 뺀다)


# --- 스레드 → 루프 -------------------------------------------------------
def install(loop: asyncio.AbstractEventLoop) -> None:
    """lifespan 이 부른다. 이후 schedule_* 가 요청 스레드에서 루프로 작업을 넘길 수 있다."""
    global _loop
    _loop = loop


def _schedule(coro_factory, label: str) -> None:
    if _loop is None or _loop.is_closed():
        log.warning("runner engine: no event loop installed; skipping %s", label)
        return

    def _spawn() -> None:
        task = _loop.create_task(coro_factory(), name=f"runner_engine:{label}")
        _scheduled.add(task)
        task.add_done_callback(_scheduled.discard)

    _loop.call_soon_threadsafe(_spawn)


def schedule_start(session_id: int) -> None:
    _schedule(lambda: start_driver(session_id), f"start_driver({session_id})")


def schedule_stop(session_id: int) -> None:
    _schedule(lambda: stop_driver(session_id), f"stop_driver({session_id})")


# --- 드라이버 ------------------------------------------------------------
def build_driver(macro: Macro, symbol: str) -> StrategyDriver:
    """실행기 세션은 종목 하나 — 초기자본은 매크로 값, 없으면 실행기 기본 주문 한도."""
    initial = float(macro.initial_capital or FALLBACK_CAPITAL)
    sym = (symbol or macro.symbol).upper()
    driver = StrategyDriver([Leg(sym, make_sim(macro, initial_capital=initial), initial)], initial, macro=macro)
    driver.enable_fill_snapshots()  # 진입이 실제로 안 나가면 되돌릴 수 있게 체결 직전 상태를 잡는다
    return driver


START_FAIL_NOTE = "서버 전략 시작 실패 — 매크로 설정을 확인해 주세요"
# 실행기는 macro["symbol"] 하나만 읽으므로 여러 종목 매크로를 되살리면 첫 종목에 전액이 들어간다.
# 새 세션은 runner.py 의 관문 세 곳이 막지만, 그 관문이 생기기 **전에** 시작해 지금도 running 인 세션은
# 서버가 재기동할 때마다 되살아난다 — 되살리지 않고 세션을 닫아 사용자가 마이페이지에서 이유를 읽게 한다.
PORTFOLIO_REFUSED_NOTE = "여러 종목 매크로 — 서버 전략을 시작하지 않고 세션을 닫았어요"


def _load_session(session_id: int) -> Optional[dict]:
    """running 세션의 드라이버 재료. 없거나 running 이 아니면 None — macro_json 이 비어 있어도 돌려준다(호출자가 실패를 기록)."""
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.status != "running":
            return None
        return {"symbol": row.symbol, "macro_json": row.macro_json, "state_json": row.state_json,
                "exec_policy": row.exec_policy or ""}


def _note_start_failure(session_id: int) -> None:
    """드라이버를 못 만들었다는 걸 세션 note·error 이벤트로 남긴다(최선 노력 — 실패해도 호출자가 삼킨다).

    조용히 실패하면 실행기는 '연결됨' 인데 신호가 영영 안 오는 세션이 된다 — 사용자가 마이페이지에서 알아야 한다.
    """
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None:
            return
        row.note = START_FAIL_NOTE
        db.add(row)
        _append_events(db, row, [{"ts": _now_iso(), "kind": "error",
                                  "severity": "ERROR", "event_name": "engine.start_failed",
                                  "event_key": f"session:{row.id}:engine.start_failed",
                                  "operation_id": f"session:{row.id}", "details": {"reason": START_FAIL_NOTE},
                                  "message": "⚠ 서버 전략을 시작하지 못했어요 — 매크로 설정이 비어 있거나 올바르지 않아 실행기는 신호를 받지 못합니다."}], component="engine")
        db.commit()


def _is_portfolio_session(session_id: int) -> bool:
    """running 세션의 저장된 매크로가 여러 종목인가. 못 읽으면 False — 그쪽은 _start_driver 가 '시작 실패' 로 기록한다."""
    info = _load_session(session_id)
    if info is None or not info["macro_json"]:
        return False
    try:
        return Macro.model_validate_json(info["macro_json"]).is_portfolio()
    except Exception:
        return False


def _refuse_portfolio_session(session_id: int) -> None:
    """여러 종목 세션을 닫는다 — note·stop 이벤트로 이유를 남긴다(최선 노력).

    'running 인데 신호가 영영 안 오는 세션' 으로 남겨 두면 사용자는 실행기가 돌고 있다고 믿는다.
    """
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.status != "running":
            return
        row.status = "stopped"
        row.stopped_at = _now_iso()
        row.note = PORTFOLIO_REFUSED_NOTE
        db.add(row)
        _append_events(db, row, [{"ts": row.stopped_at, "kind": "stop",
                                  "severity": "WARN", "event_name": "engine.portfolio_refused",
                                  "event_key": f"session:{row.id}:engine.portfolio_refused",
                                  "operation_id": f"session:{row.id}", "details": {"reason": PORTFOLIO_REFUSED_NOTE},
                                  "message": f"종료 · {PORTFOLIO_REFUSED_NOTE}"
                                             + " — 실행기는 종목 하나만 읽어 첫 종목에 전액이 들어갑니다."
                                               " 종목 하나로 나눠 다시 시작해 주세요."
                                             + (" · 포지션 보유 중" if row.in_position else "")}], component="engine")
        db.commit()
    log.warning("runner engine: session %s macro is multi-symbol — refused and stopped", session_id)


async def start_driver(session_id: int) -> bool:
    """DB 의 macro_json 으로 드라이버를 만들고 웜업 → (복구) → 구독 → 틱 루프. 이미 돌면(또는 시작 중이면) True."""
    if session_id in _drivers or session_id in _starting:
        return True
    _starting.add(session_id)
    try:
        return await _start_driver(session_id)
    finally:
        _starting.discard(session_id)


async def _start_driver(session_id: int) -> bool:
    info = await asyncio.to_thread(_load_session, session_id)
    if info is None:
        return False
    try:
        if not info["macro_json"]:
            raise ValueError("macro_json is empty")
        macro = Macro.model_validate_json(info["macro_json"])
        driver = build_driver(macro, info["symbol"])
        if info.get("exec_policy") == FILL_LEDGER_POLICY:
            driver.enable_fill_ledger()  # 세션을 만들 때 정한 값만 본다 — 실행기 버전이 바뀌어도 그대로(결정 4)
    except Exception:
        log.exception("runner engine: session %s macro invalid", session_id)
        try:
            await asyncio.to_thread(_note_start_failure, session_id)
        except Exception:
            log.exception("runner engine: session %s could not record start failure", session_id)
        return False
    if macro.is_portfolio():
        # build_driver 는 레그 하나만 만든다 — 되살리면 매크로의 초기자본 전액이 첫 종목에 들어간다(실돈 주문).
        # 관문이 생기기 전에 시작한 세션이 재기동마다 여기로 오므로, 모든 시작 경로의 마지막 관문이 여기다.
        try:
            await asyncio.to_thread(_refuse_portfolio_session, session_id)
        except Exception:
            log.exception("runner engine: session %s could not record portfolio refusal", session_id)
        return False
    live = _Live(session_id, driver, macro.exchange)
    # 국내 거래소는 거래소 인자를 실어 보낸다. 바이낸스는 인자 없이 부르는 옛 호출 모양을 그대로 둔다(candle_feed 와 같은 규칙).
    feed_kwargs = {} if live.exchange == "binance" else {"exchange": live.exchange}
    keys = driver.candle_keys()
    history: Dict[str, list] = {}
    for symbol, interval, market in keys:
        try:
            history[symbol] = await feed.history(symbol, interval, market, WARMUP_CANDLES, **feed_kwargs)
        except Exception:
            log.exception("runner engine: warmup failed for %s — starting cold", symbol)
    from .paper import parse_state  # 늦은 import: paper ↔ runner_engine 순환 방지
    state = parse_state(info["state_json"])
    # 되살리기(재기동 · 배포)라면 마지막 저장 뒤에 마감된 봉은 앞 프로세스가 판정하지 못했을 수 있다 — 웜업에 넣으면
    # 그 봉의 진입 · 청산 의도가 조용히 사라진다(2026-10-18 세션 297: 19:00 마감 직후 배포로 골든크로스 진입을 놓침).
    replay = _unjudged_last_close(history, state, keys)
    driver.warmup({s: (rows[:-1] if s in replay else rows) for s, rows in history.items()})
    # 복구는 웜업 뒤 — 웜업이 장부를 비운다. 자산은 초기자본 기준(실계좌 손익은 실행기가 안다).
    if state:
        driver.restore(state, leg_equity={}, total_equity=driver.initial)
        driver.trade_count = int(state.get("trade_count") or 0)
    if driver.fill_ledger:
        await _drop_unsent_orders(session_id, driver)
    # 그 봉 하나만 실시간 봉처럼 넘긴다 — 의도가 서면 첫 틱(≈ 다음 시가)에 체결된다. 더 오래 끊겼어도 묵은 봉의
    # 신호로 한꺼번에 사고팔지 않게 가장 최근 봉 하나만.
    for symbol, candle in replay.items():
        driver.push_candle(symbol, candle)

    async def on_candle(symbol: str, candle) -> None:
        if not live.stop_flag:
            driver.push_candle(symbol, candle)

    subs = []
    for symbol, interval, market in keys:
        hist = history.get(symbol)
        since_t = hist[-1][0] if hist else None  # Candle.t — 인덱스로 읽어 테스트 더미(tuple)도 받는다
        subs.append(feed.subscribe(symbol, interval, market, on_candle, since_t=since_t, **feed_kwargs))
    live.subs = subs
    _drivers[session_id] = live
    live.task = asyncio.get_running_loop().create_task(_run(live))
    return True


def _unjudged_last_close(history: Dict[str, list], state: Optional[dict], keys: List[tuple]) -> Dict[str, tuple]:
    """되살릴 때 실시간으로 다시 판정할 봉 — 종목마다 가장 최근 마감봉이 마지막 저장(checkpoint_ms) 뒤에 마감됐으면 그 봉.

    체결이 나면 그 자리에서 상태를 저장하므로(checkpoint_ms = 처리 시각 > 마감 시각) 판정해 체결까지 간 봉은 여기
    걸리지 않는다. 판정했지만 체결이 없던 봉이 걸리면 같은 봉으로 같은 결정을 다시 내릴 뿐이다(웜업은 그 봉을 빼고 한다).
    처음 시작(저장 없음)이면 비운다 — 시작하자마자 지난 봉의 신호로 사지 않는다.
    """
    checkpoint = (state or {}).get("checkpoint_ms")
    if not checkpoint:
        return {}
    out: Dict[str, tuple] = {}
    for symbol, interval, _market in keys:
        rows = history.get(symbol) or []
        step = _INTERVAL_MS.get(interval)
        if not rows or not step:
            continue
        last = rows[-1]
        if int(last[0]) + int(step) > int(checkpoint):
            out[symbol] = last
    return out


async def stop_driver(session_id: int) -> None:
    _ROLLBACK_RETRY.pop(session_id, None)
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


async def shutdown_drivers() -> int:
    """프로세스 종료(lifespan) — 돌고 있는 드라이버를 전부 멈추고 틱 태스크가 끝나길 기다린다(최선 노력).

    롤링 배포에서 옛 프로세스가 종료 신호를 받은 뒤에도 드라이버를 계속 돌리면 새 프로세스의 드라이버와
    같은 신호를 두 번 명령으로 남긴다. 세션 상태(DB)는 건드리지 않는다 — 새 프로세스가 state_json 으로 이어 간다.
    """
    ids = list(_drivers)
    tasks = []
    for sid in ids:
        live = _drivers.get(sid)
        task = live.task if live is not None else None
        try:
            await stop_driver(sid)
        except Exception:
            log.exception("runner engine shutdown: session %s stop failed", sid)
        if task is not None and task is not asyncio.current_task():
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if ids:
        log.info("runner engine shutdown: stopped %d driver(s)", len(ids))
    return len(ids)


LOOP_ERROR_NOTE = "서버 전략 루프 오류 — 확인 필요"


async def _run(live: _Live) -> None:
    try:
        while not live.stop_flag:
            await _tick_once(live)
            await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        # 틱 한 번의 저장 실패는 _tick_once 가 삼킨다 — 여기까지 온 건 예상 못 한 오류. 세션에 흔적을 남긴다.
        log.exception("runner engine: session %s loop died", live.session_id)
        try:
            await asyncio.to_thread(_note_loop_error, live.session_id)
        except Exception:
            log.exception("runner engine: session %s could not record loop error", live.session_id)
    finally:
        # 세션이 DB 에서 running 이 아니게 돼 루프가 스스로 멈춘 경우(또는 예외) — 등록·구독을 정리한다.
        # stop_driver 가 먼저 뺐으면 아무것도 안 한다(자기 태스크는 취소하지 않는다).
        if _drivers.get(live.session_id) is live:
            await stop_driver(live.session_id)


def _note_loop_error(session_id: int) -> None:
    """루프가 죽었다는 걸 세션 note·이벤트로 남긴다(최선 노력 — 실패해도 호출자가 삼킨다)."""
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None:
            return
        row.note = LOOP_ERROR_NOTE
        db.add(row)
        _append_events(db, row, [{"ts": _now_iso(), "kind": "error",
                                  "severity": "ERROR", "event_name": "engine.loop_failed",
                                  "event_key": f"session:{row.id}:engine.loop_failed",
                                  "operation_id": f"session:{row.id}", "details": {"reason": LOOP_ERROR_NOTE},
                                  "message": "⚠ 서버 전략 루프가 오류로 멈췄어요 — 실행기는 새 신호를 받지 못합니다. 세션을 다시 시작해 주세요."}], component="engine")
        db.commit()


async def _tick_once(live: _Live) -> None:
    """(저장 못 한 체결 먼저) → (체결 기준 장부면 체결 접기) → 시세 한 번 → 틱 → 체결이면 명령 기록 → 주기적 체크포인트.

    드라이버 상태(state_json)는 루프 스레드에서 스냅샷해 워커에 문자열로 넘긴다 — 마감봉 콜백이
    루프에서 sim 을 바꾸는 동안 워커가 state() 를 읽으면 반쪽 상태가 저장될 수 있다.
    """
    if live.unsaved:
        try:
            for fill, state_json, rollback_json in list(live.unsaved):
                await asyncio.to_thread(_persist_fill, live, fill, state_json, rollback_json)
                live.unsaved.pop(0)
        except Exception:
            log.exception("runner engine: session %s still cannot persist %d fill(s) — not draining further",
                          live.session_id, len(live.unsaved))
            return
        live.last_checkpoint = time.monotonic()
    if live.driver.fill_ledger:
        try:
            await _fold_once(live)
        except Exception:
            log.exception("runner engine: session %s fill fold failed — will retry next tick", live.session_id)
    try:
        price = await asyncio.to_thread(
            get_ticker_price_cached, live.driver.symbol, exchange=live.exchange)
    except Exception:
        price = None
    if not price:
        return
    fill = live.driver.tick(float(price), datetime.now(timezone.utc))
    state_json = json.dumps(live.driver.state())
    if fill is not None:
        rollback_json = _rollback_json(live.driver, fill)
        try:
            await asyncio.to_thread(_persist_fill, live, fill, state_json, rollback_json)
        except Exception:
            live.unsaved.append((fill, state_json, rollback_json))
            log.exception("runner engine: session %s persist fill failed — will retry next tick", live.session_id)
            return
        live.last_checkpoint = time.monotonic()
    elif time.monotonic() - live.last_checkpoint >= CHECKPOINT_SECONDS:
        rollbacks = live.driver.rollbacks
        try:
            await asyncio.to_thread(_persist_state, live, state_json)
            if live.driver.rollbacks != rollbacks and not live.stop_flag:
                # 저장하는 사이 heartbeat 가 서버 전략을 되돌렸다. 이 저장이 heartbeat 보다 늦게 끝났으면 DB 에는
                # 되돌리기 전 상태가 남는다 — 재기동이 그걸 되살리지 않게 지금 상태로 한 번 더 쓴다.
                await asyncio.to_thread(_persist_state, live, json.dumps(live.driver.state()))
        except Exception:
            log.exception("runner engine: session %s checkpoint failed", live.session_id)
        live.last_checkpoint = time.monotonic()


# --- 체결 기준 장부: 체결 기록표 → 장부 ------------------------------------------
def _ledger_of(driver: StrategyDriver):
    sim = driver.legs[0].sim
    return getattr(sim, "inner", sim).ledger


def _load_fold(session_id: int, open_refs: List[str], now_ms: int) -> Optional[dict]:
    """(워커 스레드) 접을 것 — 안 접은 체결 행(들어온 순서) + 끝난 미결 주문. 세션이 끝났으면 None.

    체결 행이 한 묶음(FOLD_BATCH)을 넘게 밀려 있으면 끝난 주문은 다음 틱에 — 주문의 체결이 장부에 다 들어간 뒤에 정리한다.
    체결 없이 끝난 진입(실패 · 만료)에는 다시 사기 전 기다릴 시각을 붙인다(옛 되돌리기와 같은 대기 — 연속일수록 길게).
    """
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.status != "running":
            return None
        fills = runner_fills.unapplied_fills(db, session_id)
        resolutions: List[dict] = []
        if len(fills) < runner_fills.FOLD_BATCH:
            minutes = None
            for cmd in runner_fills.commands_for_refs(db, session_id, open_refs):
                if cmd.status not in runner_fills.TERMINAL:
                    continue
                hold = None
                if cmd.action in ENTRY_SIDES and cmd.status in ("failed", "expired") and float(cmd.executed_qty or 0) <= 0:
                    if minutes is None:
                        minutes = _retry_minutes(db, row)
                    hold = now_ms + minutes * 60_000
                resolutions.append({"ref": cmd.order_ref, "status": cmd.status, "hold_until_ms": hold,
                                    "command_id": cmd.id, "seq": cmd.seq, "action": cmd.action})
        return {"fills": fills, "resolutions": resolutions, "user_id": row.user_id}


def _persist_fold(session_id: int, fill_ids: List[int], rolled_ids: List[int], state_json: str, events: List[dict]) -> None:
    """(워커 스레드) 접은 결과를 한 트랜잭션에 — 체결 행 '접음' 표시 · 체결 없이 끝난 진입의 되돌림 표시 · state_json · 이벤트."""
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None:
            return
        runner_fills.mark_applied(db, fill_ids, _now_ms())
        for cid in rolled_ids:
            cmd = db.get(RunnerCommand, cid)
            if cmd is not None and cmd.session_id == session_id and cmd.rollback_state not in (ROLLED_BACK, ROLLED_BACK_ALSO):
                cmd.rollback_state = ROLLED_BACK  # 연속으로 안 나간 진입 횟수를 센다(_retry_minutes)
                db.add(cmd)
        if row.status == "running":
            row.state_json = state_json
        db.add(row)
        if events:
            _append_events(db, row, events, component="engine")
        db.commit()


async def _fold_once(live: _Live) -> None:
    """체결 기록표에서 안 접은 체결 · 끝난 주문을 읽어 장부에 접고 저장한다. 같은 체결 키는 장부가 한 번만 받는다."""
    if live.unsaved_fold is not None:
        await asyncio.to_thread(_persist_fold, live.session_id, *live.unsaved_fold)
        live.unsaved_fold = None
    ledger = _ledger_of(live.driver)
    data = await asyncio.to_thread(_load_fold, live.session_id, list(ledger.orders), _now_ms())
    if data is None:
        live.stop_flag = True
        return
    if not data["fills"] and not data["resolutions"]:
        return
    result = live.driver.fold(fills=data["fills"], resolutions=data["resolutions"])
    resolved = set(result["resolved"])
    rolled = [r["command_id"] for r in data["resolutions"] if r["hold_until_ms"] and r["ref"] in resolved]
    pending = ([f["id"] for f in data["fills"]], rolled, json.dumps(live.driver.state()),
               _ledger_events(live, data, result))
    try:
        await asyncio.to_thread(_persist_fold, live.session_id, *pending)
    except Exception:
        live.unsaved_fold = pending  # 장부는 이미 접었다 — 저장만 다음 틱에 다시(키가 있어 두 번 접히지 않는다)
        raise
    live.last_checkpoint = time.monotonic()


async def _drop_unsent_orders(session_id: int, driver: StrategyDriver) -> None:
    """되살린 장부의 미결 주문 중 명령 행이 없는 것 — 신호를 명령으로 저장하기 전에 프로세스가 끝났다. 보낸 적이 없으니
    체결도 없다 — 취소로 정리한다(남겨 두면 그 주문이 끝나기를 기다리며 판단이 영영 보류된다)."""
    ledger = _ledger_of(driver)
    refs = list(ledger.orders)
    if not refs:
        return

    def known() -> set:
        with get_session() as db:
            return {c.order_ref for c in runner_fills.commands_for_refs(db, session_id, refs)}

    missing = [ref for ref in refs if ref not in await asyncio.to_thread(known)]
    if not missing:
        return
    driver.fold(resolutions=[{"ref": ref, "status": "cancelled"} for ref in missing])
    events = [{"ts": _now_iso(), "kind": "warn", "severity": "WARN", "event_name": "engine.ledger_order_dropped",
               "event_key": f"session:{session_id}:ledger:{ref}:dropped", "operation_id": f"session:{session_id}",
               "details": {"order_ref": ref, "verification": "server_command_state"},
               "message": "명령으로 저장되기 전에 서버가 다시 시작된 주문이라 보내지 않고 서버 장부에서 뺐어요."}
              for ref in missing]
    await asyncio.to_thread(_persist_fold, session_id, [], [], json.dumps(driver.state()), events)


def _amount(value: float) -> str:
    value = float(value or 0.0)
    text = f"{value:,.2f}" if abs(value) >= 1 else f"{value:.8f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _ledger_events(live: _Live, data: dict, result: dict) -> List[dict]:
    """장부를 바꾼 일을 실행 기록에 한 줄씩. 같은 체결 · 같은 정리는 한 번만 남는다(event_key)."""
    from .exchanges import quote_currency

    quote = quote_currency(live.exchange)
    sid = live.session_id
    seqs = {r["ref"]: r for r in data["resolutions"]}
    out = []

    def add(key: str, kind: str, severity: str, name: str, message: str, details: dict) -> None:
        out.append({"ts": _now_iso(), "kind": kind, "severity": severity, "event_name": name,
                    "event_key": f"session:{sid}:ledger:{key}", "operation_id": f"session:{sid}",
                    "details": {**details, "verification": "runner_reported_not_exchange_verified"}, "message": message})

    for ev in result.get("events") or []:
        kind = ev.get("kind")
        ref = str(ev.get("ref") or "")
        if kind == "entry_filled":
            if ev.get("late"):
                add(f"{ref}:late:{ev['qty']!r}", "warn", "WARN", "engine.ledger_late_fill",
                    f"정리한 주문의 매수 체결이 늦게 왔어요 — 서버 장부에 넣었어요 · {_amount(ev['qty'])} @ {_amount(ev['price'])} {quote}",
                    {"order_ref": ref, "qty": ev["qty"], "price": ev["price"]})
            else:
                add(f"{ref}:fill:{ev['qty']!r}", "info", "INFO", "engine.ledger_entry_filled",
                    f"실제 체결로 서버 장부를 맞췄어요 · {_amount(ev['qty'])} @ {_amount(ev['price'])} {quote}"
                    f" · 평단 {_amount(ev['avg_before'])} → {_amount(ev['avg_after'])} {quote}",
                    {"order_ref": ref, "virtual_price": ev.get("virtual_price"), "price": ev["price"],
                     "qty": ev["qty"], "fee": ev["fee"], "avg_before": ev["avg_before"], "avg_after": ev["avg_after"]})
        elif kind == "entry_unfilled":
            what = "첫 매수" if ev.get("order_kind") == "base" else f"물타기 {ev.get('tier')}차"
            hold = (seqs.get(ref) or {}).get("hold_until_ms")
            tail = f" · {_hold_phrase(hold, _now_ms(), '진입')}" if hold else ""
            if ev.get("order_type") == "limit_ioc" and ev.get("status") == "acked":
                # 지정가 IOC 무체결은 정상 결과 — 그 가격선에 다시 열어 두고 기다리지 않는다(설계 §6).
                add(f"{ref}:unfilled", "info", "INFO", "engine.ledger_entry_unfilled",
                    f"{what} — {_amount(ev.get('line') or 0.0)} {quote} 이하에 살 수 있는 물량이 없어 사지 않았어요"
                    " · 그 가격선에 다시 열어 둬요",
                    {"order_ref": ref, "status": ev.get("status"), "tier": ev.get("tier"), "order_type": "limit_ioc"})
            else:
                add(f"{ref}:unfilled", "warn", "WARN", "engine.ledger_entry_unfilled",
                    f"{what} 주문이 체결되지 않아 서버 장부에서 뺐어요{tail}",
                    {"order_ref": ref, "status": ev.get("status"), "tier": ev.get("tier")})
        elif kind == "entry_partial":
            add(f"{ref}:partial", "info", "INFO", "engine.ledger_entry_partial",
                f"물타기 {ev.get('tier')}차가 일부만 체결됐어요 · 남은 예산 {_amount(ev['remaining'])} {quote}은"
                f" {_amount(ev['line'])} {quote} 이하에서만 다시 사요",
                {"order_ref": ref, "tier": ev.get("tier"), "line": ev.get("line"), "remaining": ev.get("remaining")})
        elif kind == "exit_filled":
            source = "실행기 로컬 정리" if ev.get("source") == "local" else "청산"
            if ev.get("ended"):
                dust = (f" · 팔 수 없는 잔량 {_amount(ev['dust_qty'])}개는 다음 매수 평단에 합쳐요"
                        if ev.get("dust_qty") else "")
                message = (f"{source} 체결로 포지션을 정리했어요 · {_amount(ev['qty'])} @ {_amount(ev['price'])} {quote}"
                           f" · 실현손익 {'+' if ev['pnl'] > 0 else ''}{_amount(ev['pnl'])} {quote}{dust}")
            else:
                message = (f"{source}이 일부 체결됐어요 · {_amount(ev['qty'])} @ {_amount(ev['price'])} {quote}"
                           f" · 남은 {_amount(ev['left'])}개")
            add(f"exit:{ref}:{ev['qty']!r}:{ev['price']!r}", "info", "INFO", "engine.ledger_exit_filled", message,
                {"order_ref": ref, "qty": ev["qty"], "price": ev["price"], "pnl": ev["pnl"], "ended": ev["ended"],
                 "dust_qty": ev.get("dust_qty", 0.0)})
        elif kind in ("exit_unfilled", "exit_partial"):
            limit = ev.get("order_type") == "limit_ioc"
            if limit and kind == "exit_unfilled":
                # 지정가 IOC 무체결은 정상 결과 — 익절선보다 싸게 팔지 않았다(세션 321 의 꼬리 매도를 막는 자리).
                message = (f"지정가 IOC — {_amount(ev.get('limit_price') or 0.0)} {quote} 이상에 팔 수 있는 상대가 없어"
                           " 팔지 않았어요 · 보유 그대로 · 다음 봉에서 다시 판단해요")
            elif limit:
                message = (f"지정가 IOC 로 일부만 팔렸어요 · 남은 {_amount(ev['left'])}개는 다음 봉에서 다시 판단해요")
            elif kind == "exit_unfilled":
                message = "청산 주문이 체결되지 않아 보유를 그대로 둬요 · 다음 봉에서 다시 판단해요"
            else:
                message = f"청산 주문이 일부만 체결되고 끝났어요 · 남은 {_amount(ev['left'])}개는 다음 봉에서 다시 판단해요"
            add(f"{ref}:{kind}", "info" if limit else "warn", "INFO" if limit else "WARN", f"engine.ledger_{kind}",
                message, {"order_ref": ref, "status": ev.get("status"), "sold": ev.get("sold"), "left": ev.get("left"),
                          "order_type": ev.get("order_type") or ""})
        elif kind == "sell_without_position":
            add(f"orphan-sell:{ref}:{ev['qty']!r}", "warn", "WARN", "engine.ledger_sell_without_position",
                f"서버 장부에 보유가 없는데 매도 체결 보고가 왔어요 · {_amount(ev['qty'])} @ {_amount(ev['price'])} {quote}"
                " — 거래소 보유를 확인해 주세요.", {"order_ref": ref, "qty": ev["qty"], "price": ev["price"]})
    return out


def _runner_is_stale(row: RunSession, now: datetime) -> bool:
    """마지막 heartbeat(없으면 시작 시각)가 STALE_RUNNER_SECONDS 보다 오래됐는가. 시각을 못 읽으면 False(보수적)."""
    from .runner import _parse_iso  # 늦은 import: runner ↔ runner_engine 순환 방지

    last = _parse_iso(row.last_heartbeat_at or "") or _parse_iso(row.started_at or "")
    return last is not None and (now - last).total_seconds() > STALE_RUNNER_SECONDS


def _persist_state(live: _Live, state_json: str) -> None:
    """체크포인트. 실행기 heartbeat 가 오래 끊긴 세션은 여기서 닫고(stopped) 루프도 멈춘다."""
    from .runner import _append_events, notify_sessions_changed  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        row.state_json = state_json
        now = datetime.now(timezone.utc)
        if _runner_is_stale(row, now):
            row.status = "stopped"
            row.stopped_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            row.note = STALE_RUNNER_NOTE
            db.add(row)
            _append_events(db, row, [{"ts": row.stopped_at, "kind": "stop",
                                      "severity": "WARN", "event_name": "engine.runner_stale",
                                      "event_key": f"session:{row.id}:engine.runner_stale",
                                      "operation_id": f"session:{row.id}",
                                      "details": {"reason": STALE_RUNNER_NOTE, "in_position": row.in_position,
                                                  "last_heartbeat_at": row.last_heartbeat_at},
                                      "message": f"종료 · {STALE_RUNNER_NOTE}"
                                                 + f" · 마지막 heartbeat {row.last_heartbeat_at or '없음'}"
                                                 + (" · 포지션 보유 중" if row.in_position else "")}], component="engine")
            db.commit()
            log.warning("runner engine: session %s runner stale (last heartbeat %s) — stopped session and driver",
                        live.session_id, row.last_heartbeat_at)
            live.stop_flag = True  # 루프의 finally 가 등록·구독을 정리한다
            user_id = row.user_id
        else:
            db.add(row)
            db.commit()
            return
    try:
        notify_sessions_changed(user_id)
    except Exception:
        log.exception("runner engine: session %s stale-stop notify failed", live.session_id)


def _rollback_json(driver: StrategyDriver, fill: Fill) -> str:
    """진입 체결이면 그 직전 상태(JSON). 청산이거나 직전 상태를 못 잡았으면 빈 값 — 되돌리지 않는다."""
    if fill.side not in ENTRY_SIDES or driver.last_fill_snapshot is None:
        return ""
    return json.dumps(driver.last_fill_snapshot)


def _persist_fill(live: _Live, fill: Fill, state_json: str, rollback_json: str = "") -> None:
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    with get_session() as db:
        row = db.get(RunSession, live.session_id)
        if row is None or row.status != "running":
            live.stop_flag = True
            return
        cmd, created = upsert_command(db, row, command_from_fill(fill, live.driver.initial), _now_ms(),
                                      rollback_state=rollback_json)
        row.state_json = state_json
        db.add(row)
        if created:  # 중복(다른 프로세스가 이미 같은 명령을 남김)이면 신호 이벤트도 다시 쓰지 않는다
            _append_events(db, row, [_command_event(
                cmd, "command.created", "signal", "INFO",
                f"[신호 #{cmd.seq}] {cmd.reason} → {cmd.action.upper()} @ {fill.price:g}",
                verification="server_signal_not_exchange_order",
            )], component="engine")
        db.commit()


# --- Fill → 명령 -----------------------------------------------------------
def command_from_fill(fill: Fill, initial: float) -> dict:
    """진입은 초기자본 대비 금액 비율, 청산은 직전 보유 수량 대비 비율(모르면 전량). 주문 번호는 체결 기준 장부 세션만."""
    ref = getattr(fill, "order_ref", "") or ""
    limit = {"order_type": getattr(fill, "order_type", "") or "",
             "limit_price": float(getattr(fill, "limit_price", 0.0) or 0.0)}
    if fill.side in EXIT_SIDES:
        frac = 1.0 if fill.qty_before <= 0 else min(1.0, fill.qty / fill.qty_before)
        if frac >= 0.999:
            frac = 1.0
        return {"action": fill.side, "notional_frac": 0.0, "qty_frac": float(frac),
                "signal_price": float(fill.price), "reason": fill.reason or "청산", "order_ref": ref, **limit}
    notional = fill.qty * fill.price
    return {"action": fill.side, "notional_frac": float(notional / initial) if initial > 0 else 0.0, "qty_frac": 0.0,
            "signal_price": float(fill.price), "reason": fill.reason or "진입", "order_ref": ref, **limit}


def upsert_command(db, row: RunSession, cmd: dict, now_ms: int, *, rollback_state: str = "") -> tuple[RunnerCommand, bool]:
    """세션 안에서 seq 를 이어 pending 명령을 넣는다(flush 만, 커밋은 호출자). ``(행, 새로 넣었는지)``.

    직전 명령이 같은 action·signal_price 이고 COMMAND_DEDUPE_SECONDS 안에 만들어졌으며 아직 살아 있으면(pending/acked)
    같은 신호의 중복으로 보고 그 행을 그대로 돌려준다(롤링 배포 겹침 대비). failed/expired 는 다시 넣는다.
    체결 기준 장부 세션은 주문 번호까지 같아야 중복이다 — 물타기 잔예산 재매수는 같은 가격선에서 60초 안에도 나온다.
    """
    ref = str(cmd.get("order_ref") or "")[:32]
    last = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id).order_by(RunnerCommand.seq.desc())).first()
    if (
        last is not None
        and last.action == cmd["action"]
        and float(last.signal_price) == float(cmd["signal_price"])
        and (last.order_ref or "") == ref
        and last.status in ("pending", "acked")
        and 0 <= now_ms - int(last.created_ms) <= int(COMMAND_DEDUPE_SECONDS * 1000)
    ):
        log.warning("runner engine: session %s duplicate %s @ %s within %ss — reusing command #%s",
                    row.id, cmd["action"], cmd["signal_price"], COMMAND_DEDUPE_SECONDS, last.seq)
        return last, False
    item = RunnerCommand(session_id=row.id, seq=int(last.seq if last is not None else 0) + 1, action=cmd["action"],
                         notional_frac=cmd["notional_frac"], qty_frac=cmd["qty_frac"], signal_price=cmd["signal_price"],
                         reason=str(cmd["reason"])[:200], status="pending", created_at=_now_iso(), created_ms=now_ms,
                         expires_ms=now_ms + int(COMMAND_TTL_SECONDS * 1000),
                         rollback_state=rollback_state if cmd["action"] in ENTRY_SIDES else "", order_ref=ref,
                         order_type=str(cmd.get("order_type") or "")[:16],
                         limit_price=float(cmd.get("limit_price") or 0.0) if cmd.get("order_type") else 0.0)
    db.add(item)
    db.flush()
    return item, True


def insert_command(db, row: RunSession, cmd: dict, now_ms: int) -> RunnerCommand:
    """``upsert_command`` 의 행만 — 중복이면 기존 행이 돌아온다(호출자는 새 행이라고 가정하면 안 된다)."""
    return upsert_command(db, row, cmd, now_ms)[0]


def _view(c: RunnerCommand) -> dict:
    # expires_ms 는 응답 형식 유지를 위해 항상 실린다. 서버는 진입(buy/short)만 이 시각에 만료시키고 청산(sell/cover)은
    # ack 될 때까지 pending 으로 둔다 — 실행기 쪽에서는 청산의 expires_ms 를 만료 근거로 쓰면 안 된다.
    view = {"id": c.id, "seq": c.seq, "action": c.action, "notional_frac": c.notional_frac, "qty_frac": c.qty_frac,
            "signal_price": c.signal_price, "reason": c.reason, "expires_ms": c.expires_ms}
    if c.order_type:
        # 지정가 IOC(체결 기준 장부 세션의 익절 · 물타기) — 정책 세션은 레벨 21 실행기로만 생기니 받는 쪽도 레벨 21+.
        view.update(order_type=c.order_type, limit_price=c.limit_price)
    return view


def _command_event(cmd: RunnerCommand, event_name: str, kind: str, severity: str,
                   message: str, *, verification="runner_reported_not_exchange_verified") -> dict:
    """Capture this stage NOW; later mutations of RunnerCommand cannot rewrite it."""
    details = {
        "command_id": cmd.id, "command_seq": cmd.seq, "action": cmd.action,
        "status": cmd.status, "attempts": cmd.attempts, "signal_price": cmd.signal_price,
        "notional_frac": cmd.notional_frac, "qty_frac": cmd.qty_frac, "reason": cmd.reason,
        "expires_ms": cmd.expires_ms,
        "ack_source": "runner" if event_name in {"command.execution_reported", "command.failed"} else "engine",
        "verification": verification,
    }
    if event_name in {"command.execution_reported", "command.failed", "command.retry_scheduled"}:
        details.update(executed_qty=cmd.executed_qty, fill_price=cmd.fill_price, error=cmd.error)
    return {
        "ts": cmd.created_at if event_name == "command.created" else (cmd.acked_at or _now_iso()),
        "kind": kind, "severity": severity, "event_name": event_name,
        "operation_id": f"command:{cmd.id}",
        "event_key": f"command:{cmd.id}:{event_name}:{cmd.attempts}",
        "message": message,
        "details": details,
    }


def _execution_report_event(row: RunSession, cmd: RunnerCommand, ack: dict) -> dict:
    """An ok acknowledgement means handled, not necessarily a submitted/filled order.

    Old executables send zero/missing quantities for no-position and duplicate
    commands. Preserve acknowledgement/idempotency, but never invent a zero fill.
    Even positive facts remain runner reports, not independent exchange checks.
    """
    outcome = ack.get("outcome")
    no_order = outcome in ("no_position_no_order", "duplicate_command_no_reexecution", "ioc_unfilled")
    reported_fill = not no_order and all(math.isfinite(value) and value > 0
                                         for value in (cmd.executed_qty, cmd.fill_price))
    mode = row.mode if row.mode in {"mock", "testnet", "live"} else ""
    mode_label = {"mock": "모의", "testnet": "테스트넷", "live": "실전"}.get(mode, "모드 미확인")
    if outcome == "no_position_no_order":
        summary = "명령 처리 확인 · 보유 없음, 주문 없음"
    elif outcome == "duplicate_command_no_reexecution":
        summary = "명령 처리 확인 · 이미 처리한 명령, 재실행 없음"
    elif outcome == "ioc_unfilled":
        # 지정가 IOC 가 그 가격보다 유리한 상대를 못 만나 체결 없이 끝났다 — 정상 결과(재시도 · 시장가 전환 없음).
        summary = f"지정가 IOC {cmd.limit_price:g} · 조건에 맞는 상대가 없어 체결 없음"
    elif reported_fill:
        label = "체결 보고" if mode in {"mock", "testnet"} else "체결 수량·가격 보고"
        if outcome == "ioc_partial":
            label = f"지정가 IOC {cmd.limit_price:g} 일부 체결"
        else:
            outcome = "execution_reported"
        summary = f"{label} · {cmd.action.upper()} {cmd.executed_qty:g} @ {cmd.fill_price:g}"
    else:
        outcome = "acknowledged_execution_unknown"
        summary = "명령 처리 확인 · 체결 수량·가격 확인 불가"
    event = _command_event(cmd, "command.execution_reported", "order" if reported_fill else "info", "INFO",
                           f"[명령 #{cmd.seq}] 실행기 {mode_label} {summary} · {cmd.reason}")
    event["details"].update(outcome=outcome, mode=mode)
    if not reported_fill:
        for field in ("executed_qty", "fill_price"):
            value = event["details"][field]
            if no_order or not math.isfinite(value) or value <= 0:
                event["details"].pop(field, None)
    return event


def _pending_rows(db, row: RunSession) -> List[RunnerCommand]:
    return list(db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id, RunnerCommand.status == "pending")
                        .order_by(RunnerCommand.seq.asc())).all())


def _expire_entries(db, rows: List[RunnerCommand], now_ms: int) -> List[RunnerCommand]:
    """TTL 지난 pending **진입** 을 expired 로 바꾸고 그 행들을 돌려준다(청산은 만료시키지 않는다)."""
    expired = []
    for c in rows:
        if c.status == "pending" and c.action not in EXIT_SIDES and c.expires_ms <= now_ms:
            c.status = "expired"
            db.add(c)
            from .runner import _append_events
            event = _command_event(c, "command.expired", "warn", "WARN",
                                   f"[명령 #{c.seq}] 유효시간 만료 · {c.action.upper()} 진입 명령을 더 이상 전달하지 않습니다.",
                                   verification="server_command_state")
            # Expiry is observed here, not at a previous runner acknowledgement.
            event["ts"] = _now_iso()
            event["details"]["ack_source"] = "engine"
            owner = db.get(RunSession, c.session_id)
            if owner is not None:
                _append_events(db, owner, [event], component="engine")
            expired.append(c)
    return expired


def _snapshot_of(c: RunnerCommand) -> Optional[dict]:
    """진입 명령에 저장한 신호 직전 상태. 없거나(옛 명령) 이미 썼으면 None."""
    raw = c.rollback_state or ""
    if not raw.startswith("{"):
        return None
    try:
        snap = json.loads(raw)
    except ValueError:
        return None
    return snap if isinstance(snap, dict) and isinstance(snap.get("state"), dict) else None


def _opens_from_flat(c: RunnerCommand) -> bool:
    """서버 전략이 비어 있다가 여는 진입인가(추가매수가 아니라). 직전 상태가 없으면 모른다 — False."""
    snap = _snapshot_of(c)
    return snap is not None and not bool(snap["state"].get("in_position"))


def _grid_lot_of(c: RunnerCommand) -> Optional[dict]:
    """그리드 칸 매수면 그 칸 롯 정보(칸끼리 독립 — 그 칸만 되돌린다). 아니면 None."""
    snap = _snapshot_of(c)
    lot = snap.get("grid_lot") if snap else None
    return lot if isinstance(lot, dict) else None


def _deliverable(row: RunSession, rows: List[RunnerCommand]) -> List[RunnerCommand]:
    """이번 heartbeat 에 실행기로 보낼 명령 — seq 순서 그대로, **진입은 한 번에 하나**(그리드 칸 매수는 예외).

    실행기는 받은 명령을 순서대로 실행만 한다. 그래서 진입 하나를 보내면 그 결과(ack)를 본 다음 heartbeat 에
    그 뒤 명령을 보낸다(진입마다 ≈3초 늦을 뿐). 앞 진입이 실패했는데 뒤 진입 · 청산이 이미 실행돼 있으면 서버 전략을
    그 진입 직전으로 되돌릴 수 없기 때문이다(뒤 명령은 앞 진입이 체결된 것으로 보고 만든 신호다).
    진입은 다음 두 경우에도 붙잡아 둔다(TTL 이 지나면 만료 → 되돌리기 판단):
      1) 앞선 청산이 아직 끝나지 않았다 — 청산이 실패해도 진입이 나가 남은 포지션 위에 얹힌다.
      2) 비어 있는 서버 전략이 여는 진입인데 실행기는 포지션을 들고 있다 — 청산이 끝내 실패했거나 둘이 어긋났다.
    붙잡은 진입 뒤의 명령도 함께 기다린다 — 뒤의 청산이 먼저 나가면 순서가 뒤바뀌어(청산 → 진입) 실행기만 포지션이 남는다.
    그리드 칸 매수는 서로 독립이라(실패하면 그 칸만 뺀다) 이어서 보낸다 — 큰 봉에 수십 칸을 사야 할 때 하나씩 보내면
    뒤 칸이 90초 안에 못 나가 만료된다.
    """
    out: List[RunnerCommand] = []
    exit_ahead = False
    for c in rows:
        if c.status != "pending":
            continue
        if c.action in EXIT_SIDES:
            out.append(c)
            exit_ahead = True
            continue
        if exit_ahead or (row.in_position and _opens_from_flat(c)):
            break
        out.append(c)
        if _grid_lot_of(c) is None:
            break
    return out


def pending_commands(db, row: RunSession, now_ms: int) -> List[dict]:
    """TTL 지난 pending **진입** 은 expired 로 바꾸고, 지금 보낼 pending 을 seq 순으로 응답용 dict 로 돌려준다.

    청산은 만료시키지 않는다 — 늦더라도 실제 포지션을 닫아야 하며, 만료로 버리면 실행기에 포지션이 남는다.
    보낼 순서 규칙은 ``_deliverable``.
    """
    rows = _pending_rows(db, row)
    _expire_entries(db, rows, now_ms)
    return [_view(c) for c in _deliverable(row, rows)]


def exchange_commands(db, row: RunSession, acks: list, now_ms: int, *, previous_qty: float,
                      local_fills=()) -> List[dict]:
    """heartbeat 한 번의 명령 주고받기: ack 반영 → 만료 → 주문이 안 나간 진입 되돌리기 → 보낼 명령.

    ``previous_qty`` 는 이번 스냅샷 **전** 실행기가 보고한 보유 수량 — 추가매수가 일부라도 체결됐는지 가린다.
    체결 기준 장부 세션은 되돌리지 않는다 — ``_exchange_ledger_commands``.
    """
    changed = apply_acks(db, row, acks, now_ms)
    if row.exec_policy == FILL_LEDGER_POLICY:
        return _exchange_ledger_commands(db, row, local_fills, now_ms)
    rows = _pending_rows(db, row)
    expired = _expire_entries(db, rows, now_ms)
    settle_unexecuted_entries(db, row, changed, expired, now_ms, previous_qty=previous_qty, pending=rows)
    floor = _retry_floor(row.id)
    if floor is not None:
        # 되돌리기를 다음 heartbeat 로 미뤘다 — 그 뒤 명령이 그사이 실행되면 되돌릴 수 없게 된다. 끝날 때까지 붙잡는다.
        rows = [c for c in rows if c.seq < floor]
    return [_view(c) for c in _deliverable(row, rows)]


def _exchange_ledger_commands(db, row: RunSession, local_fills, now_ms: int) -> List[dict]:
    """체결 기준 장부 세션: 로컬 체결 넣기 → (새로 들어왔으면) 그 포지션을 전제로 만든 대기 명령 취소 → 진입 만료 → 보낼 명령.

    장부는 서버 전략 루프가 체결 기록표를 접어 맞춘다 — 여기서 서버 전략을 되돌리지 않는다(옛 settle_unexecuted_entries 없음).
    로컬 손절 · 안전 규칙 정리가 팔았으면 그 포지션에 얹을 물타기 · 재진입은 보내지 않는다. 실행기가 무포지션이라고 하면
    남은 익절 · 청산도 취소한다(팔 것이 없다). 같은 보고가 다시 와 새 행이 없으면 그 뒤에 생긴 명령은 건드리지 않는다.
    """
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    added = runner_fills.record_local_fills(db, row, list(local_fills or []), now_ms)
    rows = _pending_rows(db, row)
    if added:
        flat = not row.in_position and not row.position_uncertain
        events = []
        for c in rows:
            if c.action in ENTRY_SIDES or (flat and c.action in EXIT_SIDES):
                c.status = "cancelled"
                db.add(c)
                event = _command_event(c, "command.cancelled", "warn", "WARN",
                                       f"[명령 #{c.seq}] 실행기가 혼자 정리해서(로컬 손절 · 안전 규칙) 이 {c.action.upper()} "
                                       "명령은 보내지 않아요.", verification="server_command_state")
                event["ts"] = _now_iso()
                events.append(event)
        rows = [c for c in rows if c.status == "pending"]
        if events:
            db.add(row)
            _append_events(db, row, events, component="engine")
    _expire_entries(db, rows, now_ms)
    return [_view(c) for c in _deliverable(row, rows)]


def apply_acks(db, row: RunSession, acks: list, now_ms: int) -> List[RunnerCommand]:
    """실행기의 실행 결과 보고. 성공은 acked, 청산 실패는 EXIT_RETRY_MAX 까지 재시도, 진입 실패는 바로 failed.

    상태가 바뀐 명령을 돌려준다 — 진입 실패의 되돌리기 판단(``settle_unexecuted_entries``)이 쓴다.
    """
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    changed: List[RunnerCommand] = []
    if not isinstance(acks, list):
        return changed
    events = []
    # 체결 기준 장부 세션은 주문 사실을 체결 기록표에 한 번씩 넣고, 명령의 체결 수량 · 가격은 그 행들의 합 · 가중평균이다
    # (부분 청산을 다시 보내 두 번에 팔아도 앞 체결을 잃지 않는다). 장부에 접는 일은 서버 전략 루프가 한다.
    ledger = row.exec_policy == FILL_LEDGER_POLICY
    ignored = 0
    for ack in acks[:100]:
        if not isinstance(ack, dict):
            continue
        try:
            cmd = db.get(RunnerCommand, int(ack.get("command_id")))
        except (TypeError, ValueError):
            continue
        if cmd is None or cmd.session_id != row.id:
            continue
        ok = bool(ack.get("ok"))
        if cmd.status == "expired" and not ok:
            continue  # 만료된 명령의 실패 보고는 의미 없다(이미 버린 신호)
        late = cmd.status == "cancelled" or cmd.rollback_state in (ROLLED_BACK, ROLLED_BACK_ALSO)
        if cmd.status not in ("pending", "expired") and not (cmd.status == "cancelled" and ok):
            continue
        # expired + ok: 실행기가 만료 직전에 이미 실행한 경우 — 실제 주문이 나갔으니 acked 로 기록한다.
        cmd.attempts += 1
        cmd.acked_at = _now_iso()
        if ledger:
            ignored += runner_fills.record_command_orders(db, row, cmd, ack, now_ms)["ignored"]
        else:
            cmd.executed_qty = float(ack.get("executed_qty") or 0.0)
            cmd.fill_price = float(ack.get("fill_price") or 0.0)
        cmd.error = str(ack.get("error") or "")[:200]
        if ok:
            cmd.status = "acked"
            if cmd.action in EXIT_SIDES and row.note == EXIT_FAIL_NOTE:
                row.note = ""  # 재시도 끝에 청산이 됐다 — 실패 메모는 더 이상 사실이 아니다
            events.append(_execution_report_event(row, cmd, ack))
            if late and cmd.executed_qty > 0 and ledger:
                # 체결 기준 장부는 늦은 체결도 장부에 넣는다 — 실행기가 산 · 판 것은 그대로 사실이다.
                events.append(_engine_rollback_event(
                    cmd, "engine.late_execution_reported", "warn", "WARN",
                    f"취소한 주문 #{cmd.seq} 이 실제로 체결됐다는 보고가 왔어요 — 서버 장부에 넣었어요.",
                    executed_qty=cmd.executed_qty, fill_price=cmd.fill_price))
            elif late and cmd.executed_qty > 0:
                # 실행기는 받은 명령을 다음 heartbeat 에 꼭 보고하므로 일어나면 안 되는 일이다 — 그래도 났다면 숨기지 않는다.
                events.append(_engine_rollback_event(
                    cmd, "engine.late_execution_reported", "error", "ERROR",
                    f"⚠ 취소한 주문 #{cmd.seq} 이 실제로 체결됐다는 보고가 왔어요 — 서버 전략은 이 주문을 모릅니다."
                    " 거래소 포지션을 확인해 주세요.",
                    executed_qty=cmd.executed_qty, fill_price=cmd.fill_price))
        elif cmd.action in EXIT_SIDES and cmd.attempts < EXIT_RETRY_MAX:
            cmd.status = "pending"
            cmd.expires_ms = now_ms + int(COMMAND_TTL_SECONDS * 1000)
            row.note = EXIT_FAIL_NOTE
            events.append(_command_event(
                cmd, "command.failed", "error", "ERROR",
                f"⚠ 청산 주문 실패({cmd.attempts}/{EXIT_RETRY_MAX}) — 다시 시도합니다 · {cmd.error}",
            ))
            events.append(_command_event(
                cmd, "command.retry_scheduled", "warn", "WARN",
                f"[명령 #{cmd.seq}] 청산 재시도 예약 · 실패 {cmd.attempts}/{EXIT_RETRY_MAX}회 · 다음 heartbeat에서 전달",
                verification="server_command_state",
            ))
            events[-1]["details"]["ack_source"] = "engine"
        elif cmd.action in EXIT_SIDES:
            cmd.status = "failed"
            row.note = EXIT_FAIL_NOTE
            events.append(_command_event(
                cmd, "command.failed", "error", "ERROR",
                f"⚠ 청산 주문이 {EXIT_RETRY_MAX}회 실패했어요 — 거래소에서 포지션을 확인해 주세요 · {cmd.error}",
            ))
        else:
            cmd.status = "failed"
            events.append(_command_event(
                cmd, "command.failed", "error", "ERROR",
                f"⚠ 진입 실패 · {cmd.error}",
            ))
        db.add(cmd)
        changed.append(cmd)
    if ignored:
        events.append({"ts": _now_iso(), "kind": "warn", "severity": "WARN", "event_name": "engine.fill_report_ignored",
                       "operation_id": f"session:{row.id}",
                       "details": {"count": ignored, "verification": "runner_reported_not_exchange_verified"},
                       "message": f"실행기 체결 보고 {ignored}건을 읽지 못해 서버 장부에 넣지 않았어요(키 · 수량 · 가격 확인 필요)."})
    if events:
        db.add(row)
        _append_events(db, row, events, component="engine")
    return changed


# --- 주문이 안 나간 진입 되돌리기 ------------------------------------------
# 서버 전략(sim)은 신호를 내는 순간 체결한 것으로 장부를 바꾼다. 실행기가 그 주문을 못 내면(잔고 부족 · 최소 금액 ·
# 키 권한 · 로컬 리스크 보류 = 실패, 실행기가 꺼져 있어 못 가져감 = 만료) 서버만 '보유 중' 이 되어, 다시 사지 않고
# 언젠가 없는 물량을 팔라고 한다. 그래서 진입 명령마다 신호 직전 상태를 저장해 두고, 주문이 안 나간 게 확실할 때
# 그 상태로 되돌린다. 확실하지 않으면 되돌리지 않는다 — 틀리게 되돌리면 이번엔 실제 포지션을 서버가 모른다.
def _qty_accounted(row: RunSession, previous_qty: float, changed: List[RunnerCommand]) -> bool:
    """이번 보고에 체결됐다고 온 명령만으로 실행기 보유 수량 변화가 설명되는가.

    실행기는 진입 주문이 일부만 체결되고 취소되면 '실패' 로 보고하면서도 보유 수량은 늘려 둔다 — 그 경우 설명되지
    않는 수량이 남는다. 매수 수수료를 코인에서 떼는 거래소(바이낸스 현물 0.1%)는 체결 수량보다 덜 늘어나므로 그만큼 여유를 둔다.
    """
    bought = sum(float(c.executed_qty or 0.0) for c in changed if c.status == "acked" and c.action in ENTRY_SIDES)
    sold = sum(float(c.executed_qty or 0.0) for c in changed if c.status == "acked" and c.action in EXIT_SIDES)
    expected = float(previous_qty or 0.0) + bought - sold
    slack = max(1e-12, abs(expected) * 1e-9, bought * 0.002)
    return abs(float(row.position_qty or 0.0) - expected) <= slack


def _retry_minutes(db, row: RunSession) -> int:
    """이번 되돌리기 뒤 다시 진입하기까지 기다릴 분. 마지막으로 체결된 진입 뒤 되돌린 횟수로 늘린다."""
    marks = db.exec(select(RunnerCommand.status, RunnerCommand.executed_qty,
                           (RunnerCommand.rollback_state == ROLLED_BACK).label("rolled_back"))
                    .where(RunnerCommand.session_id == row.id, RunnerCommand.action.in_(ENTRY_SIDES))
                    .order_by(RunnerCommand.seq.desc()).limit(50)).all()
    streak = 0
    for status, executed_qty, rolled_back in marks:
        if status == "acked" and float(executed_qty or 0.0) > 0:
            break
        if rolled_back:
            streak += 1
    return RETRY_BACKOFF_MINUTES[min(streak, len(RETRY_BACKOFF_MINUTES) - 1)]


_REFUSAL_MESSAGES = {
    "runner_holds": "실행기가 이미 포지션을 들고 있어 서버 전략을 되돌리지 않았어요",
    "runner_changed": "실행기 보유 수량이 체결 보고와 맞지 않아(일부 체결됐을 수 있어요) 서버 전략을 되돌리지 않았어요",
    "later_executed": "뒤이은 주문이 이미 체결돼 서버 전략을 되돌리지 않았어요",
    "busy": "서버 전략이 그사이 다음 신호로 넘어가 되돌리지 못했어요",
    "lot_gone": "그 그리드 칸은 서버 전략에서 이미 팔린 뒤라 되돌리지 못했어요",
}
# 일시적으로 못 되돌린 진입(드라이버가 그 순간 다음 체결을 저장하는 중 · 루프가 바쁨) — 세션 id → {명령 id: (시도 횟수, seq)}.
# 다음 heartbeat 들이 다시 시도하고, 그동안 그 명령 뒤의 명령은 보내지 않는다(_retry_floor). 프로세스 메모리라
# 재기동하면 잊는다(그때는 불일치 경고가 남는다).
_ROLLBACK_RETRY: Dict[int, Dict[int, tuple]] = {}


def _retry_floor(session_id: int) -> Optional[int]:
    """다시 시도할 되돌리기 중 가장 이른 명령의 seq — 그보다 뒤의 명령은 되돌리기가 끝날 때까지 보내지 않는다."""
    retry = _ROLLBACK_RETRY.get(session_id)
    return min(seq for _attempts, seq in retry.values()) if retry else None


def _engine_rollback_event(cmd: RunnerCommand, event_name: str, kind: str, severity: str, message: str,
                           **details) -> dict:
    """되돌리기 판단 기록 — 서버(엔진)가 정한 사실이다. 같은 명령의 같은 판단은 한 줄(저장 실패 뒤 재시도에도)."""
    return {"ts": _now_iso(), "kind": kind, "severity": severity, "event_name": event_name,
            "operation_id": f"command:{cmd.id}", "event_key": f"command:{cmd.id}:{event_name}",
            "message": message,
            "details": {"command_id": cmd.id, "command_seq": cmd.seq, "action": cmd.action, "status": cmd.status,
                        "signal_price": cmd.signal_price, "ack_source": "engine",
                        "verification": "server_command_state", **details}}


def _held_until(state_json: str, fallback: int) -> int:
    """기록에 남길 대기 종료 시각 — 드라이버(state_json)에 실제로 걸린 값. 저장 실패 뒤 같은 ack 가 다시 오면
    드라이버는 첫 시도 때의 대기를 갖고 있다(지금 시각으로 다시 계산하면 그만큼 늦게 적힌다)."""
    try:
        held = int((json.loads(state_json or "{}") or {}).get("entry_hold_until_ms") or 0)
    except (TypeError, ValueError):
        held = 0
    return held or fallback


def _hold_phrase(until_ms: int, now_ms: int, verb: str) -> str:
    """기록 문구의 대기 안내. 대기가 이미 끝났으면(늦게 온 같은 ack 등) 그렇다고 쓴다 — '1분 동안' 으로 꾸미지 않는다."""
    if until_ms <= now_ms:
        return "대기 시간은 이미 지났어요"
    return f"{-(-(until_ms - now_ms) // 60_000)}분 동안은 새로 {verb}하지 않아요"


def _refusal(cmd: RunnerCommand, reason: str) -> dict:
    return _engine_rollback_event(cmd, "engine.rollback_refused", "warn", "WARN",
                                  f"⚠ 주문 #{cmd.seq} — {_REFUSAL_MESSAGES[reason]}. 거래소 포지션을 확인해 주세요.",
                                  reason=reason)


def _undo_lot_in_state(state: dict, info: dict) -> Optional[dict]:
    """드라이버가 없을 때(state_json 만 고친다) 그리드 칸 롯 하나를 뺀다. 그 롯이 없으면 None."""
    legs = state.get("legs") or []
    leg = next((item for item in legs if item.get("symbol") == info.get("symbol")), None) or (legs[0] if legs else None)
    grid = ((leg or {}).get("strategy") or {}).get("grid")
    if not grid:
        return None

    def same(row) -> bool:
        return (int(row.get("level", -1)) == int(info.get("level", -2))
                and abs(float(row.get("qty") or 0) - float(info.get("qty") or 0)) <= 1e-9 * max(1.0, float(info.get("qty") or 0))
                and abs(float(row.get("fill") or 0) - float(info.get("fill") or 0)) <= 1e-9 * max(1.0, float(info.get("fill") or 0)))

    keep = [row for row in grid if not same(row)]
    if len(keep) == len(grid):
        return None
    leg["strategy"]["grid"] = keep
    qty = sum(float(row["qty"]) for row in keep)
    leg["qty"], leg["in_position"] = qty, qty > 0
    leg["entry_price"] = sum(float(row["qty"]) * float(row["fill"]) for row in keep) / qty if qty > 0 else 0.0
    state["in_position"] = any(bool(item.get("in_position")) for item in legs)
    return state


def settle_unexecuted_entries(db, row: RunSession, changed: List[RunnerCommand], expired: List[RunnerCommand],
                              now_ms: int, *, previous_qty: float,
                              pending: Optional[List[RunnerCommand]] = None) -> Optional[RunnerCommand]:
    """주문이 나가지 않은 진입(이번 heartbeat 의 실패 · 만료, 앞서 일시적으로 못 되돌린 것)을 서버 전략에서도 되돌린다.

    · 그리드 칸 매수: 칸끼리 독립이라 **그 칸 롯만** 뺀다 — 다른 칸의 매수 · 매도와 뒤 명령은 그대로 둔다.
      실패 보고면 실행기 보유 수량이 이번 보고의 체결로 다 설명돼야 한다(실패한 주문이 일부라도 체결되지 않았다).
    · 그 밖의 진입: 그 진입 **직전 상태로** 되돌리고, 아직 안 보낸 뒤 명령은 취소한다(그 진입을 전제로 만든 신호다).
      조건(모두 만족할 때만 — 하나라도 불확실하면 되돌리지 않고 이벤트로 알린다):
        - 비어 있다가 여는 진입이면 실행기도 비어 있다 — 또는 들고 있는 포지션을 닫을 청산이 그 진입보다 **앞에**
          아직 실행 전으로 남아 있다(실행기가 꺼졌다 켜진 경우: 익절 청산 → 바로 재진입이 둘 다 밀렸다).
          추가매수면 실행기 보유 수량 변화가 이번 보고의 체결로 다 설명된다.
        - 그 뒤의 명령 중 실제로 체결된 것이 없다.
        - 루프의 드라이버가 그 사이 새 체결을 내지 않았다(체결 수가 명령 수와 맞는다). 아니면 다음 heartbeat 에 다시.
      가장 이른 후보부터 본다 — 앞의 것으로 되돌리면 뒤의 것까지 함께 지워진다.
    실행기가 포지션을 확신하지 못하면(position_uncertain) 아무것도 하지 않는다. 마지막으로 되돌린 명령을 돌려준다.
    ``pending`` 은 이번 heartbeat 가 읽은 pending 행(없으면 읽는다).
    """
    from .paper import parse_state  # 늦은 import: paper ↔ runner_engine 순환 방지
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    retry = dict(_ROLLBACK_RETRY.get(row.id) or {})
    retried = [db.get(RunnerCommand, cid) for cid in list(retry)]
    pool = {c.id: c for c in list(changed) + list(expired) + [c for c in retried if c is not None]
            if c.session_id == row.id and c.action in ENTRY_SIDES and c.status in ("failed", "expired")
            and _snapshot_of(c) is not None}
    for cid in list(retry):
        if cid not in pool:
            retry.pop(cid, None)  # 그사이 체결 보고가 왔거나 이미 되돌렸다 — 더 볼 것 없다
    candidates = sorted(pool.values(), key=lambda c: c.seq)
    if not candidates or row.position_uncertain:
        _keep_retry(row.id, retry)
        return None  # 불확실하면 실행기 스스로 '포지션 확인 필요' 로 멈춘다 — 서버가 짐작으로 고치지 않는다
    if pending is None:
        pending = _pending_rows(db, row)
    accounted = _qty_accounted(row, previous_qty, changed)
    later_rows = list(db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id,
                                                          RunnerCommand.seq > candidates[0].seq)
                              .order_by(RunnerCommand.seq.asc())).all())
    events: List[dict] = []
    refusals: List[tuple] = []
    grid_undone: List[RunnerCommand] = []
    rolled = None
    minutes = None
    grid_hold = 0

    def postpone(cmd: RunnerCommand) -> None:
        attempts = (retry.get(cmd.id) or (0, cmd.seq))[0] + 1
        if attempts >= ROLLBACK_RETRY_MAX:
            retry.pop(cmd.id, None)
            refusals.append((cmd, "busy"))
        else:
            retry[cmd.id] = (attempts, cmd.seq)

    for cmd in candidates:
        snap = _snapshot_of(cmd)
        if minutes is None:
            minutes = _retry_minutes(db, row)
        retry_at = now_ms + minutes * 60_000
        grid_lot = snap.get("grid_lot") if isinstance(snap.get("grid_lot"), dict) else None
        if grid_lot is not None:
            if cmd.status == "failed" and not accounted:
                retry.pop(cmd.id, None)
                refusals.append((cmd, "runner_changed"))
                continue
            outcome = undo_lot_driver(row.id, cmd.id, grid_lot, retry_at_ms=retry_at)
            if outcome is None:
                postpone(cmd)
                continue
            if outcome is False:
                retry.pop(cmd.id, None)
                refusals.append((cmd, "lot_gone"))
                continue
            if outcome == "":
                state = _undo_lot_in_state(parse_state(row.state_json) or {}, grid_lot)
                if state is None:
                    retry.pop(cmd.id, None)
                    refusals.append((cmd, "lot_gone"))
                    continue
                state["entry_hold_until_ms"] = max(int(state.get("entry_hold_until_ms") or 0), retry_at)
                outcome = json.dumps(state)
            retry.pop(cmd.id, None)
            row.state_json = outcome
            cmd.rollback_state = ROLLED_BACK_ALSO if (grid_undone or rolled) else ROLLED_BACK
            db.add(cmd)
            grid_undone.append(cmd)
            grid_hold = _held_until(outcome, retry_at)
            continue
        later = [c for c in later_rows if c.seq > cmd.seq]
        if not snap["state"].get("in_position"):
            exit_first = any(c.status == "pending" and c.action in EXIT_SIDES and c.seq < cmd.seq for c in pending)
            reason = "runner_holds" if row.in_position and not exit_first else None
        else:
            reason = None if row.in_position and accounted else "runner_changed"
        if reason is None and any(c.status == "acked" and float(c.executed_qty or 0.0) > 0 for c in later):
            reason = "later_executed"
        if reason is not None:
            retry.pop(cmd.id, None)
            refusals.append((cmd, reason))
            continue
        expected = int(snap.get("trade_count") or 0) + 1 + len(later)
        cancel_ids = [c.id for c in later if c.status == "pending"]
        outcome = rollback_driver(row.id, cmd.id, snap, expected_trade_count=expected, retry_at_ms=retry_at,
                                  cancel_ids=cancel_ids)
        if outcome is None:
            # 드라이버가 앞서 나갔으면 더 이른 상태로도 못 간다(기대 체결 수는 후보마다 같다). 다음 heartbeat 에 다시 —
            # 뒤 후보들도 함께 남긴다(이 후보가 끝내 안 되면 그다음 것을 본다).
            postpone(cmd)
            for c in candidates:
                if c.seq > cmd.seq and c.id not in retry:
                    retry[c.id] = (0, c.seq)
            break
        retry.clear()  # 앞 후보는 이미 거절로 빠졌고, 뒤 후보는 이 되돌리기에 함께 지워졌다
        state_json, to_cancel = outcome
        row.state_json = state_json or json.dumps({**snap["state"], "entry_hold_until_ms": retry_at})
        held = _held_until(row.state_json, retry_at)
        cmd.rollback_state = ROLLED_BACK
        db.add(cmd)
        for c in later:
            if c.id in to_cancel and c.status == "pending":
                c.status = "cancelled"  # 아직 안 보낸 명령 — 되돌린 시간선의 신호다
                db.add(c)
        why = ("진입 주문이 나가지 않아" if cmd.status == "failed"
               else f"진입 신호가 {COMMAND_TTL_SECONDS:g}초 안에 실행기에 전달되지 않아")
        where = "추가 진입 전으로" if snap["state"].get("in_position") else "진입 전으로"
        events.append(_engine_rollback_event(
            cmd, "engine.entry_rolled_back", "warn", "WARN",
            f"{why} 서버 전략도 {where} 되돌렸어요 · {_hold_phrase(held, now_ms, '진입')}",
            retry_at=held, count=len(to_cancel)))
        rolled = cmd
        break
    for cmd in grid_undone:
        # 칸(명령)마다 한 줄 — 명령별 검색(operation_id)에서 빠지지 않게. 알림 종류가 아니라 알림이 쌓이지 않는다.
        events.append(_engine_rollback_event(
            cmd, "engine.grid_lot_removed", "warn", "WARN",
            f"그리드 {cmd.signal_price:g} 칸 매수가 나가지 않아 그 칸만 서버 전략에서 뺐어요"
            f" · 다른 칸 체결은 그대로 · {_hold_phrase(grid_hold, now_ms, '매수')}",
            count=len(grid_undone), retry_at=grid_hold))
    if grid_undone:
        rolled = rolled or grid_undone[-1]
    _keep_retry(row.id, retry)
    if refusals:
        # 되돌리지 못한 사정은 모두 알린다 — 다른 후보로 되돌린 경우에도.
        events[:0] = [_refusal(cmd, reason) for cmd, reason in refusals[:5]]
    if events:
        db.add(row)
        _append_events(db, row, events, component="engine")  # 서버 판단 — 실행기 보고로 표시하지 않는다
    return rolled


def _keep_retry(session_id: int, retry: Dict[int, tuple]) -> None:
    if retry:
        _ROLLBACK_RETRY[session_id] = retry
    else:
        _ROLLBACK_RETRY.pop(session_id, None)


class _RollbackJob:
    """heartbeat 스레드가 루프 스레드에 맡기는 드라이버 작업 한 번(되돌리기 · 그리드 칸 빼기).

    드라이버는 루프 스레드만 만진다(틱 · 마감봉 콜백). 기다리다 시간이 넘으면 '아직 시작 안 했으면 하지 말라' 고
    표시하고 포기한다 — 시작했다면(중간에 await 가 없어 곧 끝난다) 결과를 끝까지 받는다. 그래야 heartbeat 의
    DB 기록(명령 취소 · state_json)과 드라이버가 어긋나지 않는다.
    """

    def __init__(self, fn, args: tuple) -> None:
        self.fn = fn
        self.args = args
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.claimed = False
        self.abandoned = False
        self.result = None

    def run(self) -> None:
        with self.lock:
            if self.abandoned:
                return
            self.claimed = True
        try:
            self.result = self.fn(*self.args)
        except Exception:
            log.exception("runner engine: session %s rollback failed", self.args[0])
            self.result = None
        finally:
            self.done.set()

    def wait(self, timeout: float):
        if self.done.wait(timeout):
            return self.result
        with self.lock:
            if not self.claimed:
                self.abandoned = True
                return None
        self.done.wait()
        return self.result


_ROLLED_BACK_MEMORY = 16  # 드라이버마다 기억하는 되돌리기 수 — 저장 실패 뒤 같은 ack 가 다시 오는 짧은 사이만 필요하다


def _remember(live: "_Live", command_id: int, cancel_ids: List[int]) -> None:
    live.rolled_back[command_id] = list(cancel_ids)
    while len(live.rolled_back) > _ROLLED_BACK_MEMORY:
        live.rolled_back.pop(next(iter(live.rolled_back)))


def _apply_rollback(session_id: int, command_id: int, snapshot: dict, expected: int, retry_at_ms: int,
                    cancel_ids: List[int]):
    """(루프 스레드) 돌고 있는 드라이버를 되돌린다. 반환 ``(새 state_json, 취소할 명령 id)``.

    · 드라이버가 이 프로세스에 없으면 ``("", cancel_ids)`` — 호출자가 state_json 만 고친다(다음 시작이 거기서 이어 간다).
    · 이 명령으로 이미 되돌렸으면(앞 heartbeat 의 저장이 실패해 같은 ack 가 다시 왔다) 그때의 취소 목록과 지금 상태를
      돌려준다 — 그 뒤 새로 생긴 명령은 되돌린 시간선의 정상 신호라 취소하지 않는다.
    · 시작하는 중이거나 그 사이 새 체결이 났으면 None — 되돌리지 않는다.
    """
    if session_id in _starting:
        return None  # 웜업 중 — 시작이 이미 읽은 옛 state_json 으로 복구하므로 지금 고쳐도 덮인다
    live = _drivers.get(session_id)
    if live is None:
        return "", list(cancel_ids)
    done = live.rolled_back.get(command_id)
    if done is not None:
        return json.dumps(live.driver.state()), list(done)
    if live.stop_flag or live.unsaved or live.driver.trade_count != expected:
        return None
    live.driver.rollback(snapshot, retry_at_ms=retry_at_ms)
    _remember(live, command_id, cancel_ids)
    return json.dumps(live.driver.state()), list(cancel_ids)


def _apply_lot_undo(session_id: int, command_id: int, info: dict, retry_at_ms: int):
    """(루프 스레드) 그리드 칸 롯 하나를 뺀다. 새 state_json · 드라이버 없음 "" · 그 롯이 없음 False · 지금 못 함 None."""
    if session_id in _starting:
        return None
    live = _drivers.get(session_id)
    if live is None:
        return ""
    if command_id in live.rolled_back:
        return json.dumps(live.driver.state())  # 앞 heartbeat 의 저장이 실패해 같은 ack 가 다시 왔다
    if live.stop_flag:
        return None
    if not live.driver.undo_grid_lot(info, retry_at_ms=retry_at_ms):
        return False
    _remember(live, command_id, [])
    return json.dumps(live.driver.state())


def _in_loop_thread(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _run_on_loop(fn, args: tuple):
    """드라이버 작업을 루프 스레드에서 하고 결과를 돌려준다. 시간 안에 못 하면 None(하지도 않는다)."""
    loop = _loop
    if loop is None or loop.is_closed() or not loop.is_running() or _in_loop_thread(loop):
        # 루프가 없거나(테스트 · 스크립트) 이미 루프 스레드다 — 여기서 바로 해도 경합이 없다.
        return fn(*args)
    job = _RollbackJob(fn, args)
    try:
        loop.call_soon_threadsafe(job.run)
    except RuntimeError:  # 루프가 그 사이 닫혔다(종료 중)
        return None
    return job.wait(ROLLBACK_WAIT_SECONDS)


def rollback_driver(session_id: int, command_id: int, snapshot: dict, *, expected_trade_count: int, retry_at_ms: int,
                    cancel_ids: List[int]):
    """드라이버를 체결 직전 상태로 되돌린다(루프 스레드에서). 반환은 ``_apply_rollback`` 과 같다."""
    return _run_on_loop(_apply_rollback, (session_id, command_id, snapshot, expected_trade_count, retry_at_ms,
                                          list(cancel_ids)))


def undo_lot_driver(session_id: int, command_id: int, info: dict, *, retry_at_ms: int):
    """그리드 칸 롯 하나를 뺀다(루프 스레드에서). 반환은 ``_apply_lot_undo`` 와 같다."""
    return _run_on_loop(_apply_lot_undo, (session_id, command_id, info, retry_at_ms))


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
    if row.exec_policy == FILL_LEDGER_POLICY:
        _check_ledger_mismatch(db, row, state)
        return
    expected = bool(state.get("in_position"))
    if expected == bool(reported_in_position):
        _MISMATCH_NOTED.discard(row.id)
        return
    if row.id in _MISMATCH_NOTED:
        return
    _MISMATCH_NOTED.add(row.id)
    _append_events(db, row, [{"ts": _now_iso(), "kind": "warn",
                              "severity": "WARN", "event_name": "engine.position_mismatch",
                              "operation_id": f"session:{row.id}",
                              "details": {"server_in_position": expected,
                                          "runner_in_position": bool(reported_in_position),
                                          "verification": "runner_reported_not_exchange_verified"},
                              "message": ("서버 전략은 포지션 보유 중인데 실행기는 비어 있어요" if expected
                                          else "서버 전략은 비어 있는데 실행기는 포지션을 들고 있어요")
                                         + " — 거래소 포지션을 확인해 주세요."}], component="engine")


def _check_ledger_mismatch(db, row: RunSession, state: dict) -> None:
    """체결 기준 장부 세션: 서버 장부 보유(먼지 포함)와 실행기 보고 수량을 견준다. 다르면 한 번 경고.

    접지 않은 체결 행이 있거나 장부에 미결 주문이 있으면 아직 정리 중이라 견주지 않는다(반영 전 잠깐의 차이).
    """
    from .runner import _append_events  # 늦은 import: runner ↔ runner_engine 순환 방지

    legs = state.get("legs") or [{}]
    ledger = ((legs[0] or {}).get("strategy") or {}).get("ledger") or {}
    if ledger.get("orders") or runner_fills.has_unapplied(db, row.id):
        return
    server = sum(float(lot.get("qty") or 0.0) for lot in ledger.get("lots") or [])
    server += float((ledger.get("dust") or {}).get("qty") or 0.0)
    runner = float(row.position_qty or 0.0)
    if abs(server - runner) <= max(1e-9, max(server, runner) * 0.002):
        _MISMATCH_NOTED.discard(row.id)
        return
    if row.id in _MISMATCH_NOTED:
        return
    _MISMATCH_NOTED.add(row.id)
    _append_events(db, row, [{"ts": _now_iso(), "kind": "warn", "severity": "WARN", "event_name": "engine.ledger_mismatch",
                              "operation_id": f"session:{row.id}",
                              "details": {"server_qty": server, "runner_qty": runner,
                                          "verification": "runner_reported_not_exchange_verified"},
                              "message": f"서버 장부 수량과 실행기 보유 수량이 달라요 · 서버 {server:g} · 실행기 {runner:g}"
                                         " — 거래소 보유를 확인해 주세요."}], component="engine")


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
            # 여러 종목 세션은 시작을 **시도하지도** 않는다 — 관문이 생기기 전에 시작해 아직 running 인
            # 세션이 재기동마다 되살아나 첫 종목에 전액을 넣는다. 여기서 닫고 넘어간다.
            if await asyncio.to_thread(_is_portfolio_session, sid):
                await asyncio.to_thread(_refuse_portfolio_session, sid)
                continue
            if await start_driver(sid):
                count += 1
        except Exception:
            log.exception("runner engine resume: session %s failed", sid)
    if count:
        log.info("runner engine resume: revived %d session(s)", count)
    return count
