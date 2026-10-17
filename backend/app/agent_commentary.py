"""껄무새 중계 — 실행 중인 매크로 옆에서 '지금 무슨 일인지'를 채팅 말풍선으로 남긴다.

서버가 한 곳에서 만든다(agent_commentary_runtime 이 10초마다 부른다). 화면은 저장된 말풍선을 읽기만 한다 —
예전처럼 브라우저가 화면을 열 때마다 관측을 새로 만들어 저장하면 같은 '평가손실 주의'가 열 번 넘게 쌓였다.

규칙
- 말할 순간은 규칙이 정한다(체결 · 손실 구간 · 큰 움직임 · 변동성 · 진입 대기 · 커뮤니티 글 · 연결 · 종료).
  같은 일은 ``say_key`` 로 한 번만 남는다 — 두 프로세스가 겹쳐 돌아도 말풍선이 두 번 생기지 않는다.
- 말투는 첫 줄에만 싣고, 숫자 줄은 사실만 쓴다. 매크로가 사고판 결과를 사용자 후회('~할 껄')로 말하지 않고,
  사라 · 팔라고 권하지 않는다(껄무새는 투자 자문을 하지 않는다). 손실이 -5% 를 넘으면 농담을 줄인다.
- ``decide`` 는 DB · 네트워크 없이 상태와 입력만으로 말풍선을 고른다(테스트가 여기를 본다).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
import zlib
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlmodel import select

from .db import AgentCommentaryState, AgentUtterance, RunnerCommand, RunSession, RunSessionEvent, get_session

log = logging.getLogger(__name__)

STATE_VERSION = 1
UTTERANCE_CAP = 400          # 세션당 말풍선 상한(오래된 것부터 지운다)
FILL_BACKLOG = 20            # 처음 볼 때 지난 체결은 최근 이만큼만 말한다
LOSS_BANDS = (3, 5, 10, 15, 20, 30, 50)
GAIN_BANDS = (5, 10, 20, 30, 50)
MOVE_PCT = 3.0               # 5~10분 사이 이만큼 움직이면 한마디
MOVE_COOLDOWN_MS = 15 * 60_000
REBUY_WINDOW_MS = 120_000
SLIPPAGE_PCT = 1.0
VOL_RATIO = 3.0              # 최근 1시간 5분 봉 출렁임 평균 / 직전 보통 값
VOL_HIGH_RATIO = 5.0
VOL_SINGLE_PCT = 5.0         # 5분 봉 하나의 출렁임
VOL_SINGLE_HIGH_PCT = 8.0
VOL_MIN_AVG_PCT = 1.0        # 평소가 아주 잔잔한 코인에서 배수만 커지는 것은 무시
VOL_CALM_RATIO = 1.5
VOL_CALM_MS = 30 * 60_000
VOL_REPEAT_MS = 2 * 60 * 60_000  # 같은 단계는 2시간에 한 번만 다시 말한다
VOL_MIN_BASE_BARS = 100
POST_MAX_AGE_MS = 3 * 3_600_000
POST_BEFORE_START_MS = 30 * 60_000
# 커뮤니티 글은 AI 요약이 따로 만들어진다(보통 몇 분). 요약이 '만드는 중'이면 이만큼 기다렸다가
# 제목과 내용을 함께 말한다 — 바로 말하면 83%가 제목만 나갔다(운영 2026-10-17, 81개 중 67개).
POST_SUMMARY_WAIT_MS = 5 * 60_000
SAME_TITLE_WINDOW_MS = 6 * 3_600_000
SAME_NUMBERS_WINDOW_MS = 2 * 3_600_000  # 같은 숫자를 퍼 나른 글은 거르지 않고 "같은 숫자 글이 또 올라왔어요"로 알린다
OFFLINE_MS = 60_000
# 서버가 막 켜진 동안(배포 · 재시작)은 실행기 하트비트가 새 서버에 닿기 전이라 '끊겼다'고 하지 않는다 —
# 2026-10-17 첫 배포 때 실행 중이던 세션에 가짜 '끊겼어요 → 다시 연결됐어요'가 같은 분에 붙었다.
BOOT_GRACE_MS = 3 * 60_000
_PROCESS_STARTED_MS = int(time.time() * 1000)
ONLINE_MS = 30_000
QUIET_MS = 45 * 60_000
ERROR_REPEAT_MS = 10 * 60_000
ENDED_LOOKBACK_MS = 15 * 60_000
_KST = timezone(timedelta(hours=9))

FACES = {"calm", "curious", "focused", "signal", "warning", "critical"}


# --- 글자 다듬기 ---------------------------------------------------------------
def _num(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _iso_ms(value) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def quote_of(symbol: str) -> str:
    symbol = str(symbol or "").upper()
    if symbol.startswith("KRW-"):
        return "KRW"
    for quote in ("USDC", "FDUSD", "USDT", "BUSD"):
        if symbol.endswith(quote):
            return quote
    return "USDT"


def base_of(symbol: str) -> str:
    from .news import asset_from_market_symbol
    try:
        return asset_from_market_symbol(symbol) or str(symbol or "").upper()
    except Exception:
        return str(symbol or "").upper()


def price_text(value: float, symbol: str) -> str:
    from .entry_wait import fmt_price
    text = fmt_price(float(value), symbol)
    return f"{text}원" if quote_of(symbol) == "KRW" else f"{text} {quote_of(symbol)}"


def money_text(value: float, symbol: str, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else "-" if value < 0 else ""
    amount = abs(float(value))
    if quote_of(symbol) == "KRW":
        return f"{sign}{amount:,.0f}원"
    return f"{sign}{amount:,.2f} {quote_of(symbol)}"


def qty_text(value: float) -> str:
    amount = float(value)
    digits = 2 if abs(amount) >= 1000 else 3 if abs(amount) >= 1 else 6
    text = f"{amount:,.{digits}f}".rstrip("0").rstrip(".")
    return f"{text}개"


def tidy_price(value: float) -> float:
    """서버 신호 가격(212.8935)처럼 자릿수가 긴 값을 화면용으로 줄인다."""
    size = abs(float(value))
    return round(float(value), 2 if size >= 100 else 4 if size >= 1 else 8)


_PICTOGRAPHS = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def clean_text(text: str) -> str:
    """글 제목 · 요약에서 꾸밈 그림 문자를 뺀다(인용은 그대로, 장식만)."""
    return re.sub(r"\s+", " ", _PICTOGRAPHS.sub("", str(text or ""))).strip()


def numbers_of(text: str) -> list[str]:
    """같은 소식인지 가를 숫자 — 퍼센트(6.07%)와 소수점 · 세 자리 이상 숫자. 1시간 · 24시간 같은 말은 뺀다."""
    found = set()
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(%)?", str(text or "")):
        token, percent = match.group(1), match.group(2)
        if percent:
            found.add(f"{token}%")
        elif "." in token or len(token) >= 3:
            found.add(token)
    return sorted(found)


def clean_note(note: str) -> str:
    """종료 메모에서 내부 값(최소 주문 단위 미만 잔여 수량 9.7e-09 등)을 뺀다."""
    parts = [part.strip() for part in str(note or "").split("·")]
    keep = [part for part in parts if part and "잔여 수량" not in part and not re.search(r"\de-\d", part)]
    return " · ".join(keep)


def pct_text(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}%"


def pick(variants: tuple[str, ...] | list[str], seed: str) -> str:
    """같은 상황이라도 말을 돌려 쓴다 — 시드가 같으면 늘 같은 말(재시도 · 겹침에도 글자가 바뀌지 않게)."""
    return variants[zlib.crc32(seed.encode("utf-8")) % len(variants)]


def _norm_title(text: str) -> str:
    return re.sub(r"\W+", "", str(text or "").lower())


def first_sentence(text: str, limit: int = 90, sentences: int = 1) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    text = re.sub(r"^작성자(는|가)\s+", "", text)
    head = " ".join(re.split(r"(?<=[.!?。])\s", text)[:max(1, sentences)])
    if len(head) > limit:
        head = head[: limit - 1].rstrip() + "…"
    return head


def _clean_reason(reason: str) -> str:
    text = str(reason or "").strip()
    text = re.sub(r"\s*·\s*(진입|청산)$", "", text)
    return "" if text in {"", "진입", "청산"} else text


def _sub(text: str, bold: str = "") -> dict:
    return {"b": bold, "t": text} if bold else {"t": text}


def _chip(kind: str, label: str, **extra) -> dict:
    return {"kind": kind, "label": label, **{k: v for k, v in extra.items() if v not in (None, "")}}


def _say(key: str, kind: str, at_ms: int, face: str, hl: str, *, tone: str = "", nums: str = "",
         nums_tone: str = "", subs: list | None = None, chips: list | None = None, post: dict | None = None,
         actions: list | None = None) -> dict:
    assert face in FACES
    return {"key": key, "kind": kind, "at_ms": int(at_ms), "face": face, "tone": tone, "hl": hl, "nums": nums,
            "nums_tone": nums_tone, "subs": list(subs or []), "chips": list(chips or []), "post": post,
            "actions": list(actions or [])}


# --- 세션 사실 ------------------------------------------------------------------
def exit_levels(macro: Optional[dict], side: str) -> tuple[Optional[float], Optional[float]]:
    """매크로의 익절 · 손절 기준(%) — 모르면 None."""
    if not isinstance(macro, dict):
        return None, None
    params = macro.get("params") if isinstance(macro.get("params"), dict) else {}
    risk = macro.get("risk") if isinstance(macro.get("risk"), dict) else {}
    if side == "short":
        tp = _num(params.get("short_take_profit_pct")) or _num(params.get("take_profit_pct"))
        sl = _num(params.get("short_stop_loss_pct")) or _num(risk.get("stop_loss_pct"))
    else:
        tp = _num(params.get("take_profit_pct")) or _num(params.get("long_take_profit_pct"))
        sl = _num(risk.get("stop_loss_pct"))
    return (tp if tp and tp > 0 else None), (sl if sl and sl > 0 else None)


def session_facts(row: RunSession) -> dict:
    try:
        macro = json.loads(row.macro_json) if getattr(row, "macro_json", "") else None
    except (TypeError, ValueError):
        macro = None
    side = (row.position_side or "long").lower()
    tp, sl = exit_levels(macro, side)
    mode = (getattr(row, "mode", "") or "").lower()
    if not row.testnet:
        mode_label = "실전"
    elif mode == "mock":
        mode_label = "모의"
    else:
        mode_label = "테스트넷"
    return {
        "id": row.id, "user_id": row.user_id, "symbol": row.symbol, "base": base_of(row.symbol),
        "side": side, "leverage": max(1, int(row.leverage or 1)), "market": row.market or "spot",
        "exchange": (macro or {}).get("exchange") or "binance", "mode_label": mode_label,
        "rule": (macro or {}).get("rule_type") or "", "macro": macro, "tp_pct": tp, "sl_pct": sl,
        "status": row.status, "started_ms": _iso_ms(row.started_at) or 0,
        "stopped_ms": _iso_ms(getattr(row, "stopped_at", "")) or 0,
        "last_hb_ms": _iso_ms(row.last_heartbeat_at) or 0, "last_price": float(row.last_price or 0.0),
        "in_position": bool(row.in_position), "entry_price": float(row.entry_price or 0.0),
        "position_qty": float(row.position_qty or 0.0), "unrealized_pct": float(row.unrealized_pct or 0.0),
        "realized_pnl": float(row.realized_pnl or 0.0), "note": row.note or "", "stopping": row.stop_mode in {"stop_only", "close_and_stop"},
        "position_uncertain": bool(getattr(row, "position_uncertain", False)),
        "boot_grace_until_ms": _PROCESS_STARTED_MS + BOOT_GRACE_MS,
    }


def new_state() -> dict:
    return {"v": STATE_VERSION, "started": False, "ended": False, "fill_cursor": 0, "err_cursor": 0,
            "pos": {"qty": 0.0, "avg": 0.0}, "pos_key": None, "entries": 0, "last_exit_ms": 0,
            "loss_band": 0, "gain_band": 0, "move": {}, "vol": {"level": 0, "said_ms": 0, "calm_since": 0},
            "news_rev": None, "post_ms": [], "titles": [], "numbers": [], "errors": [], "offline_since": 0,
            "pos_open_ms": 0, "uncertain": False, "last_say_ms": 0}


def _load_state(raw: str) -> dict:
    state = new_state()
    try:
        stored = json.loads(raw or "{}")
    except (TypeError, ValueError):
        stored = {}
    if isinstance(stored, dict) and stored.get("v") == STATE_VERSION:
        state.update(stored)
    return state


# --- 말할 거리 고르기(순수) -------------------------------------------------------
def decide(state: dict, facts: dict, *, fills=(), errors=(), candles=None, feed=None, wait=None,
           now_ms: int) -> tuple[list[dict], dict]:
    """상태와 이번 입력으로 새 말풍선과 다음 상태를 돌려준다. 같은 입력이면 같은 말풍선(키 · 글자)."""
    state = json.loads(json.dumps(state))  # 깊은 복사 — 호출자의 상태를 바꾸지 않는다
    says: list[dict] = []
    if state.get("ended"):
        return says, state
    if not state["started"]:
        says.append(_say_start(facts, now_ms))
        state["started"] = True
    says += _say_fills(state, facts, fills)
    says += _say_errors(state, facts, errors, now_ms)
    running = facts["status"] == "running" and not facts.get("stopping")
    if running:
        offline = _say_connection(state, facts, now_ms)
        says += offline
        says += _say_uncertain(state, facts, now_ms)
        if not state["offline_since"]:
            bands = _say_bands(state, facts, now_ms)
            says += bands
            volatile = _say_volatility(state, facts, candles, now_ms)
            says += volatile
            recent_fill = fills or now_ms - int(state.get("last_fill_ms") or 0) < 3 * 60_000
            if not bands and not volatile and not recent_fill:
                # 같은 순간 같은 이야기(손실 구간 · 출렁임 · 방금 체결)를 두 번 하지 않는다
                says += _say_move(state, facts, candles, wait, now_ms)
            says += _say_wait(state, facts, wait, now_ms)
    says += _say_posts(state, facts, feed, now_ms)
    if running and not state["offline_since"] and not says:
        says += _say_quiet(state, facts, candles, wait, now_ms)
    says.sort(key=lambda item: item["at_ms"])
    if facts["status"] != "running":
        end = _say_end(facts, now_ms)
        # 종료 보고에 실려 온 마지막 체결보다 먼저 '끝났어요'가 오지 않게 맨 뒤에 둔다.
        end["at_ms"] = max([end["at_ms"], *[item["at_ms"] for item in says]])
        says.append(end)
        state["ended"] = True
    if says:
        state["last_say_ms"] = max(state.get("last_say_ms") or 0, now_ms)
    return says, state


def _say_start(facts: dict, now_ms: int) -> dict:
    subs = []
    tone = ""
    if facts["sl_pct"] is None:
        subs.append(_sub(" · 떨어져도 매크로는 안 팔아요", "손절 기준 없음"))
        tone = "warn"
    else:
        subs.append(_sub(f"손절은 -{facts['sl_pct']:g}%에서"))
    if facts["tp_pct"] is not None:
        subs.append(_sub(f"익절은 +{facts['tp_pct']:g}%에서"))
    if facts["leverage"] > 1:
        subs.append(_sub(f"레버리지 {facts['leverage']}배라 손익도 {facts['leverage']}배로 움직여요"))
    return _say("start", "start", facts["started_ms"] or now_ms, "focused",
                f"{facts['base']} {facts['mode_label']} 시작했어요", tone=tone, subs=subs,
                chips=[_chip("macro", "내 매크로")])


def _entry_action(side: str) -> str:
    return "SELL" if side == "short" else "BUY"


def _say_fills(state: dict, facts: dict, fills) -> list[dict]:
    says = []
    symbol, side = facts["symbol"], facts["side"]
    pos = state["pos"]
    for fill in fills:
        state["fill_cursor"] = max(int(state.get("fill_cursor") or 0), int(fill["id"]))
        state["last_fill_ms"] = max(int(state.get("last_fill_ms") or 0), int(fill.get("at_ms") or 0))
        qty, price = _num(fill.get("qty")), _num(fill.get("price"))
        action = str(fill.get("action") or "").upper()
        if not qty or not price or qty <= 0 or price <= 0 or action not in {"BUY", "SELL"}:
            continue
        at = int(fill.get("at_ms") or 0)
        key = f"fill:{fill['id']}"
        chips = [_chip("macro", "체결", event_id=fill.get("id"))]
        signal = _num(fill.get("signal_price"))
        slip = (price / signal - 1) * 100 if signal and signal > 0 else 0.0
        slip_sub = (_sub(f"신호 때 가격({price_text(tidy_price(signal), symbol)})보다 {abs(slip):.1f}% {'낮게' if slip < 0 else '높게'} 체결됐어요")
                    if signal and abs(slip) >= SLIPPAGE_PCT else None)
        reason = _clean_reason(fill.get("reason") or "")
        if action == _entry_action(side):
            rebuy = pos["qty"] <= 1e-12 and state.get("last_exit_ms") and 0 <= at - int(state["last_exit_ms"]) <= REBUY_WINDOW_MS
            first = pos["qty"] <= 1e-12 and not state.get("entries")
            total = pos["qty"] + qty
            pos["avg"] = (pos["avg"] * pos["qty"] + price * qty) / total
            pos["qty"] = total
            if first or rebuy or not state.get("pos_key"):
                state["pos_key"] = f"f{fill['id']}"
                state["loss_band"] = state["gain_band"] = 0
                state["pos_open_ms"] = at
            state["entries"] = int(state.get("entries") or 0) + 1
            subs = []
            if reason:
                subs.append(_sub(f"{'산' if side == 'long' else '들어간'} 이유: {reason}"))
            elif facts["rule"] == "A" and rebuy:
                subs.append(_sub("팔면 곧바로 다시 사는 매크로예요"))
            elif facts["rule"] == "A" and first:
                subs.append(_sub("시작하자마자 사는 매크로예요"))
            if slip_sub:
                subs.append(slip_sub)
            verb = "샀어요" if side == "long" else "팔아서 들어갔어요"
            hl = (f"쉬지도 않고 바로 다시 {'샀어요' if side == 'long' else '들어갔어요'}" if rebuy
                  else f"{verb}!")
            says.append(_say(key, "fill", at, "focused" if rebuy else "signal", hl,
                             nums=f"{price_text(price, symbol)} · {qty_text(qty)} · {money_text(price * qty, symbol)}",
                             subs=subs, chips=chips))
            continue
        # 청산(롱의 매도 · 숏의 매수)
        held_avg = pos["avg"] if pos["qty"] > 1e-12 else (facts["entry_price"] or 0.0)
        closed = min(qty, pos["qty"]) if pos["qty"] > 1e-12 else qty
        pos["qty"] = max(0.0, pos["qty"] - qty)
        if pos["qty"] <= 1e-9:
            pos["qty"], pos["avg"] = 0.0, 0.0
            state["pos_key"] = None
            state["loss_band"] = state["gain_band"] = 0
            state["pos_open_ms"] = 0
            state["last_exit_ms"] = at
        exit_word = "팔았어요" if side == "long" else "정리했어요"
        if held_avg <= 0:
            says.append(_say(key, "fill", at, "focused", f"규칙대로 {exit_word}",
                             nums=f"{price_text(price, symbol)} · {qty_text(qty)}", subs=[slip_sub] if slip_sub else [],
                             chips=chips))
            continue
        direction = 1 if side == "long" else -1
        pnl = (price - held_avg) * closed * direction
        pct = (price / held_avg - 1) * 100 * direction
        stop = "손절" in str(fill.get("reason") or "")
        subs = [_sub(f"{price_text(price, symbol)} 체결 · 수수료 전")]
        if facts["leverage"] > 1:
            subs.append(_sub(f"레버리지 {facts['leverage']}배라 원금 대비 {pct * facts['leverage']:+.1f}%"))
        if slip_sub:
            subs.append(slip_sub)
        if reason and not stop:
            subs.append(_sub(f"{'판' if side == 'long' else '정리한'} 이유: {reason}"))
        closing_run = not reason and not stop and (facts.get("stopping") or facts["status"] != "running")
        if stop:
            face, hl = "calm", pick(("아프지만 규칙대로 잘랐어요", "손절선에서 규칙대로 멈췄어요"), key)
        elif closing_run:
            face, hl = "calm", "종료하면서 정리했어요"
        elif pnl >= 0:
            face, hl = "signal", pick(("익절 성공! 규칙대로 챙겼어요", "익절했어요! 이번 판은 껄무새가 웃어요"), key)
        else:
            face, hl = "focused", f"규칙대로 {exit_word}"
        says.append(_say(key, "fill", at, face, hl, nums=f"{money_text(pnl, symbol, signed=True)} · {pct_text(pct, 2)}",
                         nums_tone="up" if pnl > 0 else "down" if pnl < 0 else "", subs=subs, chips=chips))
    return says


def _say_errors(state: dict, facts: dict, errors, now_ms: int) -> list[dict]:
    says = []
    recent = [item for item in state.get("errors") or [] if isinstance(item, list) and len(item) == 2]
    first_pass = not int(state.get("err_cursor") or 0)
    for error in errors:
        state["err_cursor"] = max(int(state.get("err_cursor") or 0), int(error["id"]))
        message = re.sub(r"\s+", " ", str(error.get("message") or "")).strip()
        if not message:
            continue
        at = int(error.get("at_ms") or 0)
        if first_pass and at < now_ms - 30 * 60_000:
            continue  # 처음 볼 때 오래전 오류를 새 일처럼 말하지 않는다
        norm = _norm_title(message)[:120]
        if any(old == norm and at - int(ms) < ERROR_REPEAT_MS for old, ms in recent):
            continue
        recent.append([norm, at])
        order = error.get("event_name") in {"runner.order_failed", "runner.command_failed"} or "주문" in message
        transient = re.search(r"일시 오류|재시도|timed out|timeout|HTTPSConnectionPool|ConnectionError", message, re.I)
        if not order or transient:
            continue  # 실행기가 스스로 다시 시도하는 일시 오류 · 주문과 상관없는 오류는 활동 로그에만
        hl = "주문이 안 나갔어요"
        if len(message) > 110:
            message = message[:109].rstrip() + "…"
        says.append(_say(f"err:{error['id']}", "problem", at, "critical", hl, tone="risk",
                         subs=[_sub(message)], chips=[_chip("macro", "실행 기록", event_id=error.get("id"))]))
    state["errors"] = recent[-20:]
    return says


def _say_uncertain(state: dict, facts: dict, now_ms: int) -> list[dict]:
    if not facts.get("position_uncertain"):
        state["uncertain"] = False
        return []
    if state.get("uncertain"):
        return []
    state["uncertain"] = True
    state["uncertain_n"] = int(state.get("uncertain_n") or 0) + 1
    return [_say(f"uncertain:{state['uncertain_n']}", "problem", now_ms, "critical", "포지션 확인이 필요해요", tone="risk",
                 subs=[_sub("주문 결과를 확정하지 못했어요"), _sub("거래소에서 주문과 남은 포지션을 확인해 주세요")],
                 chips=[_chip("macro", "내 매크로")])]


def _say_connection(state: dict, facts: dict, now_ms: int) -> list[dict]:
    last = int(facts.get("last_hb_ms") or 0)
    if not last or now_ms < int(facts.get("boot_grace_until_ms") or 0):
        return []
    gap = now_ms - last
    if not state.get("offline_since") and gap >= OFFLINE_MS:
        state["offline_since"] = last
        return [_say(f"offline:{last}", "problem", now_ms, "warning",
                     pick(("어라, 실행기랑 연락이 안 돼요… PC 켜져 있나요?", "어라, 실행기 소식이 끊겼어요"), str(last)),
                     tone="warn", subs=[_sub("1분 넘게 실행기 소식이 없어요"),
                                         _sub("PC가 켜져 있고 인터넷이 되는지 확인해 주세요")])]
    if state.get("offline_since") and gap < ONLINE_MS:
        since = int(state["offline_since"])
        state["offline_since"] = 0
        minutes = max(1, round((last - since) / 60_000))
        return [_say(f"online:{since}", "problem", now_ms, "calm", "휴, 다시 연결됐어요",
                     subs=[_sub(f"{minutes}분쯤 끊겼어요")])]
    return []


def _position_money(facts: dict) -> Optional[float]:
    entry, last, qty = facts["entry_price"], facts["last_price"], facts["position_qty"]
    if entry <= 0 or last <= 0 or qty <= 0:
        return None
    return (last - entry) * qty * (1 if facts["side"] == "long" else -1)


def _say_bands(state: dict, facts: dict, now_ms: int) -> list[dict]:
    if not facts["in_position"] or facts["entry_price"] <= 0:
        if state.get("pos_key") and not facts["in_position"]:
            state["pos_key"] = None
            state["loss_band"] = state["gain_band"] = 0
            state["pos_open_ms"] = 0
        return []
    if not state.get("pos_key"):
        state["pos_key"] = f"e{facts['entry_price']:g}"
        state["loss_band"] = state["gain_band"] = 0
        state["pos_open_ms"] = now_ms
    pct = facts["unrealized_pct"]
    money = _position_money(facts)
    money_part = f" · {money_text(money, facts['symbol'], signed=True)}" if money is not None else ""
    says = []
    loss = max((band for band in LOSS_BANDS if pct <= -band), default=0)
    if loss and loss > int(state.get("loss_band") or 0):
        state["loss_band"] = loss
        deeper = [band for band in LOSS_BANDS if band > loss]
        key = f"loss:{state['pos_key']}:{loss}"
        subs = []
        if facts["sl_pct"] is None:
            subs.append(_sub("손절 기준이 없어 매크로는 계속 들고 있어요"))
        elif facts["sl_pct"] > abs(pct):
            subs.append(_sub(f"손절선(-{facts['sl_pct']:g}%)까지 {facts['sl_pct'] - abs(pct):.1f}% 남았어요"))
        if deeper:
            subs.append(_sub(f"다음엔 -{deeper[0]}%에서 말할게요"))
        if loss == 3:
            hl = ("어어? 손절 기준이 없는데, 이대로 괜찮으세요?" if facts["sl_pct"] is None
                  else "어어? -3% 넘었어요")
            face, tone = "warning", "warn"
        elif loss == 5:
            face, tone, hl = "critical", "risk", "-5%도 넘었어요. 차분히 볼게요"
        else:
            face, tone, hl = "critical", "risk", f"-{loss}%까지 내려왔어요"
        says.append(_say(key, "loss", now_ms, face, hl, tone=tone, nums=f"{pct_text(pct)}{money_part}", nums_tone="down",
                         subs=subs, chips=[_chip("chart", "차트"), _chip("macro", "내 매크로")]))
    gain = max((band for band in GAIN_BANDS if pct >= band), default=0)
    if gain and gain > int(state.get("gain_band") or 0):
        state["gain_band"] = gain
        key = f"gain:{state['pos_key']}:{gain}"
        tp = facts["tp_pct"]
        subs = []
        if tp is not None and tp > pct:
            subs.append(_sub(f"익절선(+{tp:g}%)까지 {tp - pct:.1f}%"))
        elif tp is None:
            subs.append(_sub("익절 기준이 없어 매크로는 계속 들고 있어요"))
        hl = ("익절 안 하고 버티는 당신은 강심장!" if gain == 5
              else pick((f"오오, +{gain}% 넘었어요!", f"+{gain}%! 껄무새 날개가 들썩들썩해요"), key))
        says.append(_say(key, "gain", now_ms, "signal", hl, nums=f"{pct_text(pct)}{money_part}", nums_tone="up",
                         subs=subs, chips=[_chip("chart", "차트")]))
    return says


def _closed(candles) -> list[dict]:
    return [bar for bar in (candles or []) if isinstance(bar, dict) and bar.get("closed") is not False
            and (_num(bar.get("o")) or 0) > 0]


def _say_move(state: dict, facts: dict, candles, wait, now_ms: int) -> list[dict]:
    closed = _closed(candles)
    last = facts["last_price"]
    if len(closed) < 3 or last <= 0:
        return []
    step = 300_000
    # 5~10분 전에 끝난 봉의 종가와 지금 가격을 비교한다.
    ref = next((bar for bar in reversed(closed) if int(bar["t"]) + step <= now_ms - 5 * 60_000), None)
    if ref is None or now_ms - (int(ref["t"]) + step) > 12 * 60_000:
        return []
    base = float(ref["c"])
    if base <= 0:
        return []
    holding = facts["in_position"] and facts["entry_price"] > 0
    # 사기 전 · 시작 전에 움직인 것을 '지금 일'처럼 말하지 않는다.
    since = int(state.get("pos_open_ms") or 0) if holding else int(facts.get("started_ms") or 0)
    if since and int(ref["t"]) + step < since:
        return []
    change = (last / base - 1) * 100
    threshold = MOVE_PCT * (2 if int((state.get("vol") or {}).get("level") or 0) else 1)
    if abs(change) < threshold:
        return []
    direction = "up" if change > 0 else "down"
    moves = state.setdefault("move", {})
    if now_ms - int(moves.get(direction) or 0) < MOVE_COOLDOWN_MS:
        return []
    minutes = max(1, round((now_ms - (int(ref["t"]) + step)) / 60_000))
    key = f"move:{direction}:{ref['t']}"
    symbol = facts["symbol"]
    long_side = facts["side"] == "long"
    if holding:
        good = (direction == "up") == long_side
        subs = []
        if good and facts["tp_pct"] is not None:
            tp_price = facts["entry_price"] * (1 + facts["tp_pct"] / 100 * (1 if long_side else -1))
            remain = abs(tp_price / last - 1) * 100
            if remain <= 3:
                subs.append(_sub(f"익절선 {price_text(tp_price, symbol)}까지 {remain:.1f}%"))
        if not good:
            if facts["sl_pct"] is not None:
                sl_price = facts["entry_price"] * (1 - facts["sl_pct"] / 100 * (1 if long_side else -1))
                subs.append(_sub(f"손절선 {price_text(sl_price, symbol)}까지 {abs(last / sl_price - 1) * 100:.1f}%"))
            else:
                subs.append(_sub(f"평단 {price_text(facts['entry_price'], symbol)} 기준 {pct_text(facts['unrealized_pct'])}"))
        if good and facts["unrealized_pct"] < -1:
            # 손실 중의 반등은 축하하지 않는다 — 숫자만 차분히.
            hl = pick((f"오, 조금 살아나요! {minutes}분 만에 {pct_text(change)}", f"한숨 돌렸어요. {minutes}분 만에 {pct_text(change)}"), key)
            face = "focused"
            subs = [_sub(f"평단 {price_text(facts['entry_price'], symbol)} 기준 {pct_text(facts['unrealized_pct'])}")] + subs
        elif good:
            hl = pick((f"오오, {minutes}분 만에 {pct_text(change)}! 이 속도 실화예요?",
                       f"{minutes}분 만에 {pct_text(change)}! 껄무새 날개가 들썩들썩해요"), key)
            face = "signal"
        else:
            slip = "미끄러져요" if long_side else "거꾸로 올라가요"
            hl = pick((f"어어? {minutes}분 만에 {pct_text(change)}, {slip}", f"어어? 갑자기 {slip}!"), key)
            face = "warning"
        moves[direction] = now_ms
        return [_say(key, "move", now_ms, face, hl, nums=price_text(last, symbol),
                     nums_tone="up" if direction == "up" else "down", subs=subs, chips=[_chip("chart", "차트")])]
    if wait and direction == ("up" if long_side else "down"):
        moves[direction] = now_ms
        subs = [_sub(str(wait.get("sentence")))] if wait.get("sentence") else []
        return [_say(key, "move", now_ms, "focused",
                     f"아직 조건 전이라 안 샀는데, {minutes}분 만에 {pct_text(change)}",
                     nums=price_text(last, symbol), nums_tone="up" if direction == "up" else "down", subs=subs,
                     chips=[_chip("chart", "차트")])]
    return []


def volatility(candles) -> Optional[dict]:
    """최근 1시간(5분 봉 12개) 출렁임(고가−저가)/시가 평균을 그 앞 보통 값(중앙값)과 비교한다."""
    closed = _closed(candles)
    if len(closed) < 12 + VOL_MIN_BASE_BARS:
        return None
    ranges = [(float(bar["h"]) - float(bar["l"])) / float(bar["o"]) * 100 for bar in closed]
    recent, base_part = ranges[-12:], ranges[:-12][-276:]
    base = statistics.median(base_part)
    if base <= 0:
        return None
    avg = sum(recent) / len(recent)
    last_bar = closed[-1]
    return {"ratio": avg / base, "avg": avg, "base": base, "single": ranges[-1],
            "single_low": float(last_bar["l"]), "single_high": float(last_bar["h"]),
            "single_open": float(last_bar["o"]), "single_close": float(last_bar["c"]), "bar_t": int(last_bar["t"])}


def _say_volatility(state: dict, facts: dict, candles, now_ms: int) -> list[dict]:
    stats = volatility(candles)
    vol = state.setdefault("vol", {"level": 0, "said_ms": 0, "calm_since": 0})
    if stats is None:
        return []
    symbol = facts["symbol"]
    hot = (stats["ratio"] >= VOL_RATIO and stats["avg"] >= VOL_MIN_AVG_PCT) or stats["single"] >= VOL_SINGLE_PCT
    level = 0
    if hot:
        level = 5 if stats["ratio"] >= VOL_HIGH_RATIO or stats["single"] >= VOL_SINGLE_HIGH_PCT else 3
    current = int(vol.get("level") or 0)
    says = []
    if level:
        vol["calm_since"] = 0
        repeat = now_ms - int(vol.get("said_ms") or 0) >= VOL_REPEAT_MS
        if level > current or (repeat and level >= current):
            key = f"vol:{level}:{now_ms // VOL_REPEAT_MS}"
            if stats["single"] >= VOL_SINGLE_PCT and stats["single"] >= stats["avg"] * 1.5:
                nums = (f"5분 봉 {stats['single']:.1f}% · {price_text(stats['single_open'], symbol)} → "
                        f"{price_text(stats['single_close'], symbol)}")
            else:
                nums = f"5분 봉 평균 {stats['avg']:.1f}% · 평소 {stats['base']:.2f}%"
            ratio = max(1, round(stats["ratio"]))
            if level > current and current:
                hl = f"출렁임이 더 커졌어요, 평소의 {ratio}배"
            else:
                hl = pick((f"출렁임이 평소의 {ratio}배예요, 조심!", "오늘은 롤러코스터 장이에요, 조심해서 볼게요"), key)
            subs = []
            if facts["in_position"] and facts["sl_pct"] is None:
                subs.append(_sub("손절 기준이 없어 크게 빠져도 매크로는 들고 있어요"))
            elif facts["in_position"] and facts["sl_pct"] is not None and facts["entry_price"] > 0:
                long_side = facts["side"] == "long"
                sl_price = facts["entry_price"] * (1 - facts["sl_pct"] / 100 * (1 if long_side else -1))
                remain = abs(facts["last_price"] / sl_price - 1) * 100 if facts["last_price"] > 0 else None
                if remain is not None and remain <= stats["single"]:
                    subs.append(_sub(f"손절선까지 {remain:.1f}%라 한 봉에 닿을 수도 있어요"))
            if facts["leverage"] > 1:
                subs.append(_sub(f"레버리지 {facts['leverage']}배라 손익도 {facts['leverage']}배로 움직여요"))
            if not subs:
                subs.append(_sub("가라앉으면 그때 다시 알려 드릴게요"))
            vol.update(level=level, said_ms=now_ms)
            says.append(_say(key, "volatility", now_ms, "warning", hl, tone="warn", nums=nums, subs=subs,
                             chips=[_chip("chart", "차트 · 5분 봉")]))
        return says
    if current:
        if stats["ratio"] < VOL_CALM_RATIO:
            vol["calm_since"] = vol.get("calm_since") or now_ms
            if now_ms - int(vol["calm_since"]) >= VOL_CALM_MS:
                said = int(vol.get("said_ms") or 0)
                vol.update(level=0, calm_since=0)
                says.append(_say(f"calm:{said}", "volatility", now_ms, "calm", "휴, 출렁임이 가라앉았어요",
                                 nums=f"5분 봉 평균 {stats['avg']:.1f}% · 평소 수준", chips=[_chip("chart", "차트")]))
        else:
            vol["calm_since"] = 0
    return says


def _blocked(wait: dict) -> Optional[dict]:
    """주 조건 말고 아직 안 맞은 조건(이평선 필터 · 연속 봉 · 진입 필터) 하나."""
    rows = [row for row in (wait.get("conditions") or [])[1:] if isinstance(row, dict)]
    return next((row for row in rows if row.get("ok") is False), None)


def _say_wait(state: dict, facts: dict, wait, now_ms: int) -> list[dict]:
    if facts["in_position"] or not isinstance(wait, dict) or wait.get("realtime"):
        state.pop("wait_open", None)
        return []
    bar = wait.get("next_check_ms") or 0
    says: list[dict] = []
    # '넘었어요'를 말한 봉이 사지 않고 끝났으면 그렇다고 이유와 함께 말한다 — 말없이 다음 사는 선으로 넘어가면 버그로 보인다.
    opened = int(state.get("wait_open") or 0)
    last = wait.get("last_bar") if isinstance(wait.get("last_bar"), dict) else {}
    last_at = int(last.get("at_ms") or 0)
    if opened and last_at >= opened:
        state.pop("wait_open", None)
        if last_at == opened and not last.get("entered"):
            subs = [_sub("", str(last["why"]))] if last.get("why") else []
            if wait.get("headline") or wait.get("sentence"):
                subs.append(_sub(f"새 봉 · {wait.get('headline') or wait['sentence']}"))
            says.append(_say(f"wait:miss:{opened}", "wait", now_ms, "curious", "이번 봉은 안 샀어요",
                             subs=subs, chips=[_chip("chart", "차트")]))
    nums = price_text(facts["last_price"], facts["symbol"]) if facts["last_price"] > 0 else ""
    if wait.get("reached"):
        state["wait_open"] = bar
        says.append(_say(f"wait:reached:{bar}", "wait", now_ms, "signal", "사는 선을 넘었어요! 이대로 끝나면 사요",
                         nums=nums, subs=[_sub(str(wait["sentence"]))] if wait.get("sentence") else [],
                         chips=[_chip("chart", "차트")]))
        return says
    blocked = _blocked(wait)
    if wait.get("touched") and blocked:
        state["wait_open"] = bar
        hl = "사는 선은 넘었는데, 아직 안 사요" if wait.get("rule") == "I" else "조건은 맞았는데, 아직 안 사요"
        says.append(_say(f"wait:held:{bar}", "wait", now_ms, "focused", hl, nums=nums,
                         subs=[_sub(f" · 지금 {nums}" if nums else "", str(blocked.get("wait") or blocked.get("label") or ""))],
                         chips=[_chip("chart", "차트")]))
        return says
    meter = wait.get("meter") if isinstance(wait.get("meter"), dict) else None
    progress = _num((meter or {}).get("progress"))
    if progress is not None and progress >= 0.9:
        subs = [_sub(str(wait[name])) for name in ("distance", "sentence") if wait.get(name)]
        says.append(_say(f"wait:near:{bar}", "wait", now_ms, "focused", "사는 선까지 거의 다 왔어요",
                         nums=str(wait.get("gap") or ""), subs=subs, chips=[_chip("chart", "차트")]))
    return says


def _say_quiet(state: dict, facts: dict, candles, wait, now_ms: int) -> list[dict]:
    if not (facts["in_position"] or wait):
        return []
    last = int(state.get("last_say_ms") or 0)
    if not last or now_ms - last < QUIET_MS or int((state.get("vol") or {}).get("level") or 0):
        return []
    window = [bar for bar in _closed(candles) if int(bar["t"]) >= now_ms - QUIET_MS]
    if len(window) < 3:
        return []
    low = min(float(bar["l"]) for bar in window)
    high = max(float(bar["h"]) for bar in window)
    if low <= 0 or (high / low - 1) * 100 > MOVE_PCT:
        return []
    symbol = facts["symbol"]
    key = f"quiet:{now_ms // QUIET_MS}"
    return [_say(key, "quiet", now_ms, "curious", pick(("조용~하네요. 숨 고르는 중이에요", "다들 쉬는 시간인가 봐요"), key),
                 nums=f"{price_text(low, symbol)} ~ {price_text(high, symbol)} · 45분",
                 subs=[_sub("크게 바뀌면 바로 말할게요")], chips=[_chip("chart", "차트")])]


def _post_items(feed) -> list[tuple[dict, dict]]:
    if not isinstance(feed, dict):
        return []
    items = list(feed.get("items") or [])
    analysis = list(((feed.get("analysis") or {}).get("items")) or [])
    return [(item, analysis[index] if index < len(analysis) and isinstance(analysis[index], dict) else {})
            for index, item in enumerate(items) if isinstance(item, dict)]


def _say_posts(state: dict, facts: dict, feed, now_ms: int) -> list[dict]:
    if not isinstance(feed, dict):
        return []
    if state.get("news_rev") is None or feed.get("reset"):
        # 처음 보거나 피드가 다시 만들어졌으면 지금 위치만 기억한다 — 쌓여 있던 글을 한꺼번에 쏟지 않는다.
        state["news_rev"] = feed.get("cursor")
        return []
    rev_in = state["news_rev"]
    said = [key for key in state.get("post_said") or [] if isinstance(key, str)]
    waiting = {key: int(ms) for key, ms in (state.get("post_wait") or {}).items() if now_ms - int(ms) < 3_600_000}
    hold = False
    from .agent_features.position_news.articles import article_id
    started = int(facts.get("started_ms") or 0)
    post_ms = [int(ms) for ms in state.get("post_ms") or [] if now_ms - int(ms) < 3_600_000]
    titles = [list(entry) + [False] * (4 - len(entry)) for entry in state.get("titles") or []
              if isinstance(entry, list) and len(entry) >= 3 and now_ms - int(entry[2]) < SAME_TITLE_WINDOW_MS]
    numbers = [entry for entry in state.get("numbers") or []
               if isinstance(entry, list) and len(entry) == 3 and now_ms - int(entry[1]) < SAME_NUMBERS_WINDOW_MS]
    candidates = []
    for item, assessed in _post_items(feed):
        published = _iso_ms(item.get("published"))
        if not published or now_ms - published > POST_MAX_AGE_MS or published < started - POST_BEFORE_START_MS:
            continue
        title = clean_text(item.get("title"))
        if not title:
            continue
        candidates.append((published, item, assessed, title))
    candidates.sort(key=lambda entry: entry[0])
    says = []
    for published, item, assessed, title in candidates:
        community = item.get("content_type") == "community"
        if f"post:{article_id(item)}" in said:
            continue  # 요약을 기다리느라 같은 위치부터 다시 읽은 글 — 이미 말했다
        if community and item.get("community_summary_status") == "pending":
            first = waiting.setdefault(f"post:{article_id(item)}", now_ms)
            if now_ms - first < POST_SUMMARY_WAIT_MS:
                hold = True  # 요약이 오면 제목과 함께 말한다 — 읽은 위치를 넘기지 않고 다음 바퀴에 다시 읽는다
                continue
        author = str(item.get("author") or "").strip()[:60]
        source = str(item.get("source") or ("바이낸스 스퀘어" if community else "뉴스")).strip()[:60]
        raw_summary = (item.get("community_summary") if isinstance(item.get("community_summary"), str) else "") if community \
            else (assessed.get("summary") or item.get("excerpt") or "")
        norm = _norm_title(title)
        same_title = [entry for entry in titles if entry[0] == norm]
        found = numbers_of(f"{title} {raw_summary}")
        same_numbers = [entry for entry in numbers if _same_numbers(found, entry[0])]
        key = f"post:{article_id(item)}"
        repeat = bool(same_title or same_numbers)
        if repeat:
            for entry in same_title:
                entry[3] = True
            for entry in same_numbers:
                entry[2] = True
        if community:
            hl = ("같은 숫자 글이 또 올라왔어요" if same_numbers else "같은 내용 글이 또 올라왔어요") if repeat \
                else pick(("이런 글이 올라왔네요?", "커뮤니티에 새 글이 올라왔어요", "이런 얘기도 올라왔어요"), key)
        else:
            hl = "같은 소식 기사가 또 나왔어요" if repeat else pick(("관련 기사가 나왔어요", "이런 기사가 떴어요"), key)
        summary = first_sentence(clean_text(raw_summary), limit=200, sentences=2)  # 제목 + 내용 — 두 문장까지
        subs = []
        if not community:
            effect = _position_effect(assessed, facts["side"])
            if effect == "favorable":
                subs.append(_sub("내 포지션엔 유리한 쪽 소식으로 분류됐어요"))
            elif effect == "unfavorable":
                subs.append(_sub("내 포지션엔 불리한 쪽 소식으로 분류됐어요"))
        url = str(item.get("url") or "")
        post = {"kind": "community" if community else "news", "title": title[:200], "summary": summary,
                "author": author, "source": source, "url": url if url.startswith("https://") else "",
                "published_ms": published}
        says.append(_say(key, "post", now_ms, "curious", hl, subs=subs, post=post,
                         chips=[_chip("comm" if community else "news", source, href=post["url"])]))
        post_ms.append(now_ms)
        said.append(key)
        waiting.pop(key, None)
        titles.append([norm, author, now_ms, False])
        if found and not same_numbers:
            numbers.append([found, now_ms, False])
    state["post_ms"] = post_ms[-20:]
    state["post_said"] = said[-200:]
    state["post_wait"] = waiting
    state["news_rev"] = rev_in if hold else feed.get("cursor", rev_in)
    state["titles"] = titles[-80:]
    state["numbers"] = numbers[-80:]
    return says


def _same_numbers(found: list[str], seen: list[str]) -> bool:
    """같은 소식을 퍼 나른 글인지 — 퍼센트를 포함해 숫자 두 개가 겹치거나, 소수 둘째 자리 퍼센트(6.07%)가 겹치면.

    가격(0.1519)만 겹치는 것은 같은 소식으로 보지 않는다 — 여러 글이 같은 지지 · 저항 가격을 흔히 말한다.
    """
    shared = set(found) & set(seen)
    percents = [token for token in shared if token.endswith("%")]
    if not percents:
        return False
    return len(shared) >= 2 or any("." in token and len(token[:-1].split(".")[1]) >= 2 for token in percents)


def _position_effect(assessed: dict, side: str) -> str:
    sentiment = assessed.get("sentiment") if isinstance(assessed, dict) else None
    try:
        from .agent_features.position_news import classifier
        if sentiment not in classifier.SENTIMENTS:
            return "unclear"
        return classifier.position_effect(sentiment, side)
    except Exception:
        return "unclear"


def _say_end(facts: dict, now_ms: int) -> dict:
    symbol = facts["symbol"]
    error = facts["status"] == "error"
    subs = []
    note = clean_note(facts["note"])
    if note:
        subs.append(_sub(note[:120]))
    if facts["in_position"]:
        subs.append(_sub(" · 거래소에서 포지션을 확인해 주세요", "포지션 보유 중"))
    realized = facts["realized_pnl"]
    return _say("end", "end", facts.get("stopped_ms") or now_ms, "critical" if error else "calm",
                "실행이 오류로 끝났어요" if error else "실행이 끝났어요", tone="risk" if error else "",
                nums=f"누적 실현 {money_text(realized, symbol, signed=True)}",
                nums_tone="up" if realized > 0 else "down" if realized < 0 else "", subs=subs,
                chips=[_chip("macro", "내 매크로")])


# --- 저장 · 조회 ----------------------------------------------------------------
def _payload(say: dict) -> str:
    body = {key: say[key] for key in ("face", "tone", "hl", "nums", "nums_tone", "subs", "chips", "post", "actions")}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def save(db, facts: dict, says: list[dict], state: dict, *, now_ms: int) -> int:
    """말풍선을 넣고(같은 키는 건너뜀) 상태를 바꾼다. 커밋은 호출자. 새로 들어간 개수를 돌려준다."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    added = 0
    for say in says:
        statement = insert(AgentUtterance).values(
            session_id=facts["id"], user_id=facts["user_id"], say_key=say["key"][:160], kind=say["kind"],
            at_ms=int(say["at_ms"] or now_ms), created_ms=now_ms, payload_json=_payload(say),
        ).on_conflict_do_nothing(index_elements=[AgentUtterance.session_id, AgentUtterance.say_key]
                                 ).returning(AgentUtterance.id)
        if db.exec(statement).scalar_one_or_none() is not None:
            added += 1
    row = db.get(AgentCommentaryState, facts["id"])
    if row is None:
        row = AgentCommentaryState(session_id=facts["id"])
    row.state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    row.updated_ms = now_ms
    db.add(row)
    if added:
        db.flush()
        _trim(db, facts["id"])
    return added


