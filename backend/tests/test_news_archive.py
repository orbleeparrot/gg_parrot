"""근거용 경량 아카이브 — 제목·날짜·출처·URL 만 무기한 보관한다(본문은 기존대로 30 일)."""
import logging

import pytest

pytest.importorskip("sqlmodel")

from sqlmodel import Session, SQLModel, create_engine, select

from app import news_archive
from app.db import NewsHeadlineArchive

DAY = 86_400_000
BASE = 1_800_000_000_000


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'archive.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(news_archive, "get_session", lambda: Session(engine))
    yield engine
    engine.dispose()


def item(offset_days, title="기사", url=None, **extra):
    return {"published_ms": BASE + offset_days * DAY, "title": title, "source": "CoinDesk",
            "url": url or f"https://example.test/{offset_days}", **extra}


def window(asset="BTC", **kwargs):
    return news_archive.lookup(asset, start_ms=BASE - DAY, end_ms=BASE + DAY, **kwargs)


def rows(engine):
    with Session(engine) as session:
        return session.exec(select(NewsHeadlineArchive)).all()


def warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# ---- 기본 적재·조회 ----------------------------------------------------------

def test_store_then_lookup_returns_items_inside_the_window(db):
    assert news_archive.store("BTC", [item(0, "첫날"), item(5, "닷새 뒤")]) == 2
    assert [row["title"] for row in window()] == ["첫날"]


def test_storing_the_same_url_twice_does_not_duplicate(db):
    news_archive.store("BTC", [item(0, "첫날")])
    news_archive.store("BTC", [item(0, "첫날")])
    assert len(window()) == 1


def test_store_returns_only_the_rows_actually_inserted(db):
    assert news_archive.store("BTC", [item(0, "첫날"), item(0, "같은 주소"), item(1, "다음날")]) == 2
    assert news_archive.store("BTC", [item(0, "첫날"), item(1, "다음날")]) == 0
    assert news_archive.store("BTC", [item(0, "첫날"), item(2, "새 기사")]) == 1


def test_lookup_is_scoped_to_the_asset(db):
    news_archive.store("BTC", [item(0, "비트 기사")])
    assert news_archive.lookup("ETH", start_ms=BASE - DAY, end_ms=BASE + DAY) == []


def test_same_url_under_two_coins_is_two_rows(db):
    assert news_archive.store("BTC", [item(0, "둘 다 나온 기사")]) == 1
    assert news_archive.store("ETH", [item(0, "둘 다 나온 기사")]) == 1
    assert len(window("BTC")) == 1 and len(window("ETH")) == 1


def test_asset_case_and_padding_are_normalized(db):
    news_archive.store(" btc ", [item(0, "소문자로 넣음")])
    assert [row["title"] for row in window("BTC")] == ["소문자로 넣음"]
    assert news_archive.store("BTC", [item(0, "같은 주소")]) == 0


@pytest.mark.parametrize("asset", ["", "  ", "B" * 40, "KRW-BTC", "BTC USDT", "BTC/USDT", "비트", None])
def test_assets_that_are_not_a_plain_coin_symbol_are_rejected(db, asset):
    assert news_archive.store(asset, [item(0)]) == 0
    assert rows(db) == []


def test_lookup_caps_the_number_of_rows(db):
    news_archive.store("BTC", [item(0, f"기사 {i}", url=f"https://example.test/a{i}") for i in range(20)])
    assert len(window(limit=5)) == 5


def test_items_without_a_title_or_url_are_skipped(db):
    assert news_archive.store("BTC", [{"published_ms": BASE, "title": "", "source": "", "url": ""}]) == 0


def test_long_title_and_source_are_truncated_to_their_columns(db):
    long = item(0, "가" * 900, source="s" * 400)
    assert news_archive.store("BTC", [long]) == 1
    (row,) = rows(db)
    assert len(row.title) == 500 and len(row.source) == 120


def test_many_rows_are_stored_in_one_call(db):
    items = [item(0, f"기사 {i}", url=f"https://example.test/m{i}") for i in range(450)]
    assert news_archive.store("BTC", items) == 450


# ---- 조회 구간·정렬 ----------------------------------------------------------

