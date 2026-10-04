"""분석은 좋은 말을 하지 않는다 — 경고가 있으면 먼저 말하고, 금지어와 지어낸 인용은 걸러진다.

모델은 절대 부르지 않는다. 공급자 이음매는 ``_ask_model`` 하나뿐이고, 키가 없는 시험
환경에서도 그 길이 열리도록 ``ai_available`` 과 호출 런타임을 함께 바꿔 둔다. 기본 런타임은
캐시하지 않는 가짜이고, 캐시 동작을 보는 시험만 진짜 ``AiCallRuntime`` 을 쓴다.
"""
import logging

import pytest

from app import api_usage, validate_explain
from app.ai_runtime import AiCallRuntime, ai_cache_key
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

# 경고 두 개(한_구간_집중 · 표본_부족)를 첫 문장부터 말하는 좋은 답의 머리.
LEAD = "표본이 적어 통계로 쓰기 어렵습니다. 수익이 한 구간에 쏠렸습니다."

QUOTE_STYLES = [("「", "」"), ("『", "』"), ("《", "》"), ("〈", "〉"), ("“", "”"),
                ('"', '"'), ("‘", "’"), ("'", "'")]

# 경고 하나씩만 말하는 짧은 문장. 서로의 닻 낱말을 건드리지 않게 골랐다(빠뜨림 시험용).
SAY = {"한_구간_집중": "수익이 한 시기에 쏠렸습니다",
       "표본_부족": "표본이 적습니다",
       "후반부_음수": "마지막 성과가 음수입니다",
       "거래_집중": "소수의 거래가 절반 이상을 만들었습니다"}
ALL_CODES = list(validation.WARNING_CODES)


class _StubRuntime:
    """AiCallRuntime.call 과 같은 모양 — (값, 출처) 를 돌려주고 키를 적어 둔다. 캐시는 없다."""

    def __init__(self):
        self.keys = []
        self.retries = []

    def call(self, key, loader, *, retries=None):
        self.keys.append(key)
        self.retries.append(retries)
        return loader(), "loaded"


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    stub = _StubRuntime()
    monkeypatch.setattr(validate_explain, "ai_available", lambda: True)
    monkeypatch.setattr(validate_explain, "get_ai_runtime", lambda: stub)
    return stub


@pytest.fixture
def real_runtime(monkeypatch):
    """캐시 · 단일 비행이 실제로 돈다."""
    real = AiCallRuntime(cache_ttl_seconds=60, retries=0)
    monkeypatch.setattr(validate_explain, "get_ai_runtime", lambda: real)
    return real


def _answers(monkeypatch, *texts):
    """_ask_model 을 가짜로. 답을 차례로 주고 다 쓰면 마지막 답을 반복한다. 받은 입력을 돌려준다."""
    calls = []

    def ask(payload):
        calls.append(payload)
        return texts[min(len(calls), len(texts)) - 1]

    monkeypatch.setattr(validate_explain, "_ask_model", ask)
    return calls


def _source(payload=PAYLOAD):
    return validate_explain.explain(payload)["source"]


# --- 금지어 --------------------------------------------------------------------

def test_banned_words_cover_the_new_pro_vocabulary():
    for word in ("보장", "확실", "무조건", "유망", "안전", "좋은 전략"):
        assert word in validate_explain.BANNED_WORDS
    # 이름으로 확인한 여섯 개 말고 한 개가 더 있어 모두 일곱 개다. 그 하나는 아래 시험이
    # 튜플의 값을 그대로 돌며 확인한다.
    assert len(validate_explain.BANNED_WORDS) == 7


def test_every_banned_word_in_the_answer_falls_back(monkeypatch):
    for word in validate_explain.BANNED_WORDS:
        _answers(monkeypatch, f"{LEAD} 이 전략은 {word} 입니다.")
        assert _source() == "fallback", word


@pytest.mark.parametrize("sentence", [
    "다만 결과는 불확실합니다.",
    "이 결과가 맞다고 확실하지 않습니다.",
    "앞으로의 성과는 보장되지 않습니다.",
    "이 구성이 안전하지 않을 수 있습니다.",
    "미보장 상태입니다.",
])
def test_cautious_negated_forms_are_kept(monkeypatch, sentence):
    _answers(monkeypatch, f"{LEAD} {sentence}")
    assert _source() == "ai"


