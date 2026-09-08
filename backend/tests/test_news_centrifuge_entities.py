"""Separate the Centrifuge crypto protocol from observed laboratory equipment."""
import pytest

from app import news


GOOGLE_WRAPPER = "https://news.google.com/rss/articles/example?oc=5"
OBSERVED_EQUIPMENT = [
    "Full-scale comparison of decanter centrifuge and screw press performance for municipal sludge dewatering",
    "Marathon Fusion Demonstrates Lithium and Hydrogen Isotope Enrichment With Plasma Centrifuge",
    "The Centrifuge in the Corner Is a Reproducibility Variable Nobody Logs",
    "Sedimentation Centrifuge Market Insights Highlight Segment Expansion And Market Leadership",
]
OBSERVED_PROJECT_NEWS = [
    "Centrifuge and Janus Henderson bring $687M CLO fund onchain",
    "Centrifuge launches blockchain-based CLO fund w...",
    "Centrifuge reports only 12% of tokenized assets meet DeFi standards",
    "Centrifuge's tokenized assets hit $1.6B as hold...",
    "Centrifuge partners with LI.FI to boost cross-c...",
    "Centrifuge simplifies onchain pool creation for fund managers with a new three-step process.",
]


@pytest.mark.parametrize("title", OBSERVED_EQUIPMENT)
def test_observed_equipment_is_not_a_cfg_project_or_news_candidate(title):
    item = {"title": title, "url": GOOGLE_WRAPPER}
    assert not news._is_news_article_candidate(item)
    assert not news._matches_asset(item, "CFG", "CFG")


@pytest.mark.parametrize("title", OBSERVED_EQUIPMENT)
@pytest.mark.parametrize("already_translated", [False, True])
def test_equipment_in_existing_snapshots_is_removed_before_translation(monkeypatch, title, already_translated):
    monkeypatch.setattr(news, "_ensure_title_translations",
                        lambda _: pytest.fail("Laboratory articles must not consume translation calls"))
    item = {"title": title, "url": GOOGLE_WRAPPER}
    if already_translated:
        item.update(title="원심분리기 장비에 관한 기존 번역", original_title=title)
    for payload in ({"symbol": "CFG", "items": [item]},
                    {"feature_key": "position_news", "items": [item]}):
        result = news._localize_news_payload(payload)
        assert result["items"] == []
        assert result["translation"] == {"status": "ready", "pending_count": 0}


@pytest.mark.parametrize("title", OBSERVED_PROJECT_NEWS + [
    "Centrifuge expands RWA lending",
    "Centrifuge tokenizes real-world assets",
    "센트리퓨즈, 온체인 실물 자산 시장 확대",
    "센트리퓨지, 블록체인 기반 펀드 출시",
    "Centrifuge brings sludge dewatering equipment financing onchain",
    "Centrifuge proposes tokenization of sedimentation centrifuge assets",
])
def test_observed_project_news_and_explicit_crypto_connections_remain_eligible(title):
    item = {"title": title, "url": GOOGLE_WRAPPER}
    assert news._is_news_article_candidate(item)
    assert news._matches_asset(item, "CFG", "CFG")


def test_curated_cfg_identity_does_not_depend_on_generic_catalog_names(monkeypatch):
    from app import news_asset_catalog

    def forbidden(_):
        pytest.fail("Curated CFG identity must not load or peek the asset catalog")

    monkeypatch.setattr(news_asset_catalog, "get_asset_name", forbidden)
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", forbidden)
    news._prepare_asset_identity("CFG")
    assert news._project_aliases("CFG")[0] == "centrifuge"
    assert not news._matches_asset({"title": "Centrifuge equipment sales rise"}, "CFG", "CFG")
    assert not news._matches_asset({"title": "센트리퓨즈 장비 실험 결과"}, "CFG", "CFG")
    assert news._matches_asset({"title": "Centrifuge expands RWA lending"}, "CFG", "CFG")
