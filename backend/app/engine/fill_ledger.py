"""체결 기준 장부(fill_ledger_v1) 자료 — 서버 전략 장부를 실행기가 보고한 실제 체결에 잇는다(2026-10-21 설계 §5 · §9).

장부 숫자(롯 · 현금)는 sim(CandleSim)에 있고, 바꾸는 일도 sim 의 장부 메서드(`_open_long` · `_close_all` 옆)가 한다.
여기는 그 숫자를 실제 체결에 잇는 정보 — 주문 번호 · 미결 주문 · 접은 체결 키 · 먼지 · 보류 봉 — 와 체결 보고 한 건의
계산만 둔다. DB 를 모른다. 정책이 꺼진 sim(백테스트 · 모의 · 옛 실행기 세션)에는 붙지 않는다.
"""
from __future__ import annotations

import copy
import math
from typing import Dict, List, NamedTuple, Optional

STATE_VERSION = 1
# 접은 체결 키를 이만큼 기억한다 — 접은 결과(state_json)는 저장됐는데 '접음' 표시 저장이 실패하면 같은 행이 다시 온다.
# 한 번에 접는 행(최대 50)보다 넉넉하면 된다.
KEY_MEMORY = 200
# 정리된 주문을 이만큼 기억한다 — 늦게 온 체결이 어느 차수의 것인지 찾는다.
RECENT_MEMORY = 20


def _num(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


class BuyFill(NamedTuple):
    """매수 체결 한 건을 장부 숫자로 — 보유 증가 · 체결가 · 현금에서 나간 돈 · 수수료(견적 통화)."""

    executed: float
    qty: float  # 실제로 늘어난 보유(기초자산으로 낸 수수료를 뺀 값)
    price: float
    cash_out: float  # 체결 금액 + 견적 통화로 따로 낸 수수료
    fee: float  # 이 체결의 수수료 전부(견적 통화)


class SellFill(NamedTuple):
    qty: float
    price: float
    fee: float


def buy_numbers(report: dict) -> Optional[BuyFill]:
    """매수 체결 보고를 숫자로. 수량 · 가격이 0 이하이거나 숫자가 아니면 None(장부에 영향 없음).

    ``fee_quote`` 는 견적 통화로 옮긴 수수료 전부다. 바이낸스 현물처럼 산 코인에서 수수료를 떼면 ``acquired_qty`` 가
    체결 수량보다 작고, 그 차이(× 체결가)는 이미 덜 받은 코인으로 낸 수수료라 현금에서 다시 빼지 않는다.
    """
    executed, price = _num(report.get("executed_qty")), _num(report.get("price"))
    if executed <= 0 or price <= 0:
        return None
    acquired = _num(report.get("acquired_qty"))
    acquired = executed if acquired <= 0 else min(acquired, executed)
    fee = max(0.0, _num(report.get("fee_quote")))
    paid_in_coin = (executed - acquired) * price
    quote_fee = max(0.0, fee - paid_in_coin)
    return BuyFill(executed, acquired, price, executed * price + quote_fee, max(fee, paid_in_coin))


def sell_numbers(report: dict) -> Optional[SellFill]:
    qty, price = _num(report.get("executed_qty")), _num(report.get("price"))
    if qty <= 0 or price <= 0:
        return None
    return SellFill(qty, price, max(0.0, _num(report.get("fee_quote"))))


class Ledger:
    """sim 하나의 체결 기준 장부 자료."""

    def __init__(self) -> None:
        self.seq = 0  # 마지막으로 매긴 주문 번호(o1, o2 …)
        self.epoch = 0  # 포지션이 끝날 때마다 1 — 이전 포지션 주문의 늦은 정리가 지금 장부를 고치지 않게
        self.orders: Dict[str, dict] = {}  # 체결 보고를 기다리는 주문(번호 → 가상 값 · 지금까지의 실제 체결)
        self.exit: Optional[str] = None  # 청산 대기 중인 주문 번호
        self.dust: Optional[dict] = None  # 팔 수 없는 잔량 {qty, fill, margin} — 다음 진입 평단에 합친다
        self.deferred: Optional[list] = None  # 미결 주문 때문에 판단을 미룬 마감봉 [t_ms, o, h, l, c, v]
        self.keys: List[str] = []  # 접은 체결 키(최근 KEY_MEMORY 개)
        self.recent: Dict[str, dict] = {}  # 정리된 주문(최근 RECENT_MEMORY 개)

    def next_ref(self) -> str:
        self.seq += 1
        return f"o{self.seq}"

    def seen(self, key: str) -> bool:
        return key in self.keys

    def remember(self, key: str) -> None:
        self.keys.append(key)
        del self.keys[:-KEY_MEMORY]

    def unresolved(self) -> bool:
        return bool(self.orders)

    def close_order(self, ref: str) -> Optional[dict]:
        """미결 주문 하나를 정리했다고 적는다. 그 주문(없으면 None)을 돌려준다."""
        order = self.orders.pop(ref, None)
        if order is not None:
            self.recent[ref] = {key: order[key] for key in ("side", "kind", "tier", "epoch") if key in order}
            while len(self.recent) > RECENT_MEMORY:
                self.recent.pop(next(iter(self.recent)))
        if self.exit == ref:
            self.exit = None
        return order

    def to_state(self, lots) -> dict:
        """체크포인트 칸. 롯은 하나씩 그대로 싣는다 — restore() 가 평단 하나로 합친 뒤 번호째 다시 깐다."""
        return {"v": STATE_VERSION, "seq": self.seq, "epoch": self.epoch,
                "lots": [{"ref": lot.ref, "qty": lot.qty, "fill": lot.fill, "margin": lot.margin} for lot in lots],
                "orders": copy.deepcopy(self.orders), "exit": self.exit, "dust": copy.deepcopy(self.dust),
                "deferred": copy.deepcopy(self.deferred), "keys": list(self.keys),
                "recent": copy.deepcopy(self.recent)}

    def load(self, data) -> Optional[List[dict]]:
        """체크포인트에서 되살린다. 롯 목록(dict)을 돌려준다. 모르는 판 · 빈 값이면 아무것도 바꾸지 않고 None."""
        if not isinstance(data, dict) or data.get("v") != STATE_VERSION:
            return None
        self.seq = int(data.get("seq") or 0)
        self.epoch = int(data.get("epoch") or 0)
        self.orders = copy.deepcopy(data.get("orders") or {})
        self.exit = data.get("exit") or None
        self.dust = copy.deepcopy(data.get("dust")) or None
        self.deferred = copy.deepcopy(data.get("deferred")) or None
        self.keys = [str(key) for key in (data.get("keys") or [])][-KEY_MEMORY:]
        self.recent = copy.deepcopy(data.get("recent") or {})
        return [dict(row) for row in (data.get("lots") or [])]
