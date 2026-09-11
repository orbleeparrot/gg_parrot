"""Real React chart integration; every API response is intercepted, never stored.

Run against Vite with PYTHONPATH containing Playwright. Results stay in the repo:
  CHART_TEST_BASE_URL=http://127.0.0.1:5173 python frontend/tests/chartMigrationBrowser.smoke.py
"""
import json
import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/chart-migration-preview/integration"
BASE = os.environ.get("CHART_TEST_BASE_URL", "http://127.0.0.1:5173")
CHROMIUM = os.environ.get("BROWSER_EXECUTABLE_PATH", "/data/team/clcleh123/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
START = 1767225600000
INTERVALS = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
report = {"passed": False, "cases": [], "interactions": [], "screenshots": [], "pageErrors": [], "apiRequests": []}
phase = {"mode": "normal", "tick": 0, "live_requests": 0}


def bars(interval="1m", tick=0):
    step = INTERVALS[interval]
    result = []
    for index in range(300):
        opened = 100 + math.sin((index - 1) / 4) * 6 + index * .01
        closed = 100 + math.sin(index / 4) * 6 + index * .01
        result.append({"t": START + index * step, "o": opened, "h": max(opened, closed) + 1.5, "l": min(opened, closed) - 1.5, "c": closed, "v": 1000, "closed": index < 299})
    if tick:
        result[-1]["c"] += .75
        result[-1]["h"] = max(result[-1]["h"], result[-1]["c"])
    if tick > 1:
        result[-1]["closed"] = True
        result.append({**result[-1], "t": START + 300 * step, "o": result[-1]["c"], "c": result[-1]["c"] + .3, "h": result[-1]["h"] + .3, "closed": False})
        result = result[-300:]
    return result


def route_handler(route):
    parsed = urlparse(route.request.url)
    if not parsed.path.startswith("/api/"):
        if parsed.hostname in ("127.0.0.1", "localhost"):
            route.continue_()
        else:
            route.abort()
        return
    report["apiRequests"].append({"path": parsed.path, "method": route.request.method})
    status, payload = 200, {}
    if parsed.path in ("/api/candles", "/api/candles/live"):
        interval = parse_qs(parsed.query).get("interval", ["1m"])[0]
        live = parsed.path.endswith("/live")
        if live:
            phase["live_requests"] += 1
        if phase["mode"] == "error" or (phase["mode"] == "live_error" and live):
            status, payload = 503, {"detail": "차트 검증용 연결 오류"}
        else:
            data = [] if phase["mode"] == "empty" else bars(interval, phase["tick"])
            payload = {"candles": data[-2:] if live else data, "server_time": START + 301 * INTERVALS[interval], "refresh_seconds": 2 if live else 3600, "stale": False}
    elif parsed.path.endswith("/auth/me"):
        payload = {"user": {"id": 1, "username": "차트 검증", "email": "chart@example.invalid"}}
    elif parsed.path.endswith("/runner/sessions/stream-token"):
        status, payload = 503, {"detail": "fixture has no websocket"}
    elif parsed.path.endswith("/runner/sessions"):
        payload = {"active": [{"session_id": 41, "symbol": "BTCUSDT", "status": "running", "connected": True, "in_position": True, "position_side": "long", "market": "spot", "testnet": True, "entry_price": 100, "position_qty": 1, "last_price": 104, "started_kst": "09/11 12:00", "macro": {"rule_type": "G", "position_side": "long", "symbol": "BTCUSDT", "candle_interval": "1m", "params": {"bb_period": 12, "bb_std": 1.3, "strategy": "reversion", "exit_target": "opposite"}, "risk": {}}}], "recent": [], "poll_seconds": 30}
    elif parsed.path.endswith("/hot-coins"):
        payload = {"items": []}
    elif parsed.path == "/api/backtest/limits":
        payload = {"max_bars": 20000, "interval_ms": INTERVALS, "preset_days": {"1y": 365, "6m": 182, "3m": 91, "1m": 30, "1w": 7, "1d": 1}}
    elif parsed.path.endswith("/position-news") or parsed.path.endswith("/whale-activity"):
        payload = {"status": "ready", "items": [], "analysis_status": "ready", "collection": {"status": "ready"}}
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


def configure(page, values, reset=False):
    page.evaluate("([values,reset]) => chartFixture[reset ? 'reset' : 'configure'](values)", [values, reset])
    page.wait_for_function("values => Object.entries(values).every(([key,value]) => JSON.stringify(chartFixture.inspect().config[key]) === JSON.stringify(value))", arg=values)
    page.wait_for_function("() => !!document.querySelector('.financial-candle-plot canvas')")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def screenshot(page, name):
    path = OUTPUT / name
    page.screenshot(path=str(path), full_page=False)
    report["screenshots"].append(name)


def verify_case(page, config, width):
    configure(page, config)
    state = page.evaluate("chartFixture.inspect()")
    section = page.locator("#chart-under-test")
    text = section.inner_text()
    spec = state["overlay"] or {}
    for item in spec.get("legend", []):
        assert item["label"] in text, (config, "missing legend", item["label"])
    if config["variant"] == "default" and spec.get("note"):
        assert spec["note"] in text, (config, "missing strategy explanation")
    if spec.get("rsi"):
        accessible = section.text_content()
        for label in (str(spec["rsi"]["entry"]), str(spec["rsi"]["exit"]), spec["rsi"].get("lowLabel", ""), spec["rsi"].get("highLabel", "")):
            assert label in accessible, (config, "missing RSI threshold/meaning", label)
    assert "BTCUSDT" in text and "USDT" in text and "진행 중" in text, (config, text)
    assert all(label in text for label in ("시", "고", "저", "종")), (config, "OHLC labels")
    assert state["data"][-1]["candles"] == bars(), (config, "data changed")
    assert state["data"][-1]["market"] == "spot" and state["data"][-1]["stale"] is False
    assert any(item["status"] == "ready" for item in state["loads"])
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (config, "horizontal overflow", width)
    dimensions = section.locator(".financial-candle-plot").bounding_box()
    assert dimensions and dimensions["width"] > 200 and dimensions["height"] > 70, (config, dimensions)
    assert not report["pageErrors"], report["pageErrors"]
    report["cases"].append({"width": width, **config, "legendCount": len(spec.get("legend", [])), "markers": len(spec.get("markers", [])), "priceLines": len(spec.get("priceLines", [])), "bands": len(spec.get("bands", [])), "rsi": bool(spec.get("rsi"))})


def verify_interactions(page):
    configure(page, {"rule": "G", "side": "long", "average": True, "variant": "default", "theme": "dark"}, reset=True)
    section = page.locator("#chart-under-test")
    expect(section.get_by_text("80봉", exact=True)).to_be_visible()
    section.get_by_role("button", name="차트 확대", exact=True).click()
    expect(section.get_by_text("56봉", exact=True)).to_be_visible()
    section.get_by_role("button", name="차트 축소", exact=True).click()
    expect(section.get_by_text("76봉", exact=True)).to_be_visible()
    with page.expect_response(lambda response: urlparse(response.url).path == "/api/candles/live", timeout=8000):
        page.wait_for_timeout(3200)
    expect(section.get_by_text("76봉", exact=True)).to_be_visible()
    chart = section.locator(".financial-candle-plot")
    box = chart.bounding_box()
    page.mouse.move(box["x"] + box["width"] * .3, box["y"] + box["height"] * .4)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .6, box["y"] + box["height"] * .4, steps=12)
    page.mouse.up()
    expect(section.get_by_role("button", name="최신", exact=True)).to_be_visible()
    chart.focus()
    chart.press("Home")
    first_time = section.locator(".candle-ohlc-time").inner_text()
    chart.press("End")
    end_time = section.locator(".candle-ohlc-time").inner_text()
    assert first_time != end_time, (first_time, end_time)
    chart.press("ArrowLeft")
    assert section.locator(".candle-ohlc-time").inner_text() != end_time
    chart.press("Escape")
    historical_time = section.locator(".candle-ohlc-time").inner_text()
    page.mouse.move(0, 0)
    phase["tick"] = 2
    prior_requests = phase["live_requests"]
    page.wait_for_function("() => chartFixture.inspect().data.at(-1)?.candles.at(-1)?.t === 1767243600000", timeout=8000)
    assert phase["live_requests"] > prior_requests
    expect(section.get_by_role("button", name="최신", exact=True)).to_be_visible()
    assert section.locator(".candle-ohlc-time").inner_text() == historical_time, "buffer rollover changed historical viewport"
    section.get_by_role("button", name="최신", exact=True).click()
    expect(section.get_by_text("LIVE", exact=True)).to_be_visible()
    default_allowed = chart.evaluate("element => element.dispatchEvent(new WheelEvent('wheel', {deltaY:-100,ctrlKey:true,bubbles:true,cancelable:true}))")
    assert default_allowed, "browser zoom shortcut prevented"
    assert page.evaluate("chartFixture.inspect().data.at(-1).candles.length") == 300
    before_live_error = phase["live_requests"]
    phase["mode"] = "live_error"
    page.wait_for_timeout(2300)
    assert phase["live_requests"] > before_live_error
    expect(chart).to_be_visible()
    assert "차트를 불러오지 못했어요" not in section.inner_text()
    phase["mode"], phase["tick"] = "normal", 0
    section.get_by_label("차트 봉 간격", exact=True).select_option("5m")
    page.wait_for_function("chartFixture.inspect().data.at(-1).interval === '5m'")
    assert page.evaluate("chartFixture.inspect().data.at(-1).candles") == bars("5m")
    expect(section.get_by_text("LIVE", exact=True)).to_be_visible()
    report["interactions"].extend(["zoom buttons", "drag remains active after unchanged live poll", "keyboard Home/End/arrows/Escape", "live append retains historical viewport", "latest returns to live", "browser zoom shortcut preserved", "300-bar cap", "live failure retains chart", "controlled interval reset"])
    box = chart.bounding_box()
    page.mouse.move(box["x"] + box["width"] * .3, box["y"] + 90)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .6, box["y"] + 90, steps=12)
    page.mouse.up()
    page.mouse.move(0, 0)
    expect(section.get_by_role("button", name="최신", exact=True)).to_be_visible()
    historical_time = section.locator(".candle-ohlc-time").inner_text()
    phase["mode"] = "error"
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(section.get_by_text("차트를 불러오지 못했어요: 차트 검증용 연결 오류", exact=True)).to_be_visible()
    expect(chart).to_be_visible()
    phase["mode"] = "normal"
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(section.get_by_text("차트를 불러오지 못했어요: 차트 검증용 연결 오류", exact=True)).to_have_count(0)
    assert section.locator(".candle-ohlc-time").inner_text() == historical_time, "history recovery moved viewport"
    expect(section.get_by_role("button", name="최신", exact=True)).to_be_visible()
    report["interactions"].append("history failure/recovery retains chart and viewport")


