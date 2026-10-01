"""Square chart timeframes must never become million-sized quantities."""
import json
from types import SimpleNamespace

import pytest

from app import news
from app.ai_runtime import AiCallRuntime


ORIGINAL = "做空警报 | CHIP 15m急速波动"
CORRECT = "공매도 경보 | CHIP 15분봉 급속 변동"
WRONG = "공매도 경보 | CHIP 1,500만 급속 변동"


@pytest.mark.parametrize("original, translated, facts", [
    (ORIGINAL, CORRECT, (("15", "minute"),)),
    ("CHIP 15m breakout", "CHIP 15분 돌파", (("15", "minute"),)),
    ("CHIPUSDT 15m chart", "CHIPUSDT 15분봉 차트", (("15", "minute"),)),
    ("BTC 1h / 4h candles", "BTC 1시간봉 / 4시간봉", (("1", "hour"), ("4", "hour"))),
    ("BTC chart on 15m", "BTC 15분봉 차트", (("15", "minute"),)),
    ("BTC 15-minute candles", "BTC 15분봉", (("15", "minute"),)),
    ("BTC gains over 15m", "BTC 15분 동안 상승", (("15", "minute"),)),
    ("CHIP 15m chart, volume $15m", "CHIP 15분봉 차트, 거래량 1,500만 달러", (("15", "minute"), ("15000000", "number"))),
])
def test_clear_timeframes_preserve_numbers_and_time_units(original, translated, facts):
    assert news._translation_fact_tokens(original)[0] == facts
    assert news._title_translation_is_clean(original, translated)


@pytest.mark.parametrize("translated", [
    WRONG, CORRECT.replace("15분봉", "15"), CORRECT.replace("15분봉", "15시간봉"),
    CORRECT.replace("15분봉", "16분봉"), CORRECT.replace("CHIP", "BTC"),
])
def test_captured_million_error_and_missing_or_changed_timeframe_are_rejected(translated):
    assert not news._title_translation_is_clean(ORIGINAL, translated)


@pytest.mark.parametrize("original, translated", [
    ("Fund raises $15m", "펀드, 1,500만 달러 조달"),
    ("15M funding for CHIP", "CHIP에 1,500만 자금 조달"),
    ("CHIP volume 15m", "CHIP 거래량 1,500만"),
    ("CHIP 15m tokens", "CHIP 토큰 1,500만 개"),
])
def test_monetary_million_notation_retains_its_value(original, translated):
    assert news._translation_fact_tokens(original)[0] == (("15000000", "number"),)
    assert news._title_translation_is_clean(original, translated)
    assert not news._title_translation_is_clean(original, translated.replace("1,500만", "15분"))


def test_ambiguous_lowercase_m_must_keep_the_original_notation():
    original = "Metric reaches 15m"
    assert news._translation_fact_tokens(original)[0] == (("15", "literal:m"),)
    assert news._title_translation_is_clean(original, "지표, 15m 도달")
    assert not news._title_translation_is_clean(original, "지표, 15분 도달")
    assert not news._title_translation_is_clean(original, "지표, 1,500만 도달")


def test_provider_gets_minute_fact_and_million_result_is_recorded_not_repaired(monkeypatch):
    # 15m→1,500만 같은 수치 오역은 더 이상 교정 호출을 부르지 않고 품질 경고로 남는다.
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    requests = []
    replies = [WRONG, CORRECT]
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({
            "items": [{"id": news._title_translation_id(ORIGINAL), "title_ko": replies.pop(0)}],
        }))])
    monkeypatch.setattr(news, "get_ai_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    runtime = AiCallRuntime(max_concurrent=1, acquire_timeout_seconds=0.01, cache_ttl_seconds=900)
    monkeypatch.setattr(news, "get_ai_runtime", lambda: runtime)
    assert news._request_korean_title_translations([ORIGINAL]) == {ORIGINAL: WRONG}
    assert len(requests) == 1
    first = json.loads(requests[0]["messages"][0]["content"])[0]
    assert first["protected_numbers"] == [{"value": "15", "unit": "minute"}]
    assert "15분(봉)이지 1,500만이 아니고" in requests[0]["system"]
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["json_schema"]["schema"]["properties"]["items"]["items"]["required"] == ["id", "title_ko"]
    assert news._title_translation_quality_issue(ORIGINAL, WRONG) == "fact_mismatch"


def test_durable_translation_with_quality_warning_is_served_not_reclaimed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture.invalid/news")
    monkeypatch.setattr(news, "_title_translation_cache", {ORIGINAL: WRONG})
    monkeypatch.setattr(news, "_title_translation_retry_at", {})
    monkeypatch.setattr(news, "_load_durable_title_translations", lambda titles: {ORIGINAL: WRONG})
    claims, batches = [], []
    def claim(titles, *, rejected_titles=()):
        claims.append((titles, rejected_titles))
        return {"claimed": titles, "cached": {}, "claim_token": "fixture-claim"}
    def translate(titles, *, claim_token=""):
        batches.append((titles, claim_token))
        news._remember_title_translations({ORIGINAL: CORRECT})
    monkeypatch.setattr(news, "_claim_durable_title_translations", claim)
    monkeypatch.setattr(news, "_translate_claimed_titles", translate)
    title_id = news._title_translation_id(ORIGINAL)
    news._ensure_title_translations([ORIGINAL])
    # 품질 경고만 있는 번역은 다시 사지 않는다 — 재번역 호출이 비용을 되살리지 않게.
    assert claims == []
    assert batches == []
    assert news._title_translation_cache == {ORIGINAL: WRONG}
    assert news._title_translation_id(ORIGINAL) == title_id