def _trim(db, session_id: int) -> None:
    from sqlalchemy import delete as sql_delete, func
    total = int(db.exec(select(func.count(AgentUtterance.id)).where(AgentUtterance.session_id == session_id)).one())
    overflow = total - UTTERANCE_CAP
    if overflow <= 0:
        return
    doomed = list(db.exec(select(AgentUtterance.id).where(AgentUtterance.session_id == session_id)
                          .order_by(AgentUtterance.id.asc()).limit(overflow)).all())
    if doomed:
        db.exec(sql_delete(AgentUtterance).where(AgentUtterance.id.in_(doomed)))


def utterance_view(row: AgentUtterance) -> dict:
    try:
        body = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        body = {}
    return {"id": row.id, "kind": row.kind, "at_ms": row.at_ms, **(body if isinstance(body, dict) else {})}


def list_utterances(user_id: int, session_id: int, *, after: int = 0, limit: int = 200) -> dict:
    from fastapi import HTTPException
    bound = max(1, min(int(limit or 200), UTTERANCE_CAP))
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
        rows = list(db.exec(select(AgentUtterance).where(AgentUtterance.session_id == session_id,
                                                         AgentUtterance.id > max(0, int(after or 0)))
                            .order_by(AgentUtterance.id.asc()).limit(bound)).all())
        return {"session_id": session_id, "status": row.status,
                "items": [utterance_view(item) for item in rows],
                "cursor": rows[-1].id if rows else max(0, int(after or 0))}


