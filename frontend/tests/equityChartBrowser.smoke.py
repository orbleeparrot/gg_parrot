"""Check the real EquityChart component with local fixtures; never contact a backend.

Run Vite first; EQUITY_CHART_URL defaults to http://127.0.0.1:5182.
Screenshots and the report stay in docs/chart-migration-preview/integration/.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/chart-migration-preview/integration"
URL = os.environ.get("EQUITY_CHART_URL", "http://127.0.0.1:5182")
BROWSER = os.environ.get("BROWSER_EXECUTABLE_PATH", "/data/team/clcleh123/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSER, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"candles":[],"refresh_seconds":60}'))
        page.goto(URL + "/tests/chartMigration.fixture.html")
        page.wait_for_function("!!window.chartFixture")
        page.evaluate("chartFixture.configure({equity:true})")
        plot = page.locator(".equity-chart-plot")
        expect(plot.locator("canvas").first).to_be_visible()
        expect(page.locator(".equity-caption")).to_contain_text("시작 1.0K")
        plot.focus()
        page.keyboard.press("Home")
        expect(page.locator(".equity-chart-tooltip")).to_have_text("2026-01-01 · 1.0K")
        page.keyboard.press("End")
        expect(page.locator(".equity-chart-tooltip")).to_contain_text("2026-03-31")
        page.keyboard.press("Escape")
        expect(page.locator(".equity-chart-tooltip")).to_have_count(0)
        page.locator("#equity-under-test").screenshot(path=str(OUTPUT / "equity-desktop-dark.png"))
        checks.append("Start/end dates, amounts, endpoint keyboard access, Escape, dark rendering")
        # Repeated dates must keep separate samples: each remains keyboard-readable.
        curve = [{"t": "2026-01-01T00:00:00Z", "equity": value} for value in [1000, 800, 1400, 700, 1100]]
        page.evaluate("curve => chartFixture.setCurve(curve)", curve)
        expect(page.locator(".equity-caption")).to_contain_text("최종 1.1K")
        plot.focus()
        page.keyboard.press("Home")
        for index, expected in enumerate(["1.0K", "800.00", "1.4K", "700.00", "1.1K"]):
            if index:
                page.keyboard.press("ArrowRight")
            expect(page.locator(".equity-chart-tooltip")).to_have_text("2026-01-01 · " + expected)
        checks.append("Every repeated-date point retained in source order")
        page.evaluate("chartFixture.configure({theme:'light'})")
        page.keyboard.press("Escape")
        page.locator("#equity-under-test").screenshot(path=str(OUTPUT / "equity-desktop-light.png"))
        checks.append("Theme changes repaint existing canvas")
        long_curve = [{"t": f"2026-01-{1 + index % 28:02}T00:00:00Z", "equity": 1000 + index} for index in range(1000)]
        page.evaluate("curve => chartFixture.setCurve(curve)", long_curve)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('.equity-chart canvas').clientWidth < 390")
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        expect(page.locator(".equity-caption")).to_contain_text("최종 2.0K")
        plot.focus()
        page.keyboard.press("Home")
        expect(page.locator(".equity-chart-tooltip")).to_have_text("2026-01-01 · 1.0K")
        first_x = page.locator(".equity-chart-tooltip").evaluate("el => parseFloat(el.style.left)")
        page.keyboard.press("End")
        expect(page.locator(".equity-chart-tooltip")).to_have_text("2026-01-20 · 2.0K")
        last_x = page.locator(".equity-chart-tooltip").evaluate("el => parseFloat(el.style.left)")
        width = plot.bounding_box()["width"]
        assert 0 <= first_x < last_x <= width, (first_x, last_x, width)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.keyboard.press("Escape")
        page.locator("#equity-under-test").screenshot(path=str(OUTPUT / "equity-mobile-1000-points.png"))
        checks.append("All 1000 points remain in view at mobile width, no horizontal page overflow")
        page.evaluate("chartFixture.configure({stretch:true,theme:'dark'})")
        page.wait_for_timeout(100)
        stretch_height = plot.bounding_box()["height"]
        assert 56 <= stretch_height <= 220, stretch_height
        checks.append("Stretch layout remains within its result dock")
        for values in ([0, 0, 0], [-10, -20, -30], [10, 5, 0]):
            page.evaluate("values => chartFixture.setCurve(values.map((equity,i) => ({t:'2026-01-0'+(i+1)+'T00:00:00Z',equity})))", values)
            expect(plot.locator("canvas").first).to_be_visible()
        checks.append("Flat zero, negative, and loss curves render")
        page.evaluate("chartFixture.setCurve([])")
        expect(page.get_by_text("자산곡선 데이터가 없어요.", exact=True)).to_be_visible()
        expect(page.locator(".equity-chart canvas")).to_have_count(0)
        page.evaluate("chartFixture.setCurve([{t:'2026-01-01',equity:1000},{t:'2026-01-02',equity:1200}])")
        expect(plot.locator("canvas").first).to_be_visible()
        page.evaluate("chartFixture.configure({equity:false})")
        expect(page.locator(".equity-chart canvas")).to_have_count(0)
        checks.append("Empty, remount and unmount dispose the chart safely")
        assert not errors, errors
        browser.close()
    report = {"checks": checks, "passed": len(checks), "browser_errors": errors}
    (OUTPUT / "equity-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
