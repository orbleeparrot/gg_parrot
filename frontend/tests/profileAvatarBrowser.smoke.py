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
USER_A = {"id": 101, "username": "껄무새", "email": "parrot@example.com", "points_balance": 12500, "created_at": "2026-08-01T00:00:00Z", "avatar_url": None, "bio": "", "can_change_password": True}
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
        if path == "/api/me/profile":
            self.avatar_requests.append((request.method, token))
            assert request.method == "PATCH"
            content_type = request.headers["content-type"]
            parsed = BytesParser(policy=email_policy).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + request.post_data_buffer)
            parts = {part.get_param("name", header="content-disposition"): part for part in parsed.iter_parts()}
            assert set(parts) <= {"username", "bio", "remove_avatar", "image"}
            if self.reject_upload:
                route.fulfill(status=409, json={"detail": "이미 사용 중인 이름이에요."}); return
            user["username"] = parts["username"].get_payload(decode=True).decode()
            user["bio"] = parts["bio"].get_payload(decode=True).decode()
            if "image" in parts:
                assert parts["image"].get_payload(decode=True) == PNG
                self.set_photo(token)
            elif parts["remove_avatar"].get_payload(decode=True) == b"true":
                user["avatar_url"] = None
            payload = {"user": copy.deepcopy(user)}
            if self.hold_upload: self.held_uploads.append((route, payload))
            else: route.fulfill(json=payload)
            return
        if path == "/api/me/password":
            data = request.post_data_json
            if data["current_password"] != "old-password-123":
                route.fulfill(status=400, json={"detail": "현재 비밀번호가 일치하지 않아요."}); return
            self.users["fixture-refreshed"] = user
            route.fulfill(json={"token": "fixture-refreshed", "user": user}); return
        if path == "/api/me/account":
            data = request.post_data_json
            assert data["confirmation"] == "탈퇴"
            if user["can_change_password"] and data["password"] != "old-password-123":
                route.fulfill(status=400, json={"detail": "현재 비밀번호가 일치하지 않아요."}); return
            assert user["can_change_password"] or data["credential"] == "fixture-google"
            route.fulfill(json={"ok": True}); return
        if path == "/api/auth/google/config":
            route.fulfill(json={"enabled": True, "client_id": "fixture-client"}); return
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
        context.add_init_script("""window.google = {accounts:{id:{initialize(config){window.fixtureGoogleCallback=config.callback},renderButton(node){const button=document.createElement('button');button.type='button';button.textContent='Google 본인 확인';button.onclick=()=>window.fixtureGoogleCallback({credential:'fixture-google'});node.append(button)}}}};""")
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


def edit(page):
    page.get_by_role("button", name="프로필 편집", exact=True).click()
    dialog = page.get_by_role("dialog", name="프로필 편집", exact=True)
    expect(dialog).to_be_visible()
    return dialog


def save(dialog):
    dialog.get_by_role("button", name="저장", exact=True).click()
    expect(dialog).not_to_be_visible()


