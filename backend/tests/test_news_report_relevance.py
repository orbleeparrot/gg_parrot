"""GG-008: observed name collisions must never spend AI or reach readers."""
from datetime import datetime, timezone

import pytest

from app import news, news_asset_catalog, public_news
from app.agent_features.position_news import articles
from sqlmodel import Session, SQLModel, create_engine, select


PROJECTS = {"NIGHT": "Midnight", "XPL": "Plasma", "VIRTUAL": "Virtuals Protocol", "AXS": "Axie Infinity",
            "ICP": "Internet Computer"}
NOISE = [
    ("NIGHT", "Target’s ACOTAR Midnight Party Comes With Free Merch"),
    ("NIGHT", "Midnight cookie review: chocolate taste test"),
    ("XPL", "Plasma treatment advances cancer research"),
    ("XPL", "CNC plasma cutting machines for industrial production"),
    ("XPL", "Therapeutic plasma exchange in clinical medicine"),
    ("VIRTUAL", "VIRTUAL Minecraft experience and Candy Crush games"),
    ("AXS", "Denver youth concert tickets available on AXS"),
    ("VIRTUAL", "Minecraft, Candy Crush Among 11 Games in EU Virtual Currency Crackdown"),
    ("AXS", "Beyond AXS Opens Doors to Music and Live Entertainment for Denver Youth"),
    ("NIGHT", "Nike Caitlin 1 Midnight Fever PHOTOS"),
    ("XPL", "KDE Plasma 6.8 Now Makes Tiled Windows Fit Together More Nicely"),
    ("ICP", "Why CRM Implementations Underperform Even With a Strong ICP"),
    ("ICP", "강력한 ICP가 있어도 CRM 도입이 기대에 못 미치는 이유"),
    ("ICP", "Ideal customer profiles (ICP) help crypto businesses improve CRM adoption"),
    ("ICP", "ICP monitoring after traumatic brain injury"),
]
POSITIVE = [
    ("NIGHT", "Midnight blockchain introduces privacy-preserving transactions"),
    ("NIGHT", "NIGHT token gains after mainnet launch"),
    ("XPL", "Plasma launches zero-fee USDT payments"),
    ("XPL", "XPL token holders approve governance upgrade"),
    ("VIRTUAL", "Virtuals Protocol expands onchain AI agents"),
    ("VIRTUAL", "VIRTUAL token volume grows after exchange listing"),
    ("AXS", "Axie Infinity announces a new game update"),
    ("AXS", "AXS token gains as staking demand rises"),
    ("XPL", "과매수 상태가 조정을 유발하면서 Plasma가 $0.0854 지지선 아래로 하락"),
    ("VIRTUAL", "VIRTUAL 롱 17개월 보유한 고래, 미실현 손실 240만달러"),
    ("AXS", "AXS price jumps 13% but volume drops, signaling weak momentum"),
    ("ICP", "Internet Computer (ICP) Gains 3.05% Amid Broader Uptrend"),
    ("ICP", "DFINITY unveils new canister developer tools"),
    ("ICP", "인터넷컴퓨터 새 기능 공개"),
    ("ICP", "ICP Price Rally Close to Key Resistance — Is the Next Move Up or Down?"),
    ("ICP", "ICP price falls 3.05% as the market retreats"),
    ("ICP", "ICP token holders approve staking upgrade"),
    ("ICP", "$ICP holds the breakout level"),
    ("ICP", "ICP/USDT technical analysis"),
    ("ICP", "Internet Computer brings CRM tools onto the blockchain"),
    ("ICP", "ICP Quarterly Report: The path towards Deflation & Profitability"),
]


@pytest.fixture(autouse=True)
def prepared_names(monkeypatch):
    monkeypatch.setattr(news_asset_catalog, "peek_asset_name", lambda symbol: PROJECTS.get(symbol, ""))


def item(title, **fields):
    return {"title": title, "url": "https://news.example/article", "source": "Test editorial",
            "published": datetime.now(timezone.utc).isoformat(), **fields}


@pytest.mark.parametrize("symbol,title", NOISE)
def test_name_collisions_rejected_before_collection_and_translation(monkeypatch, symbol, title):
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *_: pytest.fail("noise consumed AI"))
    assert not news._matches_asset(item(title), symbol, symbol)
    assert news._relevant_items([item(title)], asset_symbol=symbol, coin_name=symbol,
                                feed_source="google_news_rss") == []


@pytest.mark.parametrize("symbol,title", POSITIVE)
def test_real_project_and_token_news_retained(symbol, title):
    assert news._matches_asset(item(title), symbol, symbol)
    result = news._relevant_items([item(title)], asset_symbol=symbol, coin_name=symbol,
                                 feed_source="google_news_rss")
    assert len(result) == 1
    assert result[0]["asset_match"]["asset"] == symbol


@pytest.mark.parametrize("symbol,title", NOISE)
def test_old_translated_ready_rows_are_rechecked_without_fetching(monkeypatch, symbol, title):
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *_: pytest.fail("GET called AI"))
    stored = item("기존에 번역된 동명 대상의 소식", original_title=title)
    payload = public_news._public_payload(symbol, {"items": [stored], "cursor": 23})
    assert payload["items"] == []
    assert payload["cursor"] == 23


