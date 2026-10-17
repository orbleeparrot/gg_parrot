"""Public market identity. A KRW price is never a converted Binance price."""
from __future__ import annotations

import json
import math
import os
import re
from typing import Literal

Exchange = Literal["binance", "upbit", "bithumb"]
_QUOTES = {"binance": "USDT", "upbit": "KRW", "bithumb": "KRW"}
_LABELS = {"binance": "바이낸스", "upbit": "업비트", "bithumb": "빗썸"}


# 거래소별 현물 거래 수수료(퍼센트, 한쪽). 2026-10-08 확인.
#
# 예전에는 거래소와 무관하게 0.1%(바이낸스 요율)를 매겼다. 그래서 국내 백테스트가 실제보다
# 나쁘게 나왔다 — 업비트는 절반, 빗썸은 더 낮다. 거래가 잦은 설정에서 차이가 커진다
# (실측: 1주 · 5m 단타 후보가 0.1% 에서 -6.08%, 0.05% 에서 -4.75%).
#
# 이벤트·쿠폰·등급으로 자주 바뀌므로 환경변수 EXCHANGE_COMMISSION_JSON 으로 덮을 수 있게 둔다.
# 빗썸은 원화마켓 API 거래를 무료로 두고 있지만(실행기가 쓰는 방식) 0 으로 적지 않는다 —
# 이벤트가 끝나면 조용히 거짓이 되고, 0 은 "마찰이 없다" 로 읽혀 가장 위험한 쪽으로 틀린다.
_COMMISSION_PCT = {"binance": 0.1, "upbit": 0.05, "bithumb": 0.04}


# 현물 한 건 최소 주문 금액(견적 통화). 업비트 · 빗썸 원화마켓 5,000원(2026-10 확인), 바이낸스 현물은
# 종목마다 NOTIONAL 필터가 다르지만 대부분 5 USDT. 선물은 종목마다 5~100 USDT 로 크게 달라 여기서 다루지 않는다 —
# 실행기가 거래소 필터로 막는다.
_MIN_ORDER = {"binance": 5.0, "upbit": 5000.0, "bithumb": 5000.0}


def min_order_floor(exchange: str = "binance", market: str = "spot") -> float | None:
    """매크로가 만드는 한 건 주문이 넘어야 할 금액 — 최소 주문 금액에 2% 여유(수수료 · 값 흔들림)를 두고
    원화는 100원, USDT 는 0.1 단위로 올린다(5,000원 → 5,100원 · 5 USDT → 5.1). 선물은 None."""
    if market == "futures":
        return None
    exchange = normalize_exchange(exchange)
    unit = 100.0 if quote_currency(exchange) == "KRW" else 0.1
    return round(math.ceil(_MIN_ORDER[exchange] * 1.02 / unit - 1e-9) * unit, 8)


def spot_commission_pct(exchange: str = "binance") -> float:
    """이 거래소 현물 한쪽 수수료(%). 백테스트·모의·실거래가 같은 값을 본다."""
    exchange = normalize_exchange(exchange)
    raw = str(os.environ.get("EXCHANGE_COMMISSION_JSON") or "").strip()
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict) and exchange in override:
                return max(0.0, float(override[exchange]))
        except (ValueError, TypeError):
            pass
    return _COMMISSION_PCT[exchange]


def normalize_exchange(value: str | None = None) -> str:
    exchange = "binance" if value is None else str(value).strip().lower()
    if exchange not in _QUOTES:
        raise ValueError("exchange must be binance, upbit or bithumb")
    return exchange


def quote_currency(exchange: str = "binance") -> str:
    return _QUOTES[normalize_exchange(exchange)]


def is_domestic(exchange: str = "binance") -> bool:
    return normalize_exchange(exchange) != "binance"


def validate_symbol(symbol: str, exchange: str = "binance") -> str:
    symbol = str(symbol).strip().upper()
    if is_domestic(exchange):
        if not re.fullmatch(r"KRW-[A-Z0-9]{1,20}", symbol):
            raise ValueError("국내 거래소는 KRW-BTC 형식의 원화 마켓을 사용합니다")
    elif symbol.startswith("KRW-"):
        raise ValueError("바이낸스에서는 국내 거래소 KRW 마켓을 사용할 수 없습니다")
    return symbol


def capabilities(exchange: str = "binance") -> dict:
    exchange = normalize_exchange(exchange)
    domestic = is_domestic(exchange)
    return {"exchange": exchange, "label": _LABELS[exchange],
            "quote_currency": quote_currency(exchange), "spot": True,
            "futures": not domestic, "short": not domestic,
            "max_leverage": 1 if domestic else 20,
            # 실행기(v10+)는 업비트·빗썸 원화 현물에 주문을 낸다. 국내를 거짓으로 두면
            # API 가 거짓말을 하고, 다음에 이 값을 읽는 화면이 국내 실행을 막게 된다.
            "runner_supported": True}
