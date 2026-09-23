"""껄무새에게 물어볼까? — 카드 답변(성향·시장·종목·기간·빈도)으로 백테스트 상위 3개 조합을 찾는다.

법적 설계를 코드로 강제한다:
* 종목은 요청에 온 것만 쓴다(AI 가 종목을 고르는 경로 없음).
* AI 는 매크로 '뼈대'(rule_type·params)만 제안하고 성과 숫자는 전부 백테스트가 계산한다.
* 어디에도 '추천' 이라 쓰지 않는다 — 후보, 상위 조합.
* 성향이 안정형이면 선물·H(세이프티 주문) 후보를 만들지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlmodel import Session, select

from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime
from .data import NoSpotDataError
from .data import symbols as symbols_mod
from . import ask_candidates, hotcoins
from .ask_candidates import MAX_PICKS, MIN_PICKS
from . import points as points_mod
from .db import AskExtraCredit, AskMacroSession, User
from .engine.backtest import BacktestResult
from .engine.explain import explain_result
from .engine.schema import Macro
from .quests import today_kst

log = logging.getLogger(__name__)

DISCLAIMER_VERSION = "ask-v2"
DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요"
MAX_CANDIDATES = 24
TOP_N = 3
MIN_TRADES = 3
CAPITAL = 1_000_000
SESSION_TTL_MS = 30 * 60 * 1000
MAX_ASKS_PER_SESSION = 6
MANUAL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")

RiskProfile = Literal["stable", "balanced", "aggressive", "scalper"]

# 성향별 규칙 — 후보에 넣는 유형, MDD 상한, 선물 허용, 짧은 봉 여부(short), 종목 상한, 최소 거래 수.
# 순서는 템플릿 우선순위. 단타형은 짧은 봉 전용이라 I(일봉 논리)·C·H(짧은 봉에 의미 없음)를 뺀다.
PROFILES: dict[str, dict] = {
    "stable": {"label": "안정형", "mdd_cap": 10.0, "rule_types": ("C", "J", "G", "A"), "futures": False,
               "short": False, "max_symbols": 3, "min_trades": 3},
    "balanced": {"label": "균형형", "mdd_cap": 20.0, "rule_types": ("C", "J", "G", "A", "F", "E"), "futures": True,
                 "short": False, "max_symbols": 3, "min_trades": 3},
    "aggressive": {"label": "공격형", "mdd_cap": None, "rule_types": ("C", "J", "G", "A", "F", "E", "I", "H"), "futures": True,
                   "short": False, "max_symbols": 3, "min_trades": 3},
    "scalper": {"label": "단타형", "mdd_cap": None, "rule_types": ("A", "E", "F", "G", "J"), "futures": True,
                "short": True, "max_symbols": 2, "min_trades": 10},
}

# 봉 × 기간 짝 — 백테스트 봉 상한(MAX_BACKTEST_BARS=20,000)을 넘지 않는 조합만.
LONG_INTERVALS = ("1h", "4h", "1d")
LONG_PERIODS = ("3m", "6m", "1y")
SHORT_INTERVALS = ("1m", "5m", "15m")
SHORT_PERIODS = ("1w", "1m")

# v2(2026-09-23) — 카드가 사람 말을 받고 서버가 기술 값으로 바꾼다.
HORIZONS = ("days", "weeks", "months", "long")
WATCH_LEVELS = ("rarely", "sometimes", "often")

_HORIZON_PERIOD = {"days": "1w", "weeks": "3m", "months": "6m", "long": "1y"}
# 단타형은 짧은 구간만 — SHORT_PERIODS = ("1w", "1m"). 여기의 1m 은 1개월이다.
_HORIZON_PERIOD_SHORT = {"days": "1w", "weeks": "1m", "months": "1m", "long": "1m"}
_WATCH_INTERVAL = {"rarely": "1d", "sometimes": "4h", "often": "1h"}
_WATCH_INTERVAL_SHORT = {"rarely": "15m", "sometimes": "5m", "often": "1m"}


def to_period(profile: str, horizon: str) -> str:
    table = _HORIZON_PERIOD_SHORT if PROFILES[profile]["short"] else _HORIZON_PERIOD
    return table.get(horizon, table["weeks"])


def to_interval(profile: str, watch: str, period: str) -> str:
    if not PROFILES[profile]["short"]:
        return _WATCH_INTERVAL.get(watch, _WATCH_INTERVAL["sometimes"])
    interval = _WATCH_INTERVAL_SHORT.get(watch, _WATCH_INTERVAL_SHORT["sometimes"])
    # 1분 봉은 최근 1주 구간에서만 쓸 수 있다(백테스트 봉 상한).
    return "5m" if interval == "1m" and period != "1w" else interval

RULE_LABELS = {
    "A": "익절/손절 후 재진입", "C": "정기 분할매수", "E": "트레일링 스탑", "F": "RSI 조건",
    "G": "볼린저밴드 회귀", "H": "세이프티 주문", "I": "변동성 돌파", "J": "이동평균 크로스",
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class AskRequest(BaseModel):
    """v2 — 흐름 세션에서 답변을 꺼내 쓰므로 종목만 받는다(클라이언트 위변조 차단)."""

    session_id: int
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        sym = str(value or "").strip().upper()
        if not _SYMBOL_RE.match(sym):
            raise ValueError("종목 이름이 올바르지 않아요")
        return sym


class CandidatesRequest(BaseModel):
    risk_profile: RiskProfile
    market: Literal["spot", "futures"]
    leverage: int = Field(default=1, ge=1, le=3)
    invest_horizon: Literal["days", "weeks", "months", "long"]
    watch_frequency: Literal["rarely", "sometimes", "often"]

    @model_validator(mode="after")
    def _profile_rules(self) -> "CandidatesRequest":
        if self.market == "futures" and not PROFILES[self.risk_profile]["futures"]:
            raise ValueError(f"{PROFILES[self.risk_profile]['label']}은 현물만 살펴봐요")
        if self.market == "spot" and self.leverage != 1:
            raise ValueError("현물은 레버리지를 쓰지 않아요")
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

# 단타형 전용 프리셋 — 짧은 봉에 맞춘 좁은 익절·손절·지표 기간.
_SCALPER_PRESETS: dict[str, list[dict]] = {
    "A": [
        {"params": {"take_profit_pct": 1.0, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 0.7}},
        {"params": {"take_profit_pct": 1.5, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 1.0}},
    ],
    "E": [
        {"params": {"entry_mode": "immediate", "activation_profit": 1.5, "trail_percent": 0.8, "initial_capital": CAPITAL},
         "risk": {"stop_loss_pct": 0.7}},
        {"params": {"entry_mode": "dip", "entry_dip": 1.0, "activation_profit": 1.2, "trail_percent": 0.6, "initial_capital": CAPITAL},
         "risk": {"stop_loss_pct": 0.5}},
    ],
    "F": [
        {"params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": CAPITAL}},
        {"params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "exit_mode": "both", "take_profit": 1.5, "initial_capital": CAPITAL}},
    ],
    "G": [
        {"params": {"bb_period": 20, "bb_std": 2.0, "strategy": "reversion", "exit_target": "mid", "initial_capital": CAPITAL}},
        {"params": {"bb_period": 20, "bb_std": 2.5, "strategy": "reversion", "exit_target": "opposite", "initial_capital": CAPITAL}},
    ],
    "J": [
        {"params": {"ma_type": "EMA", "fast_period": 5, "slow_period": 13, "initial_capital": CAPITAL}},
        {"params": {"ma_type": "EMA", "fast_period": 9, "slow_period": 21, "initial_capital": CAPITAL}},
    ],
}


@dataclass
class _Plan:
    """세션 답변 + 고른 종목 → 기존 후보 생성 코드가 읽는 모양."""
    risk_profile: str
    market: str
    leverage: int
    symbols: list[str]
    period_preset: str
    interval: str


def _plan(answers: CandidatesRequest, symbol: str) -> _Plan:
    period = to_period(answers.risk_profile, answers.invest_horizon)
    return _Plan(
        risk_profile=answers.risk_profile, market=answers.market, leverage=answers.leverage,
        symbols=[symbol], period_preset=period,
        interval=to_interval(answers.risk_profile, answers.watch_frequency, period),
    )


def _presets_for(req: _Plan) -> dict[str, list[dict]]:
    return _SCALPER_PRESETS if PROFILES[req.risk_profile]["short"] else _PRESETS


def _allowed_types(req: _Plan) -> tuple[str, ...]:
    types = PROFILES[req.risk_profile]["rule_types"]
    # C(DCA) 는 레버리지·선물을 못 쓴다.
    if req.market == "futures":
        types = tuple(t for t in types if t != "C")
    return types


def _make_macro(req: _Plan, rule_type: str, preset: dict, symbols: list[str]) -> Optional[Macro]:
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


def _label(rule_type: str, req: _Plan, symbols: list[str]) -> str:
    where = "포트폴리오" if len(symbols) > 1 else symbols[0]
    return f"{RULE_LABELS.get(rule_type, rule_type)} · {req.interval} · {where}"


def build_templates(req: _Plan) -> list[Candidate]:
    """성향별 템플릿 후보.

    상한(24)에 걸리면 뒤쪽부터 잘리므로 모든 유형의 단일 종목 후보가 먼저,
    포트폴리오가 그다음, 두 번째 프리셋이 마지막에 오도록 순서를 정한다:
    1) 유형 × 종목 전부의 첫 프리셋(단일 종목), 2) 종목이 2개 이상이면 유형별
    포트폴리오(첫 프리셋), 3) 유형 × 종목 전부의 두 번째 프리셋(단일 종목).
    """
    out: list[Candidate] = []
    types = _allowed_types(req)
    presets = _presets_for(req)

    # 1) 모든 유형 × 모든 종목의 첫 프리셋(단일 종목)
    for rule_type in types:
        for sym in req.symbols:
            macro = _make_macro(req, rule_type, presets[rule_type][0], [sym])
            if macro is not None:
                out.append(Candidate(_label(rule_type, req, [sym]), macro, "template"))

    # 2) 종목이 2개 이상이면 유형별 포트폴리오(첫 프리셋)
    if len(req.symbols) > 1:
        for rule_type in types:
            macro = _make_macro(req, rule_type, presets[rule_type][0], req.symbols)
            if macro is not None:
                out.append(Candidate(_label(rule_type, req, req.symbols), macro, "template"))

    # 3) 두 번째 이상 프리셋 — 종목별·유형별로 추가
    depth = max(len(presets[t]) for t in types)
    for preset_idx in range(1, depth):
        for sym in req.symbols:
            for rule_type in types:
                type_presets = presets[rule_type]
                if preset_idx >= len(type_presets):
                    continue
                macro = _make_macro(req, rule_type, type_presets[preset_idx], [sym])
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


def excess_over_hold(result: BacktestResult) -> float:
    """'그냥 들고 있었을 때' 대비 몇 %p 나은가. 홀딩 기준이 없으면 절대 수익률을 그대로 쓴다.

    화면(explain.py)은 이미 홀딩 대비를 가장 중요한 틀로 보여 주는데, 정작 어떤 조합을
    보여 줄지는 절대 수익률로 골라서 "이게 1등" 이라고 해 놓고 바로 밑에서 "홀딩이 나았어"
    라고 말하는 엇갈림이 있었다. 고르는 기준을 화면이 말하는 기준에 맞춘다.
    """
    ret = float(result.final_return_pct)
    hold = result.buy_hold_return_pct
    return ret if hold is None else ret - float(hold)


def score(profile: str, result: BacktestResult) -> float:
    ret = excess_over_hold(result)
    mdd = float(result.mdd_pct)
    if profile == "stable":
        return ret / max(mdd, 1.0)
    if profile == "balanced":
        return ret - 0.5 * mdd
    return ret


def all_lost_to_hold(picked: list["Evaluated"]) -> bool:
    """보여 줄 조합이 하나도 홀딩을 못 이겼는가 — 그러면 화면 맨 위에서 먼저 알린다.

    홀딩 기준을 못 구한 결과가 섞여 있으면 '졌다' 고 단정하지 않는다(빈 목록도 마찬가지).
    """
    if not picked:
        return False
    return all(e.result.buy_hold_return_pct is not None
               and float(e.result.final_return_pct) < float(e.result.buy_hold_return_pct)
               for e in picked)


def select_top(evaluated: list[Evaluated], profile: str, n: int = TOP_N) -> list[Evaluated]:
    """MDD 상한·최소 거래 수로 거르고 성향 점수로 정렬해 상위 n개 — 같은 rule_type 은 하나만."""
    cap = PROFILES[profile]["mdd_cap"]
    min_trades = PROFILES[profile]["min_trades"]
    pool = [
        e for e in evaluated
        if e.result.total_trades >= min_trades and (cap is None or float(e.result.mdd_pct) <= cap)
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


def _ai_system(req: _Plan) -> str:
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


def propose_with_ai(req: _Plan) -> list[Candidate]:
    """Gemini 가 제안한 뼈대를 요청 조건(종목·봉·시장·레버리지)에 고정하고 스키마로 검증한다. 실패는 빈 리스트."""
    if not ai_available():
        return []
    system = _ai_system(req)
    prompt = (
        f"종목: {', '.join(req.symbols)} · 성향: {PROFILES[req.risk_profile]['label']} · "
        f"봉 간격: {req.interval} · 기간: {req.period_preset}. 매크로 3개를 JSON 으로."
    )
    key = ai_cache_key("ask", _AI_PROMPT_VERSION, _AI_MODEL,
                       {"req": asdict(req), "system": system, "prompt": prompt, "max_tokens": _AI_MAX_TOKENS})

    def load():
        response = get_ai_client().messages.create(
            model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": prompt}], purpose="ask",
            timeout=float(os.environ.get("ASK_AI_TIMEOUT_SEC", "6")),
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
        proposed = get_ai_runtime().call(key, load, retries=0)[0]
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


_CANDIDATE_PROMPT_VERSION = "ask-cand-v1"
_CANDIDATE_SYSTEM = (
    "너는 주어진 목록 안에서만 종목 후보를 고르는 도우미다. "
    "목록에 없는 종목은 절대 쓰지 마라. 가격이나 수익률을 예측하지 마라. "
    "JSON 배열만 출력해라."
)


def _candidate_ai() -> Optional[Callable[[str], str]]:
    """후보 선별에 쓸 AI 호출자. 쓸 수 없으면 None(규칙 폴백).

    기존 ``propose_with_ai`` 와 같은 경로를 쓴다 — 클라이언트는 messages.create,
    호출은 ``get_ai_runtime().call`` 로 감싸 캐시·재시도 정책을 공유한다.
    """
    if not ai_available():
        return None

    def ask_ai(prompt: str) -> str:
        key = ai_cache_key("ask-candidates", _CANDIDATE_PROMPT_VERSION, _AI_MODEL,
                           {"system": _CANDIDATE_SYSTEM, "prompt": prompt,
                            "max_tokens": _AI_MAX_TOKENS})

        def load():
            response = get_ai_client().messages.create(
                model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=_CANDIDATE_SYSTEM,
                messages=[{"role": "user", "content": prompt}], purpose="ask-candidates",
                timeout=float(os.environ.get("ASK_AI_TIMEOUT_SEC", "6")),
            )
            text = next((b.text for b in response.content
                         if getattr(b, "type", None) == "text"), None)
            if not text:
                raise ValueError("empty candidate response")
            return text

        return get_ai_runtime().call(key, load, retries=0)[0]

    return ask_ai


class AskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("ASK_DAILY_LIMIT", "5")))
    except ValueError:
        return 5


def time_budget_sec() -> float:
    try:
        return float(os.environ.get("ASK_TIME_BUDGET_SEC", "20"))
    except ValueError:
        return 20.0


def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


# 포인트로 횟수 추가 (2026-09-22): 무료 한도를 다 쓴 뒤 1회 EXTRA_PRICE 포인트, 하루 EXTRA_DAILY_CAP 회까지.
EXTRA_PRICE = 30
EXTRA_DAILY_CAP = 5


def used_today(db: Session, user: User) -> int:
    """오늘 무료 한도에서 쓴 횟수 — 추가권으로 물어본 세션(paid)은 세지 않는다."""
    return int(db.exec(
        select(func.count()).select_from(AskMacroSession)
        .where(AskMacroSession.user_id == user.id, AskMacroSession.day_kst == today_kst(),
               AskMacroSession.paid == False)  # noqa: E712 — SQL 비교
    ).one())


def free_remaining_today(db: Session, user: User) -> int:
    return max(0, daily_limit() - used_today(db, user))


def _credits_today(db: Session, user: User) -> list[AskExtraCredit]:
    return list(db.exec(
        select(AskExtraCredit)
        .where(AskExtraCredit.user_id == user.id, AskExtraCredit.day_kst == today_kst())
        .order_by(AskExtraCredit.id.asc())
    ).all())


def _unused_credit(db: Session, user: User) -> Optional[AskExtraCredit]:
    for credit in _credits_today(db, user):
        if credit.used_session_id is None:
            return credit
    return None


def extra_left_today(db: Session, user: User) -> int:
    return max(0, EXTRA_DAILY_CAP - len(_credits_today(db, user)))


def remaining_today(db: Session, user: User) -> int:
    """오늘 물어볼 수 있는 횟수 = 무료 남은 수 + 아직 안 쓴 추가권 수."""
    unused = sum(1 for c in _credits_today(db, user) if c.used_session_id is None)
    return free_remaining_today(db, user) + unused


def buy_extra(db: Session, user: User) -> dict:
    """추가권 1회를 포인트로 산다. 무료가 남아 있으면 팔지 않는다(실수 결제 방지)."""
    if free_remaining_today(db, user) > 0:
        raise AskError(409, "아직 무료 횟수가 남아 있어요. 다 쓴 뒤에 추가할 수 있어요.")
    if extra_left_today(db, user) <= 0:
        raise AskError(429, f"오늘은 추가 {EXTRA_DAILY_CAP}회까지만 살 수 있어요. 내일 다시 물어봐 주세요.")
    created_at, created_ms = _now()
    try:
        points_mod.apply(db, user, -EXTRA_PRICE, "ask_extra", ref=f"ask_extra:{today_kst()}")
    except points_mod.InsufficientPoints as exc:
        raise AskError(402, str(exc))
    db.add(AskExtraCredit(user_id=user.id, day_kst=today_kst(), price=EXTRA_PRICE,
                          created_at=created_at, created_ms=created_ms))
    db.commit()
    db.refresh(user)
    return {
        "ok": True,
        "remaining_today": remaining_today(db, user),
        "extra_left_today": extra_left_today(db, user),
        "points_balance": user.points_balance,
    }


def consented(user: User) -> bool:
    return getattr(user, "ask_consent_version", "") == DISCLAIMER_VERSION


def status(db: Session, user: User) -> dict:
    return {
        "consented": consented(user),
        "remaining_today": remaining_today(db, user),
        "daily_limit": daily_limit(),
        "disclaimer_version": DISCLAIMER_VERSION,
        "extra_price": EXTRA_PRICE,
        "extra_left_today": extra_left_today(db, user),
        "points_balance": int(user.points_balance or 0),
    }


def give_consent(db: Session, user: User) -> dict:
    user.ask_consent_version = DISCLAIMER_VERSION
    user.ask_consent_at = _now()[0]
    db.add(user)
    db.commit()
    return {"ok": True, "version": DISCLAIMER_VERSION}


def _metrics(result: BacktestResult) -> dict:
    return {
        "final_return_pct": result.final_return_pct,
        "mdd_pct": result.mdd_pct,
        "win_rate_pct": result.win_rate_pct,
        "total_trades": result.total_trades,
    }


def _result_view(e: Evaluated) -> dict:
    macro = e.candidate.macro
    # v1 은 규칙 기반 explain_result 만 쓴다 — AI 해설(ai_explain.enrich)은 사이트 전체
    # 일일 예산(AI_EXPLAIN_MAX_CALLS_PER_DAY)을 공유하는데, 여기선 한 번의 질문에 최대
    # 3개 결과가 순차로 Gemini 를 부르게 되어 예산을 빠르게 갉아먹는다. 그래서 뺀다.
    explanation = explain_result(macro, e.result)
    return {
        "label": e.candidate.label,
        "rule_type": macro.rule_type.value,
        "source": e.candidate.source,
        "macro": macro.model_dump(mode="json"),
        "metrics": _metrics(e.result),
        "explanation": explanation.model_dump(),
        "ai_generated": False,
    }


# 사용자별로 동시에 한 번만 질문을 돌린다 — 락은 집합 조작(포함 검사+추가/제거)만 감싸고,
# 백테스트가 도는 동안은 잡지 않는다.
_IN_FLIGHT: set[int] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _load_flow(db: Session, user: User, session_id: int) -> tuple[AskMacroSession, CandidatesRequest]:
    """세션 행을 찾아 만료·상한·소유자를 검사하고 저장된 카드 답변을 돌려준다."""
    row = db.get(AskMacroSession, session_id)
    if row is None or row.user_id != user.id:
        raise AskError(404, "질문 기록을 찾지 못했어요. 처음부터 다시 물어봐 주세요.")
    _, now_ms = _now()
    # expires_ms 가 0 인 행(옛 기록·마이그레이션 기본값)도 만료로 본다 — 영원한 세션을 만들지 않는다.
    if not row.expires_ms or row.expires_ms <= now_ms:
        raise AskError(410, "질문한 지 오래됐어요. 처음부터 다시 물어봐 주세요.")
    if row.ask_count >= MAX_ASKS_PER_SESSION:
        raise AskError(409, "이번 질문에서 살펴볼 수 있는 종목을 다 봤어요. 다시 물어봐 주세요.")
    try:
        answers = CandidatesRequest(**json.loads(row.request_json))
    except Exception:
        raise AskError(410, "질문 기록이 오래된 형식이에요. 처음부터 다시 물어봐 주세요.")
    return row, answers


def _candidates_record(row: AskMacroSession) -> tuple[list[dict], bool]:
    """세션에 남긴 후보 목록과 '그 목록을 AI 가 골랐는지'.

    후보 단계의 ai_used 는 "무엇을 보여 줬는가" 를 설명하는 감사 기록이라, 매크로 단계가
    같은 컬럼을 덮어쓰면 복구할 수 없다. 그래서 후보와 한 묶음으로 candidates_json 에 담는다.
    옛 행(후보 배열만 들어 있는 모양)도 그대로 읽는다.
    """
    try:
        saved = json.loads(row.candidates_json)
    except Exception:
        return [], False
    if isinstance(saved, dict):
        items = saved.get("items")
        return (items if isinstance(items, list) else []), bool(saved.get("ai_used"))
    return (saved if isinstance(saved, list) else []), False


def _tradable_symbols(market: str) -> set[str]:
    """이 시장에서 실제로 거래되는 USDT 심볼 — 빌더 검색창이 쓰는 그 목록(캐시됨)."""
    items = symbols_mod.list_symbols().get("items") or []
    return {str(i.get("symbol", "")) for i in items if isinstance(i, dict) and i.get(market)}


def _allowed_symbols(row: AskMacroSession, market: str) -> set[str]:
    """이 세션에서 고를 수 있는 종목.

    세션 후보 + 빠른 선택 칩(MANUAL_SYMBOLS) + '직접 고를래요' 로 검색해 고른 종목.
    검색을 허용해도 AI 가 지어낸 심볼은 못 들어온다 — 사람이 거래 가능 목록에서 직접 고른
    것만 통과하므로, 오히려 "종목은 사용자가 고른 것만" 에 더 가깝다.
    거래 목록을 못 받으면(업스트림 장애) 넓히지 않고 후보 + 칩으로만 둔다.
    """
    items, _ai_used = _candidates_record(row)
    try:
        picked = {c["symbol"] for c in items if isinstance(c, dict)}
    except Exception:
        picked = set()
    base = picked | set(MANUAL_SYMBOLS)
    try:
        return base | _tradable_symbols(market)
    except Exception:
        log.warning("ask: 거래 가능 종목 목록을 받지 못해 직접 고르기를 기본 목록으로 제한합니다", exc_info=True)
        return base


def run_ask(db: Session, user: User, req: AskRequest, run_backtest: Callable[[Macro], BacktestResult]) -> dict:
    """흐름 세션에서 종목을 골라 매크로 후보를 낸다 — 차감 없음(하루 한도는 후보를 낼 때 이미 셌다).

    성공했을 때만 세션의 호출 수(ask_count)를 센다 — 실패한 시도로 세션 예산을 깎지 않는다.
    """
    if not consented(user):
        raise AskError(403, "먼저 안내에 동의해 주세요.")
    row, answers = _load_flow(db, user, req.session_id)
    if req.symbol not in _allowed_symbols(row, answers.market):
        raise AskError(422, "이번 질문에서 살펴볼 수 있는 종목이 아니에요.")

    with _IN_FLIGHT_LOCK:
        if user.id in _IN_FLIGHT:
            raise AskError(429, "아직 지난 질문을 돌리는 중이에요. 잠시만요.")
        _IN_FLIGHT.add(user.id)

    try:
        started = time.monotonic()
        plan = _plan(answers, req.symbol)
        failures: list[Exception] = []

        def guarded(macro: Macro) -> BacktestResult:
            try:
                return run_backtest(macro)
            except Exception as exc:
                failures.append(exc)
                raise

        ai_candidates = propose_with_ai(plan)[:TOP_N]
        candidates = (ai_candidates + build_templates(plan))[:MAX_CANDIDATES]
        evaluated = evaluate(candidates, guarded, time_budget_sec())
        if not evaluated and failures and all(isinstance(f, NoSpotDataError) for f in failures):
            raise AskError(422, "이 종목의 시세 데이터를 찾지 못했어요. 다른 종목을 골라 주세요.")
        top = select_top(evaluated, plan.risk_profile)
        results = [_result_view(e) for e in top]
        lost_to_hold = all_lost_to_hold(top)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # 성공했을 때만 호출 수를 센다 — 실패한 시도로 예산을 깎지 않는다.
        row.ask_count += 1
        row.chosen_symbol = req.symbol
        row.candidate_count = len(evaluated)
        row.results_json = json.dumps(
            [{"label": r["label"], "rule_type": r["rule_type"], "macro": r["macro"],
              "metrics": r["metrics"]} for r in results], ensure_ascii=False)
        row.ai_used = bool(ai_candidates)  # 매크로 단계 기록(후보 단계 것은 candidates_json 안에 있다)
        row.elapsed_ms = elapsed_ms
        db.add(row)
        db.commit()
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(user.id)

    return {
        "results": results,
        # 보여 준 조합이 전부 '그냥 들고 있기' 에 졌으면 화면 맨 위에서 먼저 알린다.
        "all_lost_to_hold": lost_to_hold,
        "remaining_today": remaining_today(db, user),
        "disclaimer": DISCLAIMER,
        "disclaimer_version": DISCLAIMER_VERSION,
    }


def run_candidates(db: Session, user: User, req: CandidatesRequest) -> dict:
    """카드 답변으로 종목 후보를 낸다 — 하루 한도는 이 함수에서만 차감된다.

    v1 의 run_ask 와 같은 순서를 따른다: 한도 검사 뒤 자리표시 행을 먼저 커밋해
    check-then-insert 창을 닫고, 후보를 못 내면 행을 지우고 추가권도 돌려준다.
    """
    if not consented(user):
        raise AskError(403, "먼저 안내에 동의해 주세요.")

    # 차감이 일어나는 곳이라 run_ask 와 같은 사용자별 락을 건다 — 같은 사람이 동시에 두 번
    # 보내면 둘 다 한도 검사를 통과해 두 번 차감될 수 있다.
    with _IN_FLIGHT_LOCK:
        if user.id in _IN_FLIGHT:
            raise AskError(429, "아직 지난 질문을 돌리는 중이에요. 잠시만요.")
        _IN_FLIGHT.add(user.id)
    try:
        return _run_candidates(db, user, req)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(user.id)


def _run_candidates(db: Session, user: User, req: CandidatesRequest) -> dict:
    """run_candidates 의 알맹이 — 락을 잡은 채로 한도 검사·차감·후보 생성을 한다."""
    if remaining_today(db, user) <= 0:
        raise AskError(429, f"오늘은 {daily_limit()}번 다 물어봤어요. 내일 다시 물어봐 주세요.")

    created_at, created_ms = _now()
    credit = None if free_remaining_today(db, user) > 0 else _unused_credit(db, user)
    # 한도 검사와 저장 사이의 창을 닫으려고 행을 먼저 커밋한다(v1 과 같은 이유).
    row = AskMacroSession(
        user_id=user.id, day_kst=today_kst(),
        request_json=json.dumps(req.model_dump(), ensure_ascii=False),
        candidate_count=0, results_json="[]",
        disclaimer_version=DISCLAIMER_VERSION, ai_used=False, elapsed_ms=0,
        created_at=created_at, created_ms=created_ms, paid=credit is not None,
        candidates_json="[]", chosen_symbol="", expires_ms=created_ms + SESSION_TTL_MS,
        ask_count=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if credit is not None:
        credit.used_session_id = row.id
        db.add(credit)
        db.commit()

    try:
        tickers = hotcoins.get_cached_tickers()
        if not tickers:
            raise AskError(503, "지금 시세 목록을 불러오지 못했어요. 잠시 뒤 다시 물어봐 주세요.")
        pool = ask_candidates.build_pool(tickers, profile=req.risk_profile)
        if not pool:
            raise AskError(503, "지금 살펴볼 종목을 찾지 못했어요. 잠시 뒤 다시 물어봐 주세요.")
        candidates, ai_used = ask_candidates.choose(
            pool, profile=req.risk_profile, horizon=req.invest_horizon,
            watch=req.watch_frequency, ask_ai=_candidate_ai())
    except Exception:
        # 후보를 못 냈으면 횟수를 돌려준다 — 행을 지우고 추가권은 다시 '안 씀'으로.
        if credit is not None:
            credit.used_session_id = None
            db.add(credit)
        db.delete(row)
        db.commit()
        raise

    # 후보 단계의 ai_used 는 후보와 한 묶음으로 남긴다 — 매크로 단계가 row.ai_used 를 덮어써도
    # "이 목록을 AI 가 골랐는지" 는 그대로 남아야 한다(설계 문서 2장의 방어 장치).
    row.candidates_json = json.dumps({"ai_used": ai_used, "items": candidates}, ensure_ascii=False)
    row.ai_used = ai_used
    db.add(row)
    db.commit()
    return {
        "session_id": row.id,
        "candidates": candidates,
        "manual_symbols": list(MANUAL_SYMBOLS),
        "remaining_today": remaining_today(db, user),
        "disclaimer": DISCLAIMER,
    }
