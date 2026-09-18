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
    """성향별 템플릿 후보. 첫 프리셋은 유형별로 '종목 전부 + 포트폴리오'를 함께 묶어서 추가하고, 두 번째 프리셋은 종목별·유형별로 추가한다. 우선순위가 낮은 유형이 상한(MAX_CANDIDATES)에 잘린다."""
    out: list[Candidate] = []
    types = _allowed_types(req)
    depth = max(len(_PRESETS[t]) for t in types)
    for preset_idx in range(depth):
        if preset_idx == 0:
            # 첫 프리셋: 유형별로 '모든 종목 + 포트폴리오' 함께 추가
            for rule_type in types:
                for sym in req.symbols:
                    macro = _make_macro(req, rule_type, _PRESETS[rule_type][0], [sym])
                    if macro is not None:
                        out.append(Candidate(_label(rule_type, req, [sym]), macro, "template"))
                if len(req.symbols) > 1:
                    macro = _make_macro(req, rule_type, _PRESETS[rule_type][0], req.symbols)
                    if macro is not None:
                        out.append(Candidate(_label(rule_type, req, req.symbols), macro, "template"))
        else:
            # 두 번째 이상 프리셋: 종목별·유형별로 추가
            for sym in req.symbols:
                for rule_type in types:
                    presets = _PRESETS[rule_type]
                    if preset_idx >= len(presets):
                        continue
                    macro = _make_macro(req, rule_type, presets[preset_idx], [sym])
                    if macro is not None:
                        out.append(Candidate(_label(rule_type, req, [sym]), macro, "template"))
    return out[:MAX_CANDIDATES]


@dataclass
class Evaluated:
    candidate: Candidate
    result: BacktestResult


def evaluate(
    candidates: list[Candidate],
    run: Callable[[Macro], BacktestResult],
    time_budget_sec: float,
) -> list[Evaluated]:
    """후보를 전부 백테스트한다. 실패한 후보는 건너뛰고, 시간 예산이 끝나면 남은 후보도 건너뛴다."""
    started = time.monotonic()
    out: list[Evaluated] = []
    for cand in candidates:
        if time.monotonic() - started > time_budget_sec:
            break
        try:
            out.append(Evaluated(cand, run(cand.macro)))
        except Exception:
            continue
    return out


def score(profile: str, result: BacktestResult) -> float:
    ret = float(result.final_return_pct)
    mdd = float(result.mdd_pct)
    if profile == "stable":
        return ret / max(mdd, 1.0)
    if profile == "balanced":
        return ret - 0.5 * mdd
    return ret


def select_top(evaluated: list[Evaluated], profile: str, n: int = TOP_N) -> list[Evaluated]:
    """MDD 상한·최소 거래 수로 거르고 성향 점수로 정렬해 상위 n개 — 같은 rule_type 은 하나만."""
    cap = PROFILES[profile]["mdd_cap"]
    pool = [
        e for e in evaluated
        if e.result.total_trades >= MIN_TRADES and (cap is None or float(e.result.mdd_pct) <= cap)
    ]
    pool.sort(key=lambda e: (score(profile, e.result), e.result.total_trades), reverse=True)
    picked: list[Evaluated] = []
    seen_types: set[str] = set()
    for e in pool:
        rt = e.candidate.macro.rule_type.value
        if rt in seen_types:
            continue
        seen_types.add(rt)
        picked.append(e)
        if len(picked) >= n:
            break
    return picked


_AI_MODEL = default_model()
_AI_MAX_TOKENS = int(os.environ.get("GEMINI_ASK_MAX_TOKENS", "2048"))
_AI_PROMPT_VERSION = "ask-v1"


def _ai_system(req: AskRequest) -> str:
    types = ", ".join(_allowed_types(req))
    return (
        "너는 코인 백테스트 교육 도구의 매크로 뼈대 생성기야. 사용자가 고른 종목과 조건으로 "
        "서로 다른 스타일의 매크로 3개를 JSON 으로만 출력해(코드펜스 없이). 형식은 "
        '{"macros":[{"rule_type":"J","params":{...},"risk":{"stop_loss_pct":3}}, ...]}. '
        f"rule_type 은 {types} 중에서만 고르고 각 params 는 그 타입 스키마대로 채워. "
        "initial_capital 은 1000000 으로. 종목·봉 간격·시장·레버리지는 서버가 정하니 넣지 마. "
        "수익률이나 전망 같은 숫자를 지어내지 말고, 조언·권유 문구를 넣지 마."
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                t = rest
    return t.strip()


def propose_with_ai(req: AskRequest) -> list[Candidate]:
    """Gemini 가 제안한 뼈대를 요청 조건(종목·봉·시장·레버리지)에 고정하고 스키마로 검증한다. 실패는 빈 리스트."""
    if not ai_available():
        return []
    system = _ai_system(req)
    prompt = (
        f"종목: {', '.join(req.symbols)} · 성향: {PROFILES[req.risk_profile]['label']} · "
        f"봉 간격: {req.interval} · 기간: {req.period_preset}. 매크로 3개를 JSON 으로."
    )
    key = ai_cache_key("ask", _AI_PROMPT_VERSION, _AI_MODEL,
                       {"req": req.model_dump(), "system": system, "prompt": prompt, "max_tokens": _AI_MAX_TOKENS})

    def load():
        response = get_ai_client().messages.create(
            model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": prompt}], purpose="ask",
        )
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise ValueError("empty ask response")
        obj = json.loads(_strip_fences(text))
        macros = obj.get("macros", obj if isinstance(obj, list) else [])
        if not isinstance(macros, list):
            raise ValueError("invalid ask response")
        return macros

    try:
        proposed = get_ai_runtime().call(key, load)[0]
    except Exception:
        return []

    allowed = set(_allowed_types(req))
    out: list[Candidate] = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        rule_type = str(item.get("rule_type", "")).upper()
        if rule_type not in allowed:
            continue
        preset = {"params": item.get("params") or {}, "risk": item.get("risk") or {}}
        macro = _make_macro(req, rule_type, preset, [req.symbols[0]])
        if macro is None:
            continue
        out.append(Candidate(_label(rule_type, req, [req.symbols[0]]) + " · AI 제안", macro, "ai"))
    return out
