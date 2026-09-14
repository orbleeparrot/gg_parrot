"""Real builder retry regression with a local build and fully mocked APIs.

FRONTEND_BUILD selects the build. SYMBOL_PICKER_REPORT selects the JSON report.
No production account, backend, or exchange API is used.
"""
import importlib.util
import json
import os
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("symbol_picker_fixture", Path(__file__).with_name("accountIsolationBrowser.smoke.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
OUTPUT = Path(os.environ.get("SYMBOL_PICKER_REPORT", str(ROOT / "docs/symbol-catalog-recovery-2026-09-14/symbol-picker-browser.json")))


class Fixture(fixtures.Fixture):
    def __init__(self):
        super().__init__()
        self.symbol_calls = 0
        self.pending = []

    def route(self, route):
        if urlsplit(route.request.url).hostname not in {"127.0.0.1", "localhost"} and route.request.resource_type == "image":
            route.fulfill(content_type="image/png", body=fixtures.profile.PNG)
            return
        if urlsplit(route.request.url).path == "/api/symbols":
            self.symbol_calls += 1
            if self.symbol_calls == 1:
                route.fulfill(status=503, json={"detail": "fixture: upstream unavailable"})
            else:
                self.pending.append(route)
            return
        super().route(route)


def main():
    assert (fixtures.BUILD / "index.html").is_file(), "Build the frontend and set FRONTEND_BUILD"
    fixtures.profile.BUILD = fixtures.BUILD
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(fixtures.profile.Handler, directory=str(fixtures.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=fixtures.CHROME, headless=True, args=["--no-sandbox"])
            for width in (1440, 390):
                context = browser.new_context(viewport={"width": width, "height": 1000},
                                              has_touch=width == 390, is_mobile=width == 390, service_workers="block")
                fixture = Fixture()
                context.route("**/*", fixture.route)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/builder")
                search = page.get_by_role("combobox", name="종목 검색")
                search.fill("DOT")
                results = page.get_by_role("listbox", name="종목 검색 결과")
                retry = results.get_by_role("button", name="다시 시도", exact=True)
                expect(retry).to_be_visible()
                if width == 390:
                    retry.tap()
                else:
                    retry.click()
                expect(results.get_by_text("종목 목록을 불러오는 중…", exact=True)).to_be_visible()
                # Exceed the input's 120ms blur-close delay while the request is pending.
                page.wait_for_timeout(250)
                expect(results).to_be_visible()
                expect(search).to_be_focused()
                assert fixture.symbol_calls == 2
                assert fixture.pending
                for pending in fixture.pending:
                    pending.fulfill(json={"items": [{"symbol": "DOTUSDT", "base": "DOT", "quote": "USDT", "spot": True, "futures": True}]})
                choice = results.get_by_role("option", name="DOTUSDT", exact=True)
                expect(choice).to_be_visible()
                choice.click()
                expect(page.get_by_role("list", name="고른 종목").get_by_role("button", name="DOTUSDT 빼기", exact=True)).to_be_visible()
                assert not fixture.blocked, fixture.blocked
                checks.append({"width": width, "passed": True, "symbol_requests": fixture.symbol_calls,
                               "scenario": "503 → retry with delayed response → popup stays open → DOTUSDT selected without refocusing"})
                context.close()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"checks": checks, "page_errors": errors, "frontend_build": str(fixtures.BUILD),
                                  "scope": "All API and WebSocket traffic mocked; no production connections"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(checks)} symbol-picker retry browser checks passed")


if __name__ == "__main__":
    main()
