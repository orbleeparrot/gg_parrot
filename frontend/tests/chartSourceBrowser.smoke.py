"""Chart provenance regression; local build + fully mocked APIs/WS only.

Set FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH. No screenshots, activity files,
accounts, backend writes, or real/paid network calls are created.
"""
import importlib.util
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("chart_source_fixture", Path(__file__).with_name("exchangeBrowser.smoke.py"))
exchange_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exchange_fixture)
fixtures = exchange_fixture.fixtures


class Fixture(exchange_fixture.Fixture):
    def __init__(self):
        super().__init__()
        self.chart_calls = []
        self.phase = "fresh"
        self.actual_market = "spot"
        self.hold_live = False
        self.held_live = []
        self.last_open = 0
        self.live_tick = 0
        self.history_origin = 0
        self.history_error = False
        self.history_refresh = 300
        self.live_refresh = 2
        self.legacy = False

    def response(self, query, actual_market, live=False):
        now = int(time.time() * 1000)
        interval = query.get("interval", ["1d"])[0]
        step = fixtures.chart.INTERVALS[interval]
        end = now // step * step
        self.last_open = end
        price = 100 if actual_market == "spot" else 900
        if not live and not self.history_origin:
            self.history_origin = now
        if live and self.phase == "changing":
            self.live_tick += 1
            price += self.live_tick * 5
        stale = self.phase == "stale" and live
        awaiting = self.phase == "awaiting" and live
        if stale:
            price = 50
        count = 2 if live else 300
        rows = [{"t": end - (count - index - 1) * step, "o": price, "h": price, "l": price, "c": price, "v": 1, "closed": index < count - 1} for index in range(count)]
        if live and self.phase == "changing":
            rows[-1].update(o=100, h=price, l=100)
        origin = self.history_origin if self.phase == "changing" and not live else now - (60000 if stale else 0)
        response = {"exchange": query.get("exchange", ["binance"])[0], "symbol": query.get("symbol", ["BTCUSDT"])[0],
                "interval": interval, "market": actual_market, "requested_market": query.get("market", ["spot"])[0],
                "source": "binance:" + actual_market, "fallback": actual_market != query.get("market", ["spot"])[0],
                "candles": rows, "fetched_at_ms": origin, "server_time": now,
                "cache_age_ms": now - origin, "stale": stale, "awaiting_candle_refresh": awaiting,
                "refresh_seconds": self.live_refresh if live else self.history_refresh}
        if self.legacy:
            # Old backend caches its prepare-time server_time, and provides no
            # fetched_at_ms/cache_age_ms/awaiting/source provenance contract.
            response["server_time"] = origin
            for key in ("fetched_at_ms", "cache_age_ms", "awaiting_candle_refresh", "source", "fallback"):
                response.pop(key)
        return response

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        if parsed.path not in {"/api/candles", "/api/candles/live"}:
            super().route(route)
            return
        query = parse_qs(parsed.query)
        live = parsed.path.endswith("/live")
        self.chart_calls.append({"live": live, "market": query.get("market", ["spot"])[0], "interval": query.get("interval", ["1d"])[0]})
        if not live and self.history_error:
            self.history_error = False
            route.fulfill(status=503, json={"detail": "모의 최초 과거봉 장애"})
            return
        if live and self.hold_live:
            self.held_live.append((route, self.response(query, self.actual_market, True)))
            return
        if live and self.phase == "error":
            route.fulfill(status=503, json={"detail": "모의 실시간 시세 장애"})
            return
        actual = "futures" if live and self.phase == "mismatch" else self.actual_market
        if live and self.phase == "changing":
            # Both poll timers have the same cadence. History is fulfilled
            # first; live finishes later, reproducing revision invalidation.
            time.sleep(0.15)
        route.fulfill(json=self.response(query, actual, live))


def context_for(browser, server, fixture, errors):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="UTC", service_workers="block", reduced_motion="reduce")
    context.route("**/*", fixture.route)
    context.route_web_socket("**/*", lambda socket: socket.close())
    page = context.new_page()
    exchange_fixture.install_note_handler(page)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, f"http://127.0.0.1:{server.server_port}"


def guide_route(page, step):
    page.evaluate("step => { history.pushState({}, '', '/?guide=1&tour=' + step); dispatchEvent(new PopStateEvent('popstate')); }", step)


