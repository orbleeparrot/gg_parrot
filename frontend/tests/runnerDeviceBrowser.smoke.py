"""Runner device handoff against a real frontend build and local API fixtures.

FRONTEND_BUILD=/tmp/runner-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/runnerDeviceBrowser.smoke.py
RUNNER_DEVICE_OUTPUT overrides /tmp/ggp-runner-device-check.
No backend is started; all non-local HTTP traffic is blocked.
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
OUTPUT = Path(os.environ.get("RUNNER_DEVICE_OUTPUT", "/tmp/ggp-runner-device-check")).resolve()
USER = {"id": 101, "username": "껄무새", "email": "parrot@example.com", "points_balance": 12500,
        "avatar_url": None, "bio": "", "can_change_password": True}

# label, viewport, navigator.platform, UA, touch points, browser mobile mode, support
DEVICES = [
    ("iphone", (375, 812), "iPhone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile", 5, True, False),
    ("android", (360, 800), "Linux armv8l", "Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile", 5, True, False),
    # iPad Safari identifies as a Mac when requesting desktop websites.
    ("ipad", (820, 1180), "MacIntel", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.0 Safari/605.1.15", 5, False, False),
    ("windows-small", (375, 812), "Win32", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", 0, False, True),
    ("windows", (1440, 1000), "Win32", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", 0, False, True),
    ("mac", (1440, 1000), "MacIntel", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)", 0, False, False),
]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


class Fixture:
    def __init__(self):
        self.requests = []

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        path = url.path
        if not path.startswith("/api/"):
            route.continue_()
            return
        self.requests.append(path)
        data = {"items": [], "symbols": [], "coins": [], "active": [], "recent": []}
        if path == "/api/auth/me":
            data = {"user": USER}
        elif path == "/api/auth/google/config":
            data = {"enabled": False}
        elif path == "/api/me/runner/key":
            data = {"key": "fixture-only-runner-key-aaaaaaaaaaaa"}
        elif path == "/api/me/dashboard":
            data = {"user": USER, "tier": {"name": "새싹"},
                    "totals": {"created": 0, "sales": 0, "earned": 0, "purchased": 0},
                    "created": [], "purchased": [], "sales": [], "ledger": [], "my_posts": []}
        elif path == "/api/runner/download/info":
            data = {"available": True, "url": "https://fixture.invalid/runner.exe", "version": "6",
                    "min_runner_version": "6", "supports_launch": True, "size": 8388608}
        elif path == "/api/kimchi-premium":
            data = {"ok": True, "premium_pct": .82, "updated_at": "2026-09-10T04:20:00Z"}
        elif path == "/api/fear-greed":
            data = {"ok": True, "value": 64, "classification_ko": "탐욕"}
        elif path == "/api/hangang-temp":
            data = {"ok": True, "temperature": 24.5}
        route.fulfill(json=data)


def open_context(browser, device, theme, errors):
    _, (width, height), platform, ua, touch, mobile, _ = device
    context = browser.new_context(
        viewport={"width": width, "height": height}, user_agent=ua,
        is_mobile=mobile, has_touch=touch > 0, color_scheme=theme, reduced_motion="reduce",
    )
    fixture = Fixture()
    context.route("**/*", fixture.route)
    # Explicit platform data prevents the host OS (usually Linux in CI) from
    # accidentally deciding whether the Windows runner is offered.
    config = {"platform": platform, "touch": touch, "user": USER}
    context.add_init_script("""(() => {
      const fixture = """ + json.dumps(config) + """;
      Object.defineProperty(navigator, 'platform', {get: () => fixture.platform});
      Object.defineProperty(navigator, 'maxTouchPoints', {get: () => fixture.touch});
      Object.defineProperty(navigator, 'userAgentData', {get: () => undefined});
      localStorage.setItem('ggp_token', 'fixture-a');
      localStorage.setItem('ggp_user', JSON.stringify(fixture.user));
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        writeText: async value => { window.copiedAddress = value; }
      }});
    })();""")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, fixture


def assert_no_overflow(page):
    dimensions = page.evaluate("[innerWidth, document.documentElement.scrollWidth]")
    assert dimensions[1] <= dimensions[0], dimensions


def check_install(page, fixture, origin, supported):
    page.goto(origin + "/runner/install", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    if supported:
        expect(page.get_by_role("link", name="실행기 내려받기", exact=True)).to_be_visible()
        expect(page.locator(".runner-device-handoff")).to_have_count(0)
    else:
        expect(page.get_by_role("heading", name="실행은 Windows PC에서")).to_be_visible()
        expect(page.locator('a[href$=".exe"]')).to_have_count(0)
        expect(page.locator(".runner-install-step")).to_have_count(0)
        page.get_by_role("button", name="PC 설치 주소 복사", exact=True).click()
        assert page.evaluate("window.copiedAddress") == origin + "/runner/install"
        assert "/api/me/runner/key" not in fixture.requests, fixture.requests
        assert "/api/runner/download/info" not in fixture.requests, fixture.requests
    assert_no_overflow(page)


def check_wizard(page, fixture, origin, supported):
    fixture.requests.clear()
    # A direct URL requesting an advanced step must also respect the OS gate.
    page.goto(origin + "/runner?step=4", wait_until="networkidle")
    if supported:
        expect(page.locator(".runner-wizard")).to_be_visible()
        expect(page.locator(".runner-device-handoff")).to_have_count(0)
    else:
        expect(page.get_by_role("heading", name="실행은 Windows PC에서")).to_be_visible()
        expect(page.locator(".runner-wizard")).to_have_count(0)
        expect(page.locator('a[href^="ggparrot:"]')).to_have_count(0)
        assert not any("launch-ticket" in path or path == "/api/me/runner/key"
                       for path in fixture.requests), fixture.requests
        page.get_by_role("button", name="PC 실행 주소 복사", exact=True).click()
        expected_address = origin + "/?run=1&step=1"
        assert page.evaluate("window.copiedAddress") == expected_address
        page.evaluate("() => { navigator.clipboard.writeText = async () => { throw new Error('denied'); }; }")
        page.get_by_role("button", name="주소 복사됨", exact=True).click()
        address = page.get_by_label("주소를 복사해 Windows PC에서 여세요.")
        expect(address).to_have_value(expected_address)
        expect(address).to_be_focused()
        navigation = page.get_by_role("navigation", name="이 기기에서 할 수 있는 일")
        expect(navigation.get_by_role("link", name="직접 만들기", exact=True)).to_have_attribute("href", "/builder")
        expect(navigation.get_by_role("link", name="내 에이전트", exact=True)).to_have_attribute("href", "/agents")
        assert page.locator(".runner-device-handoff").bounding_box()["x"] >= 15
    assert_no_overflow(page)


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors, checks = [], []
    report = {"passed": False, "checks": checks, "page_errors": errors, "build": str(BUILD)}
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            for device in DEVICES:
                label, *_, supported = device
                for theme in ("dark", "light") if label == "iphone" else ("dark",):
                    context, page, fixture = open_context(browser, device, theme, errors)
                    try:
                        for scene, check in (("install", check_install), ("wizard", check_wizard)):
                            check(page, fixture, origin, supported)
                            name = f"{label}-{theme}-{scene}"
                            page.screenshot(path=str(OUTPUT / f"{name}.png"), full_page=True)
                            checks.append(name)
                            print(f"{name} passed", flush=True)
                    finally:
                        context.close()
            browser.close()
        assert not errors, errors
        report["passed"] = True
    finally:
        server.shutdown()
        server.server_close()
        (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": True, "checks": len(checks), "report": str(OUTPUT / "report.json")}))


if __name__ == "__main__":
    main()
