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


def article(title, key):
    return {"id": key, "title": title, "source": "검증 뉴스",
            "url": f"https://fixture.invalid/{key}", "published_display": "방금 전"}


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
        payload = {"items": [article("시장 ETF 자금 유입 증가", "market")] if ready else [],
                   "translation": {"status": "ready" if ready else "partial",
                                   "pending_count": 0 if ready else 1, "retry_after_seconds": 30}}
    elif path == "/api/news/coin/BTCUSDT":
        # An old-version payload can contain an English headline. It must never
        # render, and its presence must trigger retry even without metadata.
        payload = {"items": [article("비트코인 ETF 자금 유입 증가" if ready else
                                      "Bitcoin ETF inflows rise", "btc")]}
    elif path == "/api/news/coin/ETHUSDT":
        payload = {"items": [{**article("이더리움 네트워크 업데이트 발표", "eth"),
                             "is_historical": True, "published": "2022-01-02T01:30:00Z"}],
                   "translation": {"status": "ready", "pending_count": 0}}
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
    expect(eth.get_by_text("이더리움 네트워크 업데이트 발표", exact=True)).to_be_visible()
    expect(eth.get_by_text("과거 기사 · 2022.01.02 · 검증 뉴스", exact=True)).to_be_visible()
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == calls["/api/news/coin/ETHUSDT"] == 1

    page.clock.run_for(25000)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 1
    ready = True
    page.clock.run_for(6000)
    expect(market.get_by_text("시장 ETF 자금 유입 증가", exact=True).first).to_be_visible()
    expect(btc.get_by_text("비트코인 ETF 자금 유입 증가", exact=True)).to_be_visible()
    expect(page.get_by_text("한국어로 번역하고 있어요.", exact=False)).to_have_count(0)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 2
    assert calls["/api/news/coin/ETHUSDT"] == 1
    page.clock.run_for(600000)
    assert calls["/api/news/market"] == calls["/api/news/coin/BTCUSDT"] == 2
    assert calls["/api/news/coin/ETHUSDT"] == 1
    assert not errors, errors
    print(json.dumps({"passed": True, "api_calls": dict(calls), "javascript_errors": errors,
                      "pending_after_retry": 0, "english_headlines": 0}, ensure_ascii=False))
    browser.close()