def verify_mobile_touch(page):
    configure(page, {"rule": "G", "variant": "default", "theme": "dark"}, reset=True)
    chart = page.locator(".financial-candle-plot")
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    initial = page.evaluate("scrollY")
    session = page.context.new_cdp_session(page)
    x, y = box["x"] + box["width"] * .55, min(700, box["y"] + box["height"] * .8)
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for step in range(1, 11):
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y - step * 15}]})
        page.wait_for_timeout(16)
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(150)
    assert page.evaluate("scrollY") > initial + 30, "chart trapped mobile vertical page scroll"
    session.detach()
    report["interactions"].append("mobile vertical touch scroll over chart")


def verify_states(page):
    for mode in ("error", "empty"):
        phase["mode"] = mode
        page.evaluate("chartFixture.reset()")
        page.wait_for_function("chartFixture.inspect().loads.some(item => item.status === 'error')")
        assert page.locator(".financial-candle-plot canvas").count() == 0
        if mode == "error":
            expect(page.get_by_text("차트를 불러오지 못했어요: 차트 검증용 연결 오류", exact=True)).to_be_visible()
        report["interactions"].append(mode + " state")
    phase["mode"] = "normal"
    configure(page, {"rule": None, "average": True}, reset=True)
    assert page.evaluate("chartFixture.inspect().overlay.priceLines.length") == 1
    expect(page.get_by_text("내 평단(평균 진입가)", exact=True)).to_be_visible()
    report["interactions"].append("legacy session average without macro")
    configure(page, {"rule": "F", "params": {"rsi_period": 500}, "average": False})
    assert all(value is None for value in page.evaluate("chartFixture.inspect().overlay.rsi.values"))
    expect(page.locator(".financial-candle-plot.has-rsi canvas").first).to_be_visible()
    assert "과매수 65" in page.locator(".financial-candle-plot").text_content()
    assert not report["pageErrors"], report["pageErrors"]
    report["interactions"].append("RSI warm-up with no computed values retains fixed pane")


