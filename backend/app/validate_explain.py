"""검증 결과를 사람 말로 옮긴다 — 판정은 이미 서버가 내렸다.

이 분석은 좋은 말을 하면 안 된다. 수익률이 높아도 표본이 모자라면 그렇게 말한다.
칭찬하는 순간 도구가 아니라 장난감이 된다. 모델이 하는 일은 서버가 내린 경고와 숫자를
읽기 좋은 한국어로 말하는 것뿐이고, 경고를 더하거나 빼거나 누그러뜨릴 권한은 없다.
돌려주는 값도 글(text)과 출처(source)뿐이라 새 판정이 들어갈 자리가 없다.

답이 규칙을 어기면(금지어 · 수익률부터 말하기 · 서버가 싣지 않은 기사 제목 인용 · 너무
긴 글) 버리고 서버 문장으로 대신한다. 그 대신한 글에는 절대 AI 라는 이름표를 붙이지
않는다 — 일일 챌린지에서 템플릿이 AI 인 척한 채 몇 주를 간 일이 있어서 이름표(source)를
호출 결과로만 정한다.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata

from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime

logger = logging.getLogger(__name__)

# 칭찬 · 권유 · 단정의 말. 이 튜플이 그런 낱말을 코드에 적어도 되는 유일한 곳이다.
# 부분 문자열로 거른다 — 불확실 같은 정직한 말도 걸리지만, 걸리면 서버 문장으로 가므로
# 잘못 버리는 쪽이 잘못 통과시키는 쪽보다 낫다.
BANNED_WORDS = ("추천", "보장", "확실", "무조건", "유망", "안전", "좋은 전략")

# 프롬프트는 3~5 문장을 요구한다. 1200 자는 그 몇 배라서, 넘으면 규칙을 어긴 글이다.
# 자르지 않고 버린다 — 문장 중간에서 끊긴 해설은 고장 난 화면으로 보이고, 잘린 뒤쪽에
# 경고 문장이 있었다면 사라진 경고가 된다.
MAX_CHARS = 1200

# 경고가 있는데 첫 문장이 수익을 앞세우면 안 된다. 낱말 목록 + '수익 142' 같은 숫자 꼴.
_PROFIT_WORDS = ("수익률", "벌었", "앞섰", "이겼", "상회", "웃돌", "넘어섰")
_PROFIT_FIGURE = re.compile(r"수익(?:이|은|을|금)?\s*[+-]?\d")

# 따옴표 쌍. 「」 《》 “” "" ‘’ '' 가 모두 인용 표시다. 아스키 '' 와 ‘’ 는 영어 소유격
# (Binance's)과 같은 글자라서, 낱말 글자(영문 · 숫자)에 붙은 것은 따옴표로 보지 않는다.
# 한국어 조사가 바로 붙는 '상장'을 같은 꼴은 따옴표로 읽힌다.
_QUOTE_SPANS = (
    re.compile(r"「(.*?)」", re.S),
    re.compile(r"《(.*?)》", re.S),
    re.compile(r"“(.*?)”", re.S),
    re.compile(r'"(.*?)"', re.S),
    re.compile(r"(?<![A-Za-z0-9])‘(.*?)’(?![A-Za-z0-9])", re.S),
    re.compile(r"(?<![A-Za-z0-9])'(.*?)'(?![A-Za-z0-9])", re.S),
)
# 짝이 맞는 구간을 걷어 낸 뒤에도 남는 여는 · 닫는 표시는 짝 없는 인용이다(가짜 제목이
# 닫히지 않은 따옴표 뒤에 숨는 길을 막는다). 소유격과 겹치는 홑따옴표는 대상에서 뺀다.
_STRAY_QUOTE = re.compile(r'[「」《》“”"]')

# v2 — 공급자에 보내는 입력을 _model_input 으로 줄여서(링크 · 시각 제거, 소수 둘째 자리).
_PROMPT_VERSION = "validate-explain-v2"

# 서버 경고 코드 → 한 줄. 키가 engine.validation.WARNING_CODES 와 같은지는 시험이 지킨다.
_WARNING_TEXT = {
    "한_구간_집중": "수익 대부분이 한 구간에서 나왔습니다",
    "표본_부족": "거래 표본이 적어 통계로 쓰기 어렵습니다",
    "후반부_음수": "뒤쪽 구간에서 성과가 음수입니다",
    "거래_집중": "소수의 거래가 수익의 절반 이상을 만들었습니다",
}
_NO_WARNING_TEXT = "서버 판정에서 걸린 항목은 없습니다."

_SYSTEM = (
    "너는 백테스트 검증 결과를 설명하는 분석가야. 주어진 숫자와 경고만 쓰고 "
    "없는 사실을 만들지 마. 경고가 있으면 반드시 그것부터 말해. 수익률 자랑으로 "
    "시작하지 마. 전략을 쓰라거나 피하라는 말은 하지 마. 기사 제목은 입력으로 "
    "받은 것만 인용하고, 입력에 없으면 인용하지 마. 3~5 문장, 한국어."
)


def _fallback(payload: dict) -> str:
    """서버 판정을 그대로 읽는 문장. 모델 없이 만들고, 경고를 하나도 빼지 않는다."""
    codes = [str(code) for code in payload.get("warnings") or []]
    if not codes:
        return _NO_WARNING_TEXT
    # 문장이 없는 코드(서버에 새 경고가 생긴 경우)도 코드째 말한다. 경고가 있는데
    # '걸린 것 없음' 으로 읽히는 것이 가장 나쁘다.
    lines = [_WARNING_TEXT.get(code) or f"서버가 {code} 경고를 표시했습니다" for code in codes]
    return " ".join(f"{line}." for line in lines)


def _rounded(value):
    """소수 둘째 자리로 맞춘 사본. 142.00000000001 과 142.0 이 다른 캐시 키가 되지 않게 한다."""
    if isinstance(value, float):
        return round(value, 2) if value == value and value not in (float("inf"), float("-inf")) else value
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value


def _model_input(payload: dict) -> dict:
    """모델이 보는 입력이자 캐시 키의 재료. 둘이 같아야 키가 모델이 본 것을 정확히 가리킨다.

    숫자는 둘째 자리로 맞추고, 기사 항목은 제목과 출처만 남긴다 — 링크와 게시 시각은 모델에
    필요 없고, 같은 판정인데 그것만 달라 캐시가 빗나가는 일을 막는다.
    """
    shaped = _rounded(dict(payload))
    evidence = []
    for row in shaped.get("evidence") or []:
        if isinstance(row, dict) and isinstance(row.get("headlines"), dict):
            headlines = dict(row["headlines"])
            headlines["items"] = [
                {key: item[key] for key in ("title", "source") if key in item}
                for item in headlines.get("items") or [] if isinstance(item, dict)
            ]
            row = {**row, "headlines": headlines}
        evidence.append(row)
    shaped["evidence"] = evidence
    return shaped


def _ask_model(payload: dict) -> str:
    response = get_ai_client().messages.create(
        model=default_model(), max_tokens=700, system=_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        purpose="validate_explain",
    )
    for block in response.content:
        if getattr(block, "type", None) == "text" and str(block.text or "").strip():
            return str(block.text).strip()
    raise ValueError("empty analysis response")


def _normal(text) -> str:
    """제목 비교용. 공백을 하나로 접고 대소문자를 무시한다."""
    return " ".join(unicodedata.normalize("NFKC", str(text)).split()).casefold()


def _supplied_titles(payload: dict) -> set[str]:
    """서버가 근거로 실어 보낸 기사 제목들(비교용으로 다듬은 꼴)."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "title" and isinstance(value, str):
                    found.add(_normal(value))
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(payload.get("evidence"))
    found.discard("")
    return found


