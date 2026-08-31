"""Generate the daily challenge's macros (Anthropic, with a safe template fallback).

The model proposes a few beginner-friendly macros for the chosen symbol; every
proposal is validated against the real :class:`Macro` schema and anything invalid
is dropped. The list is then topped up with deterministic templates so the daily
challenge ALWAYS has exactly N valid macros — even with no key or a bad response.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .ai_runtime import ai_cache_key, get_ai_runtime, get_anthropic_client
from .engine.schema import Macro

_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
_MAX_TOKENS = int(os.environ.get("ANTHROPIC_CHALLENGE_MAX_TOKENS", "2048"))
_PROMPT_VERSION = "daily-challenge-v2"

_SYSTEM = (
    "너는 코인 백테스트 교육 데모의 전략 생성기야. 주어진 종목으로 초보용 매크로 "
    "3개를 서로 다른 스타일로 제안해. 코드펜스 없이 JSON만 출력하고 형식은 "
    '{"macros":[{"rule_type":"A","candle_interval":"1h","params":{...},'
    '"risk":{"stop_loss_pct":3},"position_side":"long"}, ...]}. '
    "rule_type 은 A(익절/손절), E(트레일링), F(RSI), J(이평크로스) 중에서만 고르고 "
    "각 params 는 그 타입에 맞게 채워. initial_capital 은 1000000 으로. "
    "레버리지·숏은 쓰지 마(long, 레버리지 1). 투자 조언 문구는 넣지 마."
)

# type -> the params each needs (mirrors engine.schema). Used for the fallback.
_TEMPLATES = [
    {"rule_type": "A", "candle_interval": "1d",
     "params": {"take_profit_pct": 5, "initial_capital": 1_000_000}, "risk": {"stop_loss_pct": 3}},
    {"rule_type": "E", "candle_interval": "1h",
     "params": {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3, "initial_capital": 1_000_000}},
    {"rule_type": "F", "candle_interval": "1h",
     "params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1_000_000}},
    {"rule_type": "J", "candle_interval": "1h",
     "params": {"ma_type": "SMA", "fast_period": 20, "slow_period": 60, "initial_capital": 1_000_000}},
]


def _templates(symbol: str) -> list[dict]:
    out = []
    for t in _TEMPLATES:
        m = dict(t)
        m["symbol"] = symbol
        m["position_side"] = "long"
        out.append(m)
    return out


def _valid(macro_dict: dict, symbol: str) -> Optional[dict]:
    try:
        macro_dict = dict(macro_dict)
        macro_dict.setdefault("symbol", symbol)
        macro_dict.setdefault("position_side", "long")
        Macro(**macro_dict)  # raises on anything invalid
        return macro_dict
    except Exception:
        return None


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                t = rest
    return t.strip()


def _ai_propose(symbol: str) -> list[dict]:
    prompt = f"종목: {symbol}. 이 종목으로 매크로 3개를 JSON으로 제안해줘."
    key = ai_cache_key(
        "daily-challenge",
        _PROMPT_VERSION,
        _MODEL,
        {"symbol": symbol, "system": _SYSTEM, "prompt": prompt, "max_tokens": _MAX_TOKENS},
    )

    def load():
        response = get_anthropic_client().messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = None
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text:
            raise ValueError("empty AI challenge response")
        obj = json.loads(_strip_fences(text))
        macros = obj.get("macros", obj if isinstance(obj, list) else [])
        if not isinstance(macros, list):
            raise ValueError("invalid AI challenge response")
        return macros

    return get_ai_runtime().call(key, load)[0]


def generate_macros(symbol: str, n: int = 3) -> list[dict]:
    """Return exactly ``n`` valid macro dicts for ``symbol`` (AI + template fill)."""
    proposed: list[dict] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            proposed = _ai_propose(symbol)
        except Exception:
            proposed = []

    valid: list[dict] = []
    for m in proposed:
        v = _valid(m, symbol)
        if v is not None:
            valid.append(v)
        if len(valid) >= n:
            break
    # Top up (or fully fall back) with deterministic templates.
    for t in _templates(symbol):
        if len(valid) >= n:
            break
        valid.append(t)
    return valid[:n]
