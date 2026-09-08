"""Only the observed non-crypto Celestia names are excluded from news."""
import pytest

from app import news


UNRELATED_TITLES = [
    "Meet The Duo Behind Celestia Pictures, The Studio That Completed 6 Projects Before Its First Official Announcement!",
    "Between mythology and tragedy, Angela Mrad opens the gates to CELESTIA",
]


@pytest.mark.parametrize("title", UNRELATED_TITLES)
def test_observed_studio_and_exhibition_do_not_enter_news(title):
    assert not news._is_news_article_candidate({"title": title, "url": "https://news.google.com/rss/articles/example"})


@pytest.mark.parametrize("title", [
    "Celestia expands its data availability network",
    "Celestia (TIA) gains 5% after network upgrade",
    "셀레스티아, 신규 블록체인 프로젝트와 협력",
    "Celestia Pictures partners with TIA token developers",
    "Angela Mrad brings the CELESTIA exhibition to an NFT platform",
    "Celestia welcomes a new developer named Angela",
])
def test_normal_celestia_news_and_explicit_crypto_connections_remain_eligible(title):
    assert news._is_news_article_candidate({"title": title})


@pytest.mark.parametrize("title", UNRELATED_TITLES)
def test_cached_original_titles_are_excluded_before_any_translation(monkeypatch, title):
    monkeypatch.setattr(news, "_ensure_title_translations",
                        lambda _titles: pytest.fail("Unrelated names must not consume translation calls"))
    for item in ({"title": title}, {"title": "이미 번역된 다른 분야의 제목", "original_title": title}):
        result = news._localize_news_payload({"items": [item]})
        assert result["items"] == []
        assert result["translation"]["pending_count"] == 0