def verify_equity(page):
    configure(page, {"equity": True, "variant": "default", "stretch": False}, reset=True)
    equity = page.locator("#equity-under-test")
    expect(equity.locator("canvas").first).to_be_visible()
    assert "2026-01-01" in equity.inner_text() and "시작 1.0K" in equity.inner_text()
    assert "2026-03-31" in equity.inner_text() and "최종" in equity.inner_text()
    chart = equity.get_by_role("img", name="자산곡선")
    chart.focus()
    chart.press("ArrowLeft")
    assert "2026-03-30" in equity.inner_text()
    configure(page, {"stretch": True, "theme": "light"})
    expect(equity.locator("canvas").first).to_be_visible()
    page.evaluate("chartFixture.setCurve([])")
    expect(equity.get_by_text("자산곡선 데이터가 없어요.", exact=True)).to_be_visible()
    page.evaluate("chartFixture.setCurve([{t:'2026-01-01T00:00:00Z',equity:1000},{t:'2026-01-01T00:00:00Z',equity:1100},{t:'2026-01-01T00:00:01Z',equity:900}])")
    expect(equity.locator("canvas").first).to_be_visible()
    assert "900.00" in equity.inner_text()
    assert not report["pageErrors"], report["pageErrors"]
    report["interactions"].extend(["equity captions and keyboard", "equity stretch and light theme", "equity empty to duplicate timestamp recovery"])


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=CHROMIUM, args=["--no-sandbox"])
            for width in (1280, 390):
                page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2 if width == 390 else 1, is_mobile=width == 390, has_touch=width == 390)
                page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
                page.route("**/*", route_handler)
                page.goto(BASE + "/tests/chartMigration.fixture.html")
                page.wait_for_load_state("networkidle")
                page.wait_for_function("window.chartFixture && chartFixture.inspect().data.length > 0")
                for index, rule in enumerate("ABCDEFGHIJK"):
                    for side in ("long", "short"):
                        for average in (False, True):
                            verify_case(page, {"rule": rule, "side": side, "average": average, "variant": "default", "theme": "dark" if index % 2 == 0 else "light"}, width)
                    for variant in ("studio", "compact", "minimal"):
                        verify_case(page, {"rule": rule, "side": "short", "average": True, "variant": variant, "theme": "dark"}, width)
                    if rule in "DFGJK":
                        configure(page, {"variant": "default", "theme": "dark"})
                        screenshot(page, f"integrated-{rule}-{width}.png")
                if width == 1280:
                    verify_interactions(page)
                    verify_states(page)
                    verify_equity(page)
                else:
                    verify_mobile_touch(page)
                page.close()
                print(json.dumps({"width": width, "cases": 77, "passed": not report["pageErrors"]}), flush=True)
            browser.close()
        assert not report["pageErrors"], report["pageErrors"]
        assert all(item["method"] == "GET" for item in report["apiRequests"])
        report["passed"] = True
    finally:
        (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": report["passed"], "cases": len(report["cases"]), "interactions": report["interactions"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
