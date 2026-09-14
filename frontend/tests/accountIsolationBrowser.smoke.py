"""Real-page account cache regression; local build + fully intercepted APIs/WS.

Set FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH. No backend, real account, or
external API is used. ACCOUNT_BROWSER_REPORT can override the report location.
"""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import importlib.util
import hashlib
import json
import os
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path(os.environ.get("FRONTEND_BUILD", "/tmp/ggp-cache-implementation-build")).resolve()
CHROME = os.environ.get("BROWSER_EXECUTABLE_PATH", "/data/team/clcleh123/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
OUTPUT = Path(os.environ.get("ACCOUNT_BROWSER_REPORT", str(ROOT / "docs/cache-audit-2026-09-14/account-isolation-regression.json")))
USERS = {
    "audit-a": {"id": 1, "username": "Account_A", "email": "a@example.invalid", "points_balance": 1000, "bio": "", "avatar_url": None, "can_change_password": True},
    "audit-b": {"id": 2, "username": "Account_B", "email": "b@example.invalid", "points_balance": 900, "bio": "", "avatar_url": None, "can_change_password": True},
}
USERS["audit-a-renewed"] = USERS["audit-a"]


def fixture_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile = fixture_module("account_profile_fixture", "profileAvatarBrowser.smoke.py")
agent = fixture_module("account_agent_fixture", "agentNotificationBrowser.smoke.py")
chart = fixture_module("account_chart_fixture", "chartMigrationBrowser.smoke.py")


class Fixture:
    def __init__(self):
        self.agent = agent.Fixtures()
        self.b_sessions_fail = False
        self.hold_unlock = False
        self.held_unlocks = []
        self.keys = {"audit-a": "A-initial-key", "audit-b": "B-initial-key"}
        self.hold_rotation = False
        self.held_rotations = []
        self.calls = []
        self.blocked = []

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            self.blocked.append(route.request.url)
            route.abort()
            return
        path = parsed.path
        token = route.request.headers.get("authorization", "").removeprefix("Bearer ")
        self.calls.append({"path": path, "token": token})
        if path == "/api/auth/me":
            route.fulfill(json={"user": USERS[token]})
        elif path == "/api/me/runner/key":
            route.fulfill(json={"key": self.keys[token]})
        elif path == "/api/me/runner/key/regenerate":
            if self.hold_rotation:
                self.held_rotations.append(route)
            else:
                self.keys[token] = "A-rotated-key"
                route.fulfill(json={"key": self.keys[token]})
        elif path == "/api/me/runner/sessions/stream-token":
            route.fulfill(status=503, json={"detail": "fixture uses polling"})
        elif path == "/api/me/runner/sessions":
            if token == "audit-b":
                route.fulfill(status=503 if self.b_sessions_fail else 200,
                              json={"detail": "B unavailable"} if self.b_sessions_fail else {"active": [], "recent": []})
            else:
                route.fulfill(json=self.agent.sessions())
        elif path == "/api/leaderboard":
            items = [{"id": 11, "rank": 1, "symbol": "BTCUSDT", "username": "Seller",
                      "created_kst": "09/14 12:00", "return_pct": 1, "likes": 0, "dislikes": 0,
                      "locked": True, "for_sale": True, "unlock_price": 100}]
            route.fulfill(json={"items": items, "page": 1, "total": 1, "has_more": False,
                                "seconds_to_reset": 3600})
        elif path.endswith("/unlock") and self.hold_unlock:
            self.held_unlocks.append(route)
        elif path == "/api/chat":
            route.fulfill(json={"items": [], "mode": "metadata", "unseen_count": 0,
                                "latest_id": 0, "seen_id": 0, "day_start_ms": 1})
        elif path in {"/api/market-context", "/api/market/briefing"}:
            route.fulfill(json={})
        elif path == "/api/symbols":
            route.fulfill(json={"items": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]})
        else:
            chart.route_handler(route)


def switch(page, token):
    page.evaluate("""({token,user}) => {
        if (token) {
          localStorage.setItem('ggp_token', token);
          localStorage.setItem('ggp_user', JSON.stringify(user));
        } else {
          localStorage.removeItem('ggp_token'); localStorage.removeItem('ggp_user');
        }
        window.dispatchEvent(new StorageEvent('storage', {key:'ggp_token'}));
    }""", {"token": token, "user": USERS.get(token)})


