"""검증 결과를 사람 말로 옮긴다 — 판정은 이미 서버가 내렸다.

이 분석은 좋은 말을 하면 안 된다. 수익률이 높아도 표본이 모자라면 그렇게 말한다.
칭찬하는 순간 도구가 아니라 장난감이 된다. 모델이 하는 일은 서버가 내린 경고와 숫자를
읽기 좋은 한국어로 말하는 것뿐이고, 경고를 더하거나 빼거나 누그러뜨릴 권한은 없다.
돌려주는 값도 글(text)과 출처(source)뿐이라 새 판정이 들어갈 자리가 없다.

답이 규칙을 어기면(금지어 · 수익률부터 말하기 · 경고 빠뜨리기 · 서버가 싣지 않은 기사 제목
인용 · 너무 긴 글) 버리고 서버 문장으로 대신한다. 그 대신한 글에는 절대 AI 라는 이름표를
붙이지 않는다 — 일일 챌린지에서 템플릿이 AI 인 척한 채 몇 주를 간 일이 있어서 이름표(source)를
호출 결과로만 정한다.

알려진 빈틈: 서버가 싣지 않은 기사를 따옴표 없이 풀어 쓰면(예: "어제 상장 기사가 있었다")
이 층은 걸러 내지 못한다. 근거가 없을 때 기사 · 뉴스를 말하는 것 자체는 정상 문장("그 날짜는
뉴스 보존 범위 밖이라 근거를 찾지 못했습니다")이라 낱말로 막으면 옳은 답이 폴백으로 간다.
그래서 막지 않고 로그(info)만 남긴다. 따옴표 인용은 서버가 실은 제목과 같아야만 통과한다.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata

from .ai_runtime import ai_available, ai_cache_key, default_model, get_ai_client, get_ai_runtime

logger = logging.getLogger(__name__)

# 칭찬 · 권유 · 단정의 말. 이 튜플이 그런 낱말을 코드에 적어도 되는 유일한 곳이다.
# 부분 문자열로 거른다. 걸리면 서버 문장으로 가므로 잘못 버리는 쪽이 잘못 통과시키는 쪽보다
# 낫다. 다만 아래 세 낱말은 부정형으로 쓰이면 오히려 우리가 원하는 조심스러운 말투라 예외를 둔다.
BANNED_WORDS = ("추천", "보장", "확실", "무조건", "유망", "안전", "좋은 전략")

# 부정형 예외는 확실 · 보장 · 안전에만 둔다. 추천 · 유망 · 무조건은 부정 접두어가 붙어도
# (비추천 같은) 그대로 판정처럼 읽히므로 어떤 경우에도 면제하지 않는다.
_NEGATABLE = ("확실", "보장", "안전")
_NEGATION_PREFIXES = "불비미"           # 불확실 · 미보장 · 비안전
# 부정형 꼬리 — 낱말 바로 뒤에 와야 한다: 확실하지 않습니다 · 보장되지는 않습니다 ·
# 안전하지 못합니다 · 확실치 않습니다 · 보장할 수 없습니다 · 확실하지않(띄어쓰기 없음).
_NEGATED_TAIL = re.compile(r"(?:(?:하|되)지는?\s?(?:않|못)|치\s?않|할\s?수\s?없|는\s?않)")
_HANGUL = re.compile(r"[가-힣]")

# 프롬프트는 3~5 문장을 요구한다. 1200 자는 그 몇 배라서, 넘으면 규칙을 어긴 글이다.
# 자르지 않고 버린다 — 문장 중간에서 끊긴 해설은 고장 난 화면으로 보이고, 잘린 뒤쪽에
# 경고 문장이 있었다면 사라진 경고가 된다.
MAX_CHARS = 1200

# 경고가 있는데 첫 문장이 수익을 앞세우면 안 된다. 낱말 목록 + 숫자와 함께 쓰인 꼴 두 가지
# ('수익은 약 142%' · '142% 의 수익'). 낱말 목록만으로는 끝이 없어서, 아래 첫 문장 닻 규칙이
# 나머지를 막는다.
_PROFIT_WORDS = ("수익률", "벌었", "앞섰", "이겼", "상회", "웃돌", "넘어섰")
# 조사는 이 넷만 본다. 의 는 뺀다 — '수익의 62% 가 소수의 거래에서' 는 거래_집중을 말하는 문장이다.
_PROFIT_FIGURE = re.compile(r"수익(?:이|은|을|금)?\s*(?:약|총)?\s*[+-]?\d")
_FIGURE_PROFIT = re.compile(r"[+-]?\d[\d.,]*\s*%\s*(?:의\s*)?(?:수익|이익|상승|올랐|올렸)")

# 인용 표시 쌍. 「」 『』 《》 〈〉 “” "" ‘’ '' 가 모두 인용이다. 아스키 ''·‘’ 는 영어
# 소유격(Binance's)과 같은 글자라서, 영문·숫자에 붙은 것은 따옴표로 보지 않는다. 한국어
# 조사는 영문이 아니므로 '상장'을 처럼 조사가 바로 붙은 홑따옴표는 인용으로 읽힌다.
_QUOTE_SPANS = (
    re.compile(r"「(.*?)」", re.S),
    re.compile(r"『(.*?)』", re.S),
    re.compile(r"《(.*?)》", re.S),
    re.compile(r"〈(.*?)〉", re.S),
    re.compile(r"“(.*?)”", re.S),
    re.compile(r'"(.*?)"', re.S),
    re.compile(r"(?<![A-Za-z0-9])‘(?!\d\d)(.*?)’(?![A-Za-z0-9])", re.S),
    re.compile(r"(?<![A-Za-z0-9])'(?!\d\d)(.*?)'(?![A-Za-z0-9])", re.S),
)
# 짝이 맞는 구간을 걷어 낸 뒤에도 남는 인용 표시는 짝 없는 인용이다(가짜 제목이 닫히지 않은
# 따옴표 뒤에 숨는 길을 막는다). 홑따옴표는 영문에 붙은 소유격과 줄인 연도('24년, 바로 뒤에
# 숫자 두 자리)만 빼고 모두 세운다. traders' 반응 · 5' 간격 · ETF' 상장 은 그대로 걸린다.
_STRAY_QUOTE = re.compile(r"""[「」『』《》〈〉“”"]|(?<![A-Za-z0-9])['‘](?!\d\d)|['’](?![A-Za-z0-9])""")
_NEWS_WORDS = re.compile(r"기사|뉴스|헤드라인")

