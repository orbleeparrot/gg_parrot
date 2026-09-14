"""Profile identity must render before the slower activity rollup.

Uses a real build and held local API fixtures; no real account or external API.
FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH select the build and Chromium.
"""
import copy
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("profile", Path(__file__).with_name("profileAvatarBrowser.smoke.py"))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
OUTPUT = Path(os.environ.get("PROFILE_LOADING_OUTPUT", "/tmp/ggp-profile-loading-check"))


class Fixture(profile.Fixtures):
    def __init__(self):
        super().__init__()
        self.pending = []
        self.dashboard_calls = 0
        self.gateway_failures = 0
        self.users["fixture-b"]["points_balance"] = 3750
        self.set_photo()

    def route(self, route):
        if urlsplit(route.request.url).path == "/api/me/dashboard":
            self.dashboard_calls += 1
            if self.gateway_failures:
                self.gateway_failures -= 1
                route.fulfill(status=502, content_type="text/html", body="<html><body>Bad Gateway</body></html>")
                return
            token = route.request.headers.get("authorization", "").removeprefix("Bearer ")
            self.pending.append((route, copy.deepcopy(self.dashboard(self.users[token]))))
            return
        super().route(route)

    def release(self, status=200):
        pending, self.pending = self.pending, []
        for route, payload in pending:
            route.fulfill(status=status, json=payload if status == 200 else {"detail": "활동 조회 실패"})


