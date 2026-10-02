"""Exchange UI regression with a local build and completely mocked APIs.

Set FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH. No account, real exchange,
backend connection, paid AI call, image, or activity-report file is created.
"""
import importlib.util
import json
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("exchange_fixture", Path(__file__).with_name("accountIsolationBrowser.smoke.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def install_note_handler(page):
    """Close update announcements normally, without pinning a note version."""
    note = page.locator('.devnote[role="dialog"]')
    page.add_locator_handler(note, lambda: note.get_by_role("button", name="확인했어요", exact=True).click())


class Fixture(fixtures.Fixture):
    def __init__(self):
        super().__init__()
        self.symbol_exchanges = []
        self.candle_markets = []
        self.pending_upbit = []
        self.ask_requests = []
        self.paper_macro = None

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        query = parse_qs(parsed.query)
        exchange = query.get("exchange", ["binance"])[0]
        if parsed.path == "/api/symbols":
            self.symbol_exchanges.append(exchange)
            domestic = exchange != "binance"
            symbols = ["BTC", "ETH"]
            route.fulfill(json={"exchange": exchange, "fetched_at": time.time(), "stale": False,
                                "items": [{"symbol": f"KRW-{base}" if domestic else f"{base}USDT", "base": base, "quote": "KRW" if domestic else "USDT", "spot": True, "futures": not domestic} for base in symbols]})
            return
        if parsed.path in {"/api/candles", "/api/candles/live"}:
            self.candle_markets.append((exchange, query.get("symbol", [""])[0]))
            if exchange == "upbit":
                self.pending_upbit.append(route)
            else:
                self.fulfill_candles(route, exchange)
            return
        if parsed.path == "/api/ask/status":
            route.fulfill(json={"consented": True, "remaining_today": 5, "daily_limit": 5})
            return
        if parsed.path == "/api/ask/candidates":
            self.ask_requests.append(route.request.post_data_json)
            route.fulfill(json={"session_id": "mock-exchange-session", "remaining_today": 4, "candidates": [{"symbol": "KRW-BTC", "base": "BTC", "reason": "검증용 원화 후보", "volume_rank": 1, "range_pct": 2}], "manual_symbols": ["KRW-ETH"]})
            return
        if parsed.path == "/api/backtest":
            capital = route.request.post_data_json["macro"]["params"]["initial_capital"]
            route.fulfill(json={"result": {"initial_capital": capital, "final_equity": capital * 1.03, "final_return_pct": 3, "mdd_pct": 2, "win_rate_pct": 60, "total_trades": 10, "buy_hold_return_pct": 2, "sharpe": 1.1, "profit_factor": 1.4, "max_consecutive_losses": 2, "equity_curve": [{"t": "2025-09-11", "equity": capital}, {"t": "2026-09-11", "equity": capital * 1.03}]}, "per_symbol": [], "human_summary": "검증용 결과", "data_source": "fixture", "period_label": "최근 1년"})
            return
        if parsed.path == "/api/paper/start":
            self.paper_macro = route.request.post_data_json["macro"]
            route.fulfill(json={"session_id": 99, "status": "running", "virtual_balance": self.paper_macro["params"]["initial_capital"]})
            return
        if parsed.path == "/api/paper/99":
            route.fulfill(json={"session_id": 99, "status": "running", "current_equity": self.paper_macro["params"]["initial_capital"], "current_return": 0, "last_price": 100, "trades": [], "liquidations": 0})
            return
        super().route(route)

    @staticmethod
    def fulfill_candles(route, exchange):
        query = parse_qs(urlsplit(route.request.url).query)
        interval = query.get("interval", ["1d"])[0]
        rows = fixtures.chart.bars(interval)
        price = {"binance": 100, "upbit": 2000000, "bithumb": 3000000}[exchange]
        for row in rows:
            row.update(o=price, h=price, l=price, c=price)
        route.fulfill(json={"candles": rows, "refresh_seconds": 3600, "server_time": rows[-1]["t"], "stale": False})


def pick_btc(page):
    search = page.get_by_role("combobox", name="종목 검색")
    search.fill("BTC")
    choice = page.get_by_role("listbox", name="종목 검색 결과").get_by_role("option", name="KRW-BTC", exact=True)
    expect(choice).to_be_visible()
    choice.click()


def main():
    assert (fixtures.BUILD / "index.html").is_file(), "Build frontend and set FRONTEND_BUILD"
    fixtures.profile.BUILD = fixtures.BUILD
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(fixtures.profile.Handler, directory=str(fixtures.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=fixtures.CHROME, headless=True, args=["--no-sandbox"])
            for width in (1440, 390):
                context = browser.new_context(viewport={"width": width, "height": 1000}, service_workers="block", reduced_motion="reduce")
                fixture = Fixture()
                context.route("**/*", fixture.route)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                install_note_handler(page)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/builder")
                exchange = page.get_by_label("거래소", exact=True)
                expect(exchange).to_have_value("binance")
                expect(page.get_by_role("button", name="숏", exact=True)).to_be_enabled()
                exchange.select_option("upbit")
                expect(page.get_by_role("button", name="숏", exact=True)).to_be_disabled()
                expect(page.get_by_role("spinbutton", name="시작 자금 (KRW)", exact=True)).to_have_value("")
                expect(page.get_by_role("button", name="KRW-BTC 빼기", exact=True)).to_have_count(0)
                pick_btc(page)
                page.wait_for_timeout(100)
                assert fixture.pending_upbit, "Upbit request must be in flight"
                exchange.select_option("bithumb")
                pick_btc(page)
                market = page.locator(".studio-chart .candle-chart-market")
                expect(market).to_contain_text("빗썸")
                expect(market).to_contain_text("3,000,000")
                for pending in fixture.pending_upbit:
                    fixture.fulfill_candles(pending, "upbit")
                page.wait_for_timeout(100)
                expect(market).to_contain_text("3,000,000")
                assert "2,000,000" not in market.inner_text(), "Late old-exchange response must be ignored"
                expect(page.get_by_role("option", name="K · 하락 방어 전환 (SAR, 선물) · 국내 현물 불가", exact=True)).to_be_disabled()
                capital = page.get_by_role("spinbutton", name="시작 자금 (KRW)", exact=True)
                capital.fill("1234567")
                page.get_by_label("매매 방식", exact=True).select_option("C")
                expect(capital).to_have_value("1234567")
                expect(page.get_by_label("봉 간격", exact=True)).to_have_value("1d")
                expect(page.get_by_label("봉 간격", exact=True).locator('option[value="1h"]')).to_be_disabled()
                expect(page.get_by_role("group", name="봉 간격 (조건에서 정해요)").locator(".is-unavailable")).to_have_count(5)
                assert set(fixture.symbol_exchanges) == {"binance", "upbit", "bithumb"}, fixture.symbol_exchanges
                assert ("bithumb", "KRW-BTC") in fixture.candle_markets
                checks.append({"width": width, "passed": True, "scenario": "native exchange symbols, money reset, short/SAR block, daily DCA, and late chart response isolation"})
                context.close()

            context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block", reduced_motion="reduce")
            fixture = Fixture()
            context.route("**/*", fixture.route)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            install_note_handler(page)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/?guide=1&tour=asset")
            exchange = page.get_by_label("사용할 거래소", exact=True)
            exchange.select_option("upbit")
            page.get_by_label("시작 자금 (KRW)", exact=True).fill("100000")
            page.get_by_label("차트로 확인할 종목 검색", exact=True).fill("BTC")
            page.get_by_role("button", name="차트 보며 조건 정하기", exact=True).click()
            page.wait_for_timeout(100)
            assert fixture.pending_upbit
            page.get_by_role("button", name="이전 화면", exact=True).click()
            page.get_by_role("button", name="종목 검색하기", exact=True).click()
            exchange.select_option("bithumb")
            page.get_by_label("시작 자금 (KRW)", exact=True).fill("700000")
            page.get_by_label("차트로 확인할 종목 검색", exact=True).fill("ETH")
            for pending in fixture.pending_upbit:
                fixture.fulfill_candles(pending, "upbit")
            page.wait_for_timeout(100)
            expect(exchange).to_have_value("bithumb")
            expect(page.get_by_label("차트로 확인할 종목 검색", exact=True)).to_have_value("ETH")
            assert parse_qs(urlsplit(page.url).query)["tour"] == ["asset"], page.url
            checks.append({"passed": True, "scenario": "late guide asset validation cannot overwrite a new exchange/ticker or advance the guide"})
            context.close()

            context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block", reduced_motion="reduce")
            fixture = Fixture()
            context.route("**/*", fixture.route)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            install_note_handler(page)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/builder")
            run = page.get_by_role("button", name="이 조건으로 백테스트", exact=True)
            expect(run).to_be_enabled()
            run.click()
            paper_tab = page.get_by_role("tab", name="페이퍼 트레이딩", exact=True)
            expect(paper_tab).to_be_enabled(timeout=10000)
            paper_tab.click()
            page.locator(".sd-paper-main").click()
            expect(page.locator(".sd-stat")).to_contain_text("현재 평가금액 (USDT)")
            page.get_by_label("거래소", exact=True).select_option("bithumb")
            expect(page.locator(".sd-stat")).to_contain_text("현재 평가금액 (USDT)")
            expect(page.locator(".sd-lock.is-warn")).to_contain_text("바이낸스")
            page.get_by_role("tab", name="백테스트", exact=True).click()
            expect(page.locator(".sd-kpis")).to_contain_text("USDT")
            checks.append({"passed": True, "scenario": "paper and backtest snapshots keep their original exchange/currency after builder edits"})
            context.close()

            # Fake identity exists only in this browser and mocked API responses.
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block", reduced_motion="reduce")
            fixture = Fixture()
            context.route("**/*", fixture.route)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            install_note_handler(page)
            page.add_init_script("localStorage.setItem('ggp_token','audit-a');localStorage.setItem('ggp_user'," + json.dumps(json.dumps(fixtures.USERS["audit-a"])) + ")")
            page.goto(f"http://127.0.0.1:{server.server_port}/builder?ask=1")
            dialog = page.get_by_role("dialog", name="껄무새에게 물어볼까?", exact=True)
            expect(dialog.get_by_role("heading", name="어느 거래소를 사용할 거예요?", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="업비트 KRW", exact=True).click()
            balance = dialog.get_by_label("업비트 사용 가능 KRW 잔액", exact=True)
            expect(balance).to_be_visible()
            balance.fill("0")
            expect(dialog.get_by_role("button", name="다음", exact=True)).to_be_disabled()
            balance.fill("1234567")
            dialog.get_by_role("button", name="다음", exact=True).click()
            dialog.get_by_role("button", name="균형형 -20%까지", exact=True).click()
            expect(dialog.get_by_role("button", name="선물 1x", exact=True)).to_be_disabled()
            dialog.get_by_role("button", name="현물 레버리지 없음", exact=True).click()
            dialog.get_by_role("button", name="몇 주 적당히", exact=True).click()
            dialog.get_by_role("button", name="가끔 봐요 몇 시간에 한 번", exact=True).click()
            dialog.get_by_role("button", name="후보 보기", exact=True).click()
            expect(dialog.locator(".ask-cand")).to_contain_text("KRW")
            assert len(fixture.ask_requests) == 1, fixture.ask_requests
            assert fixture.ask_requests[0]["exchange"] == "upbit"
            assert fixture.ask_requests[0]["account_balance"] == 1234567
            assert fixture.ask_requests[0]["market"] == "spot"
            assert fixture.ask_requests[0]["leverage"] == 1
            checks.append({"passed": True, "scenario": "exchange then positive manual KRW balance, futures disabled, native candidate and exact budget forwarded once"})
            context.close()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({"checks": checks, "page_errors": errors, "scope": "All traffic mocked; no production connection or artifact files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