# v2 — 공급자에 보내는 입력을 _model_input 으로 줄여서(허용 필드만, 링크 · 시각 제거, 소수 둘째 자리).
_PROMPT_VERSION = "validate-explain-v2"

# 서버 경고 코드 → 한 줄. 키가 engine.validation.WARNING_CODES 와 같은지는 시험이 지킨다.
_WARNING_TEXT = {
    "한_구간_집중": "수익 대부분이 한 구간에 몰렸습니다",
    "표본_부족": "거래 표본이 적어 통계로 쓰기 어렵습니다",
    "후반부_음수": "뒤쪽 구간에서 성과가 음수입니다",
    "거래_집중": "소수의 거래가 수익의 절반 이상을 만들었습니다",
}

# 서버 경고 코드 → 그 경고를 말하는 글에 나올 법한 낱말 묶음들. 묶음 안에서는 하나만 있으면
# 되고(any-of), 묶음이 여럿이면 모두 있어야 한다(한_구간_집중은 구간 말 + 쏠림 말 한 쌍).
# 낱말은 정규식이다('번' 은 숫자 뒤에서만). 하나의 낱말로 못 박으면 좋은 글을 잘못 버리므로
# 여러 낱말 중 하나로 하되, 코드끼리 낱말을 나눠 쓰지 않게 했다 — 구간 · 집중 을 두 코드가
# 함께 쓰면 후반부_음수 문장이 한_구간_집중을, 한_구간_집중 문장이 거래_집중을 대신 채운다.
_WARNING_ANCHORS = {
    "한_구간_집중": (("구간", "기간", "시기", "시점", "한 달"),
                 ("집중", "몰", "쏠", "편중", "치우")),
    "표본_부족": (("표본", "샘플", "횟수", "건수", "거래 수", "통계", r"\d\s*번"),),
    "후반부_음수": (("후반", "뒤쪽", "뒷부분", "마지막", "최근", "음수", "마이너스"),),
    "거래_집중": (("소수", "몇 건", "절반", "일부 거래", "특정 거래"),),
}
_NO_WARNING_TEXT = "서버 판정에서 걸린 항목은 없습니다."

