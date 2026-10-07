"""코치 질문 그래프 — 순수 데이터.

다음에 무엇을 묻는지, 선택지가 무엇인지, 폼에 무엇을 넣는지를 **여기서만** 정한다.
AI 는 말투와 순서만 손댄다(coach_ai.py). 그래서 AI 가 죽어도 코치가 돌고, 코치가 낼 수
있는 폼이 전부 유효하다는 것을 전수로 증명할 수 있다.

DB · HTTP · AI · 시계를 모른다 — 그 셋이 들어오면 전수 시험이 비싸진다.

패치에 쓰는 키는 전부 frontend/src/lib/macro.js 의 defaultForm() 에 있는 **평평한 폼 키**다.
규칙별 기본값(TYPE_DEFAULTS)은 거기서 그대로 옮겨 왔다 — 한쪽을 고치면 다른 쪽도 고쳐야 한다
(tests/test_coach_graph.py 가 node 로 둘을 맞춰 본다).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional

PAGE_SIZE = 5          # 한 번에 보여 주는 선택지 수. 더 있으면 "다른 선택지 보기".
MAX_SYMBOLS = 5        # buildMacro 의 symbols 상한과 같다.

# 진입 조건 · 묶음 한도를 쓸 수 있는 규칙 — engine/schema.py FILTERABLE_TYPES 와 같다.
FILTERABLE = frozenset("EFGHIJK")


@dataclass(frozen=True)
class Choice:
    value: str
    label: str              # 28자 이하 — 패널이 고정 폭 340px 다
    patch: dict             # 평평한 폼 키만
    why: str = ""           # 한 줄 근거(AI 없이도 보여 준다)


@dataclass(frozen=True)
class Question:
    key: str
    ask: str                # AI 가 없을 때 쓰는 기본 문구
    kind: Literal["choice", "number", "symbols", "period"]
    choices: tuple[Choice, ...] = ()
    field: str = ""         # kind != "choice" 일 때 채우는 폼 키
    # 선택지가 앞 답에 따라 좁혀지는 노드만 쓴다(rule · weights).
    narrow: Optional[Callable[[dict], tuple[Choice, ...]]] = None


# --- goal -------------------------------------------------------------
# 패치는 비어 있다 — rule 을 좁히는 데만 쓴다. 첫 선택지가 가장 단순하고 안전한 목적이다.
_GOAL = (
    Choice("steady", "꾸준히 조금씩 모으고 싶어요", {},
           "정해진 날에 나눠 사면 한 번의 고점에 물릴 위험이 줄어요."),
    Choice("dip", "떨어졌을 때 싸게 사고 싶어요", {},
           "많이 내린 자리를 노려 사고, 오르면 파는 방식이에요."),
    Choice("trend", "오르는 흐름을 타고 싶어요", {},
           "상승이 이어지는 동안 따라가고, 꺾이면 빠져나와요."),
    Choice("protect", "번 것을 지키고 싶어요", {},
           "수익이 나면 굳히고, 내려가면 빨리 빠져나오는 쪽이에요."),
    Choice("range", "오르내림에서 차익을 보고 싶어요", {},
           "크게 오르지 않고 왔다 갔다 할 때 그 폭에서 벌어요."),
)

# --- risk -------------------------------------------------------------
# 손절(use_stop_loss · stop_loss_pct)과 한 번에 투입하는 비율(invest_ratio_pct).
# 첫 선택지가 손실이 가장 작다. "상관없다" 는 손절을 끈다.
_RISK = (
    Choice("tight", "작게 잃기 (손절 3%)",
           {"use_stop_loss": True, "stop_loss_pct": 3, "invest_ratio_pct": 50},
           "3% 내리면 손절하고, 자금은 절반만 써요."),
    Choice("normal", "보통 (손절 5%)",
           {"use_stop_loss": True, "stop_loss_pct": 5, "invest_ratio_pct": 80},
           "5% 내리면 손절하고, 자금의 80% 를 써요."),
    Choice("wide", "크게 감수 (손절 10%)",
           {"use_stop_loss": True, "stop_loss_pct": 10, "invest_ratio_pct": 100},
           "10% 까지 버티며 자금을 모두 써요. 손실도 커질 수 있어요."),
    Choice("free", "손절은 걸지 않을래요",
           {"use_stop_loss": False, "stop_loss_pct": 3, "invest_ratio_pct": 100},
           "손절 없이 자금을 모두 써요. 내려가도 자동으로 끊지 않아요."),
)
_RISKY_TIERS = frozenset({"wide", "free"})     # H · K 같은 거친 규칙은 여기서만 후보에 든다

# --- watch ------------------------------------------------------------
_WATCH = (
    Choice("daily", "하루에 한 번 확인해요", {"candle_interval": "1d"},
           "하루 단위로 판단해요. 잔 흔들림에 덜 휘둘려요."),
    Choice("hours", "몇 시간마다 봐요", {"candle_interval": "4h"},
           "4시간마다 판단해요."),
    Choice("often", "자주 지켜봐요 (1시간)", {"candle_interval": "1h"},
           "1시간마다 판단해요. 거래가 잦아져 수수료가 늘어요."),
)

# --- rule -------------------------------------------------------------
# 규칙별 기본값 — macro.js TYPE_DEFAULTS 와 같다(A·C 는 defaultForm 의 자기 칸).
_TYPE_DEFAULTS: dict[str, dict] = {
    "A": {"take_profit_pct": 5},
    "C": {"amount_per_buy": 100000, "interval_days": 7},
    "E": {"entry_mode": "immediate", "entry_dip": 3, "activation_profit": 5, "trail_percent": 3,
          "reenter_after_exit": True, "initial_capital": 1000000},
    "F": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "confirm_candles": 1,
          "exit_mode": "indicator", "take_profit": "", "initial_capital": 1000000},
    "G": {"bb_period": 20, "bb_std": 2.0, "strategy": "reversion", "exit_target": "mid",
          "squeeze_filter": False, "squeeze_lookback": 50, "initial_capital": 1000000},
    "H": {"base_order_size": 100000, "safety_order_size": 200000, "price_deviation": 2,
          "safety_order_step_scale": 1.5, "safety_order_volume_scale": 2.0, "max_safety_orders": 5,
          "take_profit": 1.5, "initial_capital": 1000000},
    "I": {"k": 0.5, "exit_mode": "next_open", "trail_percent": 2, "take_profit": "",
          "ma_filter_period": "", "session_start_hour": 9, "initial_capital": 1000000},
    "J": {"ma_type": "SMA", "fast_period": 20, "slow_period": 60, "entry_signal": "golden_cross",
          "exit_signal": "dead_cross", "take_profit": "", "confirm_candles": 1, "initial_capital": 1000000},
    "K": {"long_take_profit_pct": "", "drop_trigger_pct": 5, "partial_exit_pct": 50, "flip_to_short": True,
          "short_take_profit_pct": 5, "short_stop_loss_pct": 3, "reenter_long_after": True,
          "initial_capital": 1000000},
}

# 규칙마다 (라벨, 한 줄 근거). B(가격대) · D(그리드)는 내지 않는다 — 가격대(buy_price · lower_price
# 등)가 종목마다 달라, 코치가 종목 시세를 모른 채 기본값을 넣으면 안 맞는 폼이 나온다.
_RULE_TEXT: dict[str, tuple[str, str]] = {
    "A": ("목표가에 팔고 다시 사기", "5% 오르면 팔고, 다시 사서 되풀이해요. 가장 단순해요."),
    "C": ("정해진 날마다 같은 금액 사기", "7일마다 같은 금액을 사요. 사는 시점을 고민하지 않아도 돼요."),
    "E": ("오르는 동안 따라가다 꺾이면 팔기", "오르는 동안 들고 가다, 고점에서 3% 내리면 팔아요."),
    "F": ("너무 내렸을 때 사고 오르면 팔기", "RSI 가 30 아래로 내리면 사고, 70 위로 오르면 팔아요."),
    "G": ("밴드 아래서 사고 가운데서 팔기", "볼린저 밴드 아래로 벗어나면 사고, 중간선에서 팔아요."),
    "H": ("내릴 때마다 나눠 더 사기", "내릴 때마다 더 사서 평균 단가를 낮춰요. 하락이 길면 위험해요."),
    "I": ("전날 변동폭을 넘으면 사기", "전날 변동폭의 절반을 넘어 오르면 사고, 다음 시가에 팔아요."),
    "J": ("이동평균이 교차하면 사고팔기", "20봉선이 60봉선을 뚫고 오르면 사고, 내려가면 팔아요."),
    "K": ("내리면 일부 팔고 반대로 걸기", "내리면 일부를 팔고 하락에 거는 선물 매도로 바꿔요. 거친 편이에요."),
}

# 목적마다 후보 — 앞쪽일수록 단순하고 안전하다. 규칙 H · K 는 거친 규칙이다.
_RULES_BY_GOAL: dict[str, tuple[str, ...]] = {
    "steady": ("C", "A", "E", "J", "G"),
    "dip": ("F", "G", "E", "H"),
    "trend": ("J", "E", "I", "A", "K"),
    "protect": ("E", "A", "J", "K", "G"),
    "range": ("G", "F", "A", "E", "H"),
}
_ROUGH_RULES = frozenset("HK")
_FALLBACK_RULES = ("A", "E", "J")

# 규칙을 고를 때 같이 정하는 공통 칸. 코치는 롱 · 1배만 만든다(공매도 · 레버리지는 모른다).
_RULE_COMMON = {"position_side": "long", "leverage": 1}


def _rule_patch(rule: str) -> dict:
    """그 규칙의 패치. rule_type 과 그 규칙의 기본값을 **함께** 담는다 — 안 담으면 이전 규칙의 값이 남는다."""
    patch = {"rule_type": rule, **_RULE_COMMON, **_TYPE_DEFAULTS[rule]}
    if rule == "C":
        patch["candle_interval"] = "1d"          # 적립식은 날짜 간격을 일봉으로 센다
    if rule not in FILTERABLE:
        # 진입 조건 · 묶음 한도는 E~K 만 쓴다 — 앞에서 켠 것이 남으면 서버가 거부한다.
        patch["use_entry_filter"] = False
        patch["use_bundle_risk"] = False
    return patch


def _rule_choice(rule: str) -> Choice:
    label, why = _RULE_TEXT[rule]
    return Choice(rule, label, _rule_patch(rule), why)


def _pick(answers: dict, key: str, table: tuple[Choice, ...]) -> str:
    """앞 답 하나. 안 물었으면 그 노드의 첫 선택지, 모르는 값이면 ValueError."""
    got = answers.get(key)
    if got is None:
        return table[0].value
    if got not in {c.value for c in table}:
        raise ValueError(f"{key}: 모르는 답이에요: {got!r}")
    return got


def _rule_values(goal: str, risk: str, watch: str) -> tuple[str, ...]:
    """goal · risk · watch 에서 규칙 3~5개를 좁힌다."""
    rules = list(_RULES_BY_GOAL[goal])
    if risk not in _RISKY_TIERS:
        rules = [r for r in rules if r not in _ROUGH_RULES]    # 작게 잃고 싶은 사람에게 거친 규칙은 안 낸다
    if watch != "daily":
        rules = [r for r in rules if r != "C"]                  # 적립식은 일봉 전제다
    for extra in _FALLBACK_RULES:                                # 3개 미만이면 무난한 규칙으로 채운다
        if len(rules) >= 3:
            break
        if extra not in rules:
            rules.append(extra)
    return tuple(rules[:PAGE_SIZE])


def _narrow_rule(answers: dict) -> tuple[Choice, ...]:
    goal = _pick(answers, "goal", _GOAL)
    risk = _pick(answers, "risk", _RISK)
    watch = _pick(answers, "watch", _WATCH)
    return tuple(_rule_choice(r) for r in _rule_values(goal, risk, watch))


# --- symbols · weights · period · capital ------------------------------
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/\-]{1,19}$")


def _parse_symbols(raw) -> list[str]:
    """쉼표 문자열 → 대문자 · 중복 없는 종목 목록. 잘못되면 ValueError."""
    if not isinstance(raw, str):
        raise ValueError("종목은 글자로 적어 주세요.")
    out: list[str] = []
    for part in raw.split(","):
        sym = part.strip().upper()
        if not sym:
            continue
        if not _SYMBOL_RE.match(sym):
            raise ValueError(f"종목 이름이 이상해요: {part.strip()!r}")
        if sym not in out:
            out.append(sym)
    if not out:
        raise ValueError("종목을 하나 이상 적어 주세요.")
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"종목은 {MAX_SYMBOLS}개까지예요.")
    return out


def _symbol_count(answers: dict) -> int:
    try:
        return len(_parse_symbols(answers.get("symbols")))
    except ValueError:
        return 0


# 첫 종목에 더 싣는 비중 — 종목 수별로 합이 정확히 100 이다.
_LEAD_WEIGHTS = {
    2: (60, 40),
    3: (50, 25, 25),
    4: (40, 20, 20, 20),
    5: (40, 15, 15, 15, 15),
}


def _weights_choices(count: int) -> tuple[Choice, ...]:
    lead = ", ".join(str(w) for w in _LEAD_WEIGHTS[count])
    return (
        Choice("even", "똑같이 나누기", {"leg_weights": ""},
               "비워 두면 종목 수대로 똑같이 나눠요."),
        Choice("lead", "첫 종목에 더 싣기", {"leg_weights": lead},
               f"첫 종목에 더 싣고 나머지는 나눠요 ({lead})."),
        Choice("custom", "폼에서 직접 정하기", {},
               "비중 칸에 직접 적을 수 있게 비워 둬요."),
    )


def _narrow_weights(answers: dict) -> tuple[Choice, ...]:
    # 종목이 하나 이하여도 (next_key 는 그때 묻지 않지만) 2개 기준으로 낸다.
    count = min(max(_symbol_count(answers), 2), MAX_SYMBOLS)
    return _weights_choices(count)


# 기간은 입력이지만 프런트가 프리셋 버튼으로 그린다 — 선택지가 곧 버튼이다. "직접 지정"(custom)은
# 시작 · 종료 날짜를 따로 받아야 해서 코치는 내지 않는다.
_PERIODS = (
    Choice("1y", "최근 1년", {"preset": "1y"}, "사계절을 다 겪어 본 가장 긴 기간이에요."),
    Choice("6m", "최근 6개월", {"preset": "6m"}, "최근 반년의 흐름이에요."),
    Choice("3m", "최근 3개월", {"preset": "3m"}, "요즘 시장에 가까워요. 표본은 적어요."),
    Choice("1m", "최근 1개월", {"preset": "1m"}, "한 달만 봐요. 우연의 영향이 커요."),
    Choice("1w", "최근 1주", {"preset": "1w"}, "일주일만 봐요. 참고용이에요."),
)

# --- entry_filter ------------------------------------------------------
_ENTRY_FILTER = (
    Choice("none", "조건 없이 바로 진입", {"use_entry_filter": False},
           "규칙이 신호를 내면 바로 사요. 가장 단순해요."),
    Choice("ma", "이동평균선 위일 때만", {
        "use_entry_filter": True, "filter_kind": "ma",
        "filter_ma_type": "SMA", "filter_ma_period": 20, "filter_ma_side": "above"},
           "20봉 평균보다 위(오르는 쪽)일 때만 새로 사요."),
    Choice("rsi", "너무 과열되지 않았을 때만", {
        "use_entry_filter": True, "filter_kind": "rsi",
        "filter_rsi_period": 14, "filter_rsi_min": "", "filter_rsi_max": 70},
           "RSI 가 70 이하일 때만 새로 사요. 달아오른 자리를 피해요."),
    Choice("bb", "볼린저 밴드 안에 있을 때만", {
        "use_entry_filter": True, "filter_kind": "bb",
        "filter_bb_period": 20, "filter_bb_num_std": 2, "filter_bb_zone": "inside"},
           "가격이 밴드 안에 있을 때만 새로 사요. 튀는 자리를 피해요."),
)

# --- bundle_risk -------------------------------------------------------
# 한도 값은 종목 수와 무관하게 늘 유효하다 — 상한 1 은 종목이 둘 이상이면 항상 종목 수보다 작다.
# 첫 선택지는 손실을 기계적으로 막는 쪽(총 노출 50%)이다.
_BUNDLE_RISK = (
    Choice("half", "총 투입을 자금의 50% 까지", {
        "use_bundle_risk": True, "bundle_max_positions": "", "bundle_max_exposure_pct": 50},
           "종목이 몇 개든 자금의 절반 넘게는 안 넣어요."),
    Choice("one", "한 번에 한 종목만 보유", {
        "use_bundle_risk": True, "bundle_max_positions": 1, "bundle_max_exposure_pct": ""},
           "동시에 두 종목이 물리지 않아요."),
    Choice("none", "한도 없이", {
        "use_bundle_risk": False, "bundle_max_positions": "", "bundle_max_exposure_pct": ""},
           "묶음 전체에는 한도를 걸지 않아요."),
)

# --- 그래프 ------------------------------------------------------------
GRAPH: dict[str, Question] = {
    "goal": Question("goal", "어떤 쪽으로 시작해 볼까요?", "choice", _GOAL),
    "risk": Question("risk", "손실은 어디까지 괜찮으세요?", "choice", _RISK),
    "watch": Question("watch", "얼마나 자주 확인하실 수 있어요?", "choice", _WATCH),
    # 정적 choices 는 앞 답을 안 물었을 때의 기본 조합(각 노드의 첫 선택지)의 후보다.
    "rule": Question("rule", "이 중에서 마음이 가는 방식은요?", "choice",
                     _narrow_rule({}), narrow=_narrow_rule),
    "symbols": Question("symbols", "어떤 종목으로 해 볼까요? 쉼표로 최대 5개까지 적어 주세요.",
                        "symbols", field="symbol"),
    "weights": Question("weights", "종목마다 자금을 어떻게 나눌까요?", "choice",
                        _weights_choices(2), narrow=_narrow_weights),
    "period": Question("period", "얼마 동안의 과거로 시험해 볼까요?", "period",
                       _PERIODS, field="preset"),
    "capital": Question("capital", "시작 자금은 얼마로 할까요? 숫자만 적어 주세요.",
                        "number", field="initial_capital"),
    "entry_filter": Question("entry_filter", "새로 사기 전에 확인할 조건을 하나 걸까요?",
                             "choice", _ENTRY_FILTER),
    "bundle_risk": Question("bundle_risk", "여러 종목을 묶어 쓰니 전체 한도를 둘까요?",
                            "choice", _BUNDLE_RISK),
}
FIRST_KEY = "goal"
ORDER = ("goal", "risk", "watch", "rule", "symbols", "weights", "period", "capital",
         "entry_filter", "bundle_risk")

# 입력 노드를 건너뛰고 마무리할 때(턴 상한) 채우는 값. 기간은 선택지의 첫째(1년)를 쓴다.
INPUT_DEFAULTS = {"symbols": "BTCUSDT", "capital": "1000000"}

# --- 시작 자금에 따라 크기가 달라지는 칸 --------------------------------
# 규칙 C(적립식)의 1회 금액과 규칙 H(마틴게일)의 주문 크기는 자금에 맞춰야 한다. H 는 서버가
# "최악의 경우 필요 자금 <= 시작 자금 x 투입 비율" 을 검사한다(schema.py _validate_new_type).
# (키, 계수, 투입 비율을 곱하는가). 값 = 시작 자금 x 레그 몫 x [투입 비율] x 계수.
#   H: 기본 주문 b, 안전 주문 2b, 배율 2.0, 최대 5번 -> b + 2b(1+2+4+8+16) = 63b.
#      63b 가 예산의 90% 가 되게: b = 예산 x 0.9/63, 안전 주문 = 2b.
CAPITAL_SCALED: dict[str, tuple[tuple[str, float, bool], ...]] = {
    "C": (("amount_per_buy", 0.05, False),),
    "H": (("base_order_size", 0.9 / 63, True), ("safety_order_size", 1.8 / 63, True)),
}
MIN_CAPITAL = 1


def _floor6(x: float) -> float:
    return max(math.floor(x * 1e6) / 1e6, 1e-6)


def _weights_share(answers: dict) -> float:
    """가장 작은 레그가 받는 자금의 몫(0~1). 서버는 레그마다 '자기 몫' 으로 자금 검사를 한다."""
    n = _symbol_count(answers)
    if n < 2:
        return 1.0
    share = 1.0 / n
    if answers.get("weights") == "lead" and n in _LEAD_WEIGHTS:
        share = min(share, min(_LEAD_WEIGHTS[n]) / 100.0)
    return share


def _invest_ratio(answers: dict) -> float:
    for c in _RISK:
        if c.value == answers.get("risk"):
            return float(c.patch.get("invest_ratio_pct", 100)) / 100.0
    return 1.0                                   # defaultForm 의 invest_ratio_pct


def _capital_patch(capital, answers: dict) -> dict:
    out = {"initial_capital": capital}
    share, ratio = _weights_share(answers), _invest_ratio(answers)
    for key, coef, uses_ratio in CAPITAL_SCALED.get(answers.get("rule"), ()):
        out[key] = _floor6(capital * share * (ratio if uses_ratio else 1.0) * coef)
    return out


def _parse_capital(raw):
    if isinstance(raw, bool):
        raise ValueError("시작 자금은 숫자로 적어 주세요.")
    if isinstance(raw, (int, float)):
        num = float(raw)
    elif isinstance(raw, str):
        try:
            num = float(raw.strip().replace(",", ""))
        except ValueError:
            raise ValueError(f"시작 자금은 숫자로 적어 주세요: {raw!r}") from None
    else:
        raise ValueError("시작 자금은 숫자로 적어 주세요.")
    if not math.isfinite(num) or num < MIN_CAPITAL or num > 1e12:
        raise ValueError(f"시작 자금은 {MIN_CAPITAL} 이상의 숫자여야 해요.")
    return int(num) if num == int(num) else num


# --- 함수 --------------------------------------------------------------
def _is_filterable(answers: dict) -> bool:
    return answers.get("rule") in FILTERABLE


def _skip(key: str, answers: dict) -> bool:
    if key == "weights":
        return _symbol_count(answers) < 2
    if key == "entry_filter":
        return not _is_filterable(answers)
    if key == "bundle_risk":
        return _symbol_count(answers) < 2 or not _is_filterable(answers)
    return False


def next_key(answers: dict) -> Optional[str]:
    """다음에 물을 질문. 더 물을 것이 없으면 None.

    건너뛰기 규칙이 여기 한 곳에 모인다 — 종목이 하나면 weights · bundle_risk 를 묻지 않고,
    규칙이 E~K 가 아니면 entry_filter 를 묻지 않는다(bundle_risk 도 E~K 만).
    """
    for key in ORDER:
        if key in answers or _skip(key, answers):
            continue
        return key
    return None


def choices_for(key: str, answers: dict) -> tuple[Choice, ...]:
    """그 질문의 선택지 **전부**(페이지 나누기는 호출부가 한다)."""
    q = GRAPH.get(key)
    if q is None:
        raise ValueError(f"모르는 질문이에요: {key!r}")
    return q.narrow(answers) if q.narrow else q.choices


def patch_for(key: str, answer, answers: dict) -> dict:
    """답 하나가 폼에 넣는 것. 모르는 답이면 ValueError — 위변조 차단."""
    q = GRAPH.get(key)
    if q is None:
        raise ValueError(f"모르는 질문이에요: {key!r}")
    if q.kind in ("choice", "period"):
        for c in choices_for(key, answers):
            if c.value == answer:
                return dict(c.patch)
        raise ValueError(f"{key}: 선택지에 없는 답이에요: {answer!r}")
    if q.kind == "symbols":
        return {q.field: ", ".join(_parse_symbols(answer))}
    if q.kind == "number":
        return _capital_patch(_parse_capital(answer), answers)
    raise ValueError(f"{key}: 처리할 수 없는 종류예요: {q.kind}")


def defaults_for_remaining(answers: dict) -> dict:
    """턴 상한에 닿았을 때 남은 칸을 메꿀 패치. 각 노드의 첫 선택지(= 가장 보수적인 것)를 쓴다."""
    work = dict(answers)
    out: dict = {}
    while True:
        key = next_key(work)
        if key is None:
            return out
        q = GRAPH[key]
        if q.kind in ("choice", "period"):
            answer = choices_for(key, work)[0].value
        else:
            answer = INPUT_DEFAULTS[key]
        out.update(patch_for(key, answer, work))
        work[key] = answer


def _choice_json(c: Choice) -> dict:
    return {"value": c.value, "label": c.label, "patch": dict(c.patch), "why": c.why}


def to_json() -> dict:
    """프런트 전수 시험이 읽는 모양. {"first": …, "nodes": {key: {kind, ask, field, choices:[…]}}}

    rule · weights 는 narrow 함수를 담을 수 없어 좁혀질 수 있는 **모든 조합**을 미리 펼친다.
    rule.by 의 키는 f"{goal}|{risk}|{watch}", weights.by 의 키는 종목 수("2"~"5") 다.
    """
    nodes: dict = {}
    for key in ORDER:
        q = GRAPH[key]
        node = {"kind": q.kind, "ask": q.ask, "field": q.field,
                "choices": [_choice_json(c) for c in q.choices]}
        if key == "rule":
            node["by"] = {
                f"{g.value}|{r.value}|{w.value}": [
                    _choice_json(c) for c in _narrow_rule(
                        {"goal": g.value, "risk": r.value, "watch": w.value})]
                for g in _GOAL for r in _RISK for w in _WATCH
            }
        elif key == "weights":
            node["by"] = {str(n): [_choice_json(c) for c in _weights_choices(n)]
                          for n in sorted(_LEAD_WEIGHTS)}
        nodes[key] = node
    return {
        "first": FIRST_KEY,
        "order": list(ORDER),
        "page_size": PAGE_SIZE,
        "input_defaults": dict(INPUT_DEFAULTS),
        "filterable": sorted(FILTERABLE),
        # 시작 자금을 정할 때 같이 바뀌는 칸: 값 = 자금 x 레그 몫 x [투입 비율] x 계수(소수 6자리 내림).
        # 레그 몫 = min(1/종목 수, 첫 종목에 더 싣기면 가장 작은 비중/100).
        "capital_scaled": {rule: [{"key": k, "coef": coef, "uses_invest_ratio": ratio}
                                  for k, coef, ratio in rows]
                           for rule, rows in CAPITAL_SCALED.items()},
        "nodes": nodes,
    }
