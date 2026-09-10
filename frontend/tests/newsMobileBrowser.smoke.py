"""Mobile coin quotes/news selection against a real build and local fixtures.

FRONTEND_BUILD=/tmp/news-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/newsMobileBrowser.smoke.py
NEWS_MOBILE_OUTPUT overrides /tmp/ggp-news-mobile-check. External traffic is blocked.
"""
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

FRONTEND = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("FRONTEND_BUILD", FRONTEND / "dist")).resolve()
OUTPUT = Path(os.environ.get("NEWS_MOBILE_OUTPUT", "/tmp/ggp-news-mobile-check"))
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT", "DOGEUSDT", "1000PEPEUSDT", "AAVEUSDT", "LINKUSDT", "LTCUSDT"]
SUMMARY = "작성자는 최근 거래량과 시장 참여를 분석했어요. 개인적인 의견이며 장기적인 흐름을 함께 살펴볼 필요가 있다고 설명했어요."


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


def article(symbol, index=0):
    suffix = f" {index + 1}" if index else ""
    return {"title": f"{symbol.removesuffix('USDT')} 거래량 증가와 시장 참여 확대에 대한 최신 소식{suffix}", "source": "검증 뉴스", "url": f"https://fixture.invalid/{symbol}/{index}", "published_display": "10분 전"}


class Fixture:
    def __init__(self):
        self.fail_xrp = False
        self.hold_eth = False
        self.held = []
        self.calls = {}

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        path = url.path
        if not path.startswith("/api/"):
            route.continue_()
            return
        self.calls[path] = self.calls.get(path, 0) + 1
        data = {"items": []}
        if path == "/api/hot-coins":
            data = {"coins": [{"symbol": symbol, "last_price": 65234.45 if index == 0 else .000012 if index == 6 else 14.35, "change_pct": 12.2 - index * 1.6, "quote_volume": 345678} for index, symbol in enumerate(SYMBOLS)]}
        elif path == "/api/news/market":
            data = {"as_of": "2026-09-10", "overview": "시장 전반의 거래량이 늘었어요.\n주요 자산으로 자금이 유입됐어요.", "items": [article("시장")], "translation": {"status": "ready"}}
        elif path.startswith("/api/news/coin/"):
            symbol = path.rsplit("/", 1)[-1]
            data = {"items": [article(symbol)], "translation": {"status": "ready"}}
            if symbol == "BTCUSDT": data["items"] = [article(symbol, index) for index in range(5)]
            if symbol == "ETHUSDT" and self.hold_eth:
                self.held.append((route, data))
                return
            if symbol == "XRPUSDT" and self.fail_xrp:
                route.fulfill(status=400, json={"detail": "테스트 뉴스 오류"})
                return
            if symbol == "ADAUSDT": data["items"] = []
            if symbol == "SOLUSDT":
                data["items"] = [{**article(symbol), "content_type": "community", "author": "시장기록자", "source": "Binance Square", "community_post_id": "fixture-sol", "community_summary_status": "ready", "community_summary": SUMMARY, "is_historical": True, "published": "2022-01-02T01:30:00Z"}]
            if symbol == "DOGEUSDT": data = {"items": [], "translation": {"status": "partial", "pending_count": 1, "retry_after_seconds": 60}}
        elif path == "/api/kimchi-premium": data = {"ok": True, "premium_pct": .82}
        elif path == "/api/fear-greed": data = {"ok": True, "value": 64}
        elif path == "/api/hangang-temp": data = {"ok": True, "temperature": 24}
        route.fulfill(json=data)


