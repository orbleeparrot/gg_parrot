"""코치의 말투 — AI 는 한 줄 문구와 라벨 · 순서만 손댄다.

**AI 가 선택지의 값 · 집합 · 패치를 바꿀 수 없다.** 그래야 코치가 낸 폼이 언제나 유효하고
(설계 원칙 3), AI 가 죽어도 코치가 돈다(원칙 1). 서버가 받은 응답을 세 가지로 걸러낸다:

- ``ask`` 는 길이 상한으로 자르고, 비었으면 그래프의 기본 문구를 쓴다.
- ``labels`` 는 그래프에 있는 value 에 대한 것만 받는다. 빈 문구도 버린다.
- ``order`` 는 그래프의 value **집합과 정확히 같을 때만** 쓴다 — 빠뜨리기 · 끼워넣기 · 중복을 막는다.

캐시 · 재시도는 ``ask.py`` 와 같은 ``get_ai_runtime().call`` 을 쓴다.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Callable, Optional

from .ai_runtime import ai_available, ai_cache_key, get_ai_client, get_ai_runtime
from .ask import _AI_MODEL, _strip_fences
from .coach_graph import Choice

PROMPT_VERSION = "coach-voice-1"
ASK_MAX_LEN = 120
LABEL_MAX_LEN = 28
_AI_MAX_TOKENS = 400

_SYSTEM = (
    "너는 투자 조건을 고르도록 돕는 코치의 말투를 다듬는 도우미다. "
    "질문 문구 한 줄과 선택지 라벨, 보여 줄 순서만 정한다. "
    "선택지를 더하거나 빼지 말고, 수익률이나 전망을 지어내지 말고, 조언·권유 문구를 넣지 마라. "
    'JSON 객체 하나만 출력해라: {"ask": "...", "labels": {"<value>": "..."}, "order": ["<value>", ...]}. '
    f"ask 는 {ASK_MAX_LEN}자 이내, 각 라벨은 {LABEL_MAX_LEN}자 이내로."
)


def _prompt(key: str, ask: str, choices: tuple[Choice, ...], answers: dict) -> str:
    body = {
        "question_key": key,
        "default_ask": ask,
        "answers_so_far": {k: v for k, v in (answers or {}).items() if k != "exchange"},
        "choices": [{"value": c.value, "label": c.label, "why": c.why} for c in choices],
    }
    return "이 질문의 말투를 다듬어 줘.\n" + json.dumps(body, ensure_ascii=False, default=str)


def _clean_labels(labels, values: list[str]) -> dict[str, str]:
    """그래프에 있는 value 의 라벨만, 비지 않고 상한 안으로. 나머지는 버린다."""
    clean: dict[str, str] = {}
    if isinstance(labels, dict):
        for value, text in labels.items():
            if value not in values or not isinstance(text, str):
                continue          # 그래프에 없는 value · 문자열 아닌 라벨은 버린다
            text = text.strip()[:LABEL_MAX_LEN].strip()
            if text:
                clean[value] = text
    return clean


def voice(key: str, ask: str, choices: tuple[Choice, ...], answers: dict,
          ask_ai: Optional[Callable[[str], str]] = None) -> tuple[str, tuple[Choice, ...], bool]:
    """(문구, 선택지, AI 를 썼는가). 어떤 실패도 그래프의 기본값으로 돌아간다 — 예외를 올리지 않는다."""
    if ask_ai is None:
        return ask, choices, False
    try:
        obj = json.loads(_strip_fences(ask_ai(_prompt(key, ask, choices, answers))))
        if not isinstance(obj, dict):
            return ask, choices, False

        values = [c.value for c in choices]

        new_ask = obj.get("ask")
        new_ask = new_ask.strip()[:ASK_MAX_LEN].strip() if isinstance(new_ask, str) else ""

        clean_labels = _clean_labels(obj.get("labels"), values)

        order = obj.get("order")
        ordered = list(choices)
        if (isinstance(order, list) and all(isinstance(v, str) for v in order)
                and len(order) == len(values) and set(order) == set(values)):
            by_value = {c.value: c for c in choices}
            ordered = [by_value[v] for v in order]

        # 라벨만 바꾼다 — value · patch · why 는 그래프의 것 그대로(dataclasses.replace).
        result = tuple(replace(c, label=clean_labels[c.value]) if c.value in clean_labels else c
                       for c in ordered)
        return (new_ask or ask), result, True
    except Exception:
        return ask, choices, False


def default_ask_ai() -> Optional[Callable[[str], str]]:
    """코치 말투에 쓸 AI 호출자. 쓸 수 없으면 None(그래프 기본값으로 돈다).

    ``ask.py`` 의 ``_candidate_ai`` 와 같은 길 — ``messages.create`` 를 ``get_ai_runtime().call`` 로 감싼다.
    """
    if not ai_available():
        return None

    def ask_ai(prompt: str) -> str:
        key = ai_cache_key("coach-voice", PROMPT_VERSION, _AI_MODEL,
                           {"system": _SYSTEM, "prompt": prompt, "max_tokens": _AI_MAX_TOKENS})

        def load():
            response = get_ai_client().messages.create(
                model=_AI_MODEL, max_tokens=_AI_MAX_TOKENS, system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}], purpose="coach-voice",
                # 화면에서 사람이 기다리는 한 턴이다 — 후보 선별(45초)보다 짧게 끊는다.
                timeout=float(os.environ.get("COACH_AI_TIMEOUT_SEC", "8")),
            )
            text = next((b.text for b in response.content
                         if getattr(b, "type", None) == "text"), None)
            if not text:
                raise ValueError("empty coach voice response")
            return text

        return get_ai_runtime().call(key, load, retries=0)[0]

    return ask_ai
