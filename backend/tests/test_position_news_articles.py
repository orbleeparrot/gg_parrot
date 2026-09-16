"""Incremental article persistence and HTTP delivery, with no external I/O."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import event, update
from sqlmodel import Session, SQLModel, create_engine, select

from app import news
from app.agent_features.position_news import articles, collector, repository, service


@pytest.fixture
def engine(tmp_path, monkeypatch):
    value = create_engine(f"sqlite:///{tmp_path / 'articles.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    SQLModel.metadata.create_all(value)
    monkeypatch.setattr(repository, "get_session", lambda: Session(value))
    monkeypatch.setattr(articles, "get_session", lambda: Session(value))
    yield value
    value.dispose()


def item(title="비트코인 현물 ETF 승인", **extra):
    return {"title": title, "source": "CoinDesk", "url": "https://example.test/story", **extra}


def test_raw_storage_ready_update_and_cursor_replay_preserve_analysis(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw])
    first = articles.read_article_feed("BTC")
    assert first["items"] == [] and first["translation"]["pending_count"] == 1
    translated = item("비트코인 ETF 승인", original_title=raw["title"])
    articles.upsert_articles("BTC", [translated], analysis={"items": [{"sentiment": "positive", "summary": "승인 소식입니다."}],
                                                           "analysis_source": "ai", "analysis_status": "ready"})
    ready = articles.read_article_feed("BTC", after_revision=first["cursor"])
    assert len(ready["items"]) == 1 and ready["translation"]["pending_count"] == 0
    assert ready["items"][0]["id"] == service._article_id(raw)
    articles.upsert_articles("BTC", [raw], analysis={"items": [{"sentiment": "unclear"}], "analysis_source": "rule"})
    unchanged = articles.read_article_feed("BTC", after_revision=ready["cursor"])
    assert unchanged["items"] == [] and unchanged["cursor"] == ready["cursor"]
    assert articles.read_article_feed("BTC")["analysis"]["items"][0]["sentiment"] == "positive"


def test_cursor_pages_do_not_skip_enrichment_or_new_articles(engine):
    articles.upsert_articles("BTC", [item(f"비트코인 기사 {n}", url=f"https://example.test/{n}") for n in range(4)])
    first = articles.read_article_feed("BTC", after_revision=0, limit=2)
    assert len(first["items"]) == 2 and first["has_more"]
    articles.upsert_articles("BTC", [{**first["items"][0], "excerpt": "추가로 확인된 내용입니다."}])
    second = articles.read_article_feed("BTC", after_revision=first["cursor"], limit=2)
    third = articles.read_article_feed("BTC", after_revision=second["cursor"], limit=2)
    assert len(second["items"]) == 2 and second["has_more"]
    assert len(third["items"]) == 1 and not third["has_more"]
    assert third["items"][0]["excerpt"] == "추가로 확인된 내용입니다."


def test_position_get_is_db_only_and_projects_ready_articles_by_identity(engine, monkeypatch):
    raw = [item("Bitcoin ETF approved"), item("비트코인 거래소 해킹", url="https://example.test/second")]
    articles.upsert_articles("BTC", raw, analysis={"items": [{"sentiment": "positive"}, {"sentiment": "negative"}], "analysis_source": "ai"})
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP readers must not translate, fetch, analyze, or schedule")
    monkeypatch.setattr(news, "_localize_news_payload", forbidden)
    monkeypatch.setattr(news, "fetch_coin_news_for_collector", forbidden)
    monkeypatch.setattr(collector.classifier, "analyze_headlines", forbidden)
    with Session(engine) as db:
        first = service.get_position_news({"symbol": "BTCUSDT", "position_side": "short"}, db=db)
    assert len(first["items"]) == 1 and first["items"][0]["position_effect"] == "favorable"
    translated = {**raw[0], "original_title": raw[0]["title"], "title": "비트코인 ETF 승인"}
    articles.upsert_articles("BTC", [translated])
    with Session(engine) as db:
        next_page = service.get_position_news({"symbol": "BTCUSDT", "position_side": "short"}, db=db, cursor=first["cursor"])
    assert len(next_page["items"]) == 1
    assert next_page["items"][0]["position_effect"] == "unfavorable"
    assert next_page["items"][0]["id"] == service._article_id(raw[0])


def test_source_article_is_visible_before_other_source_and_translation_finish(engine, monkeypatch):
    source_waiting, release_source = Event(), Event()
    translation_waiting, release_translation = Event(), Event()
    raw = item("Bitcoin ETF approved")
    payload = {"symbol": "BTC", "items": [item(), raw]}
    def fetcher(symbol, *, on_progress):
        on_progress(payload)
        source_waiting.set()
        assert release_source.wait(5)
        return payload
    def localize(items, *, on_progress=None, **kwargs):
        translation_waiting.set()
        assert release_translation.wait(5)
        return [item() if row["title"] != raw["title"] else {**row, "original_title": raw["title"], "title": "ビットコイン"}
                for row in items]
    monkeypatch.setattr(news, "_localize_coin_news_items", localize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(collector.collect_ticker, "BTC", fetcher=fetcher, allow_ai=False)
        try:
            assert source_waiting.wait(5) and translation_waiting.wait(5)
            feed = articles.read_article_feed("BTC")
            assert len(feed["items"]) == 1 and feed["translation"]["pending_count"] == 1
            assert not future.done()
        finally:
            release_translation.set()
            release_source.set()
        future.result(timeout=10)


def test_new_rss_article_merges_with_existing_snapshot_before_enrichment(engine):
    old, new = item(), item("비트코인 신규 상장 소식", url="https://example.test/new")
    collector.collect_payload("BTC", {"symbol": "BTC", "items": [old]}, allow_ai=False, localize=False)
    result = collector.publish_initial_payload("BTC", {"symbol": "BTC", "items": [new]})
    assert result["status"] == "stored"
    assert {row["title"] for row in repository.get_latest_snapshot("BTC")["news_payload"]["items"]} == {old["title"], new["title"]}
    assert len(articles.read_article_feed("BTC")["items"]) == 2


def test_translation_claim_and_store_are_batched_and_fenced(engine):
    sql = []
    def capture(conn, cursor, statement, parameters, context, many):
        sql.append(statement.split()[0])
    event.listen(engine, "before_cursor_execute", capture)
    with Session(engine) as db:
        titles = [f"Bitcoin news {n}" for n in range(10)]
        claim = repository.claim_title_translations(titles, db=db, now_ms=1000)
        assert sql == ["INSERT", "SELECT"]
        sql.clear()
        repository.store_title_translations({title: "비트코인 소식" for title in titles}, claim_token="other", db=db)
        assert sql == ["UPDATE"]
        assert repository.get_title_translations(titles, db=db) == {}
        repository.store_title_translations({title: "비트코인 소식" for title in titles}, claim_token=claim["claim_token"], db=db)
        assert len(repository.get_title_translations(titles, db=db)) == 10


def test_concurrent_translation_claims_have_one_owner_and_shared_maintenance_winner(engine):
    gate = Barrier(2)
    def claim():
        with Session(engine) as db:
            gate.wait(timeout=5)
            return repository.claim_title_translations(["Bitcoin ETF approved", "Bitcoin rallies"], db=db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(len(result["claimed"]) for result in results) == [0, 2]
    assert articles.claim_maintenance(now_ms=1000)
    assert not articles.claim_maintenance(now_ms=1001)
    assert articles.claim_maintenance(now_ms=3_601_000)


def test_pending_titles_retry_without_refetching_and_clear_pending_counter(engine, monkeypatch):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    calls = []
    def localize(items, **kwargs):
        calls.append(items)
        return [] if len(calls) == 1 else [{**raw, "original_title": raw["title"], "title": "비트코인 ETF 승인"}]
    monkeypatch.setattr(news, "_localize_coin_news_items", localize)
    monkeypatch.setattr(news, "fetch_coin_news_for_collector", lambda *_args, **_kwargs: pytest.fail("Retry must not refetch sources"))
    collector.retry_article_enrichment(now_ms=1000)
    collector.retry_article_enrichment(now_ms=1001)
    assert len(calls) == 1
    collector.retry_article_enrichment(now_ms=31_000)
    assert len(calls) == 1, "진전 없는 회차 뒤엔 루프 전체가 60초 쉰다(공급자 막힘으로 본다)"
    collector.retry_article_enrichment(now_ms=61_000)
    assert len(calls) == 2
    assert articles.read_article_feed("BTC")["translation"]["pending_count"] == 0
    assert articles.pending_article_batches(now_ms=121_000) == {}


def test_article_pruning_updates_pending_counts_without_resetting_cursor(engine):
    articles.upsert_articles("BTC", [item("Bitcoin ETF approved")], now_ms=1000)
    articles.upsert_articles("BTC", [item()], now_ms=40 * 86_400_000)
    before = articles.read_article_feed("BTC")
    assert before["translation"]["pending_count"] == 1
    assert articles.prune_articles(now_ms=40 * 86_400_000) == 1
    after = articles.read_article_feed("BTC")
    assert after["translation"]["pending_count"] == 0 and after["cursor"] == before["cursor"]
    assert len(after["items"]) == 1


def test_idle_fast_collection_does_not_run_maintenance(engine, monkeypatch):
    monkeypatch.setattr(collector, "prune_community_summaries", lambda **_: pytest.fail("Idle scan must not prune"))
    assert collector.run_collection_cycle(symbols=[], retention_days=0)["ticker_count"] == 0


def test_late_image_updates_current_article_without_reverting_title_or_analysis(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw])
    localized = {**raw, "title": "비트코인 ETF 승인", "original_title": raw["title"]}
    articles.upsert_articles("BTC", [localized], analysis={"items": [{"sentiment": "positive"}], "analysis_source": "ai"})
    articles.update_article_image("BTC", articles.article_id(raw), {**raw,
        "image": "https://example.test/image.png", "article_url": raw["url"], "image_resolved": True})
    current = articles.read_article_feed("BTC")
    assert current["items"][0]["title"] == localized["title"]
    assert current["items"][0]["image"] == "https://example.test/image.png"
    assert current["analysis"]["items"][0]["sentiment"] == "positive"
    assert articles.update_article_image("BTC", "missing", {"image": "unused"}) is None


def test_late_body_summary_cannot_replace_newer_post_version(engine):
    old = item("Bitcoin ETF approved", content_type="community", community_post_id="123",
               community_body="First public body", community_body_hash="first", community_summary_status="pending")
    articles.upsert_articles("BTC", [old])
    updated = {**old, "community_body": "Updated public body", "community_body_hash": "second"}
    articles.upsert_articles("BTC", [updated])
    stale = {**old, "title": "비트코인 ETF 승인", "original_title": old["title"],
             "community_summary": "이전 본문 요약입니다.", "community_summary_status": "ready"}
    articles.upsert_articles("BTC", [stale], enrichment_only=True)
    with Session(engine) as db:
        row = db.get(articles.NewsArticle, ("BTC", articles.article_id(old)))
        import json
        current = json.loads(row.item_json)
    assert current["community_body_hash"] == "second"
    assert current.get("community_summary") != stale["community_summary"]
    assert current["community_summary_status"] == "pending"


def test_image_patch_refreshes_an_already_cached_orm_article(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw])
    with Session(engine) as db:
        cached = db.get(articles.NewsArticle, ("BTC", articles.article_id(raw)))
        assert cached is not None
        articles.upsert_articles("BTC", [{**raw, "title": "비트코인 ETF 승인", "original_title": raw["title"]}])
        articles.update_article_image("BTC", articles.article_id(raw), {"image_resolved": True}, db=db)
    assert articles.read_article_feed("BTC")["items"][0]["title"] == "비트코인 ETF 승인"


def _select_statements(engine):
    seen = []
    def record(_conn, _cursor, statement, *_rest):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(" ".join(statement.split()))
    event.listen(engine, "before_cursor_execute", record)
    return seen


def test_identical_rediscovery_skips_reading_item_json_and_keeps_revision(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    before = articles.read_article_feed("BTC")
    seen = _select_statements(engine)
    articles.upsert_articles("BTC", [raw], now_ms=2000)
    assert not any("newsarticle.item_json" in statement for statement in seen), "같은 항목이 다시 오면 본문을 내려받지 않는다"
    after = articles.read_article_feed("BTC")
    assert after["cursor"] == before["cursor"], "내용이 그대로면 revision 도 그대로"
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert row.last_seen_ms == 2000, "관측 시각은 계속 갱신된다(정리 기준)"
        assert row.source_hash == articles.item_hash(raw) and row.content_hash == articles.text_hash(row.item_json)
    # 내용이 바뀐 항목은 본문을 읽고 revision 을 올린다
    seen.clear()
    articles.upsert_articles("BTC", [item("Bitcoin ETF approved", excerpt="새 요약")], now_ms=3000)
    assert any("newsarticle.item_json" in statement for statement in seen)
    assert articles.read_article_feed("BTC")["cursor"] == before["cursor"] + 1


def test_legacy_rows_without_hashes_are_backfilled_then_skipped(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    with Session(engine) as db:
        db.exec(update(articles.NewsArticle).values(source_hash="", content_hash=""))
        db.commit()
    seen = _select_statements(engine)
    articles.upsert_articles("BTC", [raw], now_ms=2000)  # 첫 번째: 본문을 읽어 비교하고 해시만 채운다
    assert any("newsarticle.item_json" in statement for statement in seen)
    seen.clear()
    articles.upsert_articles("BTC", [raw], now_ms=3000)  # 두 번째부터 건너뛴다
    assert not any("newsarticle.item_json" in statement for statement in seen)


def test_unchanged_enrichment_publish_is_skipped_by_content_hash(engine):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    stored = articles.pending_article_batches(now_ms=1000)["BTC"]
    seen = _select_statements(engine)
    articles.upsert_articles("BTC", stored, now_ms=2000, enrichment_only=True)
    assert not any("newsarticle.item_json" in statement for statement in seen)


def test_no_progress_stalls_the_loop_then_probes_and_recovers_fast(engine, monkeypatch):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    calls = []
    working = {"on": False}
    def localize(items, **_kwargs):
        calls.append(items)
        return [{**it, "original_title": it["title"], "title": "비트코인 ETF 승인"} for it in items] if working["on"] else []
    monkeypatch.setattr(news, "_localize_coin_news_items", localize)
    collector.retry_article_enrichment(now_ms=1000)          # 진전 없음 → 60초 정체
    assert len(calls) == 1
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert (row.enrichment_attempts, row.enrichment_next_ms) == (0, 31_000), "공급자가 막힌 회차는 실패로 세지 않고 30초 뒤로만 미룬다"
    assert articles.enrichment_stall(now_ms=31_000) == {"stalled": True, "probing": True}
    collector.retry_article_enrichment(now_ms=31_000)        # 행은 재시도 시각이 됐지만 루프가 쉬는 중
    assert len(calls) == 1
    collector.retry_article_enrichment(now_ms=61_000)        # 탐침 회차(5행) — 아직 실패 → 다시 60초
    assert len(calls) == 2
    assert articles.enrichment_stall(now_ms=100_000) == {"stalled": True, "probing": True}
    working["on"] = True
    collector.retry_article_enrichment(now_ms=121_000)       # 공급자 복구 → 탐침 성공 → 정체 해제
    assert len(calls) == 3
    assert articles.enrichment_stall(now_ms=121_000) == {"stalled": False, "probing": False}
    assert articles.read_article_feed("BTC")["translation"]["pending_count"] == 0
    assert articles.has_pending_articles(now_ms=200_000) is False


def test_probe_batches_are_small_while_stalled_and_full_afterwards(engine, monkeypatch):
    rows = [item(f"Bitcoin headline {index}", url=f"https://news.test/{index}") for index in range(8)]
    articles.upsert_articles("BTC", rows, now_ms=1000)
    sizes = []
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items, **_kwargs: sizes.append(len(items)) or [])
    collector.retry_article_enrichment(now_ms=1000)
    assert sizes == [8], "정상 상태의 첫 회차는 전체 배치"
    collector.retry_article_enrichment(now_ms=61_000)
    assert sizes == [8, 5], "정체 뒤 탐침은 5행만 읽는다"


def test_failures_count_only_when_the_provider_is_healthy_and_give_up_after_the_limit(engine, monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_ENRICHMENT_MAX_ATTEMPTS", "2")
    good = item("Bitcoin ETF approved", url="https://news.test/good")
    bad = item("看涨 STEEMUSDT 合约信号", url="https://news.test/bad")
    articles.upsert_articles("BTC", [good, bad], now_ms=1000)
    def localize(items, **_kwargs):
        return [{**it, "original_title": it["title"], "title": "비트코인 ETF 승인"} for it in items if it["url"].endswith("good")]
    monkeypatch.setattr(news, "_localize_coin_news_items", localize)
    collector.retry_article_enrichment(now_ms=1000)          # good 진전 → 정상 회차 → bad 실패 1회(다음 60초 뒤)
    with Session(engine) as db:
        bad_row = db.exec(select(articles.NewsArticle).where(articles.NewsArticle.article_id == articles.article_id(bad))).one()
        assert (bad_row.enrichment_attempts, bad_row.enrichment_next_ms, bad_row.enrichment_pending) == (1, 61_000, True)
    # 이제 bad 만 남았다 — 혼자 실패하는 회차는 공급자 막힘과 구별할 수 없으니 실패로 세지 않고 정체로 본다
    collector.retry_article_enrichment(now_ms=61_000)
    with Session(engine) as db:
        bad_row = db.exec(select(articles.NewsArticle).where(articles.NewsArticle.article_id == articles.article_id(bad))).one()
        assert (bad_row.enrichment_attempts, bad_row.enrichment_pending) == (1, True)
    # 새로 들어온 번역 가능한 기사와 함께 도는 회차에서 또 실패하면 상한(2회)에 닿아 포기한다
    articles.upsert_articles("BTC", [item("Ether ETF approved", url="https://news.test/good2")], now_ms=200_000)
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items, **_kwargs: [
        {**it, "original_title": it["title"], "title": "이더리움 ETF 승인"} for it in items if it["url"].endswith("good2")])
    collector.retry_article_enrichment(now_ms=200_000)
    with Session(engine) as db:
        bad_row = db.exec(select(articles.NewsArticle).where(articles.NewsArticle.article_id == articles.article_id(bad))).one()
        assert (bad_row.enrichment_attempts, bad_row.enrichment_pending) == (2, False)
    assert articles.has_pending_articles(now_ms=1_000_000) is False


def test_articles_older_than_the_freshness_window_are_given_up(engine, monkeypatch):
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items, **_kwargs: [])
    seven_hours = 1000 + 7 * 3600 * 1000
    collector.retry_article_enrichment(now_ms=seven_hours)
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert row.enrichment_pending is False, "6시간 넘은 기사는 번역돼도 늦으니 포기한다"
    assert articles.has_pending_articles(now_ms=seven_hours + 1) is False


def test_retry_delay_caps_at_two_minutes_and_success_resets_backoff(engine, monkeypatch):
    assert [articles.enrichment_retry_delay_ms(n) // 1000 for n in (0, 1, 2, 3, 9)] == [30, 60, 120, 120, 120]
    raw = item("Bitcoin ETF approved")
    articles.upsert_articles("BTC", [raw], now_ms=1000)
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items, **_kwargs: [])
    collector.retry_article_enrichment(now_ms=1000)
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert (row.enrichment_attempts, row.enrichment_next_ms) == (0, 31_000)
    # 원본이 바뀌어 다시 저장되면 백오프는 처음부터
    articles.upsert_articles("BTC", [item("Bitcoin ETF approved", excerpt="바뀐 요약")], now_ms=5000)
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert (row.enrichment_attempts, row.enrichment_next_ms) == (0, 0)
    # 번역이 끝나 보강이 완료되면 pending 이 꺼지고 백오프도 지운다
    articles.upsert_articles("BTC", [{**raw, "excerpt": "바뀐 요약", "original_title": raw["title"], "title": "비트코인 ETF 승인"}],
                             now_ms=6000, enrichment_only=True)
    with Session(engine) as db:
        row = db.exec(select(articles.NewsArticle)).one()
        assert row.ready is True and row.enrichment_pending is False and row.enrichment_attempts == 0


def test_lease_and_budget_claims_decide_by_returning_not_rowcount(engine, monkeypatch):
    """운영 Postgres(psycopg)에서 INSERT … ON CONFLICT 의 rowcount 는 -1 이라 rowcount 판정은 늘 진다 — RETURNING 으로 가른다."""
    seen = []
    event.listen(engine, "before_cursor_execute", lambda _c, _cur, statement, *_r: seen.append(" ".join(statement.split())))
    assert articles.claim_maintenance("article-enrichment:BTC", interval_seconds=30, now_ms=1000) is True
    assert articles.claim_maintenance("article-enrichment:BTC", interval_seconds=30, now_ms=1001) is False, "리스가 살아 있으면 진다"
    assert articles.claim_maintenance("article-enrichment:BTC", interval_seconds=30, now_ms=31_000) is True
    claims = [s for s in seen if s.startswith("INSERT INTO newsmaintenancelease")]
    assert claims and all("RETURNING" in s for s in claims)
    monkeypatch.setattr(repository, "get_session", lambda: Session(engine))
    seen.clear()
    assert repository.reserve_news_api_budget(total_limit=2, daily_limit=1, now_ms=1000) is True
    assert repository.reserve_news_api_budget(total_limit=2, daily_limit=1, now_ms=1000) is False, "하루 한도에 닿으면 진다"
    budget = [s for s in seen if s.startswith("INSERT INTO tickernewsaibudget")]
    assert budget and all("RETURNING" in s for s in budget)
