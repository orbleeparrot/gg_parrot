"""묶음 한도 — '지금 이 레그가 새로 들어가도 되는가' 를 묶음 전체를 보고 답한다.

진입 필터와 같은 자리(``CandleSim._entry_blocked``)에 꽂힌다. 그래서 청산 · 안전주문 ·
그리드 보충 · K 의 방어 숏은 이 관문을 지나지 않는다 — 설계 원칙 1 과 5 가 공짜로 따라온다.

상태를 따로 세지 않고 **레그 심에게 그때그때 물어본다.** 사건을 세면 재기동 복구 · 강제
청산 · 부분 청산마다 카운터를 맞춰야 하고, 1차에서 바로 그 종류의 어긋남이 차단 결함이
되었다. 지금 장부를 읽으면 어긋날 상태가 없다.
"""
from __future__ import annotations

from typing import List

from .schema import BundleRisk


def _inner(sim):
    """실시간은 ``LiveCandleSim`` 으로 감싼 심을 준다 — 장부는 ``.inner`` 에 있다."""
    return getattr(sim, "inner", sim)


def limit_note(risk: BundleRisk) -> str:
    """한도 문구만 필요할 때 — 자본 없이. ``BundleGate.note()`` 가 이것을 쓴다.

    요약 · 해설은 문구 한 줄이 필요할 뿐인데, 전에는 집행자를 더미 자본(``or 1.0``)으로
    만들어 불렀다. 문구는 한 곳에서만 나와야 하므로 모듈 함수로 내리고 집행자가 위임한다.
    """
    parts = []
    if risk.max_positions is not None:
        parts.append(f"한 번에 {risk.max_positions}종목까지")
    if risk.max_exposure_pct is not None:
        parts.append(f"총 노출 {risk.max_exposure_pct:g}% 까지")
    return " · ".join(parts)


class BundleGate:
    """묶음 한도의 집행자. 백테스트 루프와 ``paper.start_session`` 이 만들어 심에 꽂는다."""

    def __init__(self, risk: BundleRisk, total_capital: float) -> None:
        self._risk = risk
        self.max_positions = risk.max_positions
        self.max_exposure_pct = risk.max_exposure_pct
        self.total_capital = float(total_capital)
        self._sims: List[object] = []

    def register(self, sim) -> None:
        self._sims.append(_inner(sim))

    def blocks(self, sim) -> bool:
        """True 면 이 레그의 **새 진입**을 막는다. 청산에는 영향이 없다."""
        me = _inner(sim)
        if self.max_positions is not None and not me.in_position():
            # 이미 들고 있는 레그는 묻지 않는다 — 추가 매수는 새 종목이 아니다.
            held = sum(1 for s in self._sims if s.in_position())
            if held >= self.max_positions:
                return True
        if self.max_exposure_pct is not None:
            # 투입 자본(margin). 레버리지 걸린 명목금액이 아니다 — "총 노출 60%" 는 "내 자금의
            # 60% 까지 시장에 넣는다" 로 읽히고, 명목으로 세면 3배에서 한도를 100% 로 열어 둬도
            # 둘째 레그가 아예 거래하지 못한다. 1배에서는 ``수량 × 진입가`` 와 같은 값이다.
            # 시세를 끌어오지 않으므로 백테스트와 실시간이 같은 값을 본다(원칙 4).
            # 보유 중인 레그도 자본은 더 늘어나니 막는다.
            used = sum(s.committed_margin() for s in self._sims)
            if used >= self.total_capital * self.max_exposure_pct / 100.0:
                return True
        return False

    def note(self) -> str:
        """문구는 ``limit_note`` 한 곳에서만 만든다 — 두 벌이면 어긋나는 날이 온다."""
        return limit_note(self._risk)