# 입력 허용 필드. explain 이 신뢰 경계라서 이 밖의 키(요청 번호 · 시각 · 사용자 입력 이름 등
# 자유 문장)는 프롬프트에도 캐시 키에도 들어가지 않는다.
_ALLOWED_KEYS = ("final_return_pct", "buy_hold_return_pct", "mdd_pct", "total_trades",
                 "calmar", "sortino", "windows", "top_trade_share_pct", "top_month_share_pct",
                 "warnings", "evidence")

_SYSTEM = (
    "너는 백테스트 검증 결과를 설명하는 분석가야. 주어진 숫자와 경고만 쓰고 "
    "없는 사실을 만들지 마. 경고가 있으면 반드시 그것부터 말해. 수익률 자랑으로 "
    "시작하지 마. 전략을 쓰라거나 피하라는 말은 하지 마. 기사 제목은 입력으로 "
    "받은 것만 인용하고, 입력에 없으면 인용하지 마. 따옴표는 입력으로 받은 기사 "
    "제목에만 쓴다. 3~5 문장, 한국어."
)


class _Rejected(Exception):
    """모델의 답이 규칙을 어겨 버려야 할 때. 로더 안에서 올려 캐시에 남지 않게 한다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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

    허용 필드만 남기고, 숫자는 둘째 자리로 맞추고, 기사 항목은 제목과 출처만 남긴다 — 링크와
    게시 시각은 모델에 필요 없고, 같은 판정인데 그것만 달라 캐시가 빗나가는 일을 막는다.
    """
    shaped = _rounded({key: payload[key] for key in _ALLOWED_KEYS if key in payload})
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


def _negated(text: str, at: int, word: str) -> bool:
    """확실 · 보장 · 안전이 부정형(불확실 · 보장되지 않습니다)으로 쓰였는가."""
    if _NEGATED_TAIL.match(text, at + len(word)):
        return True
    if at >= 1 and text[at - 1] in _NEGATION_PREFIXES:
        # 접두어가 낱말 머리일 때만. 앞이 한글이면 지불보장 처럼 다른 낱말의 끝 글자다.
        return not (at >= 2 and _HANGUL.fullmatch(text[at - 2]))
    return False


def _banned_hit(text: str) -> str | None:
    for word in BANNED_WORDS:
        start = 0
        while (at := text.find(word, start)) >= 0:
            start = at + 1
            if word in _NEGATABLE and _negated(text, at, word):
                continue
            return word
    return None


def _anchored(text: str, code: str) -> bool:
    """그 경고의 낱말 묶음마다 하나 이상 글에 있는가."""
    return all(any(re.search(word, text) for word in group) for group in _WARNING_ANCHORS[code])


def _warning_mark(head: str, codes: list[str]) -> int | None:
    """첫 문장에서 경고를 말하기 시작하는 자리(경고라는 말이나 켜진 경고의 낱말이 처음 나온 곳)."""
    starts = [m.start() for word in ("경고",) for m in re.finditer(word, head)]
    for code in codes:
        for group in _WARNING_ANCHORS[code]:
            for word in group:
                starts += [m.start() for m in re.finditer(word, head)]
    return min(starts) if starts else None


