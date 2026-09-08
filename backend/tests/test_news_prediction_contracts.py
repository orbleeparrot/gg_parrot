"""Prediction contract listings must not consume news slots or translation calls."""

from datetime import datetime, timezone

import pytest

from app import news


@pytest.mark.parametrize("title", [
    "BTC price on Sep 8, 2026 at 12am EDT Crypto Prediction Market",
    "BTC price range on Sep 7, 2026 at 8pm EDT Crypto Prediction Market",
])
@pytest.mark.parametrize("source,url", [
    ("robinhood.com", "https://news.google.com/rss/articles/opaque"),
    ("Robinhood", "https://robinhood.com/prediction-markets/contract"),
])
def test_prediction_contract_listings_are_removed_before_localization(title, source, url):
    assert not news._is_news_article_candidate({"title": title, "source": source, "url": url})


@pytest.mark.parametrize("title", [
    "Robinhood launches Bitcoin prediction markets",
    "BTC price range widens as traders assess Robinhood's Crypto Prediction Market",
])
def test_editorial_reporting_about_prediction_markets_remains_eligible(title):
    assert news._is_news_article_candidate({
        "title": title,
        "source": "Robinhood",
        "url": "https://news.google.com/rss/articles/opaque",
    })


def test_cached_prediction_listing_does_not_spend_a_translation_call(monkeypatch):
    def unexpected_translation(_titles):
        pytest.fail("Prediction listings must be filtered before requesting translation")

    monkeypatch.setattr(news, "_ensure_title_translations", unexpected_translation)
    payload = news._localize_news_payload({
        "symbol": "BTC",
        "items": [{
            "title": "BTC price on Sep 8, 2026 at 12am EDT Crypto Prediction Market",
            "source": "robinhood.com",
            "url": "https://news.google.com/rss/articles/opaque",
            "published": datetime.now(timezone.utc).isoformat(),
        }],
    })
    assert payload["items"] == []
    assert payload["translation"] == {"status": "ready", "pending_count": 0}