def _quoted_spans(text: str) -> tuple[list[str], bool]:
    """따옴표로 묶인 구간들과, 짝 없는 인용 표시가 남았는지."""
    spans: list[str] = []
    rest = text
    for pattern in _QUOTE_SPANS:
        spans += pattern.findall(rest)
        rest = pattern.sub(" ", rest)
    return spans, bool(_STRAY_QUOTE.search(rest))


def _first_sentence(text: str) -> str:
    # 마침표 뒤가 공백이거나 끝일 때만 문장 끝이다 — 3.5% 의 소수점에서 자르면 안 된다.
    return re.split(r"(?<=[.!?。])\s+|\n", text.strip(), maxsplit=1)[0]


def _rejection(text, payload: dict) -> str | None:
    """답을 버려야 하면 그 이유(로그용 코드), 써도 되면 None."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text) > MAX_CHARS:
        return "too_long"
    for word in BANNED_WORDS:
        if word in text:
            return f"banned_word:{word}"
    head = _first_sentence(text)
    if payload.get("warnings") and (any(word in head for word in _PROFIT_WORDS)
                                    or _PROFIT_FIGURE.search(head)):
        return "profit_lead"
    # 인용은 서버가 실은 제목과 같은 것만 허용한다. 근거가 없으면 허용되는 제목이 없으니
    # 인용 자체가 안 된다. 근거가 있을 때가 오히려 진짜 제목을 그럴듯하게 고쳐 쓰고 싶은
    # 유혹이 큰 때라서, 있는 경우에도 한 글자 한 글자 같은 제목만 통과시킨다.
    spans, stray = _quoted_spans(text)
    if stray:
        return "unbalanced_quote"
    supplied = _supplied_titles(payload)
    for span in spans:
        if _normal(span) and _normal(span) not in supplied:
            return "invented_quote"
    return None


def explain(payload: dict) -> dict:
    """{"text", "source"}. source 가 fallback 이면 화면에 'AI 분석' 이라 쓰지 않는다."""
    if not ai_available():
        return {"text": _fallback(payload), "source": "fallback"}
    seen = _model_input(payload)
    # 키의 재료는 모델이 보는 입력 + 지시문이다. 프롬프트 버전을 올리지 않고 문구만 고쳐도
    # 옛 답이 나가지 않는다. 거른 결과가 아니라 모델의 원 답을 캐시하므로, 같은 입력을
    # 다시 검증해도 모델을 또 부르지 않는다(상한이 없는 기능이라 비용 방어선이다).
    key = ai_cache_key("validate-explain", _PROMPT_VERSION, default_model(),
                       {"payload": seen, "system": _SYSTEM})
    try:
        text = get_ai_runtime().call(key, lambda: _ask_model(seen), retries=0)[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("validation analysis failed: reason=%s", type(exc).__name__)
        return {"text": _fallback(payload), "source": "fallback"}
    reason = _rejection(text, seen)
    if reason:
        logger.warning("validation analysis rejected: reason=%s warnings=%s",
                       reason, payload.get("warnings"))
        return {"text": _fallback(payload), "source": "fallback"}
    return {"text": text, "source": "ai"}
