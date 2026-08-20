"""Position-aware news feature: pure mapping, safety, and cache contracts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.agent_features.position_news import classifier, service


@pytest.mark.parametrize(
    ("side", "sentiment", "expected"),
    [
        ("long", "positive", "favorable"),
        ("long", "negative", "unfavorable"),
        ("short", "positive", "unfavorable"),
        ("short", "negative", "favorable"),
        ("long", "neutral", "neutral"),
        ("short", "neutral", "neutral"),
        ("long", "unclear", "unclear"),
        ("short", "unclear", "unclear"),
    ],
)
def test_position_effect_truth_table(side, sentiment, expected):
    assert classifier.position_effect(sentiment, side) == expected


def test_rule_classifier_only_commits_on_clear_headline_terms():
    assert classifier.classify_headline("비트코인 현물 ETF 승인")['sentiment'] == "positive"
    assert classifier.classify_headline("거래소 해킹으로 출금 중단")['sentiment'] == "negative"
    assert classifier.classify_headline("비트코인 현물 ETF 승인 거부")['sentiment'] == "negative"
    assert classifier.classify_headline("비트코인 현물 ETF 승인을 거부")['sentiment'] == "negative"
    assert classifier.classify_headline("비트코인 현물 ETF 승인 여부 검토")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 현물 ETF 승인 가능성 낮아")['sentiment'] == "unclear"
    assert classifier.classify_headline("Crypto bank expands bitcoin services")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 숏 포지션 대규모 청산")['sentiment'] == "positive"
    assert classifier.classify_headline("비트코인 롱 포지션 대규모 청산")['sentiment'] == "negative"
    assert classifier.classify_headline("리플 SEC 소송 승소")['sentiment'] == "positive"
    assert classifier.classify_headline("거래소 비트코인 자금 유출")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 현물 ETF 순유출")['sentiment'] == "negative"
    assert classifier.classify_headline("Bitcoin not approved by regulator")['sentiment'] == "negative"
    assert classifier.classify_headline("비트코인 해킹 우려 해소")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 상승분 모두 반납")['sentiment'] == "unclear"
    assert classifier.classify_headline("ETF 승인 거부에도 비트코인 급등")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 숏 포지션 청산 가능성")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 추가 상승 전망")['sentiment'] == "unclear"
    assert classifier.classify_headline("거래소 해킹 의혹 제기")['sentiment'] == "unclear"
    assert classifier.classify_headline("거래소 해킹설 부인")['sentiment'] == "unclear"
    assert classifier.classify_headline("암호화폐 기업 파산 우려")['sentiment'] == "unclear"
    assert classifier.classify_headline("리플 기소 가능성 제기")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 폭락 가능성")['sentiment'] == "unclear"
    assert classifier.classify_headline("ETF 승인 임박 관측")['sentiment'] == "unclear"
    assert classifier.classify_headline("ETF 승인설 부인")['sentiment'] == "unclear"
    assert classifier.classify_headline("소송 제기 가능성")['sentiment'] == "unclear"
    assert classifier.classify_headline("상장 폐지 우려")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 ETF 승인에도 가격 급락")['sentiment'] == "unclear"
    assert classifier.classify_headline("비트코인 정책 토론회 개최")['sentiment'] == "unclear"


def test_ai_json_requires_every_enum_and_discards_all_freeform_text():
    valid = json.dumps({
        "overview": "Buy Bitcoin. 지금 손절이 필요합니다.",
        "items": [
            {"index": 0, "sentiment": "positive", "reason": "Go long BTC."},
            {"index": 1, "sentiment": "negative", "reason": "물타기를 고려해 보세요."},
        ],
    }, ensure_ascii=False)
    parsed = classifier.parse_ai_analysis(valid, 2)
    assert [item["sentiment"] for item in parsed["items"]] == ["positive", "negative"]
    assert set(parsed) == {"items"}
    serialized = json.dumps(parsed, ensure_ascii=False)
    assert "Buy" not in serialized
    assert "long" not in serialized
    assert "물타기" not in serialized

    incomplete = json.dumps({"items": [
        {"index": 0, "sentiment": "positive"},
    ]}, ensure_ascii=False)
    with pytest.raises(ValueError):
        classifier.parse_ai_analysis(incomplete, 2)

    invalid_enum = json.dumps({"items": [
        {"index": 0, "sentiment": "buy_now"},
    ]}, ensure_ascii=False)
    with pytest.raises(ValueError):
        classifier.parse_ai_analysis(invalid_enum, 1)


def test_missing_or_failed_ai_uses_safe_rule_fallback(monkeypatch):
    items = [{"title": "비트코인 현물 ETF 승인", "source": "테스트뉴스"}]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = classifier.analyze_headlines(items, "비트코인")
    assert without_key["analysis_source"] == "rule"
    assert without_key["analysis_status"] == "ready"
    assert without_key["items"][0]["sentiment"] == "positive"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        classifier,
        "_generate_ai_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    failed = classifier.analyze_headlines(items, "비트코인")
    assert failed["analysis_source"] == "rule"
    assert failed["analysis_status"] == "degraded"
    assert failed["ai"] is False

    monkeypatch.setattr(
        classifier,
        "_generate_ai_analysis",
        lambda *_args, **_kwargs: {
            "items": [{
                "sentiment": "positive",
                "reason": "헤드라인을 자산에 긍정적인 맥락으로 분류했어요.",
                "confidence": "medium",
            }],
        },
    )
    generated = classifier.analyze_headlines(items, "비트코인")
    assert generated["analysis_source"] == "ai"
    assert "비트코인 현물 ETF 승인" in generated["overview"]

    monkeypatch.setattr(
        classifier,
        "_generate_ai_analysis",
        lambda *_args, **_kwargs: {
            "items": [{
                "sentiment": "negative",
                "reason": "헤드라인을 자산에 부정적인 맥락으로 분류했어요.",
                "confidence": "medium",
            }],
        },
    )
    conflict = classifier.analyze_headlines(items, "비트코인")
    assert conflict["items"][0]["sentiment"] == "unclear"

    called = []
    monkeypatch.setattr(classifier, "_generate_ai_analysis", lambda *_args, **_kwargs: called.append(True))
    limited = classifier.analyze_headlines(items, "비트코인", allow_ai=False)
    assert limited["analysis_status"] == "rate_limited"
    assert limited["analysis_source"] == "rule"
    assert called == []


def _news_fixture():
    return {
        "symbol": "BTC",
        "coin_name": "비트코인",
        "updated_at": "2026-08-19T04:57:00Z",
        "refresh_seconds": 300,
        "items": [
            {
                "title": "비트코인 현물 ETF 승인",
                "source": "테스트뉴스",
                "url": "https://news.example.com/positive",
                "published": "2026-08-19T04:55:00Z",
            },
            {
                "title": "거래소 해킹으로 출금 중단",
                "source": "테스트뉴스",
                "url": "https://news.example.com/negative",
                "published": "2026-08-19T04:50:00Z",
            },
        ],
    }


def _analysis_fixture():
    return {
        "overview": "승인과 보안 사고 관련 헤드라인이 함께 확인됐어요.",
        "items": [
            {"sentiment": "positive", "reason": "승인 소식", "confidence": "medium"},
            {"sentiment": "negative", "reason": "해킹 사고", "confidence": "medium"},
        ],
        "analysis_status": "ready",
        "analysis_source": "rule",
        "ai": False,
    }


def test_payload_uses_registered_macro_direction_and_preserves_sources():
    session = {
        "session_id": 12,
        "user_macro_id": 7,
        "symbol": "BTCUSDT",
        "position_side": "short",
        "in_position": True,
    }
    payload = service.build_position_news(session, _news_fixture(), _analysis_fixture())

    assert payload["feature_key"] == "position_news"
    assert len(payload["snapshot_id"]) == 20
    assert payload["context"]["position_basis"] == "macro_configuration"
    assert payload["context"]["market_symbol"] == "BTCUSDT"
    assert payload["context"]["asset_symbol"] == "BTC"
    assert [item["position_effect"] for item in payload["items"]] == ["unfavorable", "favorable"]
    assert payload["items"][0]["position_label"] == "숏 포지션에 불리한 뉴스"
    assert payload["items"][1]["position_label"] == "숏 포지션에 유리한 뉴스"
    assert payload["items"][0]["url"] == "https://news.example.com/positive"
    assert "매매 지시가 아닙니다" in payload["disclaimer"]


def test_headline_analysis_cache_is_shared_across_long_and_short(monkeypatch):
    service._analysis_cache.clear()
    service._analysis_locks.clear()
    calls = []
    news = _news_fixture()

    monkeypatch.setattr(service.news_mod, "get_coin_news", lambda _symbol: news)

    def fake_analyze(items, coin_name, **_kwargs):
        calls.append((items, coin_name))
        return _analysis_fixture()

    monkeypatch.setattr(service.classifier, "analyze_headlines", fake_analyze)
    long_payload = service.get_position_news({
        "session_id": 1, "symbol": "BTCUSDT", "position_side": "long",
    })
    short_payload = service.get_position_news({
        "session_id": 2, "symbol": "BTCUSDT", "position_side": "short",
    })

    assert len(calls) == 1
    assert long_payload["items"][0]["position_effect"] == "favorable"
    assert short_payload["items"][0]["position_effect"] == "unfavorable"


def test_ai_cost_guard_limits_unique_uncached_analyses(monkeypatch):
    service._user_ai_usage.clear()
    service._global_ai_usage.clear()


def test_identical_cold_misses_share_one_analysis(monkeypatch):
    service._analysis_cache.clear()
    service._analysis_locks.clear()
    entered = Event()
    release = Event()
    calls = []

    def fake_analyze(_items, _coin_name, **_kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(2)
        return _analysis_fixture()

    monkeypatch.setattr(service.classifier, "analyze_headlines", fake_analyze)
    items = _news_fixture()["items"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service._analysis_for, "BTC", "비트코인", items)
        assert entered.wait(2)
        second = pool.submit(service._analysis_for, "BTC", "비트코인", items)
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2)

    assert len(calls) == 1
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(service, "_AI_USER_MAX_ANALYSES", 1)
    monkeypatch.setattr(service, "_AI_GLOBAL_MAX_ANALYSES", 2)
    monkeypatch.setattr(service.time, "time", lambda: 10_000.0)

    assert service._consume_ai_budget(11) is True
    assert service._consume_ai_budget(11) is False
    assert service._consume_ai_budget(22) is True
    assert service._consume_ai_budget(33) is False
    service._user_ai_usage.clear()
    service._global_ai_usage.clear()
