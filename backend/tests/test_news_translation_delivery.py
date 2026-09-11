"""News delivery must translate every candidate without a daily quota."""
import json
from types import SimpleNamespace

import pytest

from app import news
from app.agent_features.position_news import repository


@pytest.fixture(autouse=True)
def isolated_translation(monkeypatch):
    monkeypatch.setattr(news, "_title_translation_cache", {})
    monkeypatch.setattr(news, "_title_translation_retry_at", {})
    monkeypatch.setattr(news, "_coin_cache", {})
    monkeypatch.setattr(news, "_cache", {})
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _: None)


@pytest.mark.parametrize("legacy_limit", ["0", "1", "20"])
def test_translation_provider_has_no_daily_quota(monkeypatch, legacy_limit):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("NEWS_TRANSLATION_MAX_CALLS_PER_DAY", legacy_limit)
    monkeypatch.setattr(repository, "reserve_ai_budget", lambda **_: pytest.fail("translation has no daily budget"))
    calls = []

    def create(**kwargs):
        articles = json.loads(kwargs["messages"][0]["content"])
        calls.append(articles)
        result = [{"id": article["id"], "title_ko": f"아비트럼 토큰 업데이트 {article['title'].split()[-1]}"}
                  for article in articles]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"items": result}))])

    monkeypatch.setattr(news, "get_ai_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(news, "get_ai_runtime", lambda: SimpleNamespace(call=lambda key, load, **_: (load(), "loaded")))
    for index in range(22):
        title = f"Arbitrum token update {index}"
        assert news._request_korean_title_translations([title])[title] == f"아비트럼 토큰 업데이트 {index}"
    assert len(calls) == 22


def test_pending_titles_are_not_delivered_as_english(monkeypatch):
    monkeypatch.setattr(news, "_ensure_title_translations", lambda _: None)
    raw = {"items": [{"title": "Arbitrum token rises", "url": "https://example.com/one"},
                     {"title": "비트코인 새 소식", "url": "https://example.com/two"}]}
    result = news._localize_news_payload(raw)
    assert [item["title"] for item in result["items"]] == ["비트코인 새 소식"]
    assert result["translation"] == {"status": "partial", "pending_count": 1, "retry_after_seconds": 30}
    assert len(raw["items"]) == 2


def test_mixed_untranslated_prose_is_pending_too(monkeypatch):
    monkeypatch.setattr(news, "_ensure_title_translations", lambda _: None)
    result = news._localize_news_payload({"items": [{"title": "Bitcoin price rises 비트코인"}]})
    assert result["items"] == []
    assert result["translation"]["pending_count"] == 1


def test_foreign_headline_without_latin_letters_is_translated(monkeypatch):
    source, korean = "比特币价格上涨", "비트코인 가격 상승"
    calls = []
    monkeypatch.setattr(news, "_request_korean_title_translations", lambda titles: calls.append(titles) or {source: korean})
    result = news._localize_news_payload({"items": [{"title": source}]})
    assert calls == [[source]]
    assert result["items"][0]["title"] == korean
    assert result["translation"]["pending_count"] == 0


@pytest.mark.parametrize("market", [False, True])
def test_cached_pending_titles_recover_without_refetching(monkeypatch, market):
    english, korean = "Arbitrum token rises", "아비트럼 토큰 상승"
    source = {"title": english, "url": "https://example.com/article", "source": "CoinDesk"}
    fetch_calls, translation_calls = [], []

    def ensure(titles):
        translation_calls.append(titles)
        if len(translation_calls) == 2:
            news._remember_title_translations({english: korean})

    monkeypatch.setattr(news, "_ensure_title_translations", ensure)
    monkeypatch.setattr(news, "_summarize", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(news, "_load_durable_market_summary", lambda _: None)
    if market:
        monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: fetch_calls.append(True) or [source])
        read = news.get_market_news
    else:
        monkeypatch.setattr(news, "_coin_news_envelope", lambda *_args, **_kwargs: fetch_calls.append(True) or {"items": [source]})
        read = lambda: news.get_coin_news("ARBUSDT")
    first = read()
    assert first["items"] == []
    assert first["translation"]["pending_count"] == 1
    second = read()
    assert [item["title"] for item in second["items"]] == [korean]
    assert second["translation"] == {"status": "ready", "pending_count": 0}
    assert fetch_calls == [True]


def test_a_failed_batch_does_not_discard_later_batches(monkeypatch):
    titles = [f"Arbitrum update {i}" for i in range(11)]
    calls = []
    def translate(batch):
        calls.append(batch)
        if len(calls) == 1:
            raise news.NewsTranslationError("temporary failure")
        return {title: f"아비트럼 업데이트 {title.split()[-1]}" for title in batch}
    monkeypatch.setattr(news, "_request_korean_title_translations", translate)
    items = news._localize_coin_news_items([{"title": title} for title in titles])
    assert calls == [titles[:10], titles[10:]]
    assert [item["title"] for item in items] == ["아비트럼 업데이트 10"]


def test_long_headline_reaches_translator_without_losing_final_facts(monkeypatch):
    title = "Arbitrum " + "network expansion " * 20 + "reaches $50M"
    korean = "아비트럼 네트워크 확장 규모 $50M 도달"
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    def create(**kwargs):
        article = json.loads(kwargs["messages"][0]["content"])[0]
        assert article["title"] == title
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({
            "items": [{"id": article["id"], "title_ko": korean}],
        }))])
    monkeypatch.setattr(news, "get_ai_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(news, "get_ai_runtime", lambda: SimpleNamespace(call=lambda key, load, **_: (load(), "loaded")))
    assert news._request_korean_title_translations([title]) == {title: korean}


def test_preflight_busy_releases_claim_for_prompt_retry(monkeypatch):
    from app.ai_runtime import AiBusyError
    releases = []
    monkeypatch.setattr(news, "_renew_durable_title_translation_claims", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(news, "_release_durable_title_translation_claims", lambda titles, **kwargs: releases.append(kwargs))
    monkeypatch.setattr(news, "_request_korean_title_translations", lambda _, **_kwargs: (_ for _ in ()).throw(AiBusyError("busy")))
    with pytest.raises(news.NewsTranslationBusyError):
        news._translate_title_batch(["Arbitrum token rises"], claim_token="claim")
    assert releases == [{"claim_token": "claim", "retry_immediately": True}]