def delete_for_sessions(db, session_ids: list[int]) -> int:
    """세션 기록을 지울 때 말풍선 · 상태도 같이 지운다(커밋은 호출자). 지운 말풍선 수."""
    from sqlalchemy import delete as sql_delete
    if not session_ids:
        return 0
    removed = db.exec(sql_delete(AgentUtterance).where(AgentUtterance.session_id.in_(session_ids))
                      .returning(AgentUtterance.id)).all()
    db.exec(sql_delete(AgentCommentaryState).where(AgentCommentaryState.session_id.in_(session_ids)))
    return len(removed)


# --- 한 바퀴 ----------------------------------------------------------------------
def _fills_since(db, session_id: int, cursor: int) -> list[dict]:
    query = select(RunSessionEvent).where(RunSessionEvent.session_id == session_id, RunSessionEvent.kind == "fill",
                                          RunSessionEvent.id > cursor)
    if cursor:
        rows = list(db.exec(query.order_by(RunSessionEvent.id.asc()).limit(100)).all())
    else:
        rows = list(reversed(db.exec(query.order_by(RunSessionEvent.id.desc()).limit(FILL_BACKLOG)).all()))
    commands = {}
    command_ids = []
    for row in rows:
        match = re.fullmatch(r"command:([1-9][0-9]{0,18})", row.operation_id or "")
        if match:
            command_ids.append(int(match.group(1)))
    if command_ids:
        for command in db.exec(select(RunnerCommand).where(RunnerCommand.session_id == session_id,
                                                           RunnerCommand.id.in_(command_ids))).all():
            commands[command.id] = command
    fills = []
    for row in rows:
        try:
            details = json.loads(row.details_json or "{}")
        except (TypeError, ValueError):
            details = {}
        match = re.fullmatch(r"command:([1-9][0-9]{0,18})", row.operation_id or "")
        command = commands.get(int(match.group(1))) if match else None
        fills.append({"id": row.id, "at_ms": row.created_ms or _iso_ms(row.ts) or 0,
                      "action": str(details.get("action") or "").upper(),
                      "qty": details.get("executed_qty"), "price": details.get("fill_price"),
                      "signal_price": command.signal_price if command is not None else None,
                      "reason": command.reason if command is not None else ""})
    return fills


