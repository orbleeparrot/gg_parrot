"""진입 대기 — 조건이 올 때까지 사지 않는 매크로가 포지션 없이 돌 때의 '무엇을 · 얼마나 · 언제'.

실행기(데스크톱 앱)는 하트비트 응답의 ``entry_wait`` 로, 웹은 세션 응답의 같은 칸으로 받아 그린다.
판정은 엔진(engine/candles.py · stepper.py)과 같은 규칙을 매크로 봉 간격의 최근 캔들로 다시 계산한 것이고,
RSI · 볼린저 · 이평선은 엔진의 상태 클래스를 그대로 쓴다.

 - I 변동성 돌파: 봉 고가 ≥ 그 봉 시가 + k × 직전 봉 변동폭 (+ 이평선 필터면 마감가 ≥ 이평선) · 봉 마감 때
 - F RSI: 마감 RSI ≤ 진입 기준(숏은 ≥ 청산 기준)이 확인 봉 수만큼 이어지면
 - G 볼린저: 되돌림은 마감가 ≤ 하단(숏 ≥ 상단), 돌파는 마감가 ≥ 상단(숏 ≤ 하단)
 - J 이평선: 단기 > 장기(숏 <)가 확인 봉 수만큼 이어지면
 - B 지정가 밴드: 가격 ≤ 매수가(숏 ≥ 매도가) · 3초마다
 - E 트레일링 스탑 눌림 진입: 체크포인트의 진입 목표(live_levels.entry_target)만 쓴다.
매크로의 진입 필터(entry_filter)도 엔진처럼 직전 마감봉까지의 값으로 다시 판정해 조건 목록(conditions)에 넣는다.
'사는 선을 넘었다(touched)'와 '이대로 마감되면 산다(reached)'는 다르다 — 이평선 필터 · 연속 봉 · 진입 필터가
남아 있으면 touched 여도 reached 가 아니다(2026-10-17 KRW-FORT: 고가가 사는 선을 넘었지만 마감가가 20봉 평균
아래라 안 샀는데 화면은 '마감되면 사요'라고 했다).
쿨다운 · 진입 보류 · 하루 손실 중단은 여기서 다루지 않는다(엔진 상태).
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .engine.candles import BollingerState, MAState, RSIState

WAITING_RULES = {"B", "E", "F", "G", "I", "J"}
STEP_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}
INTERVAL_LABEL = {
    "1m": "1분", "3m": "3분", "5m": "5분", "15m": "15분", "30m": "30분", "1h": "1시간", "2h": "2시간", "4h": "4시간",
    "6h": "6시간", "8h": "8시간", "12h": "12시간", "1d": "1일", "3d": "3일", "1w": "1주",
}
LOG_LIMIT = 6


def _num(value) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def fmt_price(value: float, symbol: str = "") -> str:
    """웹 fmtPrice 와 같은 자릿수 — 1,000 이상 2자리 · 1 이상 4자리 · 그 아래 5~8자리, 원화는 .00 을 감춘다."""
    a = abs(value)
    dp = 2 if a >= 1000 else 4 if a >= 1 else 5 if a >= 0.01 else 6 if a >= 0.0001 else 8
    text = f"{value:,.{dp}f}"
    keep = 0 if str(symbol).upper().startswith("KRW-") else 2
    if "." in text:
        head, tail = text.split(".")
        tail = tail.rstrip("0")
        tail = tail + "0" * max(0, keep - len(tail))
        text = f"{head}.{tail}" if tail else head
    return text


def _pct(from_: float, to: float) -> str:
    if not from_ or from_ <= 0:
        return ""
    pct = (to - from_) / from_ * 100
    a = abs(pct)
    return f"{'+' if pct >= 0 else '−'}{a:.1f}%" if a < 10 else f"{'+' if pct >= 0 else '−'}{a:.0f}%"


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v)) if v == v else 0.0


def is_waiting(macro: Optional[dict]) -> bool:
    if not macro or macro.get("rule_type") not in WAITING_RULES:
        return False
    if macro.get("rule_type") == "E":
        return (macro.get("params") or {}).get("entry_mode") == "dip"
    return True


def _series(rule: str, params: dict, short: bool, bars: list[dict]) -> list[Optional[dict]]:
    """마감 봉마다 {hit, value, target}. 엔진 상태 클래스를 봉 하나씩 먹여 같은 값을 만든다."""
    out: list[Optional[dict]] = []
    if rule == "I":
        k = _num(params.get("k"))
        k = 0.5 if k is None else k
        ma_period = int(_num(params.get("ma_filter_period")) or 0)
        ma = MAState("SMA", ma_period) if ma_period > 0 else None
        prev, ma_prev = None, None  # 엔진처럼 이평선 · 변동폭은 직전 봉까지의 값으로 판정한다(_roll 이 판정 뒤에 갱신)
        for bar in bars:
            if prev is None:
                out.append(None)
            else:
                target = bar["o"] + k * (prev["h"] - prev["l"])
                ma_ok = ma_prev is None or bar["c"] >= ma_prev
                out.append({"hit": bar["h"] >= target and ma_ok, "value": bar["h"], "target": target,
                            "touch": bar["h"] >= target, "ma": ma_prev, "ma_period": ma_period, "close": bar["c"]})
            ma_prev = ma.update(bar["c"]) if ma else None
            prev = bar
        return out
    if rule == "F":
        period = int(_num(params.get("rsi_period")) or 14)
        entry = _num(params.get("entry_threshold"))
        entry = 30.0 if entry is None else entry
        exit_ = _num(params.get("exit_threshold"))
        exit_ = 70.0 if exit_ is None else exit_
        state = RSIState(period)
        for bar in bars:
            v = state.update(bar["c"])
            out.append(None if v is None else {"hit": v >= exit_ if short else v <= entry, "value": v, "target": exit_ if short else entry})
        return out
    if rule == "G":
        state = BollingerState(int(_num(params.get("bb_period")) or 20), _num(params.get("bb_std")) or 2.0)
        breakout = params.get("strategy") == "breakout"
        use_upper = (not short) if breakout else short
        for bar in bars:
            bands = state.update(bar["c"])
            if bands is None:
                out.append(None)
                continue
            mid, upper, lower = bands
            target = upper if use_upper else lower
            out.append({"hit": bar["c"] >= upper if use_upper else bar["c"] <= lower, "value": bar["c"], "target": target, "mid": mid,
                        "above": use_upper})
        return out
    if rule == "J":
        ma_type = params.get("ma_type") or "SMA"
        fast = MAState(ma_type, int(_num(params.get("fast_period")) or 20))
        slow = MAState(ma_type, int(_num(params.get("slow_period")) or 60))
        for bar in bars:
            f, s = fast.update(bar["c"]), slow.update(bar["c"])
            if f is None or s is None or not s:
                out.append(None)
                continue
            gap = (f - s) / s
            out.append({"hit": gap < 0 if short else gap > 0, "value": gap})
        return out
    if rule == "B":
        target = _num(params.get("sell_price") if short else params.get("buy_price"))
        for bar in bars:
            out.append(None if target is None else {"hit": bar["h"] >= target if short else bar["l"] <= target,
                                                    "value": bar["h"] if short else bar["l"], "target": target})
        return out
    return [None for _ in bars]


def _with_confirm(series: list[Optional[dict]], confirm: int) -> list[Optional[dict]]:
    streak = 0
    out = []
    for row in series:
        if not row:
            streak = 0
            out.append(row)
            continue
        streak = streak + 1 if row["hit"] else 0
        out.append({**row, "streak": streak, "enter": streak >= confirm})
    return out


def _entry_filter(macro: dict):
    """매크로의 진입 필터를 엔진과 같은 판정기로. 없거나 읽을 수 없으면 None."""
    spec = macro.get("entry_filter")
    if not spec:
        return None
    try:
        from .engine.entry_filter import FilterEval
        from .engine.schema import EntryFilter
        return FilterEval(EntryFilter.model_validate(spec))
    except Exception:
        return None


def _filter_flags(macro: dict, bars: list[dict]) -> tuple[list[Optional[bool]], Optional[bool], str]:
    """봉마다 그 봉의 진입을 필터가 허용했는지 · 지금 봉의 허용 여부 · 필터 이름.

    엔진은 전략 '뒤'에 필터를 갱신하므로 봉의 진입은 직전 마감봉까지의 값으로 판정된다 —
    그래서 진행 중인 봉의 허용 여부는 마감 전에 이미 정해져 있다.
    """
    f = _entry_filter(macro)
    if f is None:
        return [None] * len(bars), None, ""
    flags: list[Optional[bool]] = []
    for bar in bars:
        flags.append(f.allows())
        f.update(bar["c"], volume=_num(bar.get("v")))
    return flags, f.allows(), f.note()


def _gate(series: list[Optional[dict]], flags: list[Optional[bool]]) -> list[Optional[dict]]:
    """진입(enter)에 진입 필터를 더한다. 필터가 없으면 그대로."""
    out = []
    for row, flag in zip(series, flags):
        if not row or flag is None:
            out.append(row)
            continue
        out.append({**row, "filter_ok": flag, "enter": bool(row.get("enter", row.get("hit"))) and flag})
    return out


def typical_wait(series: list[Optional[dict]], step_ms: int) -> Optional[dict]:
    """최근 봉에서 조건이 새로 맞은 횟수 · 평균 간격 · 가장 길게 안 맞은 구간."""
    valid = [row for row in series if row]
    if len(valid) < 10:
        return None
    episodes, prev, gap, longest = 0, False, 0, 0
    for row in series:
        on = bool(row and row.get("enter", row.get("hit")))
        if on and not prev:
            episodes += 1
        gap = 0 if on else gap + 1
        longest = max(longest, gap)
        prev = on
    span = len(valid) * step_ms
    return {"episodes": episodes, "span_ms": span, "avg_ms": (span // episodes) if episodes else None, "longest_ms": longest * step_ms}


def _describe(rule: str, row: dict, fp, confirm: int) -> str:
    if rule == "I":
        return f"고가 {fp(row['value'])} · 사는 선 {fp(row['target'])}"
    if rule == "F":
        return f"RSI {row['value']:.1f} · 사는 선 {row['target']:g}" + (f" · {row['streak']}/{confirm}봉" if confirm > 1 else "")
    if rule == "G":
        return f"마감 {fp(row['value'])} · 밴드 {fp(row['target'])}"
    if rule == "J":
        return f"두 선 간격 {row['value'] * 100:.2f}%" + (f" · {row['streak']}/{confirm}봉" if confirm > 1 else "")
    return ""


def _ladder(marks: list[tuple], now: tuple, zone_at: float, side: str, clamp: Optional[tuple] = None) -> dict:
    """값 눈금 하나(왼쪽이 작은 값)에 선 여럿 · 지금 점 · 사는 구역.

    marks 는 (key, 이름, 값, 표시, 주 선인지), now 는 (값, 표시). side 는 사는 구역이 선의 위(above, 올라가야 삼)인지
    아래(below, 내려가야 삼)인지 — RSI 30 아래로 내려가면 사는 매크로를 '오른쪽 끝이 사는 선'인 진행 막대로 그리면
    값은 내려가는데 막대는 오른쪽으로 차서 거꾸로 읽혔다(2026-10-17).
    """
    values = [m[2] for m in marks] + [now[0]]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.15 or abs(now[0]) * 0.01 or 1.0
    lo, hi = lo - pad, hi + pad
    if clamp:
        lo, hi = max(clamp[0], lo), min(clamp[1], hi)

    def pos(v):
        return round(_clamp01((v - lo) / (hi - lo)), 4) if hi > lo else 0.5

    return {"marks": [{"key": k, "label": label, "value": text, "pos": pos(v), "main": main} for k, label, v, text, main in marks],
            "now": {"label": "지금", "value": now[1], "pos": pos(now[0])}, "zone": pos(zone_at), "zone_side": side}


_BATCHIM = {"0": True, "1": True, "3": True, "6": True, "7": True, "8": True}  # 영 · 일 · 삼 · 육 · 칠 · 팔 — 받침 있음
_RIEUL = {"1", "7", "8"}  # 일 · 칠 · 팔 — ㄹ 받침은 '로'


def _josa(text: str, pair: str) -> str:
    """숫자로 끝나는 말 뒤 조사 — pair 는 '이/가' · '으로/로'. 숫자를 읽는 소리의 받침으로 고른다."""
    last = str(text)[-1:]
    with_final = _BATCHIM.get(last, False)
    a, b = pair.split("/")
    if pair == "으로/로":
        return text + (a if with_final and last not in _RIEUL else b)
    return text + (a if with_final else b)


def _why(rule: str, row: dict, fp, confirm: int, short: bool, filter_note: str) -> str:
    """마감된 봉에서 사지 않은 이유 한 줄. 샀으면 빈 문자열."""
    if row.get("enter", row.get("hit")):
        return ""
    if rule == "I":
        if not row.get("touch"):
            return f"고가 {_josa(fp(row['value']), '이/가')} 사는 선 {fp(row['target'])}에 못 미쳤어요"
        if row.get("ma") is not None and row["close"] < row["ma"]:
            return f"{_josa(fp(row['close']), '으로/로')} 끝나서 {row['ma_period']}봉 평균 {fp(row['ma'])}에 못 미쳤어요"
    elif rule == "F":
        if not row.get("hit"):
            return f"마감 RSI {row['value']:.1f} — {row['target']:g} {'위' if short else '아래'}가 아니었어요"
        if row.get("streak", 1) < confirm:
            return f"{confirm}봉 연속 중 {row['streak']}봉째예요"
    elif rule == "G":
        if not row.get("hit"):
            return f"마감가 {_josa(fp(row['value']), '이/가')} 밴드 {fp(row['target'])} 안쪽이었어요"
    elif rule == "J":
        if not row.get("hit"):
            return "두 선이 아직 엇갈리지 않았어요"
        if row.get("streak", 1) < confirm:
            return f"{confirm}봉 연속 중 {row['streak']}봉째예요"
    if row.get("filter_ok") is False:
        return f"진입 필터({filter_note})가 막았어요"
    return ""


def compute(macro: dict, *, candles: list[dict], last_price: Optional[float] = None, side: Optional[str] = None,
            started_ms: Optional[int] = None, levels: Optional[dict] = None, symbol: str = "",
            now_ms: Optional[int] = None) -> Optional[dict]:
    """진입 대기 모델(해당 없으면 None). candles 는 매크로 간격 · 마지막 봉은 closed:false 일 수 있다."""
    if not is_waiting(macro):
        return None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rule = macro["rule_type"]
    params = macro.get("params") or {}
    short = (side or macro.get("position_side") or "long") == "short"
    interval = macro.get("candle_interval") or "1d"
    step = STEP_MS.get(interval, STEP_MS["1d"])
    bars = [b for b in candles if _num(b.get("c")) is not None]
    closed = [b for b in bars if b.get("closed") is not False]
    forming = bars[-1] if bars and bars[-1].get("closed") is False else None
    price = _num(last_price) if (_num(last_price) or 0) > 0 else (forming or (closed[-1] if closed else {})).get("c")
    confirm = max(1, int(_num(params.get("confirm_candles")) or 1))
    sym = symbol or macro.get("symbol") or ""

    def fp(v):
        return fmt_price(v, sym)

    realtime = rule == "B"
    last_closed = closed[-1] if closed else None
    next_check = None if realtime else (forming["t"] + step if forming else (last_closed["t"] + 2 * step if last_closed else None))
    checked = [b for b in closed if started_ms is not None and b["t"] + step > started_ms]
    series = _series(rule, params, short, closed)
    if rule in ("F", "J"):
        series = _with_confirm(series, confirm)
    flags, filter_now, filter_note = _filter_flags(macro, closed) if not realtime else ([None] * len(closed), None, "")
    series = _gate(series, flags)
    last_row = series[-1] if series else None
    model = {
        "rule": rule, "interval": interval, "interval_label": INTERVAL_LABEL.get(interval, interval), "realtime": realtime,
        "sentence": "", "headline": "", "distance": "", "gap": "", "remain": "", "touched": False, "reached": False, "conditions": [],
        # ladder: 돌파 + 평균선(사는 구역이 선 위). scale: 같은 모양에 zone_side 를 담은 값 눈금(RSI · 밴드 · 지정가 · 눌림) —
        # 이름을 나눈 건 0.20.4 실행기가 ladder 의 사는 구역을 늘 선 위로 칠해서다(아래 구역을 ladder 로 보내면 거꾸로 칠한다).
        "meter": None, "ladder": None, "scale": None, "buy_line": None,
        "next_check_ms": next_check, "checks": None if realtime else len(checked),
        "last_check_ms": (checked[-1]["t"] + step) if checked else None,
        "typical": None if rule == "E" else typical_wait(series, step), "log": [],
        # 방금 마감된 봉 — 샀는지와 안 샀으면 왜인지(중계가 '이번 봉은 안 샀어요'를 말할 때 쓴다)
        "last_bar": None if realtime or not last_row or not last_closed else {
            "at_ms": last_closed["t"] + step, "entered": bool(last_row.get("enter", last_row.get("hit"))),
            "why": _why(rule, last_row, fp, confirm, short, filter_note)},
    }

    soon = "곧 들어가요" if short else "곧 사요"  # 실행기 목록 칸에 들어가는 짧은 거리(gap)
    verb = "팔아서 들어가요" if short else "사요"
    touched = False
    lead = ""       # 조건 하나가 맞았을 때 거리 칸의 앞부분
    conds: list[dict] = []  # 첫 줄은 주 조건, 나머지는 함께 맞아야 하는 조건(이평선 필터 · 연속 봉 · 진입 필터)

    def cond(key, label, now, ok, *, wait="", gap="", at_close=True):
        return {"key": key, "label": label, "now": now, "ok": ok, "at_close": at_close, "wait": wait, "gap": gap}

    if rule == "I":
        k = _num(params.get("k"))
        k = 0.5 if k is None else k
        stale = forming is not None and forming["t"] + step <= now_ms  # 거래소 봉이 아직 안 바뀜 — 지난 봉의 사는 선을 쓰면 안 된다
        if not forming or stale or not last_closed or price is None:
            model["sentence"] = "새 봉이 열리면 사는 가격이 정해져요." if closed else "첫 봉이 마감되면 사는 가격이 정해져요."
            return model
        bar, prev = forming, last_closed
        target = bar["o"] + k * (prev["h"] - prev["l"])
        high = max(bar["h"], price)
        touched = high >= target
        lead = "사는 선을 넘었어요"
        conds.append(cond("line", f"사는 선 {fp(target)} 넘기",
                          f"넘음 · 고가 {fp(high)}" if touched else _pct(target, price), touched, at_close=False))
        sentence = f"가격이 {fp(target)} 위로 올라가면 사요."
        headline = f"{fp(target)} 넘으면 사요"
        bind = target  # 실제로 넘어야 하는 값 — 평균선이 사는 선보다 위면 '평균선 위에서 끝나기' 하나로 두 조건이 다 맞는다
        ma_period = int(_num(params.get("ma_filter_period")) or 0)
        ma_prev = None
        if ma_period > 0:
            ma_state = MAState("SMA", ma_period)
            for b in closed:
                ma_prev = ma_state.update(b["c"])
        ladder = None
        if ma_prev is not None:
            ma_ok = price >= ma_prev
            conds.append(cond("ma", f"{ma_period}봉 평균 {fp(ma_prev)} 위에서 끝나기",
                              f"위 · {_pct(ma_prev, price)}" if ma_ok else _pct(ma_prev, price), ma_ok,
                              wait=f"{ma_period}봉 평균 {fp(ma_prev)} 위에서 끝나야 사요", gap=f"평균선 {_pct(ma_prev, price)}"))
            if ma_prev >= target or (touched and not ma_ok):
                bind = ma_prev
                sentence = headline = f"{fp(ma_prev)} 위에서 끝나면 사요"
            # 가격 막대 하나에 두 선 — 지금 점이 어느 선 사이에 있는지로 '왜 안 사지'가 보이게.
            values = [bar["o"], price, high, target, ma_prev]
            lo, hi = min(values), max(values)
            pad = (hi - lo) * 0.12 or abs(price) * 0.01 or 1.0
            lo, hi = lo - pad, hi + pad

            def pos(v):
                return round(_clamp01((v - lo) / (hi - lo)), 4)

            ladder = {
                "marks": [{"key": "line", "label": "사는 선", "value": fp(target), "pos": pos(target), "main": bind == target},
                          {"key": "ma", "label": f"{ma_period}봉 평균", "value": fp(ma_prev), "pos": pos(ma_prev), "main": bind == ma_prev}],
                "now": {"label": "지금", "value": fp(price), "pos": pos(price)},
                "zone": pos(max(target, ma_prev)), "zone_side": "above",
            }
        both = touched and (ma_prev is None or price >= ma_prev)
        if both:
            distance = "사는 선과 평균선을 모두 넘었어요" if ma_prev is not None else "사는 선을 넘었어요 · 이대로 끝나면 사요"
        elif touched:
            distance = f"지금 {fp(price)} · {_pct(price, ma_prev)} 더 · 사는 선은 넘었고 {ma_period}봉 평균선이 남았어요"
        else:
            distance = f"지금 {fp(price)} · {_pct(price, bind)} 더 오르면"
        model.update(
            sentence=sentence,
            headline=headline,
            distance=distance,
            gap=soon if touched else _pct(price, bind),
            ladder=ladder,
            meter={"from": {"label": "이 봉 시가", "value": fp(bar["o"])}, "to": {"label": "사는 선", "value": fp(target)},
                   "now": fp(price), "progress": _clamp01((price - bar["o"]) / (target - bar["o"])) if target != bar["o"] else 0.0},
            buy_line={"price": target, "label": f"사는 선 {fp(target)}"},
        )
    elif rule == "F":
        period = int(_num(params.get("rsi_period")) or 14)
        entry = _num(params.get("entry_threshold"))
        entry = 30.0 if entry is None else entry
        exit_ = _num(params.get("exit_threshold"))
        exit_ = 70.0 if exit_ is None else exit_
        state = RSIState(period)
        now_rsi = None
        for c in [b["c"] for b in closed] + ([price] if price is not None else []):
            now_rsi = state.update(c)
        th = exit_ if short else entry
        confirm_text = f" {confirm}봉 연속" if confirm > 1 else ""
        touched = now_rsi is not None and (now_rsi >= th if short else now_rsi <= th)
        if now_rsi is not None:
            lead = f"지금 RSI {now_rsi:.1f} · 조건이 맞았어요"
            conds.append(cond("rsi", f"RSI {th:g} {'위' if short else '아래'}로 마감", f"지금 {now_rsi:.1f}", touched))
            if confirm > 1:
                streak = int((last_row or {}).get("streak") or 0)
                conds.append(cond("streak", f"{confirm}봉 연속", f"지난 봉까지 {streak}봉", streak + 1 >= confirm,
                                  wait=f"이대로 끝나면 {streak + 1}/{confirm}봉이에요", gap=f"{streak + 1}/{confirm}봉"))
        model.update(
            sentence=(f"RSI({period})가 {th:g} 위로 오르면{confirm_text} 팔아서 들어가요." if short
                      else f"RSI({period})가 {th:g} 아래로 내려가면{confirm_text} 사요."),
            distance=("RSI를 계산하고 있어요" if now_rsi is None else
                      f"{lead}, 이대로 끝나면 {verb}" if touched else
                      f"지금 RSI {now_rsi:.1f} · {abs(now_rsi - th):.1f} 더 {'오르면' if short else '내려가면'}"),
            gap="" if now_rsi is None else soon if touched else f"RSI {now_rsi:.1f}",
            remain="" if now_rsi is None or touched else f"{abs(now_rsi - th):.1f}",
            meter=None if now_rsi is None else {
                "from": {"label": "청산 기준", "value": f"{(entry if short else exit_):g}"}, "to": {"label": "사는 선", "value": f"{th:g}"},
                "now": f"{now_rsi:.1f}",
                "progress": _clamp01((now_rsi - entry) / (exit_ - entry) if short else (exit_ - now_rsi) / (exit_ - entry)) if exit_ != entry else 0.0},
        )
        if now_rsi is not None:
            other = entry if short else exit_
            model["scale"] = _ladder([("line", "들어가는 선" if short else "사는 선", th, f"{th:g}", True),
                                       ("exit", "청산 기준", other, f"{other:g}", False)],
                                      (now_rsi, f"{now_rsi:.1f}"), th, "above" if short else "below", clamp=(0.0, 100.0))
    elif rule == "G":
        state = BollingerState(int(_num(params.get("bb_period")) or 20), _num(params.get("bb_std")) or 2.0)
        bands = None
        for c in [b["c"] for b in closed] + ([price] if price is not None else []):
            bands = state.update(c)
        if bands is None or price is None:
            model["sentence"] = "밴드를 계산하고 있어요."
            return model
        mid, upper, lower = bands
        breakout = params.get("strategy") == "breakout"
        use_upper = (not short) if breakout else short
        target = upper if use_upper else lower
        name = "상단" if use_upper else "하단"
        touched = price >= target if use_upper else price <= target
        lead = f"지금 {fp(price)} · 밴드를 넘었어요"
        conds.append(cond("band", f"마감가가 {name} 밴드 {fp(target)} {'위' if use_upper else '아래'}", f"지금 {fp(price)}", touched))
        model.update(
            sentence=f"봉 마감가가 {name} 밴드 {fp(target)} {'위' if use_upper else '아래'}면 {verb}.",
            distance=(f"{lead}, 이대로 끝나면 {verb}" if touched
                      else f"지금 {fp(price)} · {_pct(price, target)} 더 {'오르면' if use_upper else '내려가면'}"),
            gap=soon if touched else _pct(price, target),
            meter={"from": {"label": "중앙선", "value": fp(mid)}, "to": {"label": f"{name} 밴드", "value": fp(target)}, "now": fp(price),
                   "progress": _clamp01((price - mid) / (target - mid)) if target != mid else 0.0},
            buy_line={"price": target, "label": f"사는 선({name} 밴드) {fp(target)}"},
        )
        model["scale"] = _ladder([("line", f"{name} 밴드", target, fp(target), True), ("mid", "중앙선", mid, fp(mid), False)],
                                  (price, fp(price)), target, "above" if use_upper else "below")
    elif rule == "J":
        fpd = int(_num(params.get("fast_period")) or 20)
        spd = int(_num(params.get("slow_period")) or 60)
        ma_type = params.get("ma_type") or "SMA"
        fast, slow = MAState(ma_type, fpd), MAState(ma_type, spd)
        f = s = None
        for c in [b["c"] for b in closed] + ([price] if price is not None else []):
            f, s = fast.update(c), slow.update(c)
        if f is None or s is None or not s:
            model["sentence"] = "이동평균을 계산하고 있어요."
            return model
        gap = (f - s) / s
        before = series[-6] if len(series) > 5 else None
        narrowing = None if not before else abs(gap) < abs(before["value"])
        touched = gap < 0 if short else gap > 0
        streak = int((last_row or {}).get("streak") or 0)
        lead = f"{fpd}선이 {'아래' if short else '위'}에 있어요"
        conds.append(cond("cross", f"{fpd}선이 {spd}선 {'아래' if short else '위'}", f"간격 {abs(gap) * 100:.2f}%", touched))
        if confirm > 1:
            conds.append(cond("streak", f"{confirm}봉 연속", f"지난 봉까지 {streak}봉", streak + 1 >= confirm,
                              wait=f"{max(1, confirm - streak)}봉 더 이어지면 사요", gap=f"{streak + 1}/{confirm}봉"))
        model.update(
            sentence=f"{fpd}선이 {spd}선 {'아래로 내려가면 팔아서 들어가요' if short else '위로 올라가면 사요'}.",
            distance=(f"{lead} · 이대로 끝나면 {verb}" if touched
                      else f"두 선 간격 {abs(gap) * 100:.2f}%" + ("" if narrowing is None else " · 좁혀지는 중" if narrowing else " · 벌어지는 중")),
            gap=soon if touched else f"간격 {abs(gap) * 100:.2f}%",
        )
    elif rule == "B":
        target = _num(params.get("sell_price") if short else params.get("buy_price"))
        other = _num(params.get("buy_price") if short else params.get("sell_price"))
        if target is None or price is None:
            return None
        touched = price >= target if short else price <= target
        model.update(
            sentence=f"가격이 {fp(target)} {'이상으로 오르면 팔아서 들어가요' if short else '이하로 내려오면 사요'}.",
            distance="가격이 닿았어요 · 곧 사요" if touched else f"지금 {fp(price)} · {_pct(price, target)} 더 {'오르면' if short else '내려오면'}",
            gap=soon if touched else _pct(price, target),
            meter=None if other is None or other == target else {
                "from": {"label": "청산가" if short else "매도가", "value": fp(other)}, "to": {"label": "사는 가격", "value": fp(target)},
                "now": fp(price), "progress": _clamp01((price - other) / (target - other))},
            buy_line={"price": target, "label": f"사는 선 {fp(target)}"},
        )
        marks = [("line", "들어가는 가격" if short else "사는 가격", target, fp(target), True)]
        if other is not None and other != target:
            marks.append(("other", "청산가" if short else "매도가", other, fp(other), False))
        model["scale"] = _ladder(marks, (price, fp(price)), target, "above" if short else "below")
    elif rule == "E":
        target = _num((levels or {}).get("entry_target"))
        dip = _num(params.get("entry_dip"))
        dip = 3.0 if dip is None else dip
        if not target or target <= 0 or price is None:
            model.update(sentence=f"기준가에서 {dip:g}% 내려오면 사요.", distance="첫 봉이 마감되면 사는 가격이 정해져요.")
            return model
        ref = target / (1 - dip / 100) if dip < 100 else target
        touched = price <= target
        lead = "가격이 닿았어요"
        conds.append(cond("line", f"사는 가격 {fp(target)}까지 내려오기", f"지금 {fp(price)}", touched, at_close=False))
        model.update(
            sentence=f"가격이 {fp(target)}까지 내려오면 사요.",
            distance="가격이 닿았어요 · 봉이 마감되면 사요" if touched else f"지금 {fp(price)} · {_pct(price, target)} 더 내려오면",
            gap=soon if touched else _pct(price, target),
            meter={"from": {"label": "기준가", "value": fp(ref)}, "to": {"label": "사는 가격", "value": fp(target)}, "now": fp(price),
                   "progress": _clamp01((ref - price) / (ref - target)) if ref != target else 0.0},
            buy_line={"price": target, "label": f"사는 선 {fp(target)}"},
        )
        model["scale"] = _ladder([("line", "사는 가격", target, fp(target), True), ("ref", "기준가", ref, fp(ref), False)],
                                  (price, fp(price)), target, "below")

    if filter_now is not None:
        conds.append(cond("filter", f"진입 필터 · {filter_note}", "직전 봉 기준 통과" if filter_now else "직전 봉 기준 막힘", filter_now,
                          wait=f"진입 필터({filter_note})가 막고 있어요", gap="필터 대기", at_close=False))
    blocked = next((c for c in conds[1:] if c["ok"] is False), None)
    if not model["remain"] and not touched:
        model["remain"] = model["gap"]  # 사는 선까지 남은 거리만(실행기 숫자 칸) — RSI 는 위에서 점수 차로
    reached = bool(touched) and blocked is None
    model.update(touched=bool(touched), reached=reached,
                 conditions=[{k: v for k, v in c.items() if k != "gap"} for c in conds])
    # 맨 위 큰 글씨 — 지금 상태 한 줄. 긴 조건 문장을 늘어놓지 않는다(조건은 체크 목록 · 막대가 맡는다).
    if reached:
        model["headline"] = "곧 사요" if realtime else "이대로 끝나면 사요"
    elif not model["headline"]:
        model["headline"] = model["sentence"].rstrip(".")
    if touched and blocked:
        # 주 조건은 맞았지만 함께 맞아야 하는 조건이 남았다 — '마감되면 사요'라고 하면 안 산 봉이 버그로 보인다.
        model["gap"] = blocked["gap"]
        if not (rule == "I" and blocked["key"] == "ma"):  # 돌파 + 평균선은 위에서 이미 '평균선이 남았어요'로 말함
            model["distance"] = f"{lead} · {blocked['wait']}"

    if not realtime and rule != "E" and started_ms is not None:
        rows = []
        for bar, row in zip(reversed(closed), reversed(series)):
            if bar["t"] + step <= started_ms or len(rows) >= LOG_LIMIT:
                break
            if row:
                rows.append({"at_ms": bar["t"] + step, "text": _describe(rule, row, fp, confirm), "hit": bool(row.get("enter", row.get("hit")))})
        model["log"] = rows
    return model


# --- 세션 · 미리보기 -----------------------------------------------------------
_memo: dict = {}
_memo_lock = threading.Lock()
MEMO_SECONDS = 10


def _candles_for(macro: dict, symbol: str, market: str) -> list[dict]:
    from . import chart as chart_mod
    exchange = macro.get("exchange") or "binance"
    kwargs = {"exchange": exchange} if exchange != "binance" else {}
    data = chart_mod.get_candles(symbol, interval=macro.get("candle_interval") or "1d", limit=chart_mod.MAX_LIMIT,
                                 market=market if exchange == "binance" else "spot", **kwargs)
    return list(data.get("candles") or [])


def for_session(*, session_id: int, macro: Optional[dict], symbol: str, market: str, side: str, last_price: float,
                started_at: str, in_position: bool, levels: Optional[dict] = None) -> Optional[dict]:
    """세션 하나의 진입 대기 모델(10초 메모). 포지션이 있거나 해당 없으면 None. 시세를 못 받아도 None."""
    if in_position or not is_waiting(macro):
        return None
    key = (session_id, round(float(last_price or 0), 10))
    now = time.monotonic()
    with _memo_lock:
        hit = _memo.get(session_id)
        if hit and hit[0] == key and now - hit[1] < MEMO_SECONDS:
            return hit[2]
    try:
        from datetime import datetime
        started_ms = int(datetime.fromisoformat(str(started_at).replace("Z", "+00:00")).timestamp() * 1000) if started_at else None
        model = compute(macro, candles=_candles_for(macro, symbol, market), last_price=last_price, side=side,
                        started_ms=started_ms, levels=levels, symbol=symbol)
    except Exception:
        return None
    with _memo_lock:
        if len(_memo) > 2000:
            _memo.clear()
        _memo[session_id] = (key, now, model)
    return model


def preview(macro: dict, *, market: str = "spot") -> dict:
    """시작 전 안내용 — 이 매크로가 기다리는 매크로인지, 조건 문장과 최근 조건이 맞은 횟수 · 간격."""
    if not is_waiting(macro):
        return {"waiting": False}
    symbol = str(macro.get("symbol") or "")
    try:
        model = compute(macro, candles=_candles_for(macro, symbol, market), symbol=symbol)
    except Exception:
        model = None
    return {"waiting": True, "sentence": (model or {}).get("sentence", ""), "typical": (model or {}).get("typical"),
            "interval_label": INTERVAL_LABEL.get(macro.get("candle_interval") or "1d", "")}
