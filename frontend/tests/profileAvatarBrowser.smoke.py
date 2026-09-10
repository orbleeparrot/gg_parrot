"""Profile/avatar regressions against a real frontend build and local fixtures.

Build first, then run with Python Playwright and Chromium:
  FRONTEND_BUILD=/tmp/profile-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
    python frontend/tests/profileAvatarBrowser.smoke.py
PROFILE_BROWSER_OUTPUT selects screenshots/report (default /tmp/ggp-profile-final-check).
No production backend is started or contacted. Every API response is a fixture.
"""

import copy
import json
import os
import struct
import threading
import zlib
from email.parser import BytesParser
from email.policy import default as email_policy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


FRONTEND = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("FRONTEND_BUILD", FRONTEND / "dist")).resolve()
OUTPUT = Path(os.environ.get("PROFILE_BROWSER_OUTPUT", "/tmp/ggp-profile-final-check")).resolve()
USER_A = {"id": 101, "username": "껄무새", "email": "parrot@example.com", "points_balance": 12500, "created_at": "2026-08-01T00:00:00Z", "avatar_url": None}
USER_B = {**USER_A, "id": 102, "username": "다른회원", "email": "another@example.com"}
TIERS = [("새싹", 0), ("브론즈", 1), ("실버", 5), ("골드", 15), ("다이아", 40)]