def test_reversed_window_returns_nothing(db):
    news_archive.store("BTC", [item(0, "첫날")])
    assert news_archive.lookup("BTC", start_ms=BASE + DAY, end_ms=BASE - DAY) == []


def test_lookup_excludes_rows_before_the_window_start(db):
    news_archive.store("BTC", [item(-5, "닷새 전"), item(0, "첫날")])
    assert [row["title"] for row in window()] == ["첫날"]


def test_lookup_window_ends_are_inclusive(db):
    news_archive.store("BTC", [item(-1, "시작 경계"), item(0, "가운데"), item(1, "끝 경계"),
                               item(2, "끝 밖"), item(-2, "시작 밖")])
    assert [row["title"] for row in window()] == ["시작 경계", "가운데", "끝 경계"]


def test_lookup_returns_oldest_first_and_breaks_ties_by_key(db):
    same = [item(0, f"동시 {i}", url=f"https://example.test/tie{i}") for i in range(6)]
    news_archive.store("BTC", [item(1, "나중"), *same, item(-1, "먼저")])
    found = window(limit=20)
    assert found[0]["title"] == "먼저" and found[-1]["title"] == "나중"
    assert window(limit=20) == found
    expected = [r.url for r in sorted((r for r in rows(db) if r.published_ms == BASE),
                                      key=lambda r: r.archive_key)]
    assert [r["url"] for r in found[1:-1]] == expected


# ---- 날짜 -------------------------------------------------------------------

@pytest.mark.parametrize("published", [0, None, -5, 1_800_000_000, 5, 10**30, float("inf"),
                                       float("nan"), "abc", True])
def test_items_without_a_plausible_millisecond_date_are_skipped(db, published):
    bad = {**item(0), "published_ms": published}
    assert news_archive.store("BTC", [bad]) == 0
    assert rows(db) == []


# ---- URL --------------------------------------------------------------------

def test_equivalent_urls_dedup_to_one_row_and_the_original_url_is_kept(db):
    variants = ["https://Example.test/news/a?utm_source=x&id=7#top",
                "http://example.test/news/a/?id=7&fbclid=abc",
                "https://example.test:443/news/a?gclid=1&id=7",
                "HTTPS://EXAMPLE.TEST/news/a?id=7&utm_medium=mail"]
    assert news_archive.store("BTC", [item(0, url=v) for v in variants]) == 1
    assert news_archive.store("BTC", [item(0, url=variants[1])]) == 0
    (row,) = rows(db)
    assert row.url == variants[0]


def test_urls_that_really_differ_stay_separate(db):
    urls = ["https://example.test/a?id=1", "https://example.test/a?id=2", "https://example.test/b"]
    assert news_archive.store("BTC", [item(0, url=u) for u in urls]) == 3


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>1</script>",
                                 "ftp://example.test/a", "//example.test/a", "example.test/a",
                                 "https:///nohost", "https://example.test/a b", "https://example.test/a\nb",
                                 "https://example.test/\x00"])
def test_urls_that_are_not_plain_http_links_are_rejected(db, url):
    assert news_archive.store("BTC", [item(0, url=url)]) == 0
    assert rows(db) == []


def test_over_long_urls_are_skipped_not_truncated(db):
    too_long = "https://example.test/" + "u" * 2000
    assert news_archive.store("BTC", [item(0, url=too_long)]) == 0
    assert rows(db) == []


def test_long_urls_sharing_a_prefix_are_two_rows_stored_whole(db):
    prefix = "https://example.test/" + "p" * 1200
    urls = [prefix + "/one", prefix + "/two"]
    assert news_archive.store("BTC", [item(0, url=u) for u in urls]) == 2
    assert sorted(row.url for row in rows(db)) == sorted(urls)


# ---- 실패 경로: 호출한 쪽으로 새지 않는다 ------------------------------------

def test_database_failure_is_logged_and_never_raised(monkeypatch, caplog):
    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(news_archive, "get_session", broken)
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", [item(0, "첫날")]) == 0
        assert news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY) == []
    assert len(warnings(caplog)) == 2


