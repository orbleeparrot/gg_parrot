import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from app import db as database, exchange_notices as notices, feed_index, public_news
from app.main import app

# 2026-10-20 운영에서 받은 빗썸 공식 /v1/notices 응답 모양 그대로.
SAMPLE = [
    {"categories": ["마켓 추가"], "title": "콘크리트(CT) 원화 마켓 추가",
     "pc_url": "https://feed.bithumb.com/notice/1655276", "published_at": "2026-10-19 16:58:27", "modified_at": "2026-10-19 17:35:25"},
    {"categories": ["입출금"], "title": "셀레스티아(TIA), 만타(MANTA) 네트워크 입출금 일시 중지 안내 (10/20 오전 9시~)",
     "pc_url": "https://feed.bithumb.com/notice/1655275", "published_at": "2026-10-19 16:30:00", "modified_at": "2026-10-19 15:56:04"},
    {"categories": ["점검"], "title": "빗썸 고객센터 상담 서비스 일시 중단 안내",
     "pc_url": "https://feed.bithumb.com/notice/1655273", "published_at": "2026-10-19 16:00:00", "modified_at": "2026-10-19 15:43:18"},
]
NOW = notices._kst_ms("2026-10-20 10:00:00")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with database.get_session() as db:
        db.exec(delete(notices.ExchangeNotice))
        db.exec(delete(notices.ExchangeNoticeState))
        db.exec(delete(feed_index.FeedAsset).where(feed_index.FeedAsset.feed == notices.FEED))
        db.exec(delete(public_news.PublicNewsLease).where(public_news.PublicNewsLease.scope == notices.SCOPE))
        db.commit()
    # 읽기는 '최근 30일' 을 지금 시각으로 자르므로 저장 시각을 지금 근처로 맞춘다.
    monkeypatch.setattr(notices, "RETENTION_SECONDS", 10 * 365 * 86400)


def test_parse_keeps_official_fields_and_links_coins_by_bracketed_ticker():
    items = notices.parse_bithumb(SAMPLE, now_ms=NOW)
    assert [item["id"] for item in items] == ["bithumb:1655276", "bithumb:1655275", "bithumb:1655273"]
    assert [item["asset_symbols"] for item in items] == [["CT"], ["MANTA", "TIA"], []]
    assert items[0]["categories"] == ["마켓 추가"] and items[0]["url"] == "https://feed.bithumb.com/notice/1655276"
    assert items[0]["published"] == "2026-10-19T07:58:27+00:00"  # KST 16:58:27


@pytest.mark.parametrize("title, assets", [
    ("콘크리트(CT) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", ["CT"]),
    ("원화(KRW) 입금 점검", []),
    ("비트코인(BTC) 입출금 재개", ["BTC"]),
    ("서버 점검 안내 (완료)", []),
])
def test_ticker_extraction_ignores_market_lists_and_plain_words(title, assets):
    assert notices.notice_assets(title) == assets


def test_parse_drops_foreign_urls_bad_dates_and_rejects_non_lists():
    rows = [dict(SAMPLE[0], pc_url="https://evil.example/notice/1"), dict(SAMPLE[1], published_at="어제"),
            dict(SAMPLE[2], published_at="2027-01-01 00:00:00"), "x"]
    assert notices.parse_bithumb(rows, now_ms=NOW) == []
    with pytest.raises(ValueError):
        notices.parse_bithumb({"error": "x"}, now_ms=NOW)


def _client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(notices, "get_http_client", lambda: client)


