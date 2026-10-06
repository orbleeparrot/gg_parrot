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


class BundleGate:
    """묶음 한도의 집행자. 백테스트 루프와 ``paper.start_session`` 이 만들어 심에 꽂는다."""

    def __init__(self, risk: BundleRisk, total_capital: float) -> None:
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
            # 진입 기준 명목금액(수량 × 평균 진입가). 시세를 끌어오지 않으므로 백테스트와
            # 실시간이 같은 값을 본다(원칙 4). 보유 중인 레그도 금액은 더 늘어나니 막는다.
            used = sum(s.total_qty() * s.avg_entry() for s in self._sims)
            if used >= self.total_capital * self.max_exposure_pct / 100.0:
                return True
        return False

    def note(self) -> str:
        parts = []
        if self.max_positions is not None:
            parts.append(f"한 번에 {self.max_positions}종목까지")
        if self.max_exposure_pct is not None:
            parts.append(f"총 노출 {self.max_exposure_pct:g}% 까지")
        return " · ".join(parts)
