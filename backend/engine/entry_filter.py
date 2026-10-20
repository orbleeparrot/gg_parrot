"""진입 필터 판정 — 마감봉 종가를 하나씩 먹고 '지금 진입해도 되는가'를 답한다.

심(sim) 클래스가 candles.py 와 stepper.py 에 나뉘어 있어 판정을 여기 한 곳에 둔다.
지표 계산은 candles.py 의 상태 기계를 그대로 재사용한다 — 옮기지 않는다(F·G·J 가 쓴다).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from .candles import BollingerState, MAState, RSIState
from .schema import EntryFilter, FilterKind, Macro


class _VolumeState:
    """직전 ``period`` 봉의 평균 거래량. 이 봉은 기준에 넣지 않는다.

    이 봉을 기준에 넣으면 ``multiple > period`` 일 때 거래량이 아무리 커도 절대 통과하지 못한다.
    직전 창이 안 차면 None. 거래량을 모르는(None) 봉은 창에 넣지 않는다 — 모르는 값이 기준이 되면 안 된다.
    """

    def __init__(self, period: int) -> None:
        self.period = period
        self._win: Deque[float] = deque(maxlen=period)

    def update(self, volume: Optional[float]) -> Optional[float]:
        if volume is None:
            return None
        avg = sum(self._win) / self.period if len(self._win) == self.period else None
        self._win.append(float(volume))     # 평균을 낸 뒤에 넣는다
        return avg


class FilterEval:
    """필터 하나의 상태와 판정. ``allows()`` 는 값이 없으면 항상 False."""

    def __init__(self, spec: EntryFilter) -> None:
        self.kind = spec.kind
        self.p = dict(spec.params)
        self._allows = False
        if self.kind is FilterKind.MA:
            self._state = MAState(self.p["ma_type"], int(self.p["period"]))
        elif self.kind is FilterKind.RSI:
            self._state = RSIState(int(self.p["period"]))
        elif self.kind is FilterKind.BOLLINGER:
            self._state = BollingerState(int(self.p["period"]), float(self.p["num_std"]))
        elif self.kind is FilterKind.VOLUME:
            self._state = _VolumeState(int(self.p["period"]))
        else:
            # 새 FilterKind 를 더하고 여기를 안 고치면 바로 터진다 -- 조용히 다른 필터로
            # 동작하는 것보다 낫다. 스키마가 모르는 kind 를 막으므로 API 로는 닿지 않는다.
            raise ValueError(f"unsupported filter kind: {self.kind}")

    def update(self, close: float, volume: Optional[float] = None) -> None:
        if self.kind is FilterKind.VOLUME:
            avg = self._state.update(volume)
            # 기준은 직전 봉들의 평균이다(이 봉 제외). 직전 창이 안 찼거나, 이 봉의 거래량을 모르거나,
            # 평균이 0(거래가 없었음 = 급증의 반대)이면 막는다.
            self._allows = (
                avg is not None and volume is not None and avg > 0
                and float(volume) >= avg * float(self.p["multiple"])
            )
            return

        value = self._state.update(close)
        if value is None:
            self._allows = False          # 아직 모른다 -> 막는다
            return

        if self.kind is FilterKind.MA:
            self._allows = close > value if self.p["side"] == "above" else close < value
        elif self.kind is FilterKind.RSI:
            low, high = self.p.get("min"), self.p.get("max")
            self._allows = (low is None or value >= low) and (high is None or value <= high)
        elif self.kind is FilterKind.BOLLINGER:
            _mid, upper, lower = value
            zone = self.p["zone"]
            if zone == "below_lower":
                self._allows = close < lower
            elif zone == "above_upper":
                self._allows = close > upper
            else:
                self._allows = lower <= close <= upper
        else:
            raise ValueError(f"unsupported filter kind: {self.kind}")

    def allows(self) -> bool:
        return self._allows

    def note(self) -> str:
        p = self.p
        if self.kind is FilterKind.MA:
            where = "위" if p["side"] == "above" else "아래"
            return f"{p['period']}봉 {p['ma_type']} 이동평균 {where}"
        if self.kind is FilterKind.RSI:
            low, high = p.get("min"), p.get("max")
            if low is not None and high is not None:
                return f"RSI({p['period']}) {low:g}~{high:g}"
            if high is not None:
                return f"RSI({p['period']}) {high:g} 이하"
            return f"RSI({p['period']}) {low:g} 이상"
        if self.kind is FilterKind.BOLLINGER:
            zones = {"below_lower": "하단 밖", "above_upper": "상단 밖", "inside": "밴드 안"}
            return f"볼린저({p['period']}, {p['num_std']:g}σ) {zones[p['zone']]}"
        if self.kind is FilterKind.VOLUME:
            return f"거래량이 {p['period']}봉 평균의 {p['multiple']:g}배 이상"
        raise ValueError(f"unsupported filter kind: {self.kind}")


def make_filter(macro: Macro) -> Optional[FilterEval]:
    """필터가 없으면 None — 호출부는 None 을 '관문 없음' 으로 읽는다."""
    return None if macro.entry_filter is None else FilterEval(macro.entry_filter)
