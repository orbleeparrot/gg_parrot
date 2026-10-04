"""2·3 층 근거 — DB 에 없으면 과거 뉴스를 조회하고, 그 결과가 아카이브를 채운다.

못 찾는 것은 정상 경로다. 예외가 호출자까지 올라가면 안 되고, 없는 기사를
지어내서도 안 된다. 어떤 시험도 네트워크를 쓰지 않는다(페처는 모두 대체한다).
"""
import logging
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlmodel")

from sqlmodel import Session, SQLModel, create_engine, select

from app import evidence, news, news_archive
from app.db import NewsHeadlineArchive

DATE = "2026-03-14"
HOUR = 3_600_000


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp() * 1000)


NOON = _ms("2026-03-14T12:00:00+00:00")  # 기준 날짜의 UTC 정오 — 화면에 보일 기사를 고르는 중심


def _feed_item(title, published, url="https://x.test/a"):
    return {"title": title, "source": "Decrypt", "url": url,
            "published": published, "published_display": ""}


def _row(title, published_ms, url=None):
    return {"title": title, "source": "CoinDesk", "published_ms": published_ms,
            "url": url or f"https://x.test/{title}"}


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'archive.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(news_archive, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def _archived(engine):
    with Session(engine) as session:
        return session.exec(select(NewsHeadlineArchive)).all()


class FakeArchive:
    """store 와 lookup 이 같은 목록을 보는 가짜 — 실제 DB 없이 '채운 뒤 다시 읽기' 를 흉내 낸다."""

    def __init__(self, monkeypatch, rows=()):
        self.rows = list(rows)
        self.lookups = []
        monkeypatch.setattr(news_archive, "lookup", self.lookup)
        monkeypatch.setattr(news_archive, "store", self.store)

    def lookup(self, asset, *, start_ms, end_ms, limit=news_archive.MAX_ROWS):
        self.lookups.append((asset, start_ms, end_ms, limit))
        return [r for r in self.rows if start_ms <= r["published_ms"] <= end_ms][:limit]

    def store(self, asset, items):
        self.rows.extend(items)
        return len(items)


def _no_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("외부 조회를 하면 안 된다")
    monkeypatch.setattr(news, "_fetch_news", explode)


# --- 2 층: DB 에 있으면 네트워크를 안 쓴다 ------------------------------------------

def test_db_hit_does_not_touch_the_network(monkeypatch):
    FakeArchive(monkeypatch, [_row("상장 공지", NOON)])
    monkeypatch.setattr(evidence, "fetch_historical",
                        lambda *a, **k: pytest.fail("DB 에 있으면 외부 조회를 하면 안 된다"))
    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True and found["items"][0]["title"] == "상장 공지"


# --- 3 층: 없으면 조회하고 그 결과가 아카이브를 채운다 ---------------------------------

def test_a_miss_falls_through_to_historical_search_and_fills_the_archive(monkeypatch):
    archive = FakeArchive(monkeypatch)
    monkeypatch.setattr(evidence, "fetch_historical",
                        lambda asset, date: [_row("그날 기사", NOON)])
    found = evidence.headlines("BTC", DATE)
    assert found["found"] is True and found["items"][0]["title"] == "그날 기사"
    assert [r["title"] for r in archive.rows] == ["그날 기사"], "조회 결과가 아카이브를 채워야 한다"


def test_nothing_found_reports_a_reason_instead_of_raising(monkeypatch):
    FakeArchive(monkeypatch)
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    assert evidence.headlines("BTC", DATE) == {"items": [], "found": False, "reason": "보존_범위_밖"}


def test_a_network_failure_is_reported_not_raised(monkeypatch, caplog):
    FakeArchive(monkeypatch)

    def boom(asset, date):
        raise TimeoutError("slow")
    monkeypatch.setattr(evidence, "fetch_historical", boom)
    with caplog.at_level(logging.WARNING):
        found = evidence.headlines("BTC", DATE)
    assert found == {"items": [], "found": False, "reason": "조회_실패"}
    assert _warnings(caplog)


def test_historical_query_uses_an_exclusive_date_window():
    query = evidence.historical_query("BTC", DATE)
    assert "after:2026-03-13" in query and "before:2026-03-16" in query


# --- 입력이 이상해도 호출자까지 예외가 오지 않는다 -------------------------------------

def test_a_malformed_date_is_reported_without_touching_db_or_network(monkeypatch):
    monkeypatch.setattr(news_archive, "lookup", lambda *a, **k: pytest.fail("DB 도 안 읽는다"))
    _no_network(monkeypatch)
    assert evidence.headlines("BTC", "어제") == {"items": [], "found": False, "reason": "조회_실패"}


