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


def item(offset_days, title):
    return {"published_ms": BASE + offset_days * DAY, "title": title,
            "source": "CoinDesk", "url": f"https://example.test/{offset_days}"}


def window(asset="BTC", **kwargs):
    return news_archive.lookup(asset, start_ms=BASE - DAY, end_ms=BASE + DAY, **kwargs)


def test_store_then_lookup_returns_items_inside_the_window(db):
    assert news_archive.store("BTC", [item(0, "첫날"), item(5, "닷새 뒤")]) == 2
    found = news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY)
    assert [row["title"] for row in found] == ["첫날"]


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


def test_asset_is_normalized_so_the_same_coin_shares_rows(db):
    news_archive.store(" btc ", [item(0, "소문자로 넣음")])
    assert [row["title"] for row in window("BTC")] == ["소문자로 넣음"]
    assert news_archive.store("BTC", [item(0, "같은 주소")]) == 0


def test_lookup_caps_the_number_of_rows(db):
    news_archive.store("BTC", [item(0, f"기사 {i}") | {"url": f"https://example.test/a{i}"}
                               for i in range(20)])
    assert len(window(limit=5)) == 5


def test_items_without_a_title_or_url_are_skipped(db):
    assert news_archive.store("BTC", [{"published_ms": BASE, "title": "", "source": "", "url": ""}]) == 0


def test_reversed_window_returns_nothing(db):
    news_archive.store("BTC", [item(0, "첫날")])
    assert news_archive.lookup("BTC", start_ms=BASE + DAY, end_ms=BASE - DAY) == []


def test_stored_strings_fit_the_column_lengths(db):
    long = {"published_ms": BASE, "title": "가" * 900, "source": "s" * 400,
            "url": "https://example.test/" + "u" * 2000}
    assert news_archive.store("B" * 40, [long]) == 1
    with Session(db) as session:
        row = session.exec(select(NewsHeadlineArchive)).one()
    assert len(row.title) == 500 and len(row.source) == 120
    assert len(row.url) == 1000 and len(row.asset_symbol) == 20


def test_many_rows_are_stored_in_one_call(db):
    items = [{"published_ms": BASE, "title": f"기사 {i}", "source": "x",
              "url": f"https://example.test/m{i}"} for i in range(450)]
    assert news_archive.store("BTC", items) == 450


def test_database_failure_is_logged_and_never_raised(monkeypatch, caplog):
    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(news_archive, "get_session", broken)
    with caplog.at_level(logging.WARNING, logger=news_archive.logger.name):
        assert news_archive.store("BTC", [item(0, "첫날")]) == 0
        assert news_archive.lookup("BTC", start_ms=BASE - DAY, end_ms=BASE + DAY) == []
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 2