def verify_realtime(browser, server, errors, legacy=False):
    fixture = Fixture()
    fixture.phase = "changing"
    fixture.history_error = not os.environ.get("CHART_REALTIME_RACE_ONLY")
    initial_error = fixture.history_error
    fixture.history_refresh = 3
    fixture.live_refresh = 3
    fixture.legacy = legacy
    context, page, base = context_for(browser, server, fixture, errors)
    page.goto(base + "/builder")
    if initial_error:
        expect(page.get_by_text("차트를 불러오지 못했어요: 모의 최초 과거봉 장애", exact=True)).to_be_visible()
    # Recovery and subsequent updates use polling only, with no conditions or
    # chart interaction. Canvas pixels are compared in memory, never stored.
    expect(page.locator(".candle-chart-current")).to_have_text("105.00", timeout=15000)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    canvas = "Array.from(document.querySelectorAll('.financial-candle-plot canvas')).map(canvas => canvas.toDataURL()).join('')"
    before_pixels = page.evaluate(canvas)
    expect(page.locator(".candle-chart-current")).to_have_text("110.00", timeout=10000)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert before_pixels != page.evaluate(canvas), "new live candle prices did not repaint the chart canvas"
    assert len([call for call in fixture.chart_calls if call["live"]]) >= 2
    if legacy:
        expect(page.locator(".candle-chart-live")).to_have_count(0)
        expect(page.locator(".candle-source")).to_contain_text("원천 수집 시각 확인 대기")

    page.evaluate("window.__chartHidden = true; Object.defineProperty(document, 'hidden', {configurable:true,get:()=>window.__chartHidden}); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(100)
    paused_count = len(fixture.chart_calls)
    paused_price = page.locator(".candle-chart-current").inner_text()
    page.wait_for_timeout(6500)
    assert len(fixture.chart_calls) == paused_count, "hidden chart kept polling"
    expect(page.locator(".candle-chart-current")).to_have_text(paused_price)
    page.evaluate("window.__chartHidden = false; document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("previous => document.querySelector('.candle-chart-current')?.textContent !== previous", arg=paused_price, timeout=1000)
    assert len(fixture.chart_calls) > paused_count, "foreground chart did not immediately refresh"
    context.close()
    return ("legacy prices update without inventing fetched_at_ms or showing LIVE; " if legacy else "") + "no-input foreground polling changes price and canvas, recovers initial history error, pauses hidden and resumes immediately"


def main():
    assert (fixtures.BUILD / "index.html").is_file(), "Build frontend and set FRONTEND_BUILD"
    fixtures.profile.BUILD = fixtures.BUILD
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(fixtures.profile.Handler, directory=str(fixtures.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=fixtures.CHROME, headless=True, args=["--no-sandbox"])
            if os.environ.get("CHART_REALTIME_ONLY"):
                checks.append(verify_realtime(browser, server, errors, legacy=bool(os.environ.get("CHART_LEGACY_ONLY"))))
                assert not errors, errors
                browser.close()
                print(json.dumps({"checks": checks, "page_errors": errors, "scope": "Mocked local HTTP only; no stored artifacts or real service calls"}, ensure_ascii=False))
                return
            fixture = Fixture()
            context, page, base = context_for(browser, server, fixture, errors)
            page.goto(base + "/?guide=1&tour=asset")
            page.get_by_role("button", name="차트 보며 조건 정하기", exact=True).click()
            page.get_by_label("매매 전략", exact=True).select_option("K")
            page.get_by_role("button", name="전략 조건 정하기", exact=True).click()
            expect(page.locator(".financial-candle-plot")).to_be_visible()
            assert fixture.chart_calls[-1]["market"] == "futures", "K guide incorrectly requests spot candles: " + repr(fixture.chart_calls)
            checks.append("K guide requests the resolved futures market (original-regression RED on old build)")
            guide_route(page, "backtest")
            page.get_by_role("button", name="이 조건으로 백테스트", exact=True).click()
            expect(page.get_by_role("button", name="페이퍼 트레이딩 진행", exact=True)).to_be_visible()
            expect(page.get_by_label("차트 봉 간격", exact=True)).to_be_disabled()
            guide_route(page, "strategy")
            page.get_by_label("매매 전략", exact=True).select_option("A")
            page.get_by_label("차트 봉 간격", exact=True).select_option("1h")
            expect(page.get_by_label("차트 봉 간격", exact=True)).to_have_value("1h")
            guide_route(page, "backtest")
            expect(page.get_by_text("결과를 낸 뒤 조건이 바뀌었어요.", exact=False)).to_be_visible()
            expect(page.get_by_label("차트 봉 간격", exact=True)).to_have_value("1d")
            expect(page.get_by_label("차트 봉 간격", exact=True)).to_be_disabled()
            assert fixture.chart_calls[-1]["market"] == "futures" and fixture.chart_calls[-1]["interval"] == "1d", fixture.chart_calls
            checks.append("Backtest chart preserves tested futures market and daily interval after current conditions change to spot/hourly")
            context.close()

            fixture = Fixture()
            context, page, base = context_for(browser, server, fixture, errors)
            page.goto(base + "/builder")
            page.get_by_role("button", name="숏", exact=True).click()
            expect(page.locator(".candle-source")).to_contain_text("선물 요청 → 현물 대체")
            expect(page.locator(".candle-chart-live")).to_be_visible()
            source = page.locator(".candle-source")
            assert source.get_attribute("open") is None
            assert "과거봉:" not in source.inner_text(), "Verbose provenance should be collapsed by default"
            source.locator("summary").click()
            expect(source).to_contain_text("과거봉:")
            expect(source.locator("div")).to_be_visible()
            source.locator("summary").click()
            page.wait_for_timeout(3500)
            live_calls = [call for call in fixture.chart_calls if call["live"]]
            assert live_calls and all(call["market"] == "spot" for call in live_calls), fixture.chart_calls
            expected_kst = datetime.fromtimestamp(fixture.last_open / 1000, timezone.utc) + timedelta(hours=9)
            expect(page.locator(".candle-ohlc-time")).to_have_text(f"{expected_kst.month}/{expected_kst.day} {expected_kst:%H:%M} KST")
            checks.append("UTC browser still renders KST; fallback is disclosed and live requests use actual spot")

            fixture.phase = "mismatch"
            before = len([call for call in fixture.chart_calls if not call["live"]])
            page.wait_for_timeout(4500)
            expect(page.locator(".candle-chart-current")).to_have_text("100.00")
            reloaded = len([call for call in fixture.chart_calls if not call["live"]]) - before
            assert reloaded == 1, f"mismatch must trigger one bounded full reload, got {reloaded}: {fixture.chart_calls}"
            checks.append("mismatched futures live data never merges into spot history; full reload is bounded")

            fixture.phase = "error"
            expect(page.locator(".candle-source")).to_contain_text("실시간: 오류", timeout=10000)
            expect(page.locator(".candle-source summary")).to_contain_text("실시간: 오류")
            expect(page.locator(".candle-chart-live")).to_have_count(0)
            expect(page.locator(".candle-chart-current")).to_have_text("100.00")
            fixture.phase = "fresh"
            expect(page.locator(".candle-chart-live")).to_be_visible(timeout=10000)
            fixture.phase = "stale"
            expect(page.locator(".candle-source")).to_contain_text("지연 · 캐시 시세", timeout=10000)
            expect(page.locator(".candle-source summary")).to_contain_text("지연 · 캐시 시세")
            expect(page.locator(".candle-chart-live")).to_have_count(0)
            expect(page.locator(".candle-chart-current")).to_have_text("100.00")
            fixture.phase = "awaiting"
            expect(page.locator(".candle-source")).to_contain_text("진행봉 갱신 대기", timeout=10000)
            expect(page.locator(".candle-source summary")).to_contain_text("진행봉 갱신 대기")
            expect(page.locator(".candle-chart-live")).to_have_count(0)
            checks.append("live errors, origin-stale data and unconfirmed closed-time candles suppress LIVE and retain valid prices")

            fixture.phase = "fresh"
            expect(page.locator(".candle-chart-live")).to_be_visible(timeout=10000)
            fixture.hold_live = True
            page.wait_for_timeout(2500)
            assert fixture.held_live
            fixture.actual_market = "futures"
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.locator(".candle-chart-current")).to_have_text("900.00")
            for route, response in fixture.held_live:
                response["candles"][-1].update(o=150, h=150, l=150, c=150)
                route.fulfill(json=response)
            fixture.hold_live = False
            page.wait_for_timeout(100)
            expect(page.locator(".candle-chart-current")).to_have_text("900.00")
            expect(page.locator(".candle-source")).not_to_contain_text("현물 대체")
            checks.append("late old-spot response cannot overwrite a full-history switch back to futures")
            context.close()
            checks.append(verify_realtime(browser, server, errors))
            checks.append(verify_realtime(browser, server, errors, legacy=True))
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({"checks": checks, "page_errors": errors, "scope": "Mocked local HTTP only; no stored artifacts or real service calls"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
