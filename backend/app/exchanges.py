"""Public market identity. A KRW price is never a converted Binance price."""
from __future__ import annotations

import re
from typing import Literal

Exchange = Literal["binance", "upbit", "bithumb"]
_QUOTES = {"binance": "USDT", "upbit": "KRW", "bithumb": "KRW"}
_LABELS = {"binance": "바이낸스", "upbit": "업비트", "bithumb": "빗썸"}


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