@pytest.mark.parametrize("sentence", [
    "이 전략은 비추천 입니다.",
    "지불보장 이 됩니다.",
    "이 전략은 확실 합니다.",
    "이 전략은 안전 합니다.",
    "보장 합니다.",
    "불유망 입니다.",
    "불무조건 입니다.",
])
def test_negation_exemption_is_narrow(monkeypatch, sentence):
    """부정 접두어가 붙은 권유어는 판정으로 읽히고, 지불보장의 '불' 은 낱말 머리가 아니다."""
    _answers(monkeypatch, f"{LEAD} {sentence}")
    assert _source() == "fallback"


def test_a_negated_form_does_not_hide_a_later_banned_use(monkeypatch):
    _answers(monkeypatch, f"{LEAD} 불확실하지만 안전합니다.")
    assert _source() == "fallback"


# --- 수익부터 말하기 -------------------------------------------------------------

def test_an_answer_that_leads_with_profit_falls_back(monkeypatch):
    """경고가 있는데 수익률부터 말하면 안 된다."""
    _answers(monkeypatch, "수익률 142% 로 홀딩을 크게 앞섰습니다. 다만 표본이 적습니다.")
    assert _source() == "fallback"


def test_a_decimal_point_does_not_hide_a_profit_lead(monkeypatch):
    """첫 문장을 소수점에서 자르면 '총 3.5% 수익률' 의 수익률이 첫 문장 밖으로 밀린다."""
    _answers(monkeypatch, "총 3.5% 수익률로 홀딩을 앞섰습니다. 다만 표본이 적습니다.")
    assert _source() == "fallback"


@pytest.mark.parametrize("text", [
    "수익은 약 142% 입니다. 표본이 적습니다.",
    "수익 약 142% 였습니다. 표본이 적습니다. 한 구간에 쏠렸습니다.",
    "142% 의 수익을 냈습니다. 표본이 적습니다. 한 구간에 쏠렸습니다.",
    "홀딩보다 높은 성과를 냈습니다. 표본이 적습니다. 한 구간에 쏠렸습니다.",
])
def test_profit_first_phrasings_that_slip_past_a_word_list_fall_back(monkeypatch, text):
    _answers(monkeypatch, text)
    assert _source() == "fallback"


@pytest.mark.parametrize("word", ["수익률", "벌었", "앞섰", "이겼", "상회", "웃돌", "넘어섰"])
def test_each_profit_word_is_checked_on_its_own(word):
    """첫 문장에 경고 닻이 있어도 수익 낱말이 있으면 수익부터 말한 것이다."""
    payload = {"warnings": ["표본_부족"], "evidence": []}
    text = f"표본이 적지만 {word}습니다. 통계로 쓰기 어렵습니다."
    assert validate_explain._rejection(text, payload) == "profit_lead"


@pytest.mark.parametrize("head", [
    "표본이 적지만 수익은 약 142% 입니다.",
    "표본이 적지만 수익 총 142% 입니다.",
    "표본이 적지만 수익 142% 입니다.",
    "표본이 적지만 142% 의 수익을 냈습니다.",
    "표본이 적지만 142% 상승했습니다.",
])
def test_each_profit_pattern_branch_is_checked_on_its_own(head):
    payload = {"warnings": ["표본_부족"], "evidence": []}
    assert validate_explain._rejection(f"{head} 통계로 쓰기 어렵습니다.", payload) == "profit_lead"


def test_the_first_sentence_must_carry_an_active_warning():
    payload = {"warnings": ["표본_부족"], "evidence": []}
    text = "홀딩과 비교한 결과가 있습니다. 표본이 적습니다."
    assert validate_explain._rejection(text, payload) == "warning_not_first"
    assert validate_explain._rejection("표본이 적습니다. 홀딩과 비교한 결과가 있습니다.", payload) is None


def test_profit_first_is_fine_when_the_server_raised_no_warning(monkeypatch):
    _answers(monkeypatch, "수익률 12% 로 홀딩보다 높았습니다. 걸린 경고는 없습니다.")
    assert _source({**PAYLOAD, "warnings": []}) == "ai"


def test_a_warning_first_answer_is_kept(monkeypatch):
    _answers(monkeypatch,
             "거래가 14 회뿐이라 통계로 쓰기 어렵습니다. 수익이 첫 구간에 몰렸습니다.")
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "ai"
    assert set(out) == {"text", "source"}, "판정이 될 만한 필드를 더 싣지 않는다"