def test_missing_table_is_logged_and_never_raised(tmp_path, monkeypatch, caplog):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(news_archive, "get_session", lambda: Session(engine))
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", [item(0)]) == 0
        assert news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY) == []
    assert len(warnings(caplog)) == 2
    engine.dispose()


def test_failure_after_the_session_is_open_is_logged_and_never_raised(db, monkeypatch, caplog):
    class Failing(Session):
        def exec(self, *args, **kwargs):
            raise RuntimeError("connection lost mid-query")

    monkeypatch.setattr(news_archive, "get_session", lambda: Failing(db))
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", [item(0)]) == 0
        assert news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY) == []
    assert len(warnings(caplog)) == 2


def test_one_failing_chunk_does_not_lose_the_others(db, monkeypatch, caplog):
    calls = {"n": 0}

    class FirstChunkFails(Session):
        def exec(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first chunk rejected")
            return super().exec(*args, **kwargs)

    monkeypatch.setattr(news_archive, "get_session", lambda: FirstChunkFails(db))
    items = [item(0, f"기사 {i}", url=f"https://example.test/c{i}") for i in range(150)]
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", items) == 50
    assert len(rows(db)) == 50 and warnings(caplog)


def test_failure_after_a_committed_chunk_reports_what_was_really_stored(db, monkeypatch, caplog):
    calls = {"n": 0}

    class SecondChunkAndRollbackFail(Session):
        def exec(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("second chunk rejected")
            return super().exec(*args, **kwargs)

        def rollback(self):
            raise RuntimeError("rollback failed too")

    monkeypatch.setattr(news_archive, "get_session", lambda: SecondChunkAndRollbackFail(db))
    items = [item(0, f"기사 {i}", url=f"https://example.test/r{i}") for i in range(150)]
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", items) == 100
    assert len(rows(db)) == 100


def test_urls_differing_only_in_a_non_utf8_percent_escape_stay_separate(db):
    urls = ["https://example.test/a?q=%ff", "https://example.test/a?q=%fe",
            "https://example.test/k?q=%B0%A1", "https://example.test/k?q=%B0%A2"]
    assert news_archive.store("BTC", [item(0, url=u) for u in urls]) == 4
    assert sorted(row.url for row in rows(db)) == sorted(urls)


@pytest.mark.parametrize("bad", [None, "x", 5, [], ["x"], object()])
def test_a_malformed_item_is_skipped_and_the_rest_are_kept(db, bad):
    assert news_archive.store("BTC", [bad, item(0, "멀쩡한 기사")]) == 1


@pytest.mark.parametrize("items", [5, None, "text", object()])
def test_a_malformed_item_list_never_raises(db, items):
    assert news_archive.store("BTC", items) == 0


def test_one_bad_row_does_not_lose_the_good_ones(db):
    batch = [item(0, "NUL 제목\x00"),
             item(1, "정상 기사 하나"),
             {**item(2, "너무 큰 날짜"), "published_ms": 10**30},
             {**item(3, "숫자 아님"), "published_ms": float("inf")},
             {**item(4, "출처에 NUL"), "source": "a\x00b"},
             item(5, "짝 없는 서로게이트\ud800"),
             item(6, "정상 기사 둘")]
    assert news_archive.store("BTC", batch) == 2
    assert sorted(row.title for row in rows(db)) == ["정상 기사 둘", "정상 기사 하나"]


@pytest.mark.parametrize("bounds", [(float("inf"), BASE), (BASE, float("inf")), (float("nan"), BASE),
                                    (None, BASE), ("a", "b")])
def test_lookup_with_odd_bounds_returns_an_empty_list(db, bounds):
    news_archive.store("BTC", [item(0, "첫날")])
    assert news_archive.lookup("BTC", start_ms=bounds[0], end_ms=bounds[1]) == []


def test_lookup_with_huge_but_finite_bounds_still_finds_rows(db):
    news_archive.store("BTC", [item(0, "첫날")])
    assert len(news_archive.lookup("BTC", start_ms=-10**30, end_ms=10**30)) == 1


def test_lookup_with_a_bad_asset_returns_nothing(db):
    news_archive.store("BTC", [item(0, "첫날")])
    assert news_archive.lookup("KRW-BTC", start_ms=BASE - DAY, end_ms=BASE + DAY) == []
