"""Verify Korean-only news delivery and automatic completion using fixture APIs.

Run against Vite with NEWS_TEST_BASE_URL and optional BROWSER_EXECUTABLE_PATH.
Every API is mocked; this regression never invokes a translation provider.
"""
import json
import os
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("NEWS_TEST_BASE_URL", "http://127.0.0.1:5188")
calls = Counter()
errors = []
ready = False
SUMMARY = ("작성자는 이더리움의 최근 거래량과 가격 흐름을 비교하며 매수세가 유지되는지 지켜볼 필요가 있다고 설명했어요. "
           "게시글에 제시된 단기 전망은 작성자의 개인적인 분석으로, 시장 상황에 따라 달라질 수 있으며 수익이나 상승을 보장하는 내용은 아니에요.")


def article(title, key):
    return {"id": key, "title": title, "source": "검증 뉴스",
            "url": f"https://fixture.invalid/{key}", "published_display": "방금 전"}


def community(historical=True):
    return {"content_type": "community", "community_post_id": "123456789", "author": "시장기록자",
            "source": "Binance Square", "url": "https://www.binance.com/en/square/post/123456789",
            "title": "이더리움에 대한 커뮤니티 작성자의 의견", "original_title": "A personal Ethereum outlook",
            "excerpt": "Original English community body", "is_historical": historical,
            "community_summary": SUMMARY if ready else "Original English summary must stay hidden",
            "community_summary_status": "ready" if ready else "pending",
            "community_summary_partial": historical,
            "published": "2022-01-02T01:30:00Z" if historical else "2026-09-08T01:30:00Z"}


def route_request(route):
    parsed = urlparse(route.request.url)
    path = parsed.path
    if not path.startswith("/api/"):
        if parsed.hostname == urlparse(BASE_URL).hostname:
            route.continue_()
        else:
            route.abort()
        return
    calls[path] += 1
    payload = {}
    if path == "/api/hot-coins":
        payload = {"coins": [{"symbol": symbol, "last_price": 10, "change_pct": 1,
                              "quote_volume": 100000} for symbol in ["BTCUSDT", "ETHUSDT"]]}
    elif path == "/api/news/market":
        payload = {"items": [article("시장 ETF 자금 유입 증가", "market"), community(historical=False)] if ready else [],
                   "translation": {"status": "ready" if ready else "partial",
                                   "pending_count": 0 if ready else 1, "retry_after_seconds": 30}}
    elif path == "/api/news/coin/BTCUSDT":
        # An old-version payload can contain an English headline. It must never
        # render, and its presence must trigger retry even without metadata.
        payload = {"items": [article("비트코인 ETF 자금 유입 증가" if ready else
                                      "Bitcoin ETF inflows rise", "btc")]}
    elif path == "/api/news/coin/ETHUSDT":
        payload = {"items": [{**article("이더리움 네트워크 업데이트 발표", "eth"),
                             "is_historical": True, "published": "2022-01-02T01:30:00Z"}, community()],
                   "translation": {"status": "ready", "pending_count": 0},
                   "community_summaries": {"status": "ready" if ready else "partial",
                                           "pending_count": 0 if ready else 1, "retry_after_seconds": 30}}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"),
        headless=True, args=["--no-sandbox"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1200})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", route_request)
    page.clock.install()
    page.goto(f"{BASE_URL}/news", wait_until="networkidle")
    market = page.locator(".news-briefing-section.is-market")
    btc = page.locator(".news-racer-briefing").filter(has=page.get_by_role("heading", name="BTC", exact=True))
    eth = page.locator(".news-racer-briefing").filter(has=page.get_by_role("heading", name="ETH", exact=True))
    expect(market.get_by_text("1개 기사 제목을 한국어로 번역하고 있어요. 완료되면 자동으로 표시해요.")).to_be_visible()
    expect(btc.get_by_text("1개 기사 제목을 한국어로 번역하고 있어요. 완료되면 자동으로 표시해요.")).to_be_visible()
    expect(page.get_by_text("Bitcoin ETF inflows rise", exact=True)).to_have_count(0)
    expect(btc.get_by_text("BTC 관련 최근 뉴스가 없어요.")).to_have_count(0)
    expect(market.get_by_text("지금은 불러올 시장 헤드라인이 없어요.")).to_have_count(0)
    expect(eth.get_by_text("이더리움 네트워크 업데이트 발표", exact=True).first).to_be_visible()
    expect(eth.get_by_text("과거 기사 · 2022.01.02 · 검증 뉴스", exact=True).first).to_be_visible()
    expect(eth.get_by_text("커뮤니티 · 시장기록자", exact=True).first).to_be_visible()
    expect(eth.get_by_text("Binance Square · 과거 게시글 · 2022.01.02", exact=True).first).to_be_visible()
    expect(page.get_by_text("A personal Ethereum outlook", exact=True)).to_have_count(0)
    expect(page.get_by_text("Original English community body", exact=True)).to_have_count(0)
    expect(page.get_by_text("Original English summary must stay hidden", exact=True)).to_have_count(0)
    expect(eth.get_by_text("본문 요약 중", exact=True).first).to_be_visible()
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == calls["/api/news/coin/ETHUSDT"] == 1

    page.clock.run_for(25000)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 1
    ready = True
    page.clock.run_for(6000)
    expect(market.get_by_text("시장 ETF 자금 유입 증가", exact=True).first).to_be_visible()
    expect(market.get_by_text("커뮤니티 · 시장기록자", exact=True).first).to_be_visible()
    expect(market.get_by_text("Binance Square · 2026.09.08 10:30 KST", exact=True).first).to_be_visible()
    expect(btc.get_by_text("비트코인 ETF 자금 유입 증가", exact=True)).to_be_visible()
    expect(page.get_by_text("한국어로 번역하고 있어요.", exact=False)).to_have_count(0)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 2
    assert calls["/api/news/coin/ETHUSDT"] == 2
    expect(eth.get_by_text("본문 일부 요약", exact=True).first).to_be_visible()
    expect(eth.get_by_text(SUMMARY, exact=True).first).to_be_visible()
    expect(market.get_by_text("본문 요약", exact=True).first).to_be_visible()
    expect(page.get_by_text("본문 요약 중", exact=True)).to_have_count(0)
    page.clock.run_for(600000)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 2
    assert calls["/api/news/coin/ETHUSDT"] == 2
    # The complete summary fits inside its row on desktop and narrow screens.
    # Advancing rotation must move by the actual outgoing row height.
    for width in [1440, 390]:
        page.set_viewport_size({"width": width, "height": 1200})
        eth.scroll_into_view_if_needed()
        eth.hover()
        page.clock.run_for(1000)
        result = eth.locator(".news-reader-row.is-community").first.evaluate("""row => {
            const text = row.querySelector('.community-body-summary-text');
            const box = row.getBoundingClientRect();
            return {textInside: text.getBoundingClientRect().bottom <= box.bottom + 1,
                    overflow: row.scrollWidth > row.clientWidth + 1};
        }""")
        assert result == {"textInside": True, "overflow": False}, result
    assert not errors, errors
    page.screenshot(path="/tmp/gg-parrot-community-news-public.png", full_page=True)
    print(json.dumps({"passed": True, "api_calls": dict(calls), "javascript_errors": errors,
                      "pending_after_retry": 0, "english_headlines": 0, "body_summaries": "ready",
                      "summary_widths_verified": [1440, 390]}, ensure_ascii=False))
    browser.close()
