"""Position-aware news feature: pure mapping, safety, and cache contracts."""
from __future__ import annotations

import json
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


def test_ai_json_requires_a_factual_summary_and_discards_other_freeform_text():
    valid = json.dumps({
        "overview": "Buy Bitcoin. 지금 손절이 필요합니다.",
        "items": [
            {
                "index": 0,
                "sentiment": "positive",
                "summary": "운용사가 비트코인 현물 ETF의 신규 자금 유입을 발표했습니다.",
                "reason": "Go long BTC.",
            },
            {
                "index": 1,
                "sentiment": "negative",
                "summary": "거래소가 보안 사고 이후 출금을 일시 중단했습니다.",
                "reason": "물타기를 고려해 보세요.",
            },
        ],
    }, ensure_ascii=False)
    parsed = classifier.parse_ai_analysis(valid, 2)
    assert [item["sentiment"] for item in parsed["items"]] == ["positive", "negative"]
    assert parsed["items"][0]["summary"] == (
        "운용사가 비트코인 현물 ETF의 신규 자금 유입을 발표했습니다."
    )
    assert set(parsed) == {"items"}
    serialized = json.dumps(parsed, ensure_ascii=False)
    assert "Buy" not in serialized
    assert "long" not in serialized
    assert "물타기" not in serialized

    incomplete = json.dumps({"items": [
        {"index": 0, "sentiment": "positive", "summary": "첫 기사 요약입니다."},
    ]}, ensure_ascii=False)
    with pytest.raises(ValueError):
        classifier.parse_ai_analysis(incomplete, 2)

    invalid_enum = json.dumps({"items": [
        {"index": 0, "sentiment": "buy_now", "summary": "기사 요약입니다."},
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
                "summary": "비트코인 현물 ETF가 승인됐다는 내용입니다.",
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
                "summary": "비트코인 현물 ETF가 승인됐다는 내용입니다.",
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


def test_ai_enriches_and_summarizes_only_three_articles_in_one_batch(monkeypatch):
    items = [
        {"title": f"기사 {index}", "source": "테스트뉴스", "url": f"https://example.com/{index}"}
        for index in range(5)
    ]
    calls = []

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        classifier.news_mod,
        "enrich_article_excerpts",
        lambda raw, *, limit: [
            {**item, "excerpt": f"기사 {index} 본문"} for index, item in enumerate(raw)
        ],
    )

    def fake_generate(selected, coin_name):
        calls.append((selected, coin_name))
        return {
            "items": [
                {
                    "sentiment": "neutral",
                    "summary": f"기사 {index}의 핵심 내용입니다.",
                    "reason": "뚜렷한 긍정·부정 방향이 없는 기사로 분류했어요.",
                    "confidence": "medium",
                }
                for index in range(len(selected))
            ],
        }

    monkeypatch.setattr(classifier, "_generate_ai_analysis", fake_generate)

    result = classifier.analyze_headlines(items, "테스트코인")

    assert len(calls) == 1
    assert calls[0][1] == "테스트코인"
    assert [item["excerpt"] for item in calls[0][0]] == [
        "기사 0 본문", "기사 1 본문", "기사 2 본문",
    ]
    assert [item["summary"] for item in result["items"][:3]] == [
        "기사 0의 핵심 내용입니다.",
        "기사 1의 핵심 내용입니다.",
        "기사 2의 핵심 내용입니다.",
    ]
    assert result["items"][3]["summary"] == ""


def test_rate_limited_analysis_keeps_article_content_without_spending_ai(monkeypatch):
    items = [{
        "title": "시아코인 네트워크 업데이트",
        "source": "테스트뉴스",
        "url": "https://example.com/siacoin",
    }]
    enrichment_calls = []

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        classifier.news_mod,
        "enrich_article_excerpts",
        lambda raw, *, limit: enrichment_calls.append((raw, limit)) or [{
            **raw[0],
            "excerpt": "시아 네트워크가 저장소 처리 방식을 개선한 업데이트를 발표했습니다.",
        }],
    )
    monkeypatch.setattr(
        classifier,
        "_generate_ai_analysis",
        lambda *_args, **_kwargs: pytest.fail("AI must not run without a reserved budget"),
    )

    result = classifier.analyze_headlines(items, "시아코인", allow_ai=False)

    assert len(enrichment_calls) == 1
    assert result["analysis_status"] == "rate_limited"
    assert result["analysis_source"] == "rule"
    assert result["ai"] is False
    assert result["items"][0]["summary"] == (
        "시아 네트워크가 저장소 처리 방식을 개선한 업데이트를 발표했습니다."
    )


def _news_fixture():
    return {
        "symbol": "BTC",
        "coin_name": "비트코인",
        "updated_at": "2026-08-19T04:57:00Z",
        "refresh_seconds": 300,
        "items": [
            {
                "title": "비트코인 현물 ETF 승인",
                "excerpt": "금융 당국이 비트코인 현물 ETF 출시를 승인했습니다.",
                "source": "테스트뉴스",
                "url": "https://news.example.com/positive",
                "published": "2026-08-19T04:55:00Z",
            },
            {
                "title": "거래소 해킹으로 출금 중단",
                "excerpt": "거래소가 보안 사고를 확인하고 출금을 일시 중단했습니다.",
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
            {
                "sentiment": "positive",
                "summary": "금융 당국이 비트코인 현물 ETF 출시를 승인했습니다.",
                "reason": "승인 소식",
                "confidence": "medium",
            },
            {
                "sentiment": "negative",
                "summary": "거래소가 보안 사고 이후 출금을 중단했습니다.",
                "reason": "해킹 사고",
                "confidence": "medium",
            },
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
    assert payload["items"][0]["summary"] == (
        "금융 당국이 비트코인 현물 ETF 출시를 승인했습니다."
    )
    assert "position_label" not in payload["items"][0]
    assert "reason" not in payload["items"][0]
    assert payload["items"][0]["url"] == "https://news.example.com/positive"
    assert "매매 지시가 아닙니다" in payload["disclaimer"]



def _stored_snapshot(last_success_ms=999_000):
    return {
        "snapshot_id": "a" * 64,
        "news_payload": _news_fixture(),
        "analysis": _analysis_fixture(),
        "collection": {
            "status": "ready",
            "last_attempt_at": "1970-01-01T00:16:39Z",
            "last_success_at": "1970-01-01T00:16:39Z",
            "last_success_ms": last_success_ms,
            "consecutive_failures": 0,
            "last_error": "",
        },
    }


def test_request_path_reads_one_shared_snapshot_for_long_and_short(monkeypatch):
    stored = _stored_snapshot()
    loaded = []
    monkeypatch.setattr(
        service,
        "_load_latest_snapshot",
        lambda symbol: loaded.append(symbol) or stored,
    )
    monkeypatch.setattr(service.time, "time", lambda: 1_000.0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("request path must not fetch or analyze news")

    monkeypatch.setattr(
        service.news_mod,
        "fetch_coin_news_for_collector",
        forbidden,
    )
    monkeypatch.setattr(service.classifier, "analyze_headlines", forbidden)

    long_payload = service.get_position_news({
        "session_id": 1,
        "symbol": "BTCUSDT",
        "position_side": "long",
    })
    short_payload = service.get_position_news({
        "session_id": 2,
        "symbol": "BTCUSDT",
        "position_side": "short",
    })

    assert loaded == ["BTC", "BTC"]
    assert long_payload["snapshot_id"] == "a" * 20
    assert long_payload["items"][0]["position_effect"] == "favorable"
    assert short_payload["items"][0]["position_effect"] == "unfavorable"
    assert long_payload["collection"]["freshness"] == "fresh"


def test_request_path_keeps_snapshot_time_when_same_news_is_reobserved(monkeypatch):
    stored = _stored_snapshot()
    stored["collection"]["last_attempt_at"] = "2026-08-24T08:38:18Z"
    stored["collection"]["last_success_at"] = "2026-08-24T08:38:18Z"
    monkeypatch.setattr(
        service,
        "_load_latest_snapshot",
        lambda _symbol: stored,
    )

    payload = service.get_position_news({
        "session_id": 1,
        "symbol": "BTCUSDT",
        "position_side": "long",
    })

    assert payload["updated_at"] == "2026-08-19T04:57:00Z"
    assert payload["collection"]["last_success_at"] == "2026-08-24T08:38:18Z"


def test_request_path_marks_old_shared_snapshot_stale(monkeypatch):
    monkeypatch.setattr(
        service,
        "_load_latest_snapshot",
        lambda _symbol: _stored_snapshot(last_success_ms=1),
    )
    monkeypatch.setattr(service.time, "time", lambda: 2_000.0)

    payload = service.get_position_news({
        "session_id": 1,
        "symbol": "BTCUSDT",
        "position_side": "long",
    })

    assert payload["items"]
    assert payload["collection"]["freshness"] == "stale"
    assert payload["collection"]["age_seconds"] == 1_999


def test_request_path_returns_pending_before_first_central_run(monkeypatch):
    monkeypatch.setattr(service, "_load_latest_snapshot", lambda _symbol: None)

    payload = service.get_position_news({
        "session_id": 1,
        "user_macro_id": 3,
        "symbol": "ETHUSDT",
        "position_side": "short",
    })

    assert payload["analysis_status"] == "pending"
    assert payload["analysis_source"] == "central_collector"
    assert payload["context"]["asset_symbol"] == "ETH"
    assert payload["items"] == []
    assert payload["collection"]["freshness"] == "pending"