def wait_for_dashboard(page, fixture):
    deadline = time.monotonic() + 5
    while not fixture.pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert fixture.pending, "The dashboard request did not reach its fixture"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(profile.Handler, directory=str(profile.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, checks = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ["BROWSER_EXECUTABLE_PATH"], args=["--no-sandbox"])

            def open_profile(fixture, width):
                context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce", service_workers="block")
                context.route("**/*", fixture.route)
                context.route_web_socket("**/*", lambda socket: socket.close())
                context.add_init_script("localStorage.setItem('ggp_token','fixture-a');localStorage.setItem('ggp_user'," + json.dumps(json.dumps(fixture.users["fixture-a"])) + ");")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/mypage", wait_until="domcontentloaded")
                return context, page

            for width in (390, 1440):
                fixture = Fixture()
                context, page = open_profile(fixture, width)
                portrait = page.locator(".me-avatar img")
                expect(portrait).to_be_visible()
                expect(portrait).to_have_js_property("naturalWidth", 8)
                expect(page.locator(".me-name")).to_have_text("껄무새")
                expect(page.locator(".me-content[aria-busy='true']")).to_be_visible()
                expect(page.locator(".me-stat dd")).to_have_count(0)
                # Loading the rollup must not replace/repaint the identity subtree.
                portrait.evaluate("element => { window.initialPortrait = element; }")
                wait_for_dashboard(page, fixture)
                fixture.release()
                expect(page.locator(".me-stat dd").first).to_contain_text("12,500")
                assert portrait.evaluate("element => element === window.initialPortrait")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                checks.append(f"identity-before-activity-{width}")

                # Switching accounts cannot flash the previous account's activity.
                page.evaluate("""user => {
                    localStorage.setItem('ggp_token', 'fixture-b');
                    localStorage.setItem('ggp_user', JSON.stringify(user));
                    window.dispatchEvent(new StorageEvent('storage', {key:'ggp_token'}));
                }""", fixture.users["fixture-b"])
                expect(page.locator(".me-name")).to_have_text("다른회원")
                expect(page.locator(".me-content[aria-busy='true']")).to_be_visible()
                expect(page.locator(".me-stat dd")).to_have_count(0)
                page.wait_for_function("document.querySelector('.me-name')?.textContent === '다른회원'")
                wait_for_dashboard(page, fixture)
                fixture.release(status=503)
                expect(page.get_by_role("alert")).to_contain_text("활동 조회 실패")
                expect(page.locator(".me-name")).to_have_text("다른회원")
                expect(page.get_by_role("link", name="프로필 설정", exact=True)).to_be_visible()
                checks.append(f"account-switch-and-activity-failure-{width}")
                before_retry = fixture.dashboard_calls
                page.get_by_role("button", name="다시 시도", exact=True).click()
                expect(page.locator(".me-content[aria-busy='true']")).to_be_visible()
                expect(page.locator(".me-name")).to_have_text("다른회원")
                wait_for_dashboard(page, fixture)
                assert fixture.dashboard_calls == before_retry + 1
                fixture.release()
                expect(page.locator(".me-stat dd").first).to_contain_text("3,750")
                expect(page.locator(".me-name")).to_have_text("다른회원")
                expect(page.get_by_role("alert")).to_have_count(0)
                checks.append(f"same-account-manual-retry-after-json-503-{width}")
                page.get_by_role("button", name="로그아웃", exact=True).click()
                expect(page).to_have_url(re.compile(r"/login$"))
                assert page.evaluate("localStorage.getItem('ggp_token')") is None
                assert not fixture.unexpected, fixture.unexpected
                context.close()

                fixture = Fixture()
                fixture.gateway_failures = 1
                context, page = open_profile(fixture, width)
                portrait = page.locator(".me-avatar img")
                expect(portrait).to_be_visible()
                expect(page.locator(".me-name")).to_have_text("껄무새")
                portrait.evaluate("element => { window.gatewayPortrait = element; }")
                expect(page.locator(".me-content[aria-busy='true']")).to_be_visible()
                # First HTML 502 is retried automatically; hold the successful
                # second response to verify the profile stays usable meanwhile.
                wait_for_dashboard(page, fixture)
                assert fixture.dashboard_calls == 2
                expect(page.get_by_role("alert")).to_have_count(0)
                expect(page.locator(".me-name")).to_have_text("껄무새")
                fixture.release()
                expect(page.locator(".me-stat dd").first).to_contain_text("12,500")
                assert portrait.evaluate("element => element === window.gatewayPortrait")
                assert not fixture.unexpected, fixture.unexpected
                checks.append(f"html-502-automatic-recovery-retains-identity-{width}")
                context.close()

                fixture = Fixture()
                fixture.gateway_failures = 3
                context, page = open_profile(fixture, width)
                expect(page.locator(".me-name")).to_have_text("껄무새")
                alert = page.get_by_role("alert")
                expect(alert).to_contain_text("서버에 일시적으로 연결하지 못했어요.", timeout=10_000)
                expect(alert).not_to_contain_text("API 대신 페이지")
                assert fixture.dashboard_calls == 3
                expect(page.locator(".me-name")).to_have_text("껄무새")
                expect(page.locator(".me-avatar img")).to_be_visible()
                page.get_by_role("button", name="다시 시도", exact=True).click()
                expect(page.locator(".me-content[aria-busy='true']")).to_be_visible()
                wait_for_dashboard(page, fixture)
                assert fixture.dashboard_calls == 4
                fixture.release()
                expect(page.locator(".me-stat dd").first).to_contain_text("12,500")
                expect(page.locator(".me-name")).to_have_text("껄무새")
                expect(page.get_by_role("alert")).to_have_count(0)
                assert not fixture.unexpected, fixture.unexpected
                checks.append(f"exhausted-html-502-friendly-error-and-manual-recovery-{width}")
                context.close()
            browser.close()
        assert not errors, errors
        (OUTPUT / "report.json").write_text(json.dumps({"passed": True, "checks": checks, "page_errors": errors,
            "frontend_build": str(profile.BUILD),
            "build_index_sha256": hashlib.sha256((profile.BUILD / "index.html").read_bytes()).hexdigest(),
            "scope": "All APIs, avatars and WebSockets mocked; no production connections"}, ensure_ascii=False, indent=2) + "\n")
        print(f"{len(checks)} profile-loading and gateway-recovery browser checks passed")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
