"""Observed CFG stock headlines and FET converter pages are not coin news."""
import pytest

from app import news


GOOGLE_WRAPPER = "https://news.google.com/rss/articles/example?oc=5"
OBSERVED_NOISE = [
    ("CFG", "HORAN Wealth LLC Trims Stock Holdings in Citizens Financial Group, Inc. $CFG"),
    ("CFG", "Formuepleje A S Trims Stock Position in Citizens Financial Group, Inc. $CFG"),
    ("CFG", "Nykredit A S Takes Position in Citizens Financial Group, Inc. $CFG"),
    ("CFG", "iSAM Funds UK Ltd Takes Position in Citizens Financial Group, Inc. $CFG"),
    ("CFG", "Empowered Funds LLC Acquires Shares of 37,918 Citizens Financial Group, Inc. $CFG"),
    ("FET", "Convert 1 FET (Artificial Superintelligence Alliance) to EUR (EUR)"),
]


@pytest.mark.parametrize("symbol,title", OBSERVED_NOISE)
def test_observed_noise_is_rejected_before_matching_or_translation(symbol, title):
    item = {"title": title, "url": GOOGLE_WRAPPER}
    assert not news._is_news_article_candidate(item)
    assert news._relevant_items([item], asset_symbol=symbol, coin_name=symbol,
                                feed_source="google_news_rss") == []


@pytest.mark.parametrize("symbol,title", OBSERVED_NOISE)
@pytest.mark.parametrize("already_translated", [False, True])
def test_raw_and_translated_cached_items_are_removed_without_translation(
    monkeypatch, symbol, title, already_translated,
):
    monkeypatch.setattr(news, "_ensure_title_translations",
                        lambda _: pytest.fail("Unrelated stock reports/tools must not consume translation calls"))
    item = {"title": title, "url": GOOGLE_WRAPPER}
    if already_translated:
        item.update(title="이미 번역된 주식 보유 기사 또는 환전 도구", original_title=title)
    for payload in ({"symbol": symbol, "items": [item]},
                    {"feature_key": "position_news", "items": [item]}):
        result = news._localize_news_payload(payload)
        assert result["items"] == []
        assert result["translation"] == {"status": "ready", "pending_count": 0}


@pytest.mark.parametrize("title", [
    "Centrifuge expands tokenized credit markets as CFG gains 5%",
    "CFG token holders approve Centrifuge governance proposal",
    "Citizens Financial Group partners with Centrifuge on lending infrastructure",
    "Citizens Financial Group announces cryptocurrency custody service",
    "Citizens Financial Group explores tokenization of real-world assets",
    "Citizens Financial Group plans stablecoin payments",
    "Citizens Financial Group enters blockchain partnership",
    "Citizens Financial Group increases Bitcoin exposure",
    "FET gains 10% as Artificial Superintelligence Alliance announces network upgrade",
    "Investors convert 1 FET (Artificial Superintelligence Alliance) to EUR (EUR) after listing",
    "Convert 1 FET (Artificial Superintelligence Alliance) to EUR (EUR): New rules explained",
    "How to convert 1 FET (Artificial Superintelligence Alliance) to EUR (EUR)",
    "FET to EUR exchange rate rises after token merger announcement",
])
def test_centrifuge_crypto_and_actual_fet_articles_remain_eligible(title):
    assert news._is_news_article_candidate({"title": title, "url": GOOGLE_WRAPPER})


@pytest.mark.parametrize("title", [
    "Convert 0.5 FET (Artificial Superintelligence Alliance) to USD (US Dollar)",
    "Convert 1,000 BTC (Bitcoin) to EUR (Euro)",
    "Convert 25 ETH (Ethereum) to USDT (Tether)",
])
def test_complete_converter_labels_cover_other_amounts_and_assets(title):
    assert not news._is_news_article_candidate({"title": title, "url": GOOGLE_WRAPPER})


def test_bank_crypto_news_is_market_news_but_not_the_cfg_token(monkeypatch):
    from app import news_asset_catalog
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", lambda _: "Centrifuge")
    item = {"title": "Citizens Financial Group $CFG launches Bitcoin custody", "url": GOOGLE_WRAPPER}
    assert news._is_news_article_candidate(item)
    assert not news._matches_asset(item, "CFG", "CFG")


@pytest.mark.parametrize("project", ["Centrifuge", "센트리퓨즈"])
def test_bank_collaboration_explicitly_naming_centrifuge_remains_cfg_news(monkeypatch, project):
    from app import news_asset_catalog
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", lambda _: "Centrifuge")
    item = {"title": f"Citizens Financial Group partners with {project} on CFG token lending",
            "url": GOOGLE_WRAPPER}
    assert news._is_news_article_candidate(item)
    assert news._matches_asset(item, "CFG", "CFG")