def _errors_since(db, session_id: int, cursor: int) -> list[dict]:
    query = select(RunSessionEvent).where(RunSessionEvent.session_id == session_id, RunSessionEvent.kind == "error",
                                          RunSessionEvent.id > cursor)
    if cursor:
        rows = list(db.exec(query.order_by(RunSessionEvent.id.asc()).limit(50)).all())
    else:
        rows = list(reversed(db.exec(query.order_by(RunSessionEvent.id.desc()).limit(5)).all()))
    return [{"id": row.id, "at_ms": row.created_ms or _iso_ms(row.ts) or 0, "message": row.message,
             "event_name": row.event_name} for row in rows]


def _default_candles(facts: dict) -> Optional[list]:
    from . import chart as chart_mod
    exchange = facts["exchange"]
    kwargs = {"exchange": exchange} if exchange != "binance" else {}
    data = chart_mod.get_candles(facts["symbol"], interval="5m", limit=chart_mod.MAX_LIMIT,
                                 market=facts["market"] if exchange == "binance" else "spot", **kwargs)
    return list(data.get("candles") or [])


def _default_feed(facts: dict, after_revision) -> Optional[dict]:
    from .agent_features.position_news.articles import read_article_feed
    if after_revision is None:
        return read_article_feed(facts["base"], after_revision=None, limit=1, include_analysis=False)
    return read_article_feed(facts["base"], after_revision=after_revision, limit=50, include_analysis=True)