def tiny_png():
    """A valid 8×8 yellow PNG; no image package or remote asset required."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    pixels = b"".join(b"\x00" + b"\xff\xd6\x2e" * 8 for _ in range(8))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


PNG = tiny_png()
UPLOAD = {"name": "profile.png", "mimeType": "image/png", "buffer": PNG}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


class Fixtures:
    def __init__(self):
        self.users = {"fixture-a": copy.deepcopy(USER_A), "fixture-b": copy.deepcopy(USER_B)}
        self.version = 0
        self.avatar_requests = []
        self.held_me = []
        self.held_uploads = []
        self.hold_me = False
        self.hold_upload = False
        self.reject_upload = False
        self.tier_index = 1
        self.unexpected = []

    def set_photo(self, token="fixture-a"):
        self.version += 1
        user = self.users[token]
        user["avatar_url"] = f"/api/avatars/{user['id']}?v=fixture-{self.version}"
        return user["avatar_url"]

    def dashboard(self, user):
        name, at = TIERS[self.tier_index]
        next_tier = TIERS[self.tier_index + 1] if self.tier_index < len(TIERS) - 1 else None
        return {
            "user": copy.deepcopy(user),
            "tier": {"name": name, "sales": at, "next_name": next_tier[0] if next_tier else None, "next_at": next_tier[1] if next_tier else None, "to_next": next_tier[1] - at if next_tier else 0, "ladder": [{"name": label, "at": threshold} for label, threshold in TIERS]},
            "totals": {"created": 0, "sales": at, "earned": 700, "purchased": 0},
            "created": [], "purchased": [], "sales": [], "ledger": [], "my_posts": [],
        }

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        if url.hostname not in {"127.0.0.1", "localhost"}:
            self.unexpected.append(request.url)
            route.abort()
            return
        if not path.startswith("/api/"):
            route.continue_()
            return
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        user = self.users.get(token)
        if path.startswith("/api/avatars/"):
            route.fulfill(content_type="image/png", body=PNG)
            return
        if path == "/api/auth/me":
            payload = {"user": copy.deepcopy(user)}
            if self.hold_me:
                self.held_me.append((route, payload))
            else:
                route.fulfill(json=payload)
            return
        if path == "/api/me/dashboard":
            route.fulfill(json=self.dashboard(user))
            return
        if path == "/api/me/avatar":
            self.avatar_requests.append((request.method, token))
            if request.method == "POST":
                content_type = request.headers.get("content-type", "")
                assert content_type.startswith("multipart/form-data; boundary="), content_type
                body = request.post_data_buffer
                parsed = BytesParser(policy=email_policy).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
                parts = list(parsed.iter_parts())
                assert len(parts) == 1 and parts[0].get_param("name", header="content-disposition") == "image"
                assert parts[0].get_filename() == "profile.png"
                assert parts[0].get_payload(decode=True) == PNG
                if self.reject_upload:
                    route.fulfill(status=400, json={"detail": "사진을 처리하지 못했어요."})
                    return
                self.set_photo(token)
            else:
                assert request.method == "DELETE", request.method
                user["avatar_url"] = None
            payload = {"user": copy.deepcopy(user)}
            if request.method == "POST" and self.hold_upload:
                self.held_uploads.append((route, payload))
            else:
                route.fulfill(json=payload)
            return
        fixtures = {
            "/api/kimchi-premium": {"ok": True, "premium_pct": .82},
            "/api/fear-greed": {"ok": True, "value": 64},
            "/api/hangang-temp": {"ok": True, "temperature": 24},
            "/api/hot-coins": {"items": []},
        }
        if path not in fixtures:
            self.unexpected.append(path)
            route.fulfill(status=404, json={"detail": "Unexpected fixture API"})
        else:
            route.fulfill(json=fixtures[path])

    def release_me(self):
        self.hold_me = False
        pending, self.held_me = self.held_me, []
        for route, payload in pending:
            route.fulfill(json=payload)

    def release_uploads(self):
        self.hold_upload = False
        pending, self.held_uploads = self.held_uploads, []
        for route, payload in pending:
            route.fulfill(json=payload)


class Suite:
    def __init__(self, browser, origin):
        self.browser, self.origin = browser, origin
        self.errors = []
        self.checks = []
        self.screenshots = []

    def open(self, fixture, width=1440, theme="dark"):
        context = self.browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme, reduced_motion="reduce")
        context.route("**/*", fixture.route)
        # A reload must use the last saved account/photo instead of resetting it.
        context.add_init_script("if (!localStorage.getItem('ggp_token')) { localStorage.setItem('ggp_token','fixture-a'); localStorage.setItem('ggp_user'," + json.dumps(json.dumps(fixture.users["fixture-a"])) + "); }")
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.origin + "/mypage", wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_visible()
        return context, page

    def record(self, name, fixture):
        assert not fixture.unexpected, fixture.unexpected
        assert not self.errors, self.errors
        self.checks.append(name)
        print(json.dumps({name: "passed"}, ensure_ascii=False), flush=True)

    def screenshot(self, page, name):
        path = OUTPUT / f"{name}.png"
        page.screenshot(path=str(path), full_page=True, animations="disabled")
        self.screenshots.append(str(path))


def expect_photo(page, url):
    for selector in (".me-avatar", ".account-trigger .user-avatar"):
        image = page.locator(selector).locator("img")
        if url:
            expect(image).to_have_attribute("src", url)
            expect(image).to_be_visible()
            expect(image).to_have_js_property("naturalWidth", 8)
        else:
            expect(image).to_have_count(0)


def main():
    if not (BUILD / "index.html").is_file():
        raise SystemExit(f"Build missing: {BUILD}; build first and set FRONTEND_BUILD")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            suite = Suite(browser, f"http://127.0.0.1:{server.server_port}")
            for width in (320, 375, 1440):
                for theme in ("light", "dark"):
                    fixture = Fixtures()
                    context, page = suite.open(fixture, width, theme)
                    tier = page.locator(".me-tier")
                    expect(tier).not_to_have_attribute("open", "")
                    expect(page.get_by_role("list", name="판매 등급별 조건")).to_be_hidden()
                    expect(tier.locator("summary")).to_contain_text("브론즈")
                    expect(tier.locator("summary")).to_contain_text("실버까지 판매 4건")
                    colors = page.locator(".me-stat.is-points dd").evaluate("""el => {
                      const sample = document.createElement('span'); sample.style.color='rgb(var(--c-brand-line))'; el.append(sample);
                      const result={actual:getComputedStyle(el).color, expected:getComputedStyle(sample).color}; sample.remove(); return result;
                    }""")
                    assert colors["actual"] == colors["expected"], colors
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, theme)
                    suite.screenshot(page, f"profile-{width}-{theme}")
                    tier.locator("summary").click()
                    expect(page.get_by_role("list", name="판매 등급별 조건")).to_be_visible()
                    expect(tier.locator("li")).to_have_count(5)
                    expect(tier.locator("li[aria-current='step']")).to_contain_text("브론즈")
                    for name, threshold in TIERS:
                        step = tier.locator("li").filter(has=page.get_by_text(name, exact=True))
                        expect(step).to_contain_text(f"판매 {threshold}건" if threshold else "시작")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, theme, "expanded")
                    if width in (320, 1440): suite.screenshot(page, f"profile-tier-{width}-{theme}")
                    tier.locator("summary").focus()
                    page.keyboard.press("Enter")
                    expect(page.get_by_role("list", name="판매 등급별 조건")).to_be_hidden()
                    suite.record(f"layout-tier-points-{width}-{theme}", fixture)
                    context.close()

            fixture = Fixtures()
            fixture.tier_index = 4
            context, page = suite.open(fixture)
            expect(page.locator(".me-tier summary")).to_contain_text("다이아")
            expect(page.locator(".me-tier summary")).to_contain_text("가장 높은 등급")
            suite.record("highest-tier-summary", fixture)
            context.close()

            fixture = Fixtures()
            fixture.users["fixture-a"].update(username="가나다라마바사아자차카타파하가나다라마바", points_balance=2147483647)
            context, page = suite.open(fixture, width=320)
            expect(page.get_by_role("heading", name=fixture.users["fixture-a"]["username"], exact=True)).to_be_visible()
            expect(page.locator(".me-stat.is-points dd")).to_have_text("2,147,483,647P")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator(".me-tier summary").click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            suite.screenshot(page, "profile-long-name-large-points-320-dark")
            suite.record("long-name-large-points-320", fixture)
            context.close()

            fixture = Fixtures()
            context, page = suite.open(fixture)
            field = page.get_by_label("프로필 사진 파일", exact=True)
            field.set_input_files(UPLOAD)
            expect(page.get_by_role("button", name="사진 삭제", exact=True)).to_be_visible()
            expect_photo(page, fixture.users["fixture-a"]["avatar_url"])
            assert fixture.avatar_requests == [("POST", "fixture-a")]
            saved = fixture.users["fixture-a"]["avatar_url"]
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_visible()
            expect_photo(page, saved)
            field.set_input_files({"name": "too-large.png", "mimeType": "image/png", "buffer": b"x" * (2 * 1024 * 1024 + 1)})
            expect(page.get_by_role("alert")).to_contain_text("2MB 이하")
            assert len(fixture.avatar_requests) == 1
            expect_photo(page, saved)
            fixture.reject_upload = True
            field.set_input_files(UPLOAD)
            expect(page.get_by_role("alert")).to_contain_text("사진을 처리하지 못했어요")
            expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_enabled()
            expect_photo(page, saved)
            fixture.reject_upload = False
            field.set_input_files(UPLOAD)
            expect(page.locator(".me-photo")).to_have_attribute("aria-busy", "false")
            expect(page.get_by_role("alert")).to_have_count(0)
            expect_photo(page, fixture.users["fixture-a"]["avatar_url"])
            page.get_by_role("button", name="사진 삭제", exact=True).click()
            expect_photo(page, None)
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_visible()
            expect_photo(page, None)
            suite.record("multipart-upload-reload-size-rejection-recovery-delete", fixture)
            context.close()

            for operation in ("upload", "delete"):
                fixture = Fixtures()
                if operation == "delete": fixture.set_photo()
                fixture.hold_me = True
                context, page = suite.open(fixture)
                assert fixture.held_me, "The header GET must still be held"
                # A unique balance makes processing this held response observable;
                # matching a photo already on screen would otherwise pass early.
                for _, payload in fixture.held_me:
                    payload["user"]["points_balance"] = 54321
                if operation == "upload":
                    page.get_by_label("프로필 사진 파일", exact=True).set_input_files(UPLOAD)
                    expect(page.get_by_role("button", name="사진 삭제", exact=True)).to_be_visible()
                else:
                    page.get_by_role("button", name="사진 삭제", exact=True).click()
                    expect(page.get_by_role("button", name="사진 삭제", exact=True)).to_have_count(0)
                current = fixture.users["fixture-a"]["avatar_url"]
                expect_photo(page, current)
                with page.expect_response(lambda response: urlsplit(response.url).path == "/api/auth/me"):
                    fixture.release_me()
                page.locator(".account-trigger").click()
                expect(page.locator(".account-points")).to_have_text("54,321P")
                expect_photo(page, current)
                assert page.evaluate("JSON.parse(localStorage.getItem('ggp_user')).avatar_url") == current
                suite.record(f"late-header-get-after-{operation}", fixture)
                context.close()

            fixture = Fixtures()
            fixture.hold_upload = True
            second_photo = fixture.set_photo("fixture-b")
            context, page = suite.open(fixture)
            page.get_by_label("프로필 사진 파일", exact=True).set_input_files(UPLOAD)
            expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_disabled()
            assert fixture.held_uploads, "The first account's POST response must be held"
            page.evaluate("""user => {
              localStorage.setItem('ggp_token','fixture-b'); localStorage.setItem('ggp_user',JSON.stringify(user));
              window.dispatchEvent(new StorageEvent('storage',{key:'ggp_token'}));
            }""", fixture.users["fixture-b"])
            expect(page.get_by_role("heading", name="다른회원", exact=True)).to_be_visible()
            expect_photo(page, second_photo)
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/me/avatar"):
                fixture.release_uploads()
            page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            expect_photo(page, second_photo)
            assert page.evaluate("JSON.parse(localStorage.getItem('ggp_user')).id") == 102
            expect(page.get_by_role("button", name="프로필 사진 변경", exact=True)).to_be_enabled()
            suite.record("held-upload-cannot-overwrite-another-account", fixture)
            context.close()
            browser.close()
            report = {"passed": True, "build": str(BUILD), "checks": suite.checks, "page_errors": suite.errors, "screenshots": suite.screenshots}
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"passed": True, "checks": len(suite.checks), "report": str(OUTPUT / "report.json")}, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
