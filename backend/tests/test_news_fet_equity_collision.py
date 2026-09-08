"""The observed Forum Energy equity reports must not become FET token news."""
import pytest

from app import news


GOOGLE_WRAPPER = "https://news.google.com/rss/articles/example?oc=5"
OBSERVED_EQUITY_TITLES = [
    "Deutsche Bank AG Invests $629,000 in Forum Energy Technologies, Inc. $FET",
    "Empowered Funds LLC Makes New $4.23 Million Investment in Forum Energy Technologies, Inc. $FET",
    "BlackRock Inc. Acquires Shares of 950,933 Forum Energy Technologies, Inc. $FET",
    "Globeflex Capital L P Makes New Investment in Forum Energy Technologies, Inc. $FET",
]


def test_observed_forum_energy_stock_reports_are_not_fet_news():
    for title in OBSERVED_EQUITY_TITLES:
        item = {"title": title, "source": "MarketBeat", "url": GOOGLE_WRAPPER}
        assert not news._is_news_article_candidate(item)
        assert not news._matches_asset(item, "FET", "FET")


def test_existing_raw_and_translated_stock_snapshots_do_not_retry_translation(monkeypatch):
    monkeypatch.setattr(news, "_ensure_title_translations",
                        lambda _: pytest.fail("Unrelated equity reports must not request translation"))
    for title in OBSERVED_EQUITY_TITLES:
        for item in ({"title": title}, {"title": "포럼 에너지 테크놀로지스 주식 투자", "original_title": title}):
            item["url"] = GOOGLE_WRAPPER
            for payload in ({"symbol": "FET", "items": [item]},
                            {"feature_key": "position_news", "items": [item]}):
                result = news._localize_news_payload(payload)
                assert result["items"] == []
                assert result["translation"] == {"status": "ready", "pending_count": 0}


def test_company_crypto_activity_is_market_news_but_not_the_fet_token():
    item = {"title": "Forum Energy Technologies $FET announces Bitcoin treasury investment",
            "url": GOOGLE_WRAPPER}
    assert news._is_news_article_candidate(item)
    assert not news._matches_asset(item, "FET", "FET")


def test_explicit_fet_project_collaborations_remain_relevant(monkeypatch):
    from app import news_asset_catalog
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", lambda _: "Artificial Superintelligence Alliance")
    for project in ("Artificial Superintelligence Alliance", "Fetch.ai", "페치"):
        item = {"title": f"Forum Energy Technologies partners with {project} on FET token infrastructure",
                "url": GOOGLE_WRAPPER}
        assert news._is_news_article_candidate(item)
        assert news._matches_asset(item, "FET", "FET")


def test_actual_fet_token_news_remains_eligible(monkeypatch):
    from app import news_asset_catalog
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", lambda _: "Artificial Superintelligence Alliance")
    for title in (
        "Artificial Superintelligence Alliance announces blockchain upgrade",
        "Fetch.ai FET token gains 10% after new partnership",
        "페치 FET 토큰, 신규 인공지능 협업 공개",
    ):
        item = {"title": title, "url": GOOGLE_WRAPPER}
        assert news._is_news_article_candidate(item)
        assert news._matches_asset(item, "FET", "FET")
