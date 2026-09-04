"""AI 원인 분석(껄무새 해설의 AI 층) — Anthropic Claude (Messages API).

SERVER-SIDE ONLY. Uses the official ``anthropic`` SDK, which reads the key from
``ANTHROPIC_API_KEY`` in the environment (local .env for dev, Render env for
deploy) — never hardcoded, never logged, never returned to the client. There is
NO user-supplied key: if the server key is set the feature is on for everyone; if
not, callers fall back to the deterministic rule-based explanation.

The model is given ONLY the already-computed backtest metrics and asked to
explain — clearly and within 5 lines — WHY the result turned out this way. It
never invents numbers, predicts the future, or gives advice (guardrails in
``_SYSTEM``). ``generate`` raises :class:`AiError` (friendly Korean) on failure;
``enrich`` swallows it and returns the rule-based baseline.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic

from .engine.backtest import BacktestResult
from .engine.explain import MOODS, Explanation, explain_result
from .engine.schema import Macro
from .engine.summary import human_summary
from .ai_runtime import (
    AiBusyError,
    ai_cache_key,
    get_ai_runtime,
    get_anthropic_client,
)

# Default per the claude-api guidance; override with ANTHROPIC_MODEL. For this
# cheap, high-volume task claude-haiku-4-5 is far more cost-effective — set the
# env var to switch without a code change.
_DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "2048"))
_MAX_CALLS_PER_DAY = max(
    0,
    int(os.environ.get("AI_EXPLAIN_MAX_CALLS_PER_DAY", "20")),
)
_PROMPT_VERSION = "backtest-explanation-v3"
_daily_budget_lock = threading.Lock()
_daily_budget: tuple[str, int] = ("", 0)

# 말투는 마스코트 컨셉 그 자체다 — '껄무새'는 "살 껄, 팔 껄" 하고 뒤늦게 되뇌는 앵무새다.
# 그래서 해설 문장은 '~껄'로 끝난다. 바뀌는 건 어미뿐이고 내용 규칙(숫자 근거, 예측·조언
# 금지)은 그대로다. 어미가 권유처럼 읽히지 않게 규칙 (5)에서 다시 못을 박는다.
_TONE = (
    "말투: 너는 '살 껄, 팔 껄' 하고 뒤늦게 중얼거리는 앵무새 '껄무새'야. "
    "모든 문장을 '~껄' 계열 어미로 끝내. "
    "예: \"손절선이 너무 빡빡했던 걸껄\", \"이건 횡보장에서 특히 힘들었을껄\", "
    "\"매매가 3번뿐이라 조건이 까다로웠던 거껄\", \"레버리지가 없었으면 청산은 안 났을껄\". "
    "'~습니다/~해요/~이다'로 끝내지 말고, 표기도 '걸'이 아니라 '껄'로 써. "
    "다만 말장난에 취해 내용을 흐리지는 마 — 어미만 껄무새고 근거는 숫자 그대로야."
)

_SYSTEM = (
    "너는 코인 '코린이(초보)'에게 백테스트 결과를 쉽게 풀어주는 도우미 '껄무새'야. "
    + _TONE + " "
    "규칙: (1) 반드시 한국어, 어려운 용어는 풀어서. (2) 주어진 숫자만 근거로 쓰고 "
    "새 수치를 지어내지 마. (3) 과거 결과의 원인만 설명하고, 미래 예측이나 "
    "'사라/팔아라/추천·수익보장' 같은 투자 조언은 절대 하지 마. "
    "(4) 전체가 10줄을 넘지 않게 간결하게. 두 가지를 담아: "
    "① 왜 이런 결과가 나왔는지(원인), ② 이 매크로를 실제로 쓴다면 어떤 성격·리스크의 "
    "전략인지, 어떤 장세에 맞고 뭘 주의해야 하는지(교육적 관점, 예측·조언 아님). "
    "(5) '살 껄/팔 껄'은 마스코트의 후회 섞인 말버릇일 뿐이야. 지금 사라거나 팔라는 "
    "권유로 읽힐 문장은 쓰지 마 — 어디까지나 지나간 시뮬레이션 이야기껄. "
    "출력은 코드펜스 없이 JSON 객체 하나만: "
    '{"mood": "<' + "|".join(MOODS) + '>", '
    '"headline": "<결과 핵심을 쉬운 한 문장으로, ~껄로 끝맺기>", '
    '"points": ["<숫자를 근거로 한 원인 3~4개, 각 한 줄 쉬운 말, ~껄로 끝맺기>"], '
    '"lesson": "<이 매크로를 실제로 쓴다면의 관점: 성격/리스크/주의점 1~2문장, ~껄로 끝맺기>"}'
)

_USER_PROMPT = (
    "다음 백테스트 지표(JSON)를 코린이 눈높이로 쉽게 풀어줘. headline은 결과 핵심 한 줄, "
    "points는 숫자 근거 원인 3~4개(각 한 줄), lesson은 '이 매크로를 실제로 쓴다면' 관점. "
    "모든 문장은 껄무새 말투로 '~껄'로 끝내. 전체 10줄 이내. 지정된 JSON으로만.\n지표:\n"
)


class AiError(Exception):
    """Raised by ``generate`` with a user-facing Korean message."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _kst_date() -> str:
    return datetime.now(
        timezone(timedelta(hours=9)),
    ).date().isoformat()


