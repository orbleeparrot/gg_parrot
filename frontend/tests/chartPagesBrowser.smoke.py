"""Real /builder and /agents route smoke with intercepted API fixtures."""
import importlib.util
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

spec = importlib.util.spec_from_file_location("chart_fixtures", Path(__file__).with_name("chartMigrationBrowser.smoke.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
result = {"passed": False, "pages": [], "errors": []}
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=fixtures.CHROMIUM, args=["--no-sandbox"])
        for width in (1440, 390):
            for route in ("builder", "agents"):
                page = browser.new_page(viewport={"width": width, "height": 1000 if width == 1440 else 844}, is_mobile=width == 390, has_touch=width == 390)
                page.route("**/*", fixtures.route_handler)
                page.on("pageerror", lambda error: result["errors"].append(str(error)))
                page.add_init_script("localStorage.setItem('ggp_theme','dark');localStorage.setItem('ggp_token','chart-fixture');localStorage.setItem('ggp_user',JSON.stringify({id:1,username:'차트 검증',email:'chart@example.invalid'}));")
                page.goto(fixtures.BASE + "/" + route)
                page.wait_for_load_state("networkidle")
                chart = page.locator(".financial-candle-plot").first
                expect(chart).to_be_visible(timeout=15000)
                expect(chart.locator("canvas").first).to_be_visible()
                chart.scroll_into_view_if_needed()
                page.wait_for_timeout(100)
                if route == "agents":
                    expect(page.get_by_text("내 평단(평균 진입가)", exact=True)).to_be_visible()
                    expect(page.get_by_text("상단 밴드", exact=True)).to_be_visible()
                else:
                    expect(page.get_by_role("group", name="차트 봉 간격", exact=True)).to_be_visible()
                    page.get_by_role("button", name="5분", exact=True).click()
                    expect(page.get_by_role("button", name="5분", exact=True)).to_have_attribute("aria-pressed", "true")
                    expect(chart.locator("canvas").first).to_be_visible()
                dimensions = chart.bounding_box()
                assert dimensions["height"] > 100 and dimensions["width"] > 200, dimensions
                if route == "builder" and width == 390:
                    assert dimensions["height"] >= 240, "mobile candle plot became too short"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (route, width, "horizontal overflow")
                file_name = f"page-{route}-{width}.png"
                page.screenshot(path=str(fixtures.OUTPUT / file_name), full_page=False)
                result["pages"].append({"route": "/" + route, "width": width, "chartDimensions": dimensions, "screenshot": file_name})
                if route == "builder":
                    page.get_by_label("매매 방식", exact=True).select_option("F")
                    expect(chart).to_have_class("financial-candle-plot has-rsi")
                    chart.scroll_into_view_if_needed()
                    page.wait_for_timeout(150)
                    assert "RSI 보조지표" in chart.text_content()
                    if width == 390:
                        assert chart.bounding_box()["height"] >= 350, "mobile RSI pane lost plot space"
                    rsi_name = f"page-builder-rsi-{width}.png"
                    page.screenshot(path=str(fixtures.OUTPUT / rsi_name), full_page=False)
                    result["pages"].append({"route": "/builder", "strategy": "F", "width": width, "chartDimensions": chart.bounding_box(), "screenshot": rsi_name})
                page.close()
        browser.close()
    assert not result["errors"], result["errors"]
    result["passed"] = True
finally:
    (fixtures.OUTPUT / "pages-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False), flush=True)
