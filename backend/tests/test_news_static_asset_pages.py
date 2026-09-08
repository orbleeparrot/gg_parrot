"""Reject observed quote pages behind Google links while keeping news reports."""
import pytest

from app import news


GOOGLE_WRAPPER = "https://news.google.com/rss/articles/example?oc=5"
OBSERVED_STATIC_PAGES = [
    ("USDai Price (USDAI/USD) Today | Live Price, Market Cap & Chart", "Binance"),
    ("USD.AI Holders And Distribution Chart", "CryptoRank"),
]


@pytest.mark.parametrize("title,source", OBSERVED_STATIC_PAGES)
def test_observed_chip_quote_and_distribution_pages_are_not_news(title, source):
    item = {"title": title, "source": source, "url": GOOGLE_WRAPPER}
    assert not news._is_news_article_candidate(item)
    assert news._relevant_items([item], asset_symbol="CHIP", coin_name="CHIP",
                                feed_source="google_news_rss") == []


@pytest.mark.parametrize("title", [
    "USDai Price (USDAI/USD) Today | Live Price, Market Cap & Charts",
    "USDai Price (USDAI / USD) Today | Live Price, Market Cap and Chart",
    "USD.AI Holders & Distribution Charts",
    "USD.AI Holders and Distribution Charts",
])
def test_static_labels_allow_singular_plural_and_conjunction_variants(title):
    assert not news._is_news_article_candidate({"title": title, "url": GOOGLE_WRAPPER})


@pytest.mark.parametrize("title", [
    "USD.AI Price Prediction 2026, 2027, 2030 & Beyond: Yearly Forecast",
    "USD.AI Price Prediction August 2026: CHIP Token",
    "Bullish extends $100M stablecoin debt facility to USD.AI for GPU-backed credit",
    "Bullish Expands Into AI Infrastructure Lending With USD.AI Deal",
    "AI News: Tradoor Sinks, TAO Stabilizes, USD.AI Surges",
    "Bullish Backs USD.AI With $100M GPU Financing",
    "USD.AI holders increase 20% after new financing announcement",
    "USD.AI holders and distribution charts show whale outflows",
    "USD.AI updates holders and distribution charts after new token unlock",
    "USD.AI Price (CHIP/USD) Today Rises 10% as Holders Accumulate",
    "유에스디에이아이, 고래 보유량 증가와 함께 10% 상승",
])
def test_forecasts_holder_changes_and_position_relevant_news_are_preserved(title):
    assert news._is_news_article_candidate({"title": title, "url": GOOGLE_WRAPPER})


@pytest.mark.parametrize("title,source", OBSERVED_STATIC_PAGES)
def test_existing_translated_snapshot_is_filtered_by_original_title_without_ai(monkeypatch, title, source):
    monkeypatch.setattr(news, "_ensure_title_translations",
                        lambda _: pytest.fail("Static asset pages must not request translation"))
    item = {"title": "이미 번역된 시세 또는 보유 차트 페이지", "original_title": title,
            "source": source, "url": GOOGLE_WRAPPER}
    result = news._localize_news_payload({"symbol": "CHIP", "items": [item]})
    assert result["items"] == []
    assert result["translation"]["pending_count"] == 0