@pytest.mark.parametrize("title", [
    "초보자가 질문하기 쉬운 토토 쇼미더벳 규칙 모음",
    "심벌의 개수와 위치는 어떤 관계일까? 캔디 카지노",
    "Stake.us Promo Code: COVERSBONUS for $55 Free SC + 550K GC",
    "PlayFame bonus and review: Get 500K GC, 250 SC & 250 Free Spins",
])
def test_gambling_search_spam_not_a_market_article(title):
    assert not news._is_news_article_candidate(item(title))
    stored = item("기존 번역 제목", original_title=title)
    assert public_news._public_payload("MARKET", {"items": [stored]})["items"] == []


@pytest.mark.parametrize("title", [
    "Bitcoin casinos face new cryptocurrency regulation",
    "암호화폐 카지노에 자금세탁 방지 규제 적용",
    "Plasma blockchain introduces stablecoin payments",
])
def test_editorial_crypto_reporting_not_dropped_for_gambling_word_alone(title):
    assert news._is_news_article_candidate(item(title))


@pytest.fixture
def article_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'relevance.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(articles, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


@pytest.mark.parametrize("symbol", ["NIGHT", "ICP"])
def test_storage_filters_noise_and_keeps_assessment_alignment(article_db, symbol):
    bad_title = next(title for asset, title in NOISE if asset == symbol)
    good_title = next(title for asset, title in POSITIVE if asset == symbol)
    bad = item("기존 번역 제목", original_title=bad_title)
    good = item("정상 코인 소식", original_title=good_title,
                url="https://news.example/good")
    articles.upsert_articles(symbol, [bad, good], analysis={"items": [{"tag": "bad"}, {"tag": "good"}]})
    feed = articles.read_article_feed(symbol)
    assert [row["title"] for row in feed["items"]] == [good["title"]]
    assert feed["analysis"]["items"] == [{"tag": "good"}]
    with Session(article_db) as db:
        assert len(db.exec(select(articles.NewsArticle)).all()) == 1


@pytest.mark.parametrize("symbol", ["NIGHT", "ICP"])
def test_legacy_rows_filter_without_losing_cursor_or_sentiment(article_db, monkeypatch, symbol):
    import json
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *_: pytest.fail("reader called AI"))
    bad_title = next(title for asset, title in NOISE if asset == symbol)
    good_title = next(title for asset, title in POSITIVE if asset == symbol)
    bad = item("기존 번역 제목", original_title=bad_title)
    good = item("정상 코인 소식", original_title=good_title)
    with Session(article_db) as db:
        db.add(articles.NewsArticleFeed(asset_symbol=symbol, revision=2, item_count=2, ready_count=2))
        for revision, raw in enumerate([bad, good], 1):
            db.add(articles.NewsArticle(asset_symbol=symbol, article_id=str(revision), revision=revision,
                ready=True, item_json=json.dumps(raw), assessment_json=json.dumps({"revision": revision}),
                first_seen_ms=1, last_seen_ms=1))
        db.commit()
    first = articles.read_article_feed(symbol, after_revision=0, limit=1)
    assert first["items"] == [] and first["analysis"]["items"] == []
    assert first["cursor"] == 1 and first["has_more"]
    second = articles.read_article_feed(symbol, after_revision=first["cursor"], limit=1)
    assert [row["title"] for row in second["items"]] == [good["title"]]
    assert second["analysis"]["items"] == [{"revision": 2}]
    assert second["cursor"] == 2 and not second["has_more"]


@pytest.mark.parametrize("symbol,title", NOISE)
def test_enrichment_never_translates_old_unrelated_items(monkeypatch, symbol, title):
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda rows, **_: rows)
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *_: pytest.fail("noise consumed AI"))
    result = news._localize_news_payload({"symbol": symbol, "items": [item(title)]})
    assert result["items"] == []
    assert result["translation"]["pending_count"] == 0


def test_icp_search_category_and_crypto_source_do_not_prove_asset_identity():
    raw = item("Why CRM Implementations Underperform Even With a Strong ICP",
               categories=["ICP", "Internet Computer", "crypto"], source="CoinDesk")
    assert news._relevant_items([raw], asset_symbol="ICP", coin_name="Internet Computer",
                                feed_source="google_news_rss") == []


def test_icp_rechecking_uses_source_title_not_a_misleading_translation():
    stored = item("인터넷컴퓨터 코인 소식",
                  original_title="Why CRM Implementations Underperform Even With a Strong ICP")
    assert public_news._public_payload("ICP", {"items": [stored]})["items"] == []


@pytest.mark.parametrize("symbol,title", [row for row in POSITIVE if row[0] == "ICP"])
def test_valid_icp_ready_articles_survive_rechecking(symbol, title):
    stored = item("정상 코인 소식", original_title=title)
    assert public_news._public_payload(symbol, {"items": [stored]})["items"] == [stored]


def test_icp_community_projection_keeps_collector_validated_posts():
    # Square identity is checked against the body during collection; readers
    # must not reject an opaque headline after that private body is stripped.
    stored = item("갈레온 브레인 결과", original_title="Galeon Brain Result",
                  content_type="community", categories=["ICP"], source="Binance Square",
                  community_post_id="icp-result", community_summary="작성자의 ICP 거래 결과입니다.",
                  community_summary_status="ready")
    assert public_news._public_payload("ICP", {"items": [stored]})["items"] == [stored]
