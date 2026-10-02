"""Catalogue freshness regression: local build, mocked APIs, no DB/AI or artifacts."""
import importlib.util
import json
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("symbol_refresh_fixture", Path(__file__).with_name("exchangeBrowser.smoke.py"))
exchange_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exchange_fixture)
fixtures = exchange_fixture.fixtures


class Fixture(fixtures.Fixture):
    def __init__(self, expired=False):
        super().__init__()
        self.expired = expired
        self.catalogue_calls = {"binance": 0, "upbit": 0, "bithumb": 0}

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        if parsed.path == "/api/symbols":
            exchange = parse_qs(parsed.query).get("exchange", ["binance"])[0]
            self.catalogue_calls[exchange] += 1
            first_stale = exchange == "upbit" and self.catalogue_calls[exchange] == 1
            symbol = "KRW-BTC" if first_stale else "KRW-NEW" if exchange == "upbit" else "KRW-ETH" if exchange == "bithumb" else "BTCUSDT"
            quote = "USDT" if exchange == "binance" else "KRW"
            base = symbol.removeprefix("KRW-").removesuffix("USDT")
            route.fulfill(json={"exchange": exchange, "items": [{"symbol": symbol, "base": base, "quote": quote, "spot": True, "futures": exchange == "binance"}],
                                "fetched_at": time.time() - (301 if self.expired else 90) if first_stale else time.time(), "stale": first_stale})
            return
        super().route(route)


def main():
    assert (fixtures.BUILD / "index.html").is_file(), "Build frontend and set FRONTEND_BUILD"
    fixtures.profile.BUILD = fixtures.BUILD
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(fixtures.profile.Handler, directory=str(fixtures.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=fixtures.CHROME, headless=True, args=["--no-sandbox"])
            # A delayed blur commit from an unmounted picker must never restore
            # the previous exchange/form after the exchange control changes.
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
            fixture = Fixture()
            context.route("**/*", fixture.route)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            exchange_fixture.install_note_handler(page)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/builder")
            page.get_by_role("button", name="BTCUSDT 빼기", exact=True).click()
            search = page.get_by_role("combobox", name="종목 검색")
            search.fill("BTC")
            expect(page.get_by_role("listbox", name="종목 검색 결과").get_by_role("option", name="BTCUSDT", exact=True)).to_be_visible()
            page.evaluate("""() => {
                document.querySelector('input[aria-label="종목 검색"]').blur();
                const capital = document.querySelector('input[aria-label="시작 자금 (USDT)"]');
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(capital, '777');
                capital.dispatchEvent(new Event('input', {bubbles:true}));
            }""")
            page.wait_for_timeout(250)
            expect(page.get_by_role("spinbutton", name="시작 자금 (USDT)", exact=True)).to_have_value("777")
            checks.append({"passed": True, "scenario": "pending symbol blur preserves other condition edits"})
            page.get_by_role("button", name="BTCUSDT 빼기", exact=True).click()
            search.fill("BTC")
            expect(page.get_by_role("listbox", name="종목 검색 결과").get_by_role("option", name="BTCUSDT", exact=True)).to_be_visible()
            page.evaluate("""() => {
                document.querySelector('input[aria-label="종목 검색"]').blur();
                const exchange = [...document.querySelectorAll('select')].find(el => [...el.options].some(option => option.value === 'upbit'));
                exchange.value = 'upbit';
                exchange.dispatchEvent(new Event('change', {bubbles:true}));
            }""")
            page.wait_for_timeout(250)
            expect(page.get_by_label("거래소", exact=True)).to_have_value("upbit")
            expect(page.get_by_role("button", name="BTCUSDT 빼기", exact=True)).to_have_count(0)
            checks.append({"passed": True, "scenario": "pending symbol blur cannot overwrite the newly selected exchange"})
            context.close()
            for width in (1440, 390):
                for expired in (False, True):
                    context = browser.new_context(viewport={"width": width, "height": 1000}, service_workers="block", reduced_motion="reduce")
                    fixture = Fixture(expired=expired)
                    context.route("**/*", fixture.route)
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    page = context.new_page()
                    exchange_fixture.install_note_handler(page)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}/builder")
                    exchange = page.get_by_label("거래소", exact=True)
                    exchange.select_option("upbit")
                    search = page.get_by_role("combobox", name="종목 검색")
                    search.fill("BTC")
                    results = page.get_by_role("listbox", name="종목 검색 결과")
                    if expired:
                        expect(results.get_by_text("종목 목록을 못 불러왔어요.", exact=False)).to_be_visible()
                        expect(results.get_by_role("option", name="KRW-BTC", exact=True)).to_have_count(0)
                        results.get_by_role("button", name="다시 시도", exact=True).click()
                    else:
                        expect(page.get_by_text("마지막 확인한 종목 목록이에요.", exact=False)).to_be_visible()
                        expect(results.get_by_role("option", name="KRW-BTC", exact=True)).to_be_visible()
                        page.get_by_role("button", name="다시 확인", exact=True).click()
                    search.fill("NEW")
                    choice = results.get_by_role("option", name="KRW-NEW", exact=True)
                    expect(choice).to_be_visible()
                    expect(page.get_by_text("마지막 확인한 종목 목록이에요.", exact=False)).to_have_count(0)
                    choice.click()
                    expect(page.get_by_role("button", name="KRW-NEW 빼기", exact=True)).to_be_visible()
                    assert fixture.catalogue_calls["upbit"] == 2, fixture.catalogue_calls
                    exchange.select_option("bithumb")
                    search.fill("NEW")
                    expect(results.get_by_role("option", name="KRW-NEW", exact=True)).to_have_count(0)
                    search.fill("ETH")
                    expect(results.get_by_role("option", name="KRW-ETH", exact=True)).to_be_visible()
                    checks.append({"width": width, "expired": expired, "passed": True,
                                   "scenario": "stale/expired catalogue -> explicit refresh -> newly listed ticker selectable -> exchange isolation"})
                    context.close()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({"checks": checks, "page_errors": errors, "scope": "All API and WebSocket traffic mocked; no account/DB/AI or artifact files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