def open_page(browser, origin, fixture, width, theme, errors):
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme, reduced_motion="reduce")
    context.route("**/*", fixture.route)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + "/news", wait_until="domcontentloaded")
    expect(page.locator(".news-racer-mobile-row")).to_have_count(10)
    return context, page


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"): launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            origin = f"http://127.0.0.1:{server.server_port}"
            for width in (320, 375, 767, 768, 1440):
                for theme in ("light", "dark"):
                    fixture = Fixture()
                    context, page = open_page(browser, origin, fixture, width, theme, errors)
                    if width <= 767:
                        expect(page.locator(".news-racer-map")).to_be_hidden()
                        expect(page.get_by_role("list", name="경주마 상승률 순위")).to_be_visible()
                        expect(page.locator(".news-racer-mobile-row").first).to_contain_text("65,234.45")
                        expect(page.locator(".news-racer-mobile-row").first).to_contain_text("+12.20%")
                        long_ticker = page.locator(".news-racer-mobile-coin b").nth(6)
                        expect(long_ticker).to_have_text("1000PEPE")
                        assert long_ticker.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                        metrics = page.locator(".news-racer-mobile-row").evaluate_all("""rows => rows.map(row => ({height:row.getBoundingClientRect().height, overflow:row.scrollWidth>row.clientWidth, font:parseFloat(getComputedStyle(row.querySelector('.news-racer-mobile-price')).fontSize)}))""")
                        assert all(row["height"] >= 48 and not row["overflow"] and row["font"] >= 13 for row in metrics), metrics
                        expect(page.locator("#news-racer-mobile-reader a").filter(has_text="BTC 거래량").first).to_be_visible()
                        article_list = page.get_by_role("list", name="BTC 뉴스 목록")
                        expect(article_list.locator(":scope > li")).to_have_count(5)
                        list_metrics = article_list.evaluate("""list => {
                          const rows = [...list.children].map(row => row.getBoundingClientRect());
                          const box = list.getBoundingClientRect();
                          return {clientHeight:list.clientHeight,scrollHeight:list.scrollHeight,scrollTop:list.scrollTop,
                            thirdBottom:rows[2].bottom,fourthTop:rows[3].top,bottom:box.bottom,overflow:getComputedStyle(list).overflowY};
                        }""")
                        assert list_metrics["scrollHeight"] > list_metrics["clientHeight"], list_metrics
                        assert list_metrics["thirdBottom"] <= list_metrics["bottom"] + 1, list_metrics
                        assert list_metrics["fourthTop"] >= list_metrics["bottom"] - 1, list_metrics
                        assert list_metrics["overflow"] == "auto", list_metrics
                        if width in (320, 375):
                            article_list.screenshot(path=str(OUTPUT / f"three-news-{width}-{theme}.png"), animations="disabled")
                        article_list.evaluate("list => { list.scrollTop = list.scrollHeight; }")
                        assert article_list.evaluate("list => list.scrollTop > 0")
                        page.locator(".news-racer-mobile-row").nth(4).click()
                        expect(page.get_by_role("heading", name="SOL 뉴스", exact=True)).to_be_visible()
                        assert page.get_by_role("list", name="SOL 뉴스 목록").get_attribute("tabindex") is None
                        expect(page.locator(".news-racer-mobile-summary p")).to_have_text(SUMMARY)
                        expect(page.locator(".news-racer-mobile-article-meta")).to_contain_text("과거 게시글 · 2022.01.02")
                        expect(page.locator(".news-racer-mobile-reader-head a")).to_have_attribute("href", "/builder?symbol=SOLUSDT")
                        if width in (320, 375):
                            page.set_viewport_size({"width": width, "height": 1200})
                            page.locator(".news-racer-mobile").evaluate("el => window.scrollBy(0, el.getBoundingClientRect().top - 128)")
                            page.locator(".news-racer-mobile").screenshot(path=str(OUTPUT / f"quotes-reader-{width}-{theme}.png"), animations="disabled")
                    else:
                        expect(page.locator(".news-racer-mobile")).to_be_hidden()
                        expect(page.locator(".news-racer-map")).to_be_visible()
                        expect(page.locator(".news-map-tile")).to_have_count(10)
                        expect(page.locator(".news-map-ticker").first).to_have_attribute("href", "/builder?symbol=BTCUSDT")
                        if width == 1440:
                            page.locator(".news-racer-map").screenshot(path=str(OUTPUT / f"treemap-{width}-{theme}.png"), animations="disabled")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, theme)
                    checks.append(f"layout-selection-{width}-{theme}")
                    context.close()

            fixture = Fixture()
            fixture.hold_eth = True
            fixture.fail_xrp = True
            context, page = open_page(browser, origin, fixture, 375, "dark", errors)
            reader = page.locator("#news-racer-mobile-reader")
            page.locator(".news-racer-mobile-row").nth(1).click()
            expect(reader.get_by_role("status")).to_contain_text("ETH 뉴스를 불러오는 중")
            fixture.hold_eth = False
            for route, data in fixture.held: route.fulfill(json=data)
            expect(reader.get_by_text(article("ETHUSDT")["title"], exact=True)).to_be_visible()
            page.locator(".news-racer-mobile-row").nth(2).click()
            expect(reader.get_by_role("alert")).to_contain_text("뉴스를 불러오지 못했어요")
            fixture.fail_xrp = False
            reader.get_by_role("button", name="다시 시도", exact=True).click()
            expect(reader.get_by_text(article("XRPUSDT")["title"], exact=True)).to_be_visible()
            assert fixture.calls["/api/news/coin/XRPUSDT"] == 2
            page.locator(".news-racer-mobile-row").nth(3).click()
            expect(reader.get_by_text("최근 ADA 뉴스가 없어요.", exact=True)).to_be_visible()
            page.locator(".news-racer-mobile-row").nth(5).click()
            expect(reader.get_by_role("status")).to_contain_text("한국어로 번역하고 있어요")
            expect(reader.get_by_text("최근 DOGE 뉴스가 없어요.", exact=True)).to_have_count(0)
            checks.append("selected-news-loading-error-retry-empty-translation")
            context.close()
            browser.close()
            assert not errors, errors
            report = {"passed": True, "checks": checks, "page_errors": errors, "build": str(BUILD)}
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"passed": True, "checks": len(checks), "report": str(OUTPUT / "report.json")}))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