def _return_figure(head: str, payload: dict) -> int | None:
    """첫 문장에서 서버가 준 수익 숫자(전략 · 홀딩, 42%)가 처음 나온 자리."""
    forms: set[str] = set()
    for key in ("final_return_pct", "buy_hold_return_pct"):
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        for places in (0, 1, 2):
            shown = f"{abs(value):.{places}f}"
            forms.add(shown.rstrip("0").rstrip(".") if "." in shown else shown)
    if not forms:
        return None
    pattern = r"(?<![\d.,])(?:%s)(?!\d|\.\d)\s*%%" % "|".join(re.escape(form) for form in forms)
    found = re.search(pattern, head)
    return found.start() if found else None


def _rejection(text, payload: dict) -> str | None:
    """답을 버려야 하면 그 이유(로그용 코드), 써도 되면 None."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text) > MAX_CHARS:
        return "too_long"
    word = _banned_hit(text)
    if word:
        return f"banned_word:{word}"
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
    codes = [str(code) for code in payload.get("warnings") or []]
    if not codes:
        return None
    # 서버가 낸 경고는 하나도 빠지면 안 된다. 확인할 수 없는 코드는 통과시키지 않는다(폴백이 말한다).
    for code in codes:
        if code not in _WARNING_ANCHORS:
            return f"unknown_warning:{code}"
    head = _first_sentence(text)
    if (any(word in head for word in _PROFIT_WORDS)
            or _PROFIT_FIGURE.search(head) or _FIGURE_PROFIT.search(head)):
        return "profit_lead"
    # 수익 숫자가 경고를 말하기 전에 나오면 수익부터 말한 것이다(낱말 목록에 없는 표현도 잡는다).
    figure, mark = _return_figure(head, payload), _warning_mark(head, codes)
    if figure is not None and mark is not None and figure < mark:
        return "profit_lead"
    for code in codes:
        if not _anchored(text, code):
            return f"missing_warning:{code}"
    # 첫 문장이 경고를 말해야 한다 — '경고가 두 가지 나왔습니다' 같은 안내 문장도 괜찮고,
    # 아니면 켜진 경고 중 하나를 온전히 말해야 한다.
    if "경고" not in head and not any(_anchored(head, code) for code in codes):
        return "warning_not_first"
    return None


def _ask_checked(seen: dict) -> str:
    """모델에게 묻고 규칙을 어긴 답은 올린다. 예외로 끝난 호출은 런타임이 캐시하지 않는다."""
    text = _ask_model(seen)
    reason = _rejection(text, seen)
    if reason:
        raise _Rejected(reason)
    return text


def explain(payload: dict) -> dict:
    """{"text", "source"}. source 가 fallback 이면 화면에 'AI 분석' 이라 쓰지 않는다."""
    if not ai_available():
        logger.info("validation analysis skipped: reason=no_ai_key")
        return {"text": _fallback(payload), "source": "fallback"}
    seen = _model_input(payload)
    # 키의 재료는 모델이 보는 입력 + 지시문이다. 프롬프트 버전을 올리지 않고 문구만 고쳐도
    # 옛 답이 나가지 않는다. 규칙을 통과한 답만 캐시된다 — 버려진 답은 예외라 남지 않아서,
    # 사용자가 다시 검증하면 모델을 새로 부른다(다시 검증하는 것이 이 기능의 목적이다).
    # 호출 상한은 이 함수가 아니라 호출하는 경로(Task 9 의 라우트)가 건다.
    key = ai_cache_key("validate-explain", _PROMPT_VERSION, default_model(),
                       {"payload": seen, "system": _SYSTEM})
    try:
        text = get_ai_runtime().call(key, lambda: _ask_checked(seen), retries=0)[0]
    except _Rejected as rejected:
        logger.warning("validation analysis rejected: reason=%s warnings=%s",
                       rejected.reason, payload.get("warnings"))
        return {"text": _fallback(payload), "source": "fallback"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("validation analysis failed: reason=%s", type(exc).__name__)
        return {"text": _fallback(payload), "source": "fallback"}
    if not _supplied_titles(seen) and _NEWS_WORDS.search(text):
        logger.info("validation analysis mentions news with no evidence supplied")
    return {"text": text, "source": "ai"}
