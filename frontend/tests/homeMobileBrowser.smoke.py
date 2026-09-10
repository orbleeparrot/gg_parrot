"""Home document flow and device actions against a real build and local fixtures.

Build first, then run with Python Playwright and Chromium:
  FRONTEND_BUILD=/tmp/home-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
    python frontend/tests/homeMobileBrowser.smoke.py
HOME_MOBILE_OUTPUT selects screenshots/report (default /tmp/ggp-home-mobile-check).
Only a temporary local static server runs. External requests are blocked and every
API response is a fixture; no production backend or account is used.
"""

import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


FRONTEND = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("FRONTEND_BUILD", FRONTEND / "dist")).resolve()
OUTPUT = Path(os.environ.get("HOME_MOBILE_OUTPUT", "/tmp/ggp-home-mobile-check")).resolve()
DEVICE_CASES = [
    ("iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", "매크로 둘러보기"),
    ("android", "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/128.0.0.0 Mobile Safari/537.36", "매크로 둘러보기"),
    ("windows-narrow", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36", "빠른 실행"),
]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


def fixtures(route):
    url = urlsplit(route.request.url)
    if url.hostname not in {"127.0.0.1", "localhost"}:
        route.abort()
        return
    if not url.path.startswith("/api/"):
        route.continue_()
        return
    data = {"items": [], "symbols": [], "coins": [], "active": [], "recent": []}
    if url.path == "/api/auth/google/config":
        data = {"enabled": False}
    elif url.path == "/api/kimchi-premium":
        data = {"ok": True, "premium_pct": .82, "label": "김프", "updated_at": "2026-09-10T04:20:00Z"}
    elif url.path == "/api/fear-greed":
        data = {"ok": True, "value": 64, "classification_ko": "탐욕"}
    elif url.path == "/api/hangang-temp":
        data = {"ok": True, "temperature": 24.5}
    route.fulfill(json=data)


def open_page(browser, origin, errors, *, width, theme="dark", height=844, user_agent=None, clock=False):
    options = {"viewport": {"width": width, "height": height}, "color_scheme": theme, "reduced_motion": "no-preference"}
    if user_agent:
        options["user_agent"] = user_agent
    context = browser.new_context(**options)
    context.route("**/*", fixtures)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    if clock:
        page.clock.install()
    page.goto(origin + "/", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    return context, page


def assert_static_home(page):
    expect(page.locator(".home-mobile-stack")).to_be_visible()
    expect(page.locator(".home-hero-shell, .home-hero-pagination, .home-entry-mascot")).to_have_count(0)
    expect(page.locator(".home-community-post-list")).to_have_count(1)
    expect(page.locator(".home-community-post-list li")).to_have_count(5)
    expect(page.locator(".home-mobile-stack h1")).to_have_count(1)
    expect(page.locator(".home-mobile-stack #home-community-title")).to_have_count(1)
    assert page.locator(".home-community-post-track").evaluate(
        "el => getComputedStyle(el).animationName === 'none' && getComputedStyle(el).transform === 'none'"
    )


def measure(page):
    return page.evaluate("""() => {
      const frame = document.querySelector('.home-mobile-stack');
      const belt = document.querySelector('.home-community-post-track');
      const underline = getComputedStyle(document.querySelector('.home-entry-title span'));
      const nested = [...frame.querySelectorAll('*')].filter(el =>
        ['auto','scroll'].includes(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 1
      ).map(el => el.className);
      return {
        width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight, viewport: innerHeight,
        animation: getComputedStyle(belt).animationName,
        transform: getComputedStyle(belt).transform,
        underline: underline.textDecorationThickness, nested,
      };
    }""")


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors, checks = [], {}
    report = {"passed": False, "build": str(BUILD), "checks": checks, "page_errors": errors}
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            for width, theme in [(320, "dark"), (390, "dark"), (390, "light"), (768, "dark"), (1099, "light")]:
                context, page = open_page(browser, origin, errors, width=width, theme=theme)
                assert_static_home(page)
                data = measure(page)
                assert data["scrollWidth"] <= width, data
                assert data["scrollHeight"] > data["viewport"], data
                assert not data["nested"], data
                assert float(data["underline"].removesuffix("px")) >= 4, data
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                assert page.evaluate("window.scrollY") > 0
                expect(page.locator(".home-community-post-list li").last).to_be_in_viewport()
                assert page.locator(".home-community-preview").evaluate(
                    "el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; }"
                )
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(OUTPUT / f"home-{width}-{theme}.png"), full_page=True)
                checks[f"mobile-{width}-{theme}"] = data
                context.close()

            # Advance beyond two desktop dwell periods with normal motion enabled.
            # Mobile content must remain static even while browser timers advance.
            context, page = open_page(browser, origin, errors, width=390, clock=True)
            assert_static_home(page)
            before = measure(page)
            page.clock.fast_forward(25_000)
            assert_static_home(page)
            after = measure(page)
            assert after == before, {"before": before, "after": after}
            checks["mobile-static-after-25-seconds"] = "passed"
            context.close()

            for device_name, user_agent, expected in DEVICE_CASES:
                context, page = open_page(browser, origin, errors, width=390, user_agent=user_agent)
                choice = page.locator("[data-home-entry-primary]")
                expect(choice.locator("strong")).to_have_text(expected)
                if device_name != "windows-narrow":
                    expect(choice).to_contain_text("실행은 Windows PC에서 이어가요.")
                    page.screenshot(path=str(OUTPUT / f"home-{device_name}.png"), full_page=True)
                choice.click()
                page.wait_for_url("**/login?**" if device_name == "windows-narrow" else "**/leaderboard")
                if device_name == "windows-narrow":
                    assert parse_qs(urlsplit(page.url).query)["next"] == ["/?run=1&step=1&view=leaderboard"]
                checks[f"cta-{device_name}"] = "passed"
                context.close()

            context, page = open_page(browser, origin, errors, width=1440, height=1000)
            expect(page.locator(".home-hero-shell")).to_be_visible()
            expect(page.locator(".home-mobile-stack")).to_have_count(0)
            expect(page.locator(".home-entry-mascot")).to_be_visible()
            expect(page.locator(".home-hero-pagination button")).to_have_count(2)
            page.get_by_role("button", name="두 번째 화면, 커뮤니티", exact=True).click()
            expect(page.locator(".home-entry-hero.is-community")).to_be_visible()
            expect(page.locator(".home-community-post-list")).to_have_count(2)
            assert page.locator(".home-community-post-track").evaluate(
                "el => getComputedStyle(el).animationName"
            ) == "home-community-post-belt"
            expect(page.locator(".home-hero-slide-frame.is-exiting")).to_have_count(0)
            page.screenshot(path=str(OUTPUT / "home-desktop-community.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert_static_home(page)
            page.set_viewport_size({"width": 1440, "height": 1000})
            expect(page.locator(".home-hero-shell")).to_be_visible()
            expect(page.locator(".home-hero-pagination button")).to_have_count(2)
            checks["desktop-carousel-and-breakpoint-resize"] = "passed"
            context.close()
            browser.close()
        assert not errors, errors
        report["passed"] = True
        print(json.dumps({"passed": True, "checks": len(checks), "report": str(OUTPUT / "report.json")}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