def test_fetch_follows_only_the_documented_redirect_to_the_notice_server(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "api.bithumb.com":
            return httpx.Response(302, headers={"location": "https://feed-api.bithumb.com:443/v1/notices"})
        return httpx.Response(200, json=SAMPLE)
    _client(monkeypatch, handler)
    assert notices.fetch_bithumb() == SAMPLE
    assert seen == [notices.BITHUMB_URL, "https://feed-api.bithumb.com/v1/notices?count=20"]

    _client(monkeypatch, lambda request: httpx.Response(302, headers={"location": "https://evil.example/v1/notices"}))
    with pytest.raises(ValueError):
        notices.fetch_bithumb()
    _client(monkeypatch, lambda request: httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    with pytest.raises(ValueError):
        notices.fetch_bithumb()


def test_collect_stores_links_and_pages_newest_first(monkeypatch):
    monkeypatch.setattr(notices, "fetch_bithumb", lambda: SAMPLE)
    assert notices.collect_once(now_ms=NOW)["status"] == "ready"
    # 티커 없는 공지(고객센터 점검)는 저장만 하고 소식 칸엔 내지 않는다(시안 B).
    page = notices.get_exchange_notices("bithumb", limit=1)
    assert [item["title"][:5] for item in page["items"]] == ["콘크리트("] and page["total"] == 2
    assert page["collection"]["status"] == "ready" and page["next_cursor"]
    rest = notices.get_exchange_notices("bithumb", limit=1, cursor=feed_index.decode_cursor(page["next_cursor"]))
    assert [item["id"] for item in rest["items"]] == ["bithumb:1655275"] and rest["next_cursor"] is None and rest["total"] is None
    with database.get_session() as db:
        assert db.get(notices.ExchangeNotice, "bithumb:1655273") is not None
    assert [item["id"] for item in notices.get_exchange_notices("bithumb", q="입출금")["items"]] == ["bithumb:1655275"]
    assert [item["id"] for item in notices.get_exchange_notices("bithumb", "TIA")["items"]] == ["bithumb:1655275"]
    item = page["items"][0]
    assert item["content_type"] == "official" and item["categories"] == ["마켓 추가"] and item["asset_symbols"] == ["CT"]
    # 같은 공지를 다시 받아도 한 줄 — 제목이 바뀌면 고쳐 쓴다.
    changed = [dict(SAMPLE[0], title="콘크리트(CT) 원화 마켓 추가 (시간 변경)")] + SAMPLE[1:]
    monkeypatch.setattr(notices, "fetch_bithumb", lambda: changed)
    with database.get_session() as db:
        db.exec(delete(public_news.PublicNewsLease).where(public_news.PublicNewsLease.scope == notices.SCOPE))
        db.commit()
    notices.collect_once(now_ms=NOW)
    with database.get_session() as db:
        rows = db.exec(select(notices.ExchangeNotice)).all()
    assert len(rows) == 3 and any(row.title.endswith("(시간 변경)") for row in rows)


def test_failed_fetch_keeps_stored_notices_and_backs_off(monkeypatch):
    monkeypatch.setattr(notices, "fetch_bithumb", lambda: SAMPLE)
    notices.collect_once(now_ms=NOW)
    with database.get_session() as db:
        db.exec(delete(public_news.PublicNewsLease).where(public_news.PublicNewsLease.scope == notices.SCOPE))
        db.commit()

    def boom():
        raise httpx.ConnectError("down")
    monkeypatch.setattr(notices, "fetch_bithumb", boom)
    assert notices.collect_once(now_ms=NOW)["status"] == "error"
    page = notices.get_exchange_notices("bithumb")
    assert len(page["items"]) == 2 and page["collection"]["status"] == "error"
    with database.get_session() as db:
        lease = db.get(public_news.PublicNewsLease, notices.SCOPE)
    assert lease.next_run_ms >= int(time.time() * 1000) + 100_000  # 60초 × 2


def test_trends_route_serves_notices_for_bithumb_and_marks_upbit_not_connected(monkeypatch):
    monkeypatch.setattr(notices, "fetch_bithumb", lambda: SAMPLE)
    notices.collect_once(now_ms=NOW)
    client = TestClient(app)
    data = client.get("/api/trends?region=kr&exchange=bithumb&sections=notices&limit=20").json()
    assert [item["id"] for item in data["notices"]["items"]][:1] == ["bithumb:1655276"]
    assert data["rankings"] is None and data["news"] is None and data["community"] is None
    picked = client.get("/api/trends?region=kr&exchange=bithumb&sections=notices&symbol=KRW-TIA").json()
    assert [item["id"] for item in picked["notices"]["items"]] == ["bithumb:1655275"]
    upbit = client.get("/api/trends?region=kr&exchange=upbit&sections=notices").json()
    assert upbit["notices"]["items"] == [] and upbit["notices"]["collection"]["status"] == "not_configured"
    assert client.get("/api/trends?region=kr&sections=rumors").status_code == 422
    assert json.dumps(data, ensure_ascii=False).count("feed.bithumb.com/notice/") == 2 and data["notices"]["total"] == 2