def _default_wait(wait_args: dict) -> Optional[dict]:
    from . import entry_wait
    return entry_wait.for_session(**wait_args)


def _targets(db, now_ms: int) -> list[int]:
    running = list(db.exec(select(RunSession.id).where(RunSession.status == "running")).all())
    ended = list(db.exec(select(AgentCommentaryState.session_id).join(
        RunSession, RunSession.id == AgentCommentaryState.session_id).where(
        RunSession.status != "running", AgentCommentaryState.updated_ms >= now_ms - ENDED_LOOKBACK_MS,
        ~AgentCommentaryState.state_json.contains('"ended":true'))).all())
    return list(dict.fromkeys([*running, *ended]))


def evaluate_session(session_id: int, *, now_ms: Optional[int] = None, candles_for=None, feed_for=None,
                     wait_for=None) -> int:
    """세션 하나를 보고 말풍선을 남긴다. 새로 남긴 개수."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with get_session() as db:
        row = db.get(RunSession, session_id)
        if row is None:
            return 0
        stored = db.get(AgentCommentaryState, session_id)
        state = _load_state(stored.state_json if stored is not None else "")
        if state.get("ended"):
            return 0
        facts = session_facts(row)
        fills = _fills_since(db, session_id, int(state.get("fill_cursor") or 0))
        errors = _errors_since(db, session_id, int(state.get("err_cursor") or 0))
        wait_args = None
        if facts["status"] == "running" and not facts["in_position"]:
            from . import runner as runner_mod
            try:
                wait_args = runner_mod._entry_wait_args(row)
            except Exception:
                wait_args = None
    # 시세 · 기사는 DB 연결을 놓은 뒤에 읽는다(네트워크를 기다리는 동안 연결을 붙잡지 않게).
    candles = feed = wait = None
    if wait_args is not None:
        try:
            wait = (wait_for or _default_wait)(wait_args)
        except Exception:
            wait = None
    if facts["status"] == "running":
        try:
            candles = (candles_for or _default_candles)(facts)
        except Exception:
            candles = None
    try:
        feed = (feed_for or _default_feed)(facts, state.get("news_rev"))
    except Exception:
        feed = None
    says, next_state = decide(state, facts, fills=fills, errors=errors, candles=candles, feed=feed, wait=wait,
                              now_ms=now_ms)
    with get_session() as db:
        added = save(db, facts, says, next_state, now_ms=now_ms)
        db.commit()
    return added


def run_cycle(*, now_ms: Optional[int] = None, claim: bool = True, **sources) -> dict:
    """실행 중(과 방금 끝난) 세션을 한 바퀴 돈다. 한 세션의 실패가 다른 세션을 막지 않는다."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if claim:
        from .agent_features.position_news.articles import claim_maintenance
        # 두 프로세스가 같은 바퀴를 동시에 돌지 않게만 막는다(3초) — 체결이 깨운 바퀴가 정기 바퀴에 막히지 않게 짧게.
        if not claim_maintenance("agent-commentary", interval_seconds=3, now_ms=now_ms):
            return {"skipped": True, "sessions": 0, "added": 0}
    with get_session() as db:
        targets = _targets(db, now_ms)
    added = failed = 0
    for session_id in targets:
        try:
            added += evaluate_session(session_id, now_ms=now_ms, **sources)
            _FAILURES.pop(session_id, None)
        except Exception:
            failed += 1
            count = _FAILURES[session_id] = _FAILURES.get(session_id, 0) + 1
            # 같은 세션이 계속 실패해도 로그가 10초마다 쌓이지 않게 — 처음과 그 뒤 60번째마다만 자세히.
            log.warning("agent commentary failed (session=%s, times=%s)", session_id, count,
                        exc_info=count == 1 or count % 60 == 0)
    if len(_FAILURES) > 1000:
        _FAILURES.clear()
    return {"skipped": False, "sessions": len(targets), "added": added, "failed": failed}


_FAILURES: dict[int, int] = {}


def kst_clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, _KST).strftime("%H:%M")