# --- 경고를 빠뜨리지 않는다 ----------------------------------------------------------

def test_every_warning_code_has_a_sentence_and_anchors():
    """서버가 새 경고 코드를 만들면 여기서 먼저 걸린다 — 문장 없이 조용히 사라지지 않는다."""
    assert set(validate_explain._WARNING_TEXT) == set(validation.WARNING_CODES)
    assert set(validate_explain._WARNING_ANCHORS) == set(validation.WARNING_CODES)
    assert set(SAY) == set(validation.WARNING_CODES)


@pytest.mark.parametrize("code", ALL_CODES)
def test_each_codes_own_fallback_sentence_satisfies_its_anchors(code):
    assert validate_explain._anchored(validate_explain._WARNING_TEXT[code], code)
    assert validate_explain._anchored(SAY[code], code)


def test_an_answer_that_says_every_warning_is_kept():
    payload = {"warnings": ALL_CODES, "evidence": []}
    text = ". ".join(SAY[code] for code in ALL_CODES) + "."
    assert validate_explain._rejection(text, payload) is None


@pytest.mark.parametrize("dropped", ALL_CODES)
def test_an_answer_that_drops_one_warning_falls_back(dropped):
    payload = {"warnings": ALL_CODES, "evidence": []}
    text = ". ".join(SAY[code] for code in ALL_CODES if code != dropped) + "."
    assert validate_explain._rejection(text, payload) == f"missing_warning:{dropped}"


def test_a_trade_count_sentence_does_not_stand_in_for_trade_concentration():
    """거래_집중 의 닻을 '거래' 한 글자로 잡으면 표본_부족 문장이 대신 채운다."""
    payload = {"warnings": ["표본_부족", "거래_집중"], "evidence": []}
    assert validate_explain._rejection("거래 표본이 적습니다.", payload) == "missing_warning:거래_집중"


def test_an_unknown_warning_code_rejects_the_answer_with_its_real_reason(monkeypatch, caplog):
    """확인할 수 없는 코드는 KeyError 로 새지 않고 이유 코드로 버려진다."""
    _answers(monkeypatch, LEAD)
    with caplog.at_level(logging.WARNING, logger=validate_explain.logger.name):
        assert _source({**PAYLOAD, "warnings": ["새_경고"]}) == "fallback"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "unknown_warning:새_경고" in messages
    assert "KeyError" not in messages
    assert validate_explain._rejection(LEAD, {"warnings": ["새_경고"], "evidence": []})         == "unknown_warning:새_경고"


def test_the_fallback_says_every_warning_the_server_raised():
    out = validate_explain._fallback({"warnings": ALL_CODES})
    for code in ALL_CODES:
        assert validate_explain._WARNING_TEXT[code] in out


def test_the_fallback_would_pass_its_own_checks():
    """폴백 문장이 금지어나 인용을 품거나 닻이 빠지면 폴백이 스스로 규칙을 어긴다."""
    payload = {"warnings": ALL_CODES, "evidence": []}
    assert validate_explain._rejection(validate_explain._fallback(payload), payload) is None


def test_no_warning_sentence_is_flattering_or_labelled_ai():
    for code, sentence in validate_explain._WARNING_TEXT.items():
        assert not any(word in sentence for word in validate_explain.BANNED_WORDS), code
        assert "AI" not in sentence, code


def test_an_unknown_warning_code_is_still_said_not_dropped():
    text = validate_explain._fallback({"warnings": ["새_경고"]})
    assert "새_경고" in text
    assert "없습니다" not in text, "경고가 있는데 없다고 말하면 안 된다"


def test_the_fallback_with_no_warnings_says_so_plainly():
    assert "없습니다" in validate_explain._fallback({"warnings": []})


# --- 폴백 ---------------------------------------------------------------------

def test_the_fallback_never_claims_to_be_ai(monkeypatch):
    def boom(payload):
        raise RuntimeError("provider down")
    monkeypatch.setattr(validate_explain, "_ask_model", boom)
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert "AI" not in out["text"]
    assert "표본" in out["text"], "서버 판정은 폴백에서도 읽혀야 한다"


