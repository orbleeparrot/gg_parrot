"""분석은 좋은 말을 하지 않는다 — 경고가 있으면 먼저 말하고, 금지어와 지어낸 인용은 걸러진다.

모델은 절대 부르지 않는다. 공급자 이음매는 ``_ask_model`` 하나뿐이고, 키가 없는 시험
환경에서도 그 길이 열리도록 ``ai_available`` 과 호출 런타임을 함께 바꿔 둔다.
"""
import logging

import pytest

from app import api_usage, validate_explain
from app.ai_runtime import ai_cache_key
from app.engine import validation

PAYLOAD = {"final_return_pct": 142.0, "buy_hold_return_pct": 38.0, "mdd_pct": 31.0,
           "total_trades": 14, "calmar": 1.2, "sortino": 0.9,
           "windows": [180.0, 5.0, -12.0, 3.0],
           "warnings": ["한_구간_집중", "표본_부족"],
           "evidence": []}

TITLE = "Binance Lists New Token"
EVIDENCE = [{"date": "2024-03-11", "change_pct": -8.2, "volume_ratio": 3.1,
             "headlines": {"items": [{"title": TITLE, "source": "wire",
                                      "url": "https://example.test/a", "published_ms": 1}],
                           "found": True, "reason": ""}}]
WITH_EVIDENCE = {**PAYLOAD, "evidence": EVIDENCE}

QUOTE_STYLES = [("「", "」"), ("《", "》"), ("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'")]


class _StubRuntime:
    """AiCallRuntime.call 과 같은 모양 — (값, 출처) 를 돌려주고 키를 적어 둔다."""

    def __init__(self):
        self.keys = []

    def call(self, key, loader, *, retries=None):
        self.keys.append(key)
        return loader(), "loaded"


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    stub = _StubRuntime()
    monkeypatch.setattr(validate_explain, "ai_available", lambda: True)
    monkeypatch.setattr(validate_explain, "get_ai_runtime", lambda: stub)
    return stub


def _answers(monkeypatch, text):
    calls = []

    def ask(payload):
        calls.append(payload)
        return text

    monkeypatch.setattr(validate_explain, "_ask_model", ask)
    return calls


def test_banned_words_cover_the_new_pro_vocabulary():
    for word in ("보장", "확실", "무조건", "유망", "안전", "좋은 전략"):
        assert word in validate_explain.BANNED_WORDS
    # 위 여섯 개 말고 기존 한 개가 더 있어 모두 일곱 개다(그 하나는 아래 시험이 값으로 훑는다).
    assert len(validate_explain.BANNED_WORDS) == 7


def test_every_banned_word_in_the_answer_falls_back(monkeypatch):
    for word in validate_explain.BANNED_WORDS:
        _answers(monkeypatch, f"거래가 14 회뿐입니다. 이 전략은 {word} 입니다.")
        assert validate_explain.explain(PAYLOAD)["source"] == "fallback", word


def test_an_answer_that_leads_with_profit_falls_back(monkeypatch):
    """경고가 있는데 수익률부터 말하면 안 된다."""
    _answers(monkeypatch, "수익률 142% 로 홀딩을 크게 앞섰습니다. 다만 표본이 적습니다.")
    assert validate_explain.explain(PAYLOAD)["source"] == "fallback"


def test_a_decimal_point_does_not_hide_a_profit_lead(monkeypatch):
    """첫 문장을 소수점에서 자르면 '총 3.5% 수익률' 의 수익률이 첫 문장 밖으로 밀린다."""
    _answers(monkeypatch, "총 3.5% 수익률로 홀딩을 앞섰습니다. 다만 표본이 적습니다.")
    assert validate_explain.explain(PAYLOAD)["source"] == "fallback"


def test_profit_first_is_fine_when_the_server_raised_no_warning(monkeypatch):
    _answers(monkeypatch, "수익률 12% 로 홀딩보다 높았습니다. 걸린 경고는 없습니다.")
    assert validate_explain.explain({**PAYLOAD, "warnings": []})["source"] == "ai"