def _reserve_durable_ai_explain_budget(*, daily_limit: int) -> bool:
    from .agent_features.position_news.repository import reserve_ai_budget

    return reserve_ai_budget(
        daily_limit=daily_limit,
        namespace="ai_explain",
    )


def _reserve_ai_explain_call() -> bool:
    global _daily_budget
    if _MAX_CALLS_PER_DAY <= 0:
        return False
    if os.environ.get("DATABASE_URL"):
        try:
            return _reserve_durable_ai_explain_budget(
                daily_limit=_MAX_CALLS_PER_DAY,
            )
        except Exception:
            # A configured shared database owns the global budget. Fail closed
            # instead of multiplying paid calls when that database is down.
            return False
    day = _kst_date()
    with _daily_budget_lock:
        budget_day, used = _daily_budget
        if budget_day != day:
            used = 0
        if used >= _MAX_CALLS_PER_DAY:
            _daily_budget = (day, used)
            return False
        _daily_budget = (day, used + 1)
        return True


def ai_available() -> bool:
    """True when the server Anthropic key is configured (feature is on)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _facts(macro: Macro, r: BacktestResult, per_symbol=None) -> str:
    data = {
            "요약": human_summary(macro),
            "종목": macro.symbol,
            "레버리지": macro.leverage,
            "최종수익률%": r.final_return_pct,
            "그냥홀딩수익률%": r.buy_hold_return_pct,
            "MDD%": r.mdd_pct,
            "승률%": r.win_rate_pct,
            "총매매횟수": r.total_trades,
            "샤프지수": r.sharpe,
            "손익비": r.profit_factor,
            "최대연속손절": r.max_consecutive_losses,
            "청산횟수": r.liquidation_count,
    }
    if per_symbol:
        # Portfolio: give the per-symbol breakdown so the AI can say which coin
        # helped/hurt and how correlated the legs were.
        data["멀티종목_포트폴리오"] = True
        data["종목별"] = [
            {
                "종목": s.get("symbol"),
                "수익률%": s.get("final_return_pct"),
                "MDD%": s.get("mdd_pct"),
                "승률%": s.get("win_rate_pct"),
                "매매횟수": s.get("total_trades"),
            }
            for s in per_symbol
        ]
    return json.dumps(data, ensure_ascii=False)


def _extract_text(resp) -> Optional[str]:
    """First text block of a Claude response (thinking blocks may precede it)."""
    try:
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
    except (AttributeError, TypeError):
        return None
    return None


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        # drop a leading language hint line (e.g. "json\n{...}")
        if "\n" in t:
            first, rest = t.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                t = rest
    return t.strip()


def generate_with_cache_status(
    macro: Macro,
    result: BacktestResult,
    *,
    per_symbol=None,
    model: Optional[str] = None,
) -> tuple[Explanation, str]:
    """Return an explanation and ``loaded|cached|shared`` runtime state."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AiError("서버에 Anthropic 키가 설정되지 않았어요.")
    system = _SYSTEM
    if per_symbol:
        system += (
            " 이건 여러 종목을 함께 굴린 '포트폴리오' 결과야. 종목별 성과 차이(어느 코인이 "
            "끌어올리고 어느 코인이 깎아먹었는지)와 분산 효과 관점도 쉽게 짚어줘."
        )
    selected_model = model or _DEFAULT_MODEL
    facts = _facts(macro, result, per_symbol)
    cache_key = ai_cache_key(
        "backtest-explanation",
        _PROMPT_VERSION,
        selected_model,
        {"system": system, "facts": facts, "max_tokens": _MAX_TOKENS},
    )

    def load_explanation():
        if not _reserve_ai_explain_call():
            raise AiError("오늘 사용할 수 있는 AI 심화 분석 횟수를 모두 사용했어요.")
        response = get_anthropic_client().messages.create(
            model=selected_model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": _USER_PROMPT + facts}],
        )
        text = _extract_text(response)
        if not text:
            raise AiError("AI 응답을 해석하지 못했어요.")
        try:
            obj = json.loads(_strip_fences(text))
        except (json.JSONDecodeError, TypeError):
            raise AiError("AI 응답 형식이 올바르지 않아요.")

        headline = str(obj.get("headline", "")).strip()
        points = [str(point) for point in obj.get("points", []) if str(point).strip()][:5]
        if not headline or not points:
            raise AiError("AI 응답이 비어 있어요.")
        base_mood = explain_result(macro, result).mood
        mood = obj.get("mood")
        return Explanation(
            mood=mood if mood in MOODS else base_mood,
            headline=headline,
            points=points,
            lesson=str(obj.get("lesson", "")).strip(),
            source="ai",
        )

    try:
        explanation, runtime_state = get_ai_runtime().call(cache_key, load_explanation)
    except AiBusyError:
        raise AiError("AI 요청이 몰려 있어요. 잠시 후 다시 시도해 주세요.")
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise AiError("Anthropic 키가 유효하지 않거나 권한이 없어요.")
    except anthropic.RateLimitError:
        raise AiError("요청이 몰렸어요(레이트 리밋). 잠시 후 다시 시도해 주세요.")
    except anthropic.APIStatusError as exc:
        detail = str(getattr(exc, "message", "") or exc).lower()
        if "credit" in detail or "billing" in detail:
            raise AiError("Anthropic 크레딧이 부족해요. 콘솔에서 결제/충전을 확인해 주세요.")
        raise AiError("AI 호출에 실패했어요.")
    except anthropic.APIConnectionError:
        raise AiError("네트워크 오류로 AI 호출에 실패했어요. 잠시 후 다시 시도해 주세요.")

    return explanation, runtime_state


def generate(macro: Macro, result: BacktestResult, *, per_symbol=None, model: Optional[str] = None) -> Explanation:
    """Call Claude and return an AI Explanation. Raises :class:`AiError` on failure."""
    return generate_with_cache_status(
        macro,
        result,
        per_symbol=per_symbol,
        model=model,
    )[0]


def enrich(macro: Macro, result: BacktestResult, base: Optional[Explanation] = None) -> Explanation:
    """Return an AI Explanation, or the rule-based ``base`` on any failure."""
    if base is None:
        base = explain_result(macro, result)
    if not ai_available():
        return base
    try:
        return generate(macro, result)
    except Exception:
        return base