def main():
    assert (BUILD / "index.html").is_file(), "Build the current frontend and set FRONTEND_BUILD"
    profile.BUILD = BUILD
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(profile.Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    checks, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])

            def open_page(path, fixture, token="audit-a"):
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
                context.route("**/*", fixture.route)
                context.route_web_socket("**/*", lambda socket: socket.close())
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                if token:
                    page.add_init_script('localStorage.setItem("ggp_token",' + json.dumps(token)
                                         + ');localStorage.setItem("ggp_user",' + json.dumps(json.dumps(USERS[token])) + ");")
                page.goto(base + path)
                return context, page

            fixture = Fixture()
            fixture.b_sessions_fail = True
            context, page = open_page("/agents", fixture)
            expect(page.locator(".agent-chart-pane")).to_have_attribute("aria-label", "LINKUSDT 실시간 차트")
            switch(page, "audit-b")
            expect(page.get_by_text("실행 상태 오류: B unavailable")).to_be_visible()
            expect(page.locator(".agent-workspace")).to_have_count(0)
            expect(page.locator(".agent-chart-pane")).to_have_count(0)
            checks.append("Switching accounts immediately hides the previous session, even when the new request fails")
            context.close()

            fixture = Fixture()
            fixture.hold_unlock = True
            context, page = open_page("/leaderboard?from=quick-run", fixture)
            page.get_by_role("button", name="언락 100P 후 사용", exact=True).click()
            page.wait_for_timeout(50)
            assert fixture.held_unlocks
            switch(page, "audit-b")
            expect(page.get_by_role("button", name="언락 100P 후 사용", exact=True)).to_be_enabled()
            for held in fixture.held_unlocks:
                held.fulfill(json={"points_balance": 80, "user_macro": {"id": 99}})
            page.wait_for_timeout(100)
            assert page.evaluate("JSON.parse(localStorage.getItem('ggp_user')).points_balance") == 900
            assert urlsplit(page.url).path == "/leaderboard"
            checks.append("A late unlock cannot overwrite the new member's balance or navigate to the old member's macro")
            context.close()

            fixture = Fixture()
            context, page = open_page("/builder", fixture)
            capital = page.locator('[data-field="initial_capital"] input')
            expect(capital).to_be_visible()
            capital.fill("12345")
            # Renew immediately, before the 300ms persistence debounce: the live
            # component must survive and keep unsaved input for the same member.
            switch(page, "audit-a-renewed")
            expect(capital).to_have_value("12345")
            checks.append("Same-member token renewal preserves current builder input before its save debounce")
            page.wait_for_function("sessionStorage.getItem('ggp_account:v1:member:1:studio')?.includes('12345')")
            switch(page, "audit-b")
            expect(capital).not_to_have_value("12345")
            page.wait_for_timeout(400)
            assert page.evaluate("sessionStorage.getItem('ggp_account:v1:member:1:studio')") is None
            assert page.evaluate("sessionStorage.getItem('ggp_account:v1:member:2:studio')?.includes('12345') || false") is False
            checks.append("Another member receives independent builder state and old draft writes stay retired")
            capital.fill("22222")
            page.wait_for_function("sessionStorage.getItem('ggp_account:v1:member:2:studio')?.includes('22222')")
            switch(page, None)
            expect(capital).not_to_have_value("22222")
            assert page.evaluate("sessionStorage.getItem('ggp_account:v1:member:2:studio')") is None
            checks.append("Logout removes owned work rather than exposing it in guest mode")
            capital.fill("77777")
            page.wait_for_function("sessionStorage.getItem('ggp_account:v1:guest:studio')?.includes('77777')")
            switch(page, "audit-a")
            expect(capital).to_have_value("77777")
            assert page.evaluate("sessionStorage.getItem('ggp_account:v1:guest:studio')") is None
            checks.append("Guest-created work continues after login and becomes owned by that member")
            context.close()

            fixture = Fixture()
            context, page = open_page("/mypage/settings?tab=security", fixture)
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="회원 키 관리", exact=True).click()
            # Password inputs have no textbox role, so identify by their label.
            settings_key = page.locator("#profile-settings-member-key").get_by_label("회원 키", exact=True)
            expect(settings_key).to_have_value("A-initial-key")
            page.get_by_role("button", name="회원 키", exact=True).click()
            header = page.get_by_role("dialog", name="껄무새 회원 키", exact=True)
            expect(header.get_by_label("회원 키", exact=True)).to_have_value("A-initial-key")
            header.get_by_role("button", name="재발급", exact=True).click()
            expect(settings_key).to_have_value("A-rotated-key")
            expect(header.get_by_label("회원 키", exact=True)).to_have_value("A-rotated-key")
            checks.append("Rotating a header member key updates the already-open security panel immediately")

            fixture.hold_rotation = True
            header.get_by_role("button", name="재발급", exact=True).click()
            page.wait_for_timeout(50)
            assert fixture.held_rotations
            switch(page, "audit-b")
            expect(header).to_have_count(0)
            page.get_by_role("button", name="회원 키 관리", exact=True).click()
            expect(settings_key).to_have_value("B-initial-key")
            for held in fixture.held_rotations:
                held.fulfill(json={"key": "A-late-key"})
            page.wait_for_timeout(100)
            expect(settings_key).to_have_value("B-initial-key")
            checks.append("A late member-key rotation cannot display the previous account's key after switching members")
            context.close()

            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sources = ["frontend/src/lib/auth.js", "frontend/src/lib/accountStorage.js", "frontend/src/lib/studioSession.js",
               "frontend/src/pages/Agents.jsx", "frontend/src/pages/Leaderboard.jsx", "frontend/src/pages/Studio.jsx",
               "frontend/src/lib/runnerKeyStore.js", "frontend/src/components/RunnerSessions.jsx"]
    OUTPUT.write_text(json.dumps({"passed": len(checks), "checks": checks, "frontend_build": str(BUILD),
                                  "build_index_sha256": hashlib.sha256((BUILD / "index.html").read_bytes()).hexdigest(),
                                  "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
                                  "page_errors": errors, "scope": "All APIs and WebSockets mocked; no production connections"},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(checks)} account-isolation browser checks passed")


if __name__ == "__main__":
    main()