def test_an_archive_that_raises_is_a_lookup_failure_and_skips_the_network(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(news_archive, "lookup", boom)
    _no_network(monkeypatch)
    with caplog.at_level(logging.WARNING):
        found = evidence.headlines("BTC", DATE)
    assert found == {"items": [], "found": False, "reason": "조회_실패"}
    assert _warnings(caplog)


def test_a_failing_archive_write_is_reported_not_raised(monkeypatch):
    FakeArchive(monkeypatch)
    monkeypatch.setattr(evidence, "fetch_historical", lambda a, d: [_row("기사", NOON)])

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(news_archive, "store", boom)
    assert evidence.headlines("BTC", DATE)["reason"] == "조회_실패"


@pytest.mark.parametrize("asset", ["", "BTC OR ETH", "btc:x", "-BTC", "A" * 40])
def test_an_invalid_asset_never_reaches_the_search_query(monkeypatch, asset):
    with pytest.raises(ValueError):
        evidence.historical_query(asset, DATE)
    FakeArchive(monkeypatch)
    _no_network(monkeypatch)
    assert evidence.headlines(asset, DATE) == {"items": [], "found": False, "reason": "조회_실패"}


def test_a_lowercase_asset_behaves_the_same_on_a_hit_and_on_a_miss(monkeypatch):
    archive = FakeArchive(monkeypatch)
    queries = []

    def fake(query, **kwargs):
        queries.append(query)
        return [_feed_item("그날", "2026-03-14T12:00:00+00:00")]
    monkeypatch.setattr(news, "_fetch_news", fake)

    miss = evidence.headlines(" btc ", DATE)
    assert miss["found"] is True and queries[0].startswith("BTC crypto ")
    assert archive.lookups[0][0] == "BTC"
    assert evidence.headlines("btc", DATE)["found"] is True


def test_the_lookup_window_covers_a_kst_day(monkeypatch):
    archive = FakeArchive(monkeypatch)
    monkeypatch.setattr(evidence, "fetch_historical", lambda asset, date: [])
    evidence.headlines("BTC", DATE)
    _asset, start, end, _limit = archive.lookups[0]
    # KST 2026-03-14 은 UTC 03-13 15:00 ~ 03-14 15:00 이다.
    assert start <= _ms("2026-03-13T15:00:00+00:00")
    assert end >= _ms("2026-03-14T15:00:00+00:00")


# --- 보여 주는 기사: 날짜에 가까운 것 ----------------------------------------------

def test_the_shown_headlines_are_the_ones_closest_to_the_date_oldest_first(monkeypatch):
    offsets = (-36, -30, -20, -11, -6, -2, 1, 5, 9, 14, 23, 33)
    archive = FakeArchive(monkeypatch, [_row(f"h{h:+d}", NOON + h * HOUR) for h in offsets])
    monkeypatch.setattr(evidence, "fetch_historical", lambda *a: pytest.fail("DB 적중"))
    shown = evidence.headlines("BTC", DATE)["items"]
    # 앞에서 5 건을 자르면 전날 새벽(-36, -30 ...)만 보인다 — 가까운 5 건이어야 한다.
    assert [r["title"] for r in shown] == ["h-6", "h-2", "h+1", "h+5", "h+9"]
    assert archive.lookups[0][3] > news_archive.MAX_ROWS, "고르려면 보여 줄 수보다 많이 읽어야 한다"


# --- fetch_historical: 기존 news._fetch_news 를 쓰고 네트워크는 대체한다 -----------------

def test_fetch_historical_harvests_the_full_feed_strictly(monkeypatch):
    calls = []

    def fake(query, **kwargs):
        calls.append((query, kwargs))
        return []
    monkeypatch.setattr(news, "_fetch_news", fake)
    evidence.fetch_historical("BTC", DATE)
    assert evidence.HARVEST_LIMIT == 50
    assert calls[0][1]["limit"] == 50, "기본 limit(8) 로 돌아가면 아카이브가 거의 안 찬다"
    assert calls[0][1]["strict"] is True, "전송 실패가 빈 결과로 둔갑하면 안 된다"


def test_fetch_historical_keeps_every_dated_item_including_the_edge_day(monkeypatch):
    monkeypatch.setattr(news, "_fetch_news", lambda query, **kw: [
        _feed_item("다음날", "2026-03-15T20:00:00+00:00", "https://x.test/next"),
        _feed_item("이른", "2026-03-13T02:00:00+00:00", "https://x.test/early"),
        _feed_item("날짜 없음", None, "https://x.test/none"),
        _feed_item("날짜 깨짐", "not-a-date", "https://x.test/bad"),
    ])
    items = evidence.fetch_historical("BTC", DATE)
    assert [i["title"] for i in items] == ["이른", "다음날"], "오래된 순, 다음날 기사도 남긴다"
    assert items[0] == {"title": "이른", "source": "Decrypt", "url": "https://x.test/early",
                        "published_ms": _ms("2026-03-13T02:00:00+00:00")}


def test_fetch_historical_lets_a_transport_failure_propagate(monkeypatch):
    def fail(query, **kwargs):
        raise news.NewsFetchError("down")
    monkeypatch.setattr(news, "_fetch_news", fail)
    with pytest.raises(news.NewsFetchError):
        evidence.fetch_historical("BTC", DATE)


def test_a_transport_failure_surfaces_as_a_lookup_failure(monkeypatch):
    FakeArchive(monkeypatch)

    def fail(query, **kwargs):
        raise news.NewsFetchError("down")
    monkeypatch.setattr(news, "_fetch_news", fail)
    assert evidence.headlines("BTC", DATE) == {"items": [], "found": False, "reason": "조회_실패"}


def test_an_empty_feed_is_not_found_rather_than_a_failure(monkeypatch):
    FakeArchive(monkeypatch)
    monkeypatch.setattr(news, "_fetch_news", lambda query, **kw: [])
    assert evidence.headlines("BTC", DATE) == {"items": [], "found": False, "reason": "보존_범위_밖"}


@pytest.mark.parametrize("value, expected", [
    ("2026-03-14T01:00:00+00:00", _ms("2026-03-14T01:00:00+00:00")),
    ("2026-03-14T01:00:00Z", _ms("2026-03-14T01:00:00+00:00")),
    ("2026-03-14T10:00:00+09:00", _ms("2026-03-14T01:00:00+00:00")),
    ("2026-03-14T01:00:00", _ms("2026-03-14T01:00:00+00:00")),  # 시간대 없으면 UTC
    ("1970-01-01T00:00:00+00:00", 0),
    ("9999-12-31T23:59:59+00:00", _ms("9999-12-31T23:59:59+00:00")),
    ("0001-01-01T00:00:00+14:00", -62135647200000),  # 아주 먼 과거도 던지지 않는다(거르는 것은 store)
])
def test_published_values_convert_to_utc_milliseconds(value, expected):
    assert evidence._published_ms(value) == expected


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45T00:00:00Z", 12345])
def test_unreadable_or_overflowing_published_values_become_none_without_raising(value):
    assert evidence._published_ms(value) is None


# --- 진짜 news_archive.store 를 거치는 왕복 -----------------------------------------

def test_the_first_lookup_fills_the_real_archive_and_the_second_is_served_from_it(db, monkeypatch):
    monkeypatch.setattr(news, "_fetch_news", lambda query, **kw: [
        _feed_item("전날 기사", "2026-03-13T09:00:00+00:00", "https://x.test/prev"),
        _feed_item("그날 기사", "2026-03-14T11:00:00+00:00", "https://x.test/day"),
        _feed_item("다음날 기사", "2026-03-15T09:00:00+00:00", "https://x.test/next"),
        _feed_item("주소 이상", "2026-03-14T11:30:00+00:00", "ftp://x.test/ftp"),
        _feed_item("범위 밖 날짜", "1970-01-01T00:00:00+00:00", "https://x.test/old"),
        _feed_item("날짜 없음", None, "https://x.test/none"),
    ])
    stored_counts = []
    real_store = news_archive.store

    def spying_store(asset, items):
        stored_counts.append(real_store(asset, items))
        return stored_counts[-1]
    monkeypatch.setattr(news_archive, "store", spying_store)

    first = evidence.headlines("BTC", DATE)

    assert stored_counts == [3], "store 가 실제로 받아들인 것은 날짜·주소가 정상인 3 건뿐"
    assert sorted(r.title for r in _archived(db)) == ["그날 기사", "다음날 기사", "전날 기사"]
    assert first["found"] is True
    # 화면 구간은 검색어와 같은 사흘(전날·당일·다음날)이라 store 가 받은 3 건이 모두 보인다.
    assert [r["title"] for r in first["items"]] == ["전날 기사", "그날 기사", "다음날 기사"], \
        "보이는 것은 아카이브가 가진 것이다 — store 가 거른 것은 안 보인다"

    _no_network(monkeypatch)  # 두 번째는 네트워크 없이 아카이브에서만
    second = evidence.headlines("BTC", DATE)
    assert second == first, "첫 호출과 이후 호출이 같은 것을 보여 준다"

    # 이웃 날짜 조회가 곧바로 적중한다 — 다음날 기사를 버리지 않은 덕이다.
    assert [r["title"] for r in evidence.headlines("BTC", "2026-03-15")["items"]] == \
        ["그날 기사", "다음날 기사"]


def test_a_feed_with_only_the_next_day_is_shown_and_not_refetched(db, monkeypatch):
    fetches = []

    def fake(query, **kwargs):
        fetches.append(query)
        return [_feed_item("다음날 기사", "2026-03-15T09:00:00+00:00", "https://x.test/next")]
    monkeypatch.setattr(news, "_fetch_news", fake)

    first = evidence.headlines("BTC", DATE)
    assert first["found"] is True and [r["title"] for r in first["items"]] == ["다음날 기사"]

    _no_network(monkeypatch)  # 화면 구간이 검색 구간과 같으면 아카이브만으로 두 번째가 된다
    assert evidence.headlines("BTC", DATE) == first
    assert len(fetches) == 1


def test_the_display_window_matches_the_query_window():
    start, end = evidence._day_bounds(DATE)
    assert start == _ms("2026-03-13T00:00:00+00:00")  # after:2026-03-13 (포함)
    assert end + 1 == _ms("2026-03-16T00:00:00+00:00")  # before:2026-03-16 (제외)