def main():
    if not (BUILD / "index.html").is_file(): raise SystemExit(f"Build missing: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"): launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            suite = Suite(browser, f"http://127.0.0.1:{server.server_port}")
            for width in (320, 375, 1440):
                for theme in ("light", "dark"):
                    fixture = Fixtures(); context, page = suite.open(fixture, width, theme)
                    metrics = page.locator(".me-stats").evaluate("""el => [...el.children].map(e=>({label:e.querySelector('dt').getBoundingClientRect().top, value:e.querySelector('dd').getBoundingClientRect().top, font:getComputedStyle(e.querySelector('dd')).fontSize}))""")
                    assert len({x['label'] for x in metrics}) == 1, metrics
                    assert len({x['value'] for x in metrics}) == 1, metrics
                    assert len({x['font'] for x in metrics}) == 1, metrics
                    expect(page.get_by_role("list", name="판매 등급별 조건")).to_be_hidden()
                    page.locator(".me-tier summary").click()
                    expect(page.locator(".me-tier-list li")).to_have_count(5)
                    page.locator(".me-tier summary").click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    suite.screenshot(page, f"settings-{width}-{theme}")
                    dialog = edit(page)
                    dialog.get_by_label("프로필명", exact=True).fill("변경전취소")
                    dialog.get_by_label("프로필 사진 파일").set_input_files(UPLOAD)
                    assert len(fixture.avatar_requests) == 0
                    expect(dialog.locator(".user-avatar img")).to_be_visible()
                    assert dialog.evaluate("e=>e.scrollWidth <= e.clientWidth")
                    suite.screenshot(page, f"editor-{width}-{theme}")
                    page.keyboard.press("Escape")
                    expect(dialog).not_to_be_visible()
                    expect(page.get_by_role("button", name="프로필 편집", exact=True)).to_be_focused()
                    expect(page.locator(".me-name")).to_have_text("껄무새")
                    expect_photo(page, None)
                    suite.record(f"aligned-stats-editor-cancel-{width}-{theme}", fixture); context.close()

            fixture = Fixtures(); context, page = suite.open(fixture)
            dialog = edit(page)
            name = dialog.locator('input[autocomplete="nickname"]')
            name.fill("새프로필"); dialog.locator("textarea").fill("나의 소개글")
            field = dialog.get_by_label("프로필 사진 파일")
            field.set_input_files({"name":"large.png", "mimeType":"image/png", "buffer":b'x'*(2*1024*1024+1)})
            expect(dialog.get_by_role("alert")).to_contain_text("2MB")
            field.set_input_files(UPLOAD)
            fixture.reject_upload = True
            dialog.get_by_role("button", name="저장", exact=True).click()
            expect(dialog.get_by_role("alert")).to_contain_text("이미 사용 중")
            expect(name).to_have_value("새프로필")
            expect(page.locator(".me-name")).to_have_text("껄무새")
            fixture.reject_upload = False; save(dialog)
            expect(page.locator(".me-name")).to_have_text("새프로필")
            expect(page.locator(".me-bio")).to_have_text("나의 소개글")
            photo = fixture.users['fixture-a']['avatar_url']; expect_photo(page, photo)
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".me-bio")).to_have_text("나의 소개글"); expect_photo(page, photo)
            dialog = edit(page); dialog.get_by_role("button", name="삭제", exact=True).click(); save(dialog); expect_photo(page, None)
            suite.record("atomic-edit-rejection-recovery-reload-remove", fixture); context.close()

            fixture = Fixtures(); fixture.hold_me = True; context, page = suite.open(fixture)
            assert fixture.held_me
            for _, payload in fixture.held_me: payload['user']['points_balance'] = 54321
            dialog = edit(page); dialog.locator('input[autocomplete="nickname"]').fill("새로운이름"); dialog.locator('textarea').fill('저장한 소개'); dialog.get_by_label('프로필 사진 파일').set_input_files(UPLOAD); save(dialog)
            photo = fixture.users['fixture-a']['avatar_url']
            fixture.release_me(); page.locator('.account-trigger').click()
            expect(page.locator('.account-points')).to_have_text('54,321P')
            expect(page.locator('.account-name')).to_have_text('새로운이름')
            expect(page.locator('.me-bio')).to_have_text('저장한 소개'); expect_photo(page, photo)
            suite.record('late-header-response-preserves-edited-profile', fixture); context.close()

            fixture = Fixtures(); fixture.hold_upload = True; second_photo=fixture.set_photo('fixture-b'); context,page=suite.open(fixture)
            dialog=edit(page); dialog.locator('input[autocomplete="nickname"]').fill('늦은변경'); dialog.get_by_role('button',name='저장',exact=True).click()
            expect(dialog.get_by_role('button',name='저장 중…')).to_be_disabled()
            page.evaluate("""user=>{localStorage.setItem('ggp_token','fixture-b');localStorage.setItem('ggp_user',JSON.stringify(user));window.dispatchEvent(new StorageEvent('storage',{key:'ggp_token'}));}""",fixture.users['fixture-b'])
            expect(page.locator('.me-name')).to_have_text('다른회원')
            fixture.release_uploads(); page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            expect(page.locator('.me-name')).to_have_text('다른회원'); expect_photo(page,second_photo)
            suite.record('old-account-edit-cannot-overwrite-current-account',fixture);context.close()

            fixture=Fixtures();context,page=suite.open(fixture)
            page.get_by_role('button',name='비밀번호 변경',exact=True).click(); dialog=page.get_by_role('dialog',name='비밀번호 변경')
            dialog.get_by_label('현재 비밀번호',exact=True).fill('wrong')
            dialog.get_by_label('새 비밀번호',exact=True).fill('new-password-123')
            dialog.get_by_label('새 비밀번호 확인',exact=True).fill('mismatch')
            dialog.get_by_role('button',name='변경',exact=True).click();expect(dialog.get_by_role('alert')).to_contain_text('새 비밀번호가 일치')
            dialog.get_by_label('새 비밀번호 확인',exact=True).fill('new-password-123');dialog.get_by_role('button',name='변경',exact=True).click();expect(dialog.get_by_role('alert')).to_contain_text('현재 비밀번호가 일치')
            dialog.get_by_label('현재 비밀번호',exact=True).fill('old-password-123');dialog.get_by_role('button',name='변경',exact=True).click();expect(dialog).not_to_be_visible()
            expect(page.get_by_role('status')).to_contain_text('비밀번호를 변경')
            assert page.evaluate("localStorage.getItem('ggp_token')")=='fixture-refreshed'
            assert 'new-password-123' not in page.evaluate('JSON.stringify(localStorage)')
            suite.record('password-confirmation-validation-and-session-refresh',fixture);context.close()

            for google in (False,True):
                fixture=Fixtures();fixture.users['fixture-a']['can_change_password']=not google;context,page=suite.open(fixture,width=375)
                if google: expect(page.get_by_role('button',name='비밀번호 변경',exact=True)).to_have_count(0)
                page.get_by_role('button',name='회원 탈퇴',exact=True).click(); dialog=page.get_by_role('dialog',name='회원 탈퇴')
                submit=dialog.get_by_role('button',name='회원 탈퇴',exact=True);expect(submit).to_be_disabled()
                expect(dialog).to_contain_text('탈퇴한 회원');expect(dialog).to_contain_text('실행 중인 매크로')
                dialog.get_by_role('textbox').first.fill('탈퇴')
                if google: dialog.get_by_role('button',name='Google 본인 확인',exact=True).click()
                else:
                    dialog.get_by_label('현재 비밀번호',exact=True).fill('wrong');submit.click();expect(dialog.get_by_role('alert')).to_contain_text('현재 비밀번호가 일치')
                    dialog.get_by_label('현재 비밀번호',exact=True).fill('old-password-123')
                suite.screenshot(page,f'withdrawal-{google}')
                submit.click();expect(page).to_have_url(__import__('re').compile('/login\?notice='))
                assert page.evaluate("localStorage.getItem('ggp_token')") is None
                suite.record(f'withdrawal-confirmation-and-auth-{google}',fixture);context.close()
            browser.close()
            report={'passed':True,'build':str(BUILD),'checks':suite.checks,'page_errors':suite.errors,'screenshots':suite.screenshots}
            (OUTPUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps({'passed':True,'checks':len(suite.checks)}))
    finally: server.shutdown()


if __name__ == '__main__': main()
