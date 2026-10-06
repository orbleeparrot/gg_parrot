"""거래소 어댑터 — 봇은 돈과 수량으로만 말하고, 거래소 사정은 여기 안에 있다.

수수료 모델이 거래소마다 다르다: 바이낸스 현물은 매수 수수료를 기초자산에서 떼므로
실제 보유 수량이 체결 수량보다 적고, 업비트·빗썸은 원화에서 떼므로 체결 수량이 그대로
보유 수량이다. 이 차이를 봇 본문에서 분기하면 가장 예민한 코드에 거래소 지식이 번진다.
그래서 브로커가 acquired_qty 를 채워서 돌려준다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 더 기다려도 바뀌지 않는 상태. OPEN 은 아직 끝나지 않은 주문이다.
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED"})


@dataclass(frozen=True)
class Order:
    """어느 거래소에서 왔든 봇이 같은 모양으로 읽는 주문 결과.

    acquired_qty 는 수수료를 뺀 '실제로 손에 남는' 수량이다.
    fees_known 이 거짓이면 수수료를 확정하지 못한 것이고, 봇은 포지션을 불확실로 본다.
    """

    status: str
    executed_qty: float
    avg_price: float
    acquired_qty: float
    fees_known: bool


@dataclass(frozen=True)
class OrderRules:
    """주문을 보내기 전에 알아야 하는 거래소 규격."""

    step: float          # 수량 최소 단위 (국내 시장가 매수는 금액으로 내므로 0)
    min_notional: float  # 1 회 최소 주문 금액 (호가 통화 기준)