def test_a_warning_first_answer_is_kept(monkeypatch):
    _answers(monkeypatch,
             "거래가 14 회뿐이라 통계로 쓰기 어렵습니다. 수익의 대부분이 첫 구간에서 나왔습니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "ai"
    assert set(out) == {"text", "source"}, "판정이 될 만한 필드를 더 싣지 않는다"


def test_the_fallback_never_claims_to_be_ai(monkeypatch):
    def boom(payload):
        raise RuntimeError("provider down")
    monkeypatch.setattr(validate_explain, "_ask_model", boom)
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert "AI" not in out["text"]
    assert "표본" in out["text"], "서버 판정은 폴백에서도 읽혀야 한다"


def test_without_a_key_the_model_is_never_asked(monkeypatch):
    monkeypatch.setattr(validate_explain, "ai_available", lambda: False)
    calls = _answers(monkeypatch, "거래가 적습니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert calls == []


# --- 지어낸 인용 -------------------------------------------------------------

def test_evidence_is_never_invented(monkeypatch):
    """서버가 근거를 못 찾았으면 AI 가 기사 제목을 지어내도 버린다."""
    _answers(monkeypatch, "거래가 적습니다. 「바이낸스 상장 공지」 기사가 있었습니다.")
    assert validate_explain.explain({**PAYLOAD, "evidence": []})["source"] == "fallback"


def test_a_title_that_was_never_supplied_falls_back_even_with_evidence(monkeypatch):
    """근거가 있어도, 실린 적 없는 제목을 따옴표로 옮기면 버린다."""
    _answers(monkeypatch, "거래가 적습니다. 「Binance Lists Two New Tokens Amid Surge」 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "fallback"


def test_an_embellished_real_title_falls_back(monkeypatch):
    _answers(monkeypatch, f"거래가 적습니다. 「{TITLE} Despite Regulatory Fears」 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "fallback"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_a_supplied_title_in_any_quote_style_is_kept(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"거래가 적습니다. {open_mark}{TITLE}{close_mark} 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "ai"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_a_made_up_title_in_any_quote_style_falls_back(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"거래가 적습니다. {open_mark}바이낸스 상장 공지{close_mark} 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "fallback"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_emphasis_quotes_are_not_allowed_when_no_evidence_was_supplied(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"거래가 {open_mark}14 회{close_mark}뿐입니다.")
    assert validate_explain.explain(PAYLOAD)["source"] == "fallback"


def test_title_matching_ignores_case_and_spacing(monkeypatch):
    _answers(monkeypatch, "거래가 적습니다. 「binance   LISTS new\ttoken」 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "ai"


def test_one_bad_span_among_good_ones_falls_back(monkeypatch):
    _answers(monkeypatch, f"거래가 적습니다. 「{TITLE}」 와 「없던 기사」 가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "fallback"


def test_an_unclosed_quote_cannot_hide_a_made_up_title(monkeypatch):
    _answers(monkeypatch, "거래가 적습니다. 「바이낸스 상장 공지 기사가 있었습니다.")
    assert validate_explain.explain(WITH_EVIDENCE)["source"] == "fallback"


def test_an_apostrophe_inside_a_title_is_not_a_quotation(monkeypatch):
    other = [{"date": "2024-03-11", "headlines": {"items": [{"title": "Binance's Listing"}]}}]
    _answers(monkeypatch, "거래가 적습니다. 「Binance's Listing」 기사가 있었습니다.")
    assert validate_explain.explain({**PAYLOAD, "evidence": other})["source"] == "ai"


def test_plain_prose_without_quotes_is_kept_even_with_no_evidence(monkeypatch):
    _answers(monkeypatch, "거래가 14 회뿐이라 통계로 쓰기 어렵습니다.")
    assert validate_explain.explain(PAYLOAD)["source"] == "ai"


# --- 길이와 폴백 문장 ----------------------------------------------------------

def test_a_too_long_answer_falls_back_instead_of_being_cut(monkeypatch):
    long_text = "거래가 14 회뿐이라 통계로 쓰기 어렵습니다. " + "구간별 성과가 고르지 않습니다. " * 80
    _answers(monkeypatch, long_text)
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert out["text"] != long_text[:validate_explain.MAX_CHARS]


def test_every_warning_code_has_a_sentence():
    """서버가 새 경고 코드를 만들면 여기서 먼저 걸린다 — 문장 없이 조용히 사라지지 않는다."""
    assert set(validate_explain._WARNING_TEXT) == set(validation.WARNING_CODES)


def test_no_warning_sentence_is_flattering_or_labelled_ai():
    for code, sentence in validate_explain._WARNING_TEXT.items():
        assert not any(word in sentence for word in validate_explain.BANNED_WORDS), code
        assert "AI" not in sentence, code


def test_the_fallback_says_every_warning_the_server_raised():
    out = validate_explain._fallback({"warnings": list(validation.WARNING_CODES)})
    for code in validation.WARNING_CODES:
        assert validate_explain._WARNING_TEXT[code] in out


def test_the_fallback_would_pass_its_own_checks():
    """폴백 문장이 금지어나 인용을 품으면 폴백이 스스로 규칙을 어긴다."""
    text = validate_explain._fallback({"warnings": list(validation.WARNING_CODES)})
    assert validate_explain._rejection(text, {"warnings": list(validation.WARNING_CODES),
                                              "evidence": []}) is None


def test_an_unknown_warning_code_is_still_said_not_dropped():
    text = validate_explain._fallback({"warnings": ["새_경고"]})
    assert "새_경고" in text
    assert "없습니다" not in text, "경고가 있는데 없다고 말하면 안 된다"


def test_the_fallback_with_no_warnings_says_so_plainly():
    assert "없습니다" in validate_explain._fallback({"warnings": []})


# --- 기록 · 캐시 · 사용량 --------------------------------------------------------

def test_a_rejection_is_logged_with_its_reason(monkeypatch, caplog):
    _answers(monkeypatch, "수익률 142% 로 앞섰습니다.")
    with caplog.at_level(logging.WARNING, logger=validate_explain.logger.name):
        validate_explain.explain(PAYLOAD)
    assert any("rejected" in r.getMessage() and "profit_lead" in r.getMessage()
               for r in caplog.records)


def test_a_provider_failure_is_logged(monkeypatch, caplog):
    def boom(payload):
        raise RuntimeError("provider down")
    monkeypatch.setattr(validate_explain, "_ask_model", boom)
    with caplog.at_level(logging.WARNING, logger=validate_explain.logger.name):
        validate_explain.explain(PAYLOAD)
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


def test_the_cache_key_ignores_float_noise_and_link_fields(monkeypatch, runtime):
    """같은 판정인데 부동소수 꼬리나 기사 링크·시각이 달라 캐시가 안 맞으면 매번 돈이 든다."""
    _answers(monkeypatch, "거래가 적습니다.")
    noisy = {**WITH_EVIDENCE, "final_return_pct": 142.00000000001,
             "evidence": [{**EVIDENCE[0], "headlines": {"items": [
                 {"title": TITLE, "source": "wire", "url": "https://example.test/other",
                  "published_ms": 999}], "found": True, "reason": ""}}]}
    validate_explain.explain(WITH_EVIDENCE)
    validate_explain.explain(noisy)
    assert runtime.keys[0] == runtime.keys[1]


def test_the_cache_key_changes_when_anything_the_model_sees_changes(monkeypatch, runtime):
    _answers(monkeypatch, "거래가 적습니다.")
    validate_explain.explain(PAYLOAD)
    validate_explain.explain({**PAYLOAD, "total_trades": 15})
    validate_explain.explain({**PAYLOAD, "warnings": ["표본_부족"]})
    validate_explain.explain(WITH_EVIDENCE)
    assert len(set(runtime.keys)) == 4


def test_the_cache_key_changes_when_the_instructions_change(monkeypatch, runtime):
    """프롬프트 버전을 올리지 않고 문구만 고쳐도 옛 답이 나가면 안 된다."""
    _answers(monkeypatch, "거래가 적습니다.")
    validate_explain.explain(PAYLOAD)
    monkeypatch.setattr(validate_explain, "_SYSTEM", validate_explain._SYSTEM + " 다른 문구.")
    validate_explain.explain(PAYLOAD)
    assert runtime.keys[0] != runtime.keys[1]


def test_the_model_sees_the_same_projection_the_key_is_built_from(monkeypatch, runtime):
    calls = _answers(monkeypatch, "거래가 적습니다.")
    validate_explain.explain(WITH_EVIDENCE)
    seen = calls[0]
    assert ai_cache_key("validate-explain", validate_explain._PROMPT_VERSION,
                        validate_explain.default_model(),
                        {"payload": seen, "system": validate_explain._SYSTEM}) == runtime.keys[0]
    assert seen["evidence"][0]["headlines"]["items"] == [{"title": TITLE, "source": "wire"}]


def test_the_cost_screen_lists_this_feature_without_a_daily_cap():
    rows = {code: (label, limit) for code, label, limit in api_usage.PURPOSES}
    assert rows["validate_explain"] == ("검증 결과 해설", None)