def test_without_a_key_the_model_is_never_asked(monkeypatch, caplog):
    monkeypatch.setattr(validate_explain, "ai_available", lambda: False)
    calls = _answers(monkeypatch, LEAD)
    with caplog.at_level(logging.INFO, logger=validate_explain.logger.name):
        out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert calls == []
    assert any("no_ai_key" in r.getMessage() for r in caplog.records)


def test_a_too_long_answer_falls_back_instead_of_being_cut(monkeypatch):
    long_text = LEAD + " " + "구간별 성과가 고르지 않습니다. " * 80
    _answers(monkeypatch, long_text)
    out = validate_explain.explain(PAYLOAD)
    assert out["source"] == "fallback"
    assert out["text"] != long_text[:validate_explain.MAX_CHARS]


# --- 지어낸 인용 -------------------------------------------------------------

def test_evidence_is_never_invented(monkeypatch):
    """서버가 근거를 못 찾았으면 AI 가 기사 제목을 지어내도 버린다."""
    _answers(monkeypatch, f"{LEAD} 「바이낸스 상장 공지」 기사가 있었습니다.")
    assert _source({**PAYLOAD, "evidence": []}) == "fallback"


def test_a_title_that_was_never_supplied_falls_back_even_with_evidence(monkeypatch):
    """근거가 있어도, 실린 적 없는 제목을 따옴표로 옮기면 버린다."""
    _answers(monkeypatch, f"{LEAD} 「Binance Lists Two New Tokens Amid Surge」 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


def test_an_embellished_real_title_falls_back(monkeypatch):
    _answers(monkeypatch, f"{LEAD} 「{TITLE} Despite Regulatory Fears」 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_a_supplied_title_in_any_quote_style_is_kept(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"{LEAD} {open_mark}{TITLE}{close_mark} 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "ai"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_a_made_up_title_in_any_quote_style_falls_back(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"{LEAD} {open_mark}바이낸스 상장 공지{close_mark} 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


@pytest.mark.parametrize("open_mark,close_mark", QUOTE_STYLES)
def test_emphasis_quotes_are_not_allowed_when_no_evidence_was_supplied(monkeypatch, open_mark, close_mark):
    _answers(monkeypatch, f"{LEAD} 거래가 {open_mark}14 회{close_mark}뿐입니다.")
    assert _source() == "fallback"


@pytest.mark.parametrize("stray", ["「", "』", "〈", "‘", "'", "“"])
def test_an_unpaired_quote_mark_cannot_hide_a_made_up_title(monkeypatch, stray):
    _answers(monkeypatch, f"{LEAD} {stray}바이낸스 상장 공지 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


def test_title_matching_ignores_case_and_spacing(monkeypatch):
    _answers(monkeypatch, f"{LEAD} 「binance   LISTS new\ttoken」 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "ai"


def test_one_bad_span_among_good_ones_falls_back(monkeypatch):
    _answers(monkeypatch, f"{LEAD} 「{TITLE}」 와 「없던 기사」 가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


def test_an_apostrophe_inside_a_title_is_not_a_quotation(monkeypatch):
    other = [{"date": "2024-03-11", "headlines": {"items": [{"title": "Binance's Listing"}]}}]
    _answers(monkeypatch, f"{LEAD} 「Binance's Listing」 기사가 있었습니다.")
    assert _source({**PAYLOAD, "evidence": other}) == "ai"


def test_plain_prose_without_quotes_is_kept_even_with_no_evidence(monkeypatch):
    _answers(monkeypatch, LEAD)
    assert _source() == "ai"


def test_a_bare_news_mention_without_evidence_is_kept_but_logged(monkeypatch, caplog):
    """'뉴스 보존 범위 밖이라 근거를 찾지 못했습니다' 는 옳은 답이라 낱말로 막지 않는다."""
    _answers(monkeypatch, f"{LEAD} 그 날짜는 뉴스 보존 범위 밖이라 근거를 찾지 못했습니다.")
    with caplog.at_level(logging.INFO, logger=validate_explain.logger.name):
        assert _source() == "ai"
    assert any("no evidence" in r.getMessage() for r in caplog.records)


def test_the_prompt_reserves_quote_marks_for_supplied_titles():
    assert "따옴표는 입력으로 받은 기사 제목에만 쓴다" in validate_explain._SYSTEM


# --- 기록 · 입력 · 키 ------------------------------------------------------------

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


def test_only_allowed_fields_reach_the_model_and_the_cache_key(monkeypatch, runtime):
    """요청 번호 · 시각 · 사용자가 쓴 이름 같은 것이 프롬프트에 들어가면 안 되고, 키를 흔들어도 안 된다."""
    calls = _answers(monkeypatch, LEAD)
    extras = {"request_id": "r-1", "created_at": "2026-10-04T00:00:00Z", "name": "내 전략 이름"}
    validate_explain.explain(PAYLOAD)
    validate_explain.explain({**PAYLOAD, **extras, "top_trade_share_pct": None})
    for seen in calls:
        assert not set(extras) & set(seen)
    assert "top_trade_share_pct" in calls[1]
    # 허용 필드(top_trade_share_pct 포함)만 키에 영향을 준다 — 추가 키만 다른 두 번은 같은 키다.
    validate_explain.explain({**PAYLOAD, "request_id": "r-2", "name": "다른 이름"})
    validate_explain.explain(PAYLOAD)
    assert runtime.keys[2] == runtime.keys[3]


def test_the_cache_key_ignores_float_noise_and_link_fields(monkeypatch, runtime):
    """같은 판정인데 부동소수 꼬리나 기사 링크·시각이 달라 캐시가 안 맞으면 매번 돈이 든다."""
    _answers(monkeypatch, LEAD)
    noisy = {**WITH_EVIDENCE, "final_return_pct": 142.00000000001,
             "evidence": [{**EVIDENCE[0], "headlines": {"items": [
                 {"title": TITLE, "source": "wire", "url": "https://example.test/other",
                  "published_ms": 999}], "found": True, "reason": ""}}]}
    validate_explain.explain(WITH_EVIDENCE)
    validate_explain.explain(noisy)
    assert runtime.keys[0] == runtime.keys[1]


def test_the_cache_key_changes_when_anything_the_model_sees_changes(monkeypatch, runtime):
    _answers(monkeypatch, LEAD)
    validate_explain.explain(PAYLOAD)
    validate_explain.explain({**PAYLOAD, "total_trades": 15})
    validate_explain.explain({**PAYLOAD, "warnings": ["표본_부족"]})
    validate_explain.explain(WITH_EVIDENCE)
    assert len(set(runtime.keys)) == 4


def test_the_cache_key_changes_when_the_instructions_change(monkeypatch, runtime):
    """프롬프트 버전을 올리지 않고 문구만 고쳐도 옛 답이 나가면 안 된다."""
    _answers(monkeypatch, LEAD)
    validate_explain.explain(PAYLOAD)
    monkeypatch.setattr(validate_explain, "_SYSTEM", validate_explain._SYSTEM + " 다른 문구.")
    validate_explain.explain(PAYLOAD)
    assert runtime.keys[0] != runtime.keys[1]


def test_the_model_sees_the_same_projection_the_key_is_built_from(monkeypatch, runtime):
    calls = _answers(monkeypatch, LEAD)
    validate_explain.explain(WITH_EVIDENCE)
    seen = calls[0]
    assert ai_cache_key("validate-explain", validate_explain._PROMPT_VERSION,
                        validate_explain.default_model(),
                        {"payload": seen, "system": validate_explain._SYSTEM}) == runtime.keys[0]
    assert seen["evidence"][0]["headlines"]["items"] == [{"title": TITLE, "source": "wire"}]


# --- 진짜 런타임으로 본 캐시 ------------------------------------------------------

def test_the_same_input_does_not_call_the_model_again(monkeypatch, real_runtime):
    calls = _answers(monkeypatch, LEAD)
    assert _source() == "ai"
    assert _source() == "ai"
    assert len(calls) == 1


def test_a_different_input_asks_the_model_again(monkeypatch, real_runtime):
    calls = _answers(monkeypatch, LEAD)
    _source()
    _source({**PAYLOAD, "total_trades": 15})
    assert len(calls) == 2


def test_a_rejected_answer_is_not_cached_so_the_next_call_asks_again(monkeypatch, real_runtime):
    """다시 검증하면 다시 묻는다. 한 번 버려진 답이 캐시 시간 내내 템플릿으로 남지 않는다."""
    calls = _answers(monkeypatch, "수익률 142% 로 앞섰습니다.", LEAD)
    assert _source() == "fallback"
    assert _source() == "ai"
    assert len(calls) == 2
    assert _source() == "ai"
    assert len(calls) == 2, "받아들여진 답은 캐시에서 나간다"


def test_a_provider_failure_is_not_cached_either(monkeypatch, real_runtime):
    state = {"n": 0}

    def flaky(payload):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("provider down")
        return LEAD

    monkeypatch.setattr(validate_explain, "_ask_model", flaky)
    assert _source() == "fallback"
    assert _source() == "ai"


def test_the_cost_screen_lists_this_feature_without_a_daily_cap():
    rows = {code: (label, limit) for code, label, limit in api_usage.PURPOSES}
    assert rows["validate_explain"] == ("검증 결과 해설", None)


# --- 2차 보강 ---------------------------------------------------------------------

@pytest.mark.parametrize("text,code", [
    ("수익의 62% 가 소수의 거래에서 나왔습니다.", "거래_집중"),
    ("수익의 80% 이상이 한 구간에 몰렸습니다.", "한_구간_집중"),
])
def test_a_share_of_profit_sentence_is_not_a_profit_lead(text, code):
    """수익의 N% 는 몫을 말하는 경고 문장이다. 조사 '의' 를 수익 숫자로 읽으면 안 된다."""
    assert validate_explain._rejection(text, {"warnings": [code], "evidence": []}) is None


def test_the_fallback_sentence_for_concentration_is_not_a_profit_lead():
    payload = {"warnings": ["한_구간_집중"], "final_return_pct": 142.0, "evidence": []}
    assert validate_explain._rejection(validate_explain._fallback(payload), payload) is None


RETURNS = {"final_return_pct": 142.0, "buy_hold_return_pct": 38.0, "evidence": []}


@pytest.mark.parametrize("text,warnings", [
    ("검증 기간 동안 성과는 142% 로 홀딩의 38% 보다 높았습니다. 다만 한 구간에 쏠렸습니다.",
     ["한_구간_집중"]),
    ("최근 1년 성과는 142% 였고 홀딩은 38% 였습니다. 표본이 적고 한 구간에 쏠렸습니다.",
     ["한_구간_집중", "표본_부족"]),
])
def test_a_loose_time_word_does_not_make_a_profit_lead_a_warning(text, warnings):
    """기간 · 최근 은 낱말이 있다고 경고를 말한 것이 아니다 — 쏠림 말이 없으니 첫 문장이 경고가 아니다."""
    payload = {**RETURNS, "warnings": warnings}
    assert validate_explain._rejection(text, payload) == "warning_not_first"


def test_a_return_figure_before_the_warning_is_a_profit_lead():
    """위치 규칙 — 낱말 목록에 없는 표현이라도 수익 숫자가 경고보다 앞서면 수익부터 말한 것이다."""
    payload = {**RETURNS, "warnings": ["표본_부족", "한_구간_집중"]}
    text = "142% 의 성과였지만 표본이 적습니다. 한 구간에 쏠렸습니다."
    assert validate_explain._rejection(text, payload) == "profit_lead"


def test_a_return_figure_after_the_warning_is_fine():
    payload = {**RETURNS, "warnings": ["표본_부족", "한_구간_집중"]}
    text = "표본이 적어서 142% 라는 성과는 믿기 어렵습니다. 한 구간에 쏠렸습니다."
    assert validate_explain._rejection(text, payload) is None


def test_a_longer_number_is_not_mistaken_for_the_return_figure():
    payload = {**RETURNS, "warnings": ["표본_부족"]}
    text = "1142% 와 38.5% 는 숫자일 뿐이고 표본이 적습니다."
    assert validate_explain._rejection(text, payload) is None


def test_the_other_warning_overlap_no_longer_fills_in_for_a_missing_one():
    """구간 · 집중 을 두 코드가 함께 쓰던 때의 오통과. 후반부 문장은 한_구간을, 한_구간 문장은 거래_집중을 못 채운다."""
    tail = validate_explain._rejection("마지막 구간 성과가 음수입니다.",
                                       {"warnings": ["한_구간_집중", "후반부_음수"], "evidence": []})
    assert tail == "missing_warning:한_구간_집중"
    spread = validate_explain._rejection("수익이 한 시기에 집중됐습니다.",
                                         {"warnings": ["거래_집중", "한_구간_집중"], "evidence": []})
    assert spread == "missing_warning:거래_집중"
    assert not validate_explain._anchored("마지막 구간 성과가 음수입니다", "한_구간_집중")
    assert not validate_explain._anchored("수익이 한 시기에 집중됐습니다", "거래_집중")


def test_the_spread_warning_needs_both_a_span_word_and_a_lean_word():
    assert not validate_explain._anchored("한 구간입니다", "한_구간_집중")
    assert not validate_explain._anchored("수익이 쏠렸습니다", "한_구간_집중")
    assert validate_explain._anchored("한 달에 편중됐습니다", "한_구간_집중")


@pytest.mark.parametrize("code,sentence", [
    ("표본_부족", "거래가 14번뿐입니다."),
    ("표본_부족", "거래 수가 14건에 그쳤습니다."),
    ("거래_집중", "일부 거래가 수익 대부분을 만들었습니다."),
    ("거래_집중", "특정 거래 한두 건이 수익을 만들었습니다."),
    ("한_구간_집중", "수익이 일부 시점에 편중되어 있습니다."),
    ("후반부_음수", "뒷부분 구간에서는 손실이 났습니다."),
    ("후반부_음수", "끝으로 갈수록 마이너스입니다."),
])
def test_natural_phrasings_of_each_warning_are_kept(code, sentence):
    assert validate_explain._rejection(sentence, {"warnings": [code], "evidence": []}) is None


def test_a_bare_word_for_times_is_not_a_sample_size_anchor():
    assert not validate_explain._anchored("한 번에 정리하면 이렇습니다", "표본_부족")


def test_an_intro_sentence_that_announces_the_warnings_counts_as_first():
    text = "이번 검증에서 경고가 두 가지 나왔습니다. 표본이 적고 수익이 한 구간에 쏠렸습니다."
    assert validate_explain._rejection(text, PAYLOAD) is None


def test_an_intro_sentence_does_not_excuse_a_figure_ahead_of_it():
    payload = {**RETURNS, "warnings": ["표본_부족"]}
    text = "142% 를 냈지만 경고가 있습니다. 표본이 적습니다."
    assert validate_explain._rejection(text, payload) == "profit_lead"


@pytest.mark.parametrize("sentence", [
    "미래 수익을 보장할 수 없습니다.",
    "앞으로의 성과가 보장되지는 않습니다.",
    "이 구성이 안전하지는 않습니다.",
    "이 구성은 안전하지 못합니다.",
    "결과가 확실치 않습니다.",
    "결과가 확실하지않습니다.",
    "결과가 확실하지 않습니다.",
])
def test_more_cautious_negated_forms_are_kept(sentence):
    assert validate_explain._banned_hit(sentence) is None


@pytest.mark.parametrize("sentence", [
    "이 결과는 확실합니다.",
    "성과를 보장합니다.",
    "이 구성은 안전합니다.",
    "지불보장 입니다.",
    "이 전략은 비추천 입니다.",
    "이 전략은 유망하지 않습니다.",
    "무조건하지 않습니다.",
])
def test_the_extra_tails_do_not_open_a_door_for_the_other_banned_words(sentence):
    assert validate_explain._banned_hit(sentence) is not None


def test_an_abbreviated_year_is_not_an_unpaired_quote(monkeypatch):
    _answers(monkeypatch, f"{LEAD} '24년 대비 거래가 줄었습니다.")
    assert _source() == "ai"


def test_an_abbreviated_year_does_not_swallow_a_real_quote(monkeypatch):
    _answers(monkeypatch, f"{LEAD} '24년 대비 '바이낸스 상장 공지' 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "fallback"


@pytest.mark.parametrize("fragment", ["traders' 반응이 있었습니다.", "5' 간격입니다.",
                                      "ETF' 상장 기사입니다.", "'제목이 이어집니다."])
def test_no_other_bare_apostrophe_is_exempt(monkeypatch, fragment):
    _answers(monkeypatch, f"{LEAD} {fragment}")
    assert _source() == "fallback"


def test_the_share_figures_reach_the_model(monkeypatch):
    calls = _answers(monkeypatch, LEAD)
    validate_explain.explain({**PAYLOAD, "top_month_share_pct": 71.234, "top_trade_share_pct": 62.0})
    assert calls[0]["top_month_share_pct"] == 71.23
    assert calls[0]["top_trade_share_pct"] == 62.0


def test_the_call_is_made_with_no_retry(monkeypatch, runtime):
    """재시도가 켜진 채 부르면 실패한 공급자 호출이 두 번 청구된다. 기본 런타임 설정과 무관하게 0 이다."""
    _answers(monkeypatch, LEAD)
    validate_explain.explain(PAYLOAD)
    assert runtime.retries == [0]


def test_a_year_apostrophe_next_to_a_real_supplied_quote_keeps_the_real_one(monkeypatch):
    _answers(monkeypatch, f"{LEAD} '24년 3월 '{TITLE}' 기사가 있었습니다.")
    assert _source(WITH_EVIDENCE) == "ai"


def test_the_bare_negative_tail_is_matched_only_right_after_the_word():
    assert validate_explain._NEGATED_TAIL.match("는 않습니다")
    assert validate_explain._banned_hit("확실하다고 합니다") is not None


def test_the_plans_own_natural_phrasing_of_a_spread_warning_is_kept(monkeypatch):
    """'수익의 대부분이 첫 구간에서 나왔습니다' 는 한 구간 쏠림을 말한다."""
    _answers(monkeypatch,
             "거래가 14 회뿐이라 통계로 쓰기 어렵습니다. 수익의 대부분이 첫 구간에서 나왔습니다.")
    assert _source() == "ai"
    for text in ("수익의 대다수가 한 구간에서 나왔습니다.", "대부분이 특정 시기에서 나왔습니다."):
        assert validate_explain._rejection(text, {"warnings": ["한_구간_집중"], "evidence": []}) is None


@pytest.mark.parametrize("text", [
    "뒤쪽 구간에서 대부분 손실이 났습니다.",
    "대부분 손실이 뒤쪽 구간에서 났습니다.",
    "대부분이 뒤쪽 구간에서 마이너스입니다.",
    "최근 대부분의 구간에서 손실이 났습니다.",
    "대부분 구간에서 마이너스입니다.",
    "대부분의 기간 동안 손실이 났습니다.",
    "대부분의 시기에 횡보했고 마지막에는 음수입니다.",
    "대부분의 구간이 음수입니다.",
    "뒤쪽 대부분의 구간은 마이너스입니다.",
])
def test_a_late_loss_sentence_with_a_majority_word_does_not_fill_in_for_a_spread_warning(text):
    """대부분 이 후반부_음수 문장에 섞여도 한_구간_집중을 대신 채우면 안 된다."""
    payload = {"warnings": ["한_구간_집중", "후반부_음수"], "evidence": []}
    assert not validate_explain._anchored(text, "한_구간_집중")
    assert validate_explain._rejection(text, payload) == "missing_warning:한_구간_집중"


@pytest.mark.parametrize("text", [
    "수익의 대부분이 첫 구간에서 나왔습니다.",
    "수익의 대다수가 한 구간에서 나왔습니다.",
    "대부분이 특정 시기에서 나왔습니다.",
    "대부분이 일부 시기에서 나왔습니다.",
    "대부분이 한 시기에서 나왔습니다.",
    "대부분이 특정 구간에서 나왔습니다.",
    "수익 대부분이 한 달에 몰렸습니다.",
])
def test_the_majority_phrasings_that_do_express_a_spread_are_kept(text):
    assert validate_explain._rejection(text, {"warnings": ["한_구간_집중"], "evidence": []}) is None


@pytest.mark.parametrize("text", [
    "대부분의 수익이 한 구간에서 나왔습니다.",
    "첫 구간에서 수익 대부분이 나왔습니다.",
    "짧은 기간에 수익 대부분이 나왔습니다.",
])
def test_the_known_false_rejections_stay_rejected(text):
    """넓히지 않기로 한 것들 — 폴백으로 가는 쪽이 안전하다."""
    assert validate_explain._rejection(text, {"warnings": ["한_구간_집중"], "evidence": []})         == "missing_warning:한_구간_집중"


def test_a_majority_word_before_a_period_word_is_rejected_with_the_real_reason():
    payload = {"warnings": ["한_구간_집중", "후반부_음수"], "evidence": []}
    assert validate_explain._rejection("최근 대부분의 구간에서 손실이 났습니다.", payload)         == "missing_warning:한_구간_집중"
