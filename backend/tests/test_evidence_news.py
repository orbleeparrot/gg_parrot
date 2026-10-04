"""2·3 층 근거 — DB 에 없으면 과거 뉴스를 조회하고, 그 결과가 아카이브를 채운다.

못 찾는 것은 정상 경로다. 예외가 호출자까지 올라가면 안 되고, 없는 기사를
지어내서도 안 된다. 어떤 시험도 네트워크를 쓰지 않는다.
"""
from datetime import datetime, timezone

import pytest

from app import evidence, news

DATE = "2026-03-14"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp() * 1000)


def _feed_item(title, published, url="https://x.test/a"):
    return {"title": title, "source": "Decrypt", "url": url,
            "published": published, "published_display": ""}


def test_db_hit_does_not_touch_the_network(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup",
                        lambda *a, **k: [{"title": "상장 공지", "source": "CoinDesk",
                                          "url": "https://x.test/1", "published_ms": 1}])

    def explode(*a, **k):
        raise AssertionError("DB 에 있으면 외부 조회를 하면 안 된다")
    monkeypatch.setattr(evidence, "fetch_historical", explode)

    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True and found["items"][0]["title"] == "상장 공지"


def test_a_miss_falls_through_to_historical_search_and_fills_the_archive(monkeypatch):
    stored = {}
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical",
                        lambda asset, date: [{"title": "그날 기사", "source": "Decrypt",
                                              "url": "https://x.test/2", "published_ms": 2}])
    monkeypatch.setattr(evidence.news_archive, "store",
                        lambda asset, items: stored.setdefault(asset, items) and len(items))

    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True
    assert stored["BTC"][0]["title"] == "그날 기사", "조회 결과가 아카이브를 채워야 한다"


def test_nothing_found_reports_a_reason_instead_of_raising(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    found = evidence.headlines("BTC", DATE)
    assert found == {"items": [], "found": False, "reason": "보존_범위_밖"}


def test_a_network_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])

    def boom(asset, date):
        raise TimeoutError("slow")
    monkeypatch.setattr(evidence, "fetch_historical", boom)
    found = evidence.headlines("BTC", DATE)
    assert found["found"] is False and found["reason"] == "조회_실패"


def test_historical_query_uses_an_exclusive_date_window():
    query = evidence.historical_query("BTC", DATE)
    assert "after:2026-03-13" in query and "before:2026-03-16" in query


# --- 브리프 밖 보강: 호출자까지 예외가 올라오지 않는다 -----------------------------

def test_a_malformed_date_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    found = evidence.headlines("BTC", "어제")
    assert found == {"items": [], "found": False, "reason": "조회_실패"}


def test_an_archive_that_raises_does_not_break_the_lookup(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(evidence.news_archive, "lookup", boom)
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    assert evidence.headlines("BTC", DATE)["found"] is False


def test_a_failing_archive_write_still_returns_what_was_found(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical",
                        lambda asset, date: [{"title": "기사", "source": "S",
                                              "url": "https://x.test/3", "published_ms": 5}])

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(evidence.news_archive, "store", boom)
    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True and found["items"][0]["title"] == "기사"


def test_the_returned_items_are_capped_at_the_archive_row_limit(monkeypatch):
    many = [{"title": f"t{i}", "source": "S", "url": f"https://x.test/{i}", "published_ms": i + 1}
            for i in range(20)]
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: many)
    monkeypatch.setattr(evidence.news_archive, "store", lambda asset, items: len(items))
    assert len(evidence.headlines("BTC", DATE)["items"]) == evidence.news_archive.MAX_ROWS


def test_the_lookup_window_covers_a_kst_day(monkeypatch):
    seen = {}
    monkeypatch.setattr(evidence.news_archive, "lookup",
                        lambda asset, *, start_ms, end_ms, **k: seen.update(s=start_ms, e=end_ms) or [])
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    evidence.headlines("BTC", DATE)
    # KST 2026-03-14 은 UTC 03-13 15:00 ~ 03-14 15:00 이다.
    assert seen["s"] <= _ms("2026-03-13T15:00:00+00:00")
    assert seen["e"] >= _ms("2026-03-14T15:00:00+00:00")


@pytest.mark.parametrize("asset", ["", "BTC OR ETH", "btc:x", "A" * 21])
def test_a_non_coin_asset_never_reaches_the_search_query(asset):
    with pytest.raises(ValueError):
        evidence.historical_query(asset, DATE)


# --- fetch_historical: 기존 news._fetch_news 를 쓰고 네트워크는 대체한다 ------------

def test_fetch_historical_converts_dates_and_keeps_only_the_window(monkeypatch):
    calls = []

    def fake(query, **kwargs):
        calls.append((query, kwargs))
        return [
            _feed_item("범위 안 늦은", "2026-03-14T20:00:00+00:00", "https://x.test/late"),
            _feed_item("범위 안 이른", "2026-03-13T02:00:00+00:00", "https://x.test/early"),
            _feed_item("범위 밖", "2026-03-20T00:00:00+00:00", "https://x.test/out"),
            _feed_item("날짜 없음", None, "https://x.test/none"),
            _feed_item("날짜 깨짐", "not-a-date", "https://x.test/bad"),
        ]
    monkeypatch.setattr(news, "_fetch_news", fake)

    items = evidence.fetch_historical("BTC", DATE)

    assert calls[0][1].get("strict") is True, "전송 실패가 빈 결과로 둔갑하면 안 된다"
    assert [i["title"] for i in items] == ["범위 안 이른", "범위 안 늦은"], "오래된 순"
    assert items[0] == {"title": "범위 안 이른", "source": "Decrypt", "url": "https://x.test/early",
                        "published_ms": _ms("2026-03-13T02:00:00+00:00")}


def test_fetch_historical_lets_a_transport_failure_propagate(monkeypatch):
    def fail(query, **kwargs):
        raise news.NewsFetchError("down")
    monkeypatch.setattr(news, "_fetch_news", fail)
    with pytest.raises(news.NewsFetchError):
        evidence.fetch_historical("BTC", DATE)


def test_a_transport_failure_surfaces_as_a_lookup_failure(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])

    def fail(query, **kwargs):
        raise news.NewsFetchError("down")
    monkeypatch.setattr(news, "_fetch_news", fail)
    assert evidence.headlines("BTC", DATE) == {"items": [], "found": False, "reason": "조회_실패"}


def test_an_empty_feed_is_not_found_rather_than_a_failure(monkeypatch):
    monkeypatch.setattr(evidence.news_archive, "lookup", lambda *a, **k: [])
    monkeypatch.setattr(news, "_fetch_news", lambda query, **kw: [])
    assert evidence.headlines("BTC", DATE) == {"items": [], "found": False, "reason": "보존_범위_밖"}
