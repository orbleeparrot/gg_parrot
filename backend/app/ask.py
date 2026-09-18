"""껄무새에게 물어볼까? — 카드 답변(성향·시장·종목·기간·빈도)으로 백테스트 상위 3개 조합을 찾는다.

법적 설계를 코드로 강제한다:
* 종목은 요청에 온 것만 쓴다(AI 가 종목을 고르는 경로 없음).
* AI 는 매크로 '뼈대'(rule_type·params)만 제안하고 성과 숫자는 전부 백테스트가 계산한다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보, 상위 조합.
* 성향이 안정형이면 선물·H(세이프티 주문) 후보를 만들지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlmodel import Session, select

from . import ai_explain as ai_explain_mod
from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime
from .db import AskMacroSession, User
from .engine.backtest import BacktestResult
from .engine.explain import explain_result
from .engine.schema import Macro
from .quests import today_kst

DISCLAIMER_VERSION = "ask-v1"
DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요"
MAX_CANDIDATES = 24
TOP_N = 3
MIN_TRADES = 3
CAPITAL = 1_000_000

RiskProfile = Literal["stable", "balanced", "aggressive"]

# 성향별 규칙 — 후보에 넣는 유형, MDD 상한, 선물 허용 여부. 순서는 템플릿 우선순위.
PROFILES: dict[str, dict] = {
    "stable": {"label": "안정형", "mdd_cap": 10.0, "rule_types": ("C", "J", "G", "A"), "futures": False},
    "balanced": {"label": "균형형", "mdd_cap": 20.0, "rule_types": ("C", "J", "G", "A", "F", "E"), "futures": True},
    "aggressive": {"label": "공격형", "mdd_cap": None, "rule_types": ("C", "J", "G", "A", "F", "E", "I", "H"), "futures": True},
}

RULE_LABELS = {
    "A": "익절/손절 후 재진입", "C": "정기 분할매수", "E": "트레일링 스탑", "F": "RSI 조건",
    "G": "볼린저밴드 회귀", "H": "세이프티 주문", "I": "변동성 돌파", "J": "이동평균 크로스",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class AskRequest(BaseModel):
    risk_profile: RiskProfile
    market: Literal["spot", "futures"] = "spot"
    leverage: int = Field(default=1, ge=1, le=3)
    symbols: list[str] = Field(min_length=1, max_length=3)
    period_preset: Literal["3m", "6m", "1y"] = "3m"
    interval: Literal["1h", "4h", "1d"] = "1h"

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            sym = str(raw).strip().upper()
            if not _SYMBOL_RE.match(sym):
                raise ValueError(f"종목은 USDT 페어여야 해요: {raw}")
            if sym not in seen:
                seen.append(sym)
        if len(seen) > 3:
            raise ValueError("종목은 최대 3개까지예요")
        return seen

    @model_validator(mode="after")
    def _profile_rules(self) -> "AskRequest":
        if self.market == "futures" and not PROFILES[self.risk_profile]["futures"]:
            raise ValueError("안정형은 현물만 살펴봐요")
        if self.market == "spot" and self.leverage != 1:
            raise ValueError("현물은 레버리지를 쓸 수 없어요")
        return self


@dataclass
class Candidate:
    label: str
    macro: Macro
    source: str  # "template" | "ai"


# 유형별 파라미터 프리셋. 절대 가격이 필요한 B·D 는 없다. C 는 initial_capital 을 스스로 계산한다.
_PRESETS: dict[str, list[dict]] = {
    "C": [
        {"params": {"amount_per_buy": 50_000, "interval_days": 1}},
        {"params": {"amount_per_buy": 100_000, "interval_days": 7}},
    ],
    "A": [
        {"params": {"take_profit_pct": 3, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 2}},
        {"params": {"take_profit_pct": 5, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 3}},
    ],
    "J": [
        {"params": {"ma_type": "SMA", "fast_period": 20, "slow_period": 60, "initial_capital": CAPITAL}},
        {"params": {"ma_type": "EMA", "fast_period": 10, "slow_period": 30, "initial_capital": CAPITAL}},
    ],
    "G": [
        {"params": {"bb_period": 20, "bb_std": 2.0, "strategy": "reversion", "exit_target": "mid", "initial_capital": CAPITAL}},
        {"params": {"bb_period": 20, "bb_std": 2.5, "strategy": "reversion", "exit_target": "opposite", "initial_capital": CAPITAL}},
    ],
    "F": [
        {"params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": CAPITAL}},
        {"params": {"rsi_period": 14, "entry_threshold": 25, "exit_threshold": 65, "exit_mode": "both", "take_profit": 5, "initial_capital": CAPITAL}},
    ],
    "E": [
        {"params": {"entry_mode": "immediate", "activation_profit": 5, "trail_percent": 3, "initial_capital": CAPITAL}},
        {"params": {"entry_mode": "dip", "entry_dip": 3, "activation_profit": 4, "trail_percent": 2, "initial_capital": CAPITAL}},
    ],
    "I": [
        {"params": {"k": 0.5, "exit_mode": "next_open", "initial_capital": CAPITAL}},
        {"params": {"k": 0.6, "exit_mode": "trailing", "trail_percent": 2, "ma_filter_period": 20, "initial_capital": CAPITAL}},
    ],
    "H": [
        {"params": {"base_order_size": 100_000, "safety_order_size": 100_000, "price_deviation": 2,
                    "max_safety_orders": 5, "take_profit": 2, "initial_capital": CAPITAL}},
    ],
}


def _allowed_types(req: AskRequest) -> tuple[str, ...]:
    types = PROFILES[req.risk_profile]["rule_types"]
    # C(DCA) 는 레버리지·선물을 못 쓴다.
    if req.market == "futures":
        types = tuple(t for t in types if t != "C")
    return types


def _make_macro(req: AskRequest, rule_type: str, preset: dict, symbols: list[str]) -> Optional[Macro]:
    body = {
        "symbol": symbols[0],
        "symbols": symbols if len(symbols) > 1 else None,
        "rule_type": rule_type,
        "position_side": "long",
        "candle_interval": req.interval,
        "market": req.market,
        "leverage": req.leverage if rule_type != "C" else 1,
        "params": dict(preset["params"]),
        "risk": dict(preset.get("risk", {})),
        "period": {"preset": req.period_preset},
    }
    try:
        return Macro(**body)
    except Exception:
        return None


def _label(rule_type: str, req: AskRequest, symbols: list[str]) -> str:
    where = "포트폴리오" if len(symbols) > 1 else symbols[0]
    return f"{RULE_LABELS.get(rule_type, rule_type)} · {req.interval} · {where}"


def build_templates(req: AskRequest) -> list[Candidate]:
    """성향별 템플릿 후보. '각 유형의 첫 프리셋 × 모든 종목' 을 먼저 채우고, 두 번째 프리셋은 뒤에 붙인다."""
    out: list[Candidate] = []
    types = _allowed_types(req)
    depth = max(len(_PRESETS[t]) for t in types)
    for preset_idx in range(depth):
        for sym in req.symbols:
            for rule_type in types:
                presets = _PRESETS[rule_type]
                if preset_idx >= len(presets):
                    continue
                macro = _make_macro(req, rule_type, presets[preset_idx], [sym])
                if macro is not None:
                    out.append(Candidate(_label(rule_type, req, [sym]), macro, "template"))
        if preset_idx == 0 and len(req.symbols) > 1:
            # 종목 2개 이상이면 자본을 나눠 함께 돌리는 포트폴리오 후보도 한 벌.
            for rule_type in types:
                macro = _make_macro(req, rule_type, _PRESETS[rule_type][0], req.symbols)
                if macro is not None:
                    out.append(Candidate(_label(rule_type, req, req.symbols), macro, "template"))
    return out[:MAX_CANDIDATES]
