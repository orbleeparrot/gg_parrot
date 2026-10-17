"""Domestic community reads never collect; source and asset boundaries stay narrow."""
from datetime import datetime, timezone
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import delete, select

from app import feed_index
from app import db as database, domestic_community as community, public_news


@pytest.fixture(autouse=True)
def isolated_domestic(monkeypatch):
    # Legacy DC-specific contracts stay isolated from unrelated source HTTP.
    monkeypatch.setattr(community, "SOURCES", community.SOURCES[:1])
    with database.get_session() as db:
        db.exec(delete(community.DomesticCommunityItem))
        db.exec(delete(community.DomesticCommunityState))
        db.exec(delete(community.FeedAsset))
        db.exec(delete(public_news.PublicNewsLease).where(public_news.PublicNewsLease.scope == community.SCOPE))
        db.commit()
    community.clear_caches()
    monkeypatch.setenv("DOMESTIC_COMMUNITY_ENABLED", "false")


def listings(now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return {exchange: {"fetched_ms": now_ms, "items": [
        {"symbol": "KRW-BTC", "base": "BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
        {"symbol": "KRW-ETH", "base": "ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
        {"symbol": "KRW-SC", "base": "SC", "korean_name": "시아코인", "english_name": "Siacoin"},
        {"symbol": "KRW-ICP", "base": "ICP", "korean_name": "인터넷컴퓨터", "english_name": "Internet Computer"},
        {"symbol": "KRW-S", "base": "S", "korean_name": "소닉", "english_name": "Sonic"},
    ][:2 if exchange == "upbit" else 5]} for exchange in ("upbit", "bithumb")}


def row(title="비트코인 가격 이야기", number="123", date=None, url=None):
    date = date or datetime.now(community._KST).strftime("%Y-%m-%d %H:%M:%S")
    url = url or f"/board/view/?id=bitcoins_new1&no={number}&page=1"
    return f'''<tr class="ub-content us-post" data-no="{number}">
        <td class="gall_num">{number}</td>
        <td class="gall_tit ub-word"><a href="{url}"><em></em>{title}</a>
        <a class="reply_numbox" href="{url}">[234]</a></td>
        <td class="gall_writer" data-ip="127.0" data-nick="private">private</td>
        <td class="gall_date" title="{date}">01:12</td></tr>'''


def stored(*, status="ready", now_ms=None, sources=None, items=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with database.get_session() as db:
        db.merge(community.DomesticCommunityState(status=status, last_success_ms=now_ms,
               last_attempt_ms=now_ms, listings_json=json.dumps(listings(now_ms)),
               sources_json=json.dumps(sources or [{"id": "dcinside_bitcoin", "status": "ready"}])))
        for item in items or []:
            db.add(community.DomesticCommunityItem(id=item.get("id", "btc"), title=item.get("title", "비트코인"),
                   url="https://gall.dcinside.com/board/view/?id=bitcoins_new1&no=123", source=community.SOURCE,
                   published=community._iso(now_ms), published_ms=now_ms, collected_ms=now_ms,
                   asset_symbols_json=json.dumps(item.get("assets", ["BTC"]))))
        db.flush()
        community.reconcile_assets(db)  # 개편 전에 저장된 글처럼 — 수집기가 코인 연결을 채운다
        db.commit()
    community.clear_caches()


def forbid(*args, **kwargs):
    pytest.fail("HTTP readers must never fetch or schedule collection")


def test_cold_and_ready_reads_are_select_only(monkeypatch):
    monkeypatch.setattr(community, "fetch_listing", forbid)
    monkeypatch.setattr(community.krw, "list_symbols", forbid)
    monkeypatch.setattr(community, "collect_once", forbid)
    cold = community.get_domestic_community("upbit")
    assert cold["items"] == [] and cold["collection"]["status"] == "pending"
    stored(items=[{}])
    statements = []
    def capture(_conn, _cursor, sql, *_args):
        statements.append(sql)
    event.listen(database._engine, "before_cursor_execute", capture)
    try:
        result = community.get_domestic_community("upbit", "KRW-BTC")
    finally:
        event.remove(database._engine, "before_cursor_execute", capture)
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    item = result["items"][0]
    assert item["content_type"] == "community" and item["language"] == "ko" and item["region"] == "kr"
    assert item["excerpt"] == "" and item["asset_symbols"] == ["BTC"]
    assert result["symbol"] == "KRW-BTC" and result["refresh_seconds"] == 60
    assert "author" not in item and "private" not in json.dumps(result)


def test_parser_real_source_shape_sanitization_timestamp_and_dedup():
    html = row("<b>비트코인</b> &amp; 이더리움 가격<script>ignore()</script>") + row("비트코인 중복", url="/board/view/?no=123&id=bitcoins_new1&page=2")
    html += row("비트코인 공지", number="공지") + row("비트코인 광고", number="AD")
    items, stats = community.parse_listing(html, listings())
    assert stats["normal_rows"] == 2 and stats["duplicates"] == 1
    assert items[0]["title"] == "비트코인 & 이더리움 가격"
    assert items[0]["asset_symbols"] == ["BTC", "ETH"]
    assert items[0]["url"].endswith("id=bitcoins_new1&no=123")
    assert datetime.fromisoformat(items[0]["published"]).utcoffset().total_seconds() == 0
    assert abs(items[0]["published_ms"] - int(time.time() * 1000)) < 2000
    assert "private" not in json.dumps(items)


@pytest.mark.parametrize("title", ["비트맵 이미지 편집 이야기", "우리 지역 도지사 이야기", "icp sc 클라이언트 이야기", "ICP 검사 결과 이야기", "ICP-OES 측정 장비", "S class 자동차", "SCAM 가격 이야기", "BTCUSDT 봇 오류", "그냥 점심 이야기"])
def test_conservative_entity_match_drops_noise(title):
    items, stats = community.parse_listing(row(title), listings())
    assert items == [] and stats["irrelevant"] == 1


@pytest.mark.parametrize("title,asset", [("KRW-SC 가격 이야기", "SC"), ("$S 가격 이야기", "S"), ("ICP 상승 이유", "ICP"), ("시아코인 가격 이야기", "SC"), ("Bitcoin 전망 이야기", "BTC")])
def test_explicit_entities_are_matched(title, asset):
    items, _ = community.parse_listing(row(title), listings())
    assert items[0]["asset_symbols"] == [asset]


@pytest.mark.parametrize("url", ["https://evil.test/board/view/?id=bitcoins_new1&no=123", "http://gall.dcinside.com/board/view/?id=bitcoins_new1&no=123", "//127.0.0.1/board/view/?id=bitcoins_new1&no=123", "/board/view/?id=other&no=123", "javascript:alert(1)", "/board/view/?id=bitcoins_new1&no=abc"])
def test_post_url_allowlist(url):
    assert community.canonical_post_url(url) is None


def test_empty_relevant_feed_is_ready_but_broken_html_errors():
    items, stats = community.parse_listing(row("오늘 점심 이야기"), listings())
    assert items == [] and stats["normal_rows"] == 1
    with pytest.raises(ValueError, match="no recognized"):
        community.parse_listing("<html>captcha or changed layout</html>", listings())


def test_invalid_old_and_future_dates_are_dropped():
    now = datetime.now(timezone.utc)
    html = row(date="invalid") + row(number="124", date="2000-01-01 00:00:00") + row(number="125", date="2099-01-01 00:00:00")
    items, stats = community.parse_listing(html, listings(), now_ms=int(now.timestamp() * 1000))
    assert items == [] and stats["invalid_date"] == 3


def test_exchange_listing_filters_and_unlisted_symbol_validation():
    stored(items=[{"id": "sc", "assets": ["SC"]}, {"id": "mixed", "assets": ["ETH", "SC"]}])
    upbit = community.get_domestic_community("upbit")
    assert [i["id"] for i in upbit["items"]] == ["mixed"]
    assert upbit["items"][0]["asset_symbols"] == ["ETH"]
    assert len(community.get_domestic_community("bithumb")["items"]) == 2
    assert len(community.get_domestic_community("bithumb", "KRW-SC")["items"]) == 2
    with pytest.raises(ValueError, match="상장"):
        community.get_domestic_community("upbit", "KRW-SC")
    with pytest.raises(ValueError):
        community.get_domestic_community("binance")
    with pytest.raises(ValueError):
        community.get_domestic_community("upbit", "BTCUSDT")


def test_stale_source_preserves_recent_items_but_expired_listings_hide_them():
    now = int(time.time() * 1000)
    stored(now_ms=now, items=[{}])
    result = community.get_domestic_community("upbit", now_ms=now + 181_000)
    assert result["items"] and result["stale"] and result["collection"]["status"] == "stale"
    expired = community.get_domestic_community("upbit", now_ms=now + 301_000)
    assert expired["items"] == [] and expired["collection"]["status"] == "stale"


def test_source_failure_is_error_and_partial_exchange_failure_is_stale():
    stored(status="error", items=[{}])
    assert community.get_domestic_community("upbit")["collection"]["status"] == "error"
    with database.get_session() as db:
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        state.status = "ready"
        state.sources_json = json.dumps([{"id": "upbit", "status": "error"}])
        db.add(state)
        db.commit()
    community.clear_caches()
    assert community.get_domestic_community("upbit")["collection"]["status"] == "stale"
    assert community.get_domestic_community("bithumb")["collection"]["status"] == "ready"


def mock_collection(monkeypatch, html=None):
    monkeypatch.setattr(community, "fetch_listing", lambda: row() if html is None else html)
    monkeypatch.setattr(community.krw, "list_symbols", lambda ex: {
        "items": listings()[ex]["items"], "fetched_at": time.time()})


def unlock():
    with database.get_session() as db:
        lease = db.get(public_news.PublicNewsLease, community.SCOPE)
        lease.next_run_ms = 0
        db.add(lease)
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        if state:
            sources = json.loads(state.sources_json)
            for source in sources:
                source["next_attempt_ms"] = 0
            state.sources_json = json.dumps(sources)
            db.add(state)
        db.commit()


def test_collection_shared_lease_idempotence_and_retention(monkeypatch):
    mock_collection(monkeypatch)
    assert community.get_domestic_community("upbit")["items"] == []
    assert community.collect_once()["status"] == "ready"
    assert len(community.get_domestic_community("upbit")["items"]) == 1
    monkeypatch.setattr(community, "fetch_listing", forbid)
    assert community.collect_once()["status"] == "skipped"
    unlock()
    mock_collection(monkeypatch)
    now = int(time.time() * 1000)
    with database.get_session() as db:
        db.add(community.DomesticCommunityItem(id="expired", title="비트코인", url="old", published="2000", published_ms=1, collected_ms=1))
        db.commit()
    monkeypatch.setattr(community, "MAX_ITEMS", 1)
    monkeypatch.setattr(community, "fetch_listing", lambda: row() + row(number="124") + row(number="125"))
    community.collect_once()
    with database.get_session() as db:
        rows = db.exec(select(community.DomesticCommunityItem)).all()
        assert len(rows) == 1 and rows[0].id != "expired"
        lease = db.get(public_news.PublicNewsLease, community.SCOPE)
        assert now + 59_000 <= lease.next_run_ms <= int(time.time() * 1000) + 61_000


def test_failed_collection_has_bounded_backoff_and_keeps_last_success(monkeypatch):
    mock_collection(monkeypatch)
    community.collect_once()
    before = community.get_domestic_community("upbit")["collection"]["last_success_at"]
    unlock()
    def blocked():
        raise httpx.HTTPStatusError("blocked", request=httpx.Request("GET", community.LIST_URL), response=httpx.Response(403))
    monkeypatch.setattr(community, "fetch_listing", blocked)
    result = community.collect_once()
    assert result["status"] == "error" and result["sources"][-1]["error"] == "HTTPStatusError"
    result = community.get_domestic_community("upbit")
    assert result["items"] and result["collection"]["last_success_at"] == before
    with database.get_session() as db:
        lease = db.get(public_news.PublicNewsLease, community.SCOPE)
        assert 59_000 <= lease.next_run_ms - int(time.time() * 1000) <= 61_000
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        source = next(s for s in json.loads(state.sources_json) if s["id"] == "dcinside_bitcoin")
        assert 119_000 <= source["next_attempt_ms"] - int(time.time() * 1000) <= 121_000


def test_robots_deny_does_not_fetch_listing(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return "User-agent: *\nDisallow: /board/"
    monkeypatch.setattr(community, "_robots_cache", None)
    monkeypatch.setattr(community, "_fetch_text", fetch)
    with pytest.raises(ValueError, match="robots"):
        community.fetch_listing()
    assert calls == [community.ROBOTS_URL]


def test_disabled_runtime_does_not_spawn(monkeypatch):
    monkeypatch.setattr(community, "_runtime", None)
    community.start_runtime()
    assert community._runtime is None


def test_reads_select_one_page_in_sql_and_share_the_state(monkeypatch):
    # 2026-10-18: 예전엔 요청마다 최대 2,000건 전체를 메모리에 만들고 잘랐다 — 이제 DB 가 한 쪽(limit+1)만 준다.
    stored(items=[{"id": f"p{n}", "title": f"비트코인 이야기 {n}"} for n in range(30)])
    states = []
    actual = community._read_state
    monkeypatch.setattr(community, "_read_state", lambda: states.append(1) or actual())
    statements = []
    def capture(_conn, _cursor, sql, *_args):
        if "domesticcommunityitem" in sql and sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)
    event.listen(database._engine, "before_cursor_execute", capture)
    try:
        for _ in range(5):
            page = community.get_domestic_community("upbit", limit=10)
            community.get_domestic_community("bithumb", "KRW-BTC", limit=10)
    finally:
        event.remove(database._engine, "before_cursor_execute", capture)
    assert len(page["items"]) == 10 and page["total"] == 30 and page["next_cursor"]
    assert len(states) == 1  # 수집 상태(상장 목록)는 한 번 읽어 나눠 쓴다
    item_reads = [sql for sql in statements if "count(" not in sql.lower()]
    assert len(item_reads) == 2  # 같은 쪽은 캐시 — 거래소 · 코인 조합마다 한 번
    assert all("LIMIT" in sql.upper() for sql in item_reads)


@pytest.mark.parametrize("status,retry,expected", [("ready", 60, 60), ("skipped", 60, 60), ("error", 900, 60)])
def test_runtime_keeps_catalog_cadence_during_source_backoff(monkeypatch, status, retry, expected):
    runtime = community._Runtime()
    waits = []
    def wait(seconds):
        waits.append(seconds)
        runtime.stop.set()
    monkeypatch.setattr(runtime.stop, "wait", wait)
    monkeypatch.setattr(community, "collect_once", lambda **kw: {"status": status, "retry_seconds": retry})
    runtime.run()
    assert waits == [expected]


def test_stop_fences_late_collector_publication_and_lease_finish(monkeypatch):
    mock_collection(monkeypatch)
    entered, release = Event(), Event()
    def slow():
        entered.set()
        assert release.wait(5)
        return row()
    monkeypatch.setattr(community, "fetch_listing", slow)
    runtime = community._Runtime()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(community.collect_once, stop_event=runtime.stop, write_lock=runtime.write_lock)
        try:
            assert entered.wait(3)
            runtime.stop.set()
            with runtime.write_lock:
                pass
            monkeypatch.setattr(community, "finish_work", forbid)
        finally:
            release.set()
        assert task.result(timeout=3)["status"] == "cancelled"
    with database.get_session() as db:
        assert db.get(community.DomesticCommunityState, community.SCOPE) is None
        assert db.exec(select(community.DomesticCommunityItem)).all() == []


def test_offset_pages_results_and_mentions_ignore_display_filters():
    stored(items=[{"id": f"p{n}", "title": f"비트코인 이야기 {n}", "assets": ["BTC"] if n % 2 else ["BTC", "ETH"]}
                  for n in range(5)])
    first = community.get_domestic_community("upbit", limit=2)
    second = community.get_domestic_community("upbit", limit=2, offset=2)
    assert first["total"] == second["total"] == 5
    assert len(first["items"]) == len(second["items"]) == 2
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    filtered = community.get_domestic_community("upbit", "KRW-ETH", limit=1)
    assert filtered["total"] == 3
    # 언급 수는 코인·검색 필터와 상관없이 같은 값 — 순위표의 '언급' 열이 쓰는 숫자다.
    assert filtered["mentions"] == first["mentions"] == {"BTC": 5, "ETH": 3}
    with pytest.raises(ValueError, match="시작 위치"):
        community.get_domestic_community("upbit", offset=-1)


def test_cursor_pages_do_not_shift_when_new_posts_arrive_at_the_head():
    now = int(time.time() * 1000)
    stored(items=[{"id": f"p{n:02d}", "title": f"비트코인 이야기 {n}"} for n in range(5)], now_ms=now)
    first = community.get_domestic_community("upbit", limit=2)
    # 첫 쪽을 본 뒤 새 글 3개가 머리에 붙는다(오프셋이면 다음 쪽이 같은 글을 다시 준다).
    stored(items=[{"id": f"new{n}", "title": f"이더리움 새 글 {n}", "assets": ["ETH"]} for n in range(3)], now_ms=now + 1000)
    cursor = feed_index.decode_cursor(first["next_cursor"])
    second = community.get_domestic_community("upbit", limit=2, cursor=cursor)
    third = community.get_domestic_community("upbit", limit=2, cursor=feed_index.decode_cursor(second["next_cursor"]))
    seen = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert seen == ["p04", "p03", "p02", "p01", "p00"]
    assert third["next_cursor"] is None and second["total"] is None and "mentions" not in second


def test_community_collection_loop_warms_the_reader(monkeypatch):
    calls = []
    monkeypatch.setattr(community, "collect_once", lambda **_: None)
    monkeypatch.setattr(community, "warm_reader", lambda: calls.append("warm"))
    runtime = community._Runtime()
    monkeypatch.setattr(runtime.stop, "wait", lambda _seconds: runtime.stop.set())
    runtime.run()
    assert calls == ["warm"]


def test_reader_never_serves_a_list_whose_exchange_listings_have_expired(monkeypatch):
    # 2026-10-18: 낡은 값 창을 10분으로 두자, 5분 넘게 아무도 안 본 뒤 첫 사람에게 상장 목록이 만료된 읽기용 목록이
    # 나가 글이 0건으로 보였다. 창은 상장 목록 유효기간보다 짧아야 한다.
    assert community.READ_STALE_SECONDS + community.READ_FRESH_SECONDS + community.REFRESH_SECONDS \
        < community.LISTING_MAX_AGE_SECONDS
    real = int(time.time() * 1000)
    stored(items=[{}], now_ms=real)
    clock = [1000.0]
    monkeypatch.setattr(community._responses, "clock", lambda: clock[0])
    community.warm_reader()
    later = real + 6 * 60_000
    with database.get_session() as db:  # 그사이 수집기는 DB 의 상장 목록을 새로 받아 두었다
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        state.listings_json = json.dumps(listings(later))
        db.add(state)
        db.commit()
    clock[0] += 6 * 60
    result = community.get_domestic_community("upbit", now_ms=later)
    assert len(result["items"]) == 1
