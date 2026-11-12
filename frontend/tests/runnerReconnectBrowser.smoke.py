"""Runner reconnect UI regression; every API/WebSocket and execution is a fixture."""
from __future__ import annotations

import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

_helpers = runpy.run_path(str(Path(__file__).with_name("agentNotificationBrowser.smoke.py")))
BASE_MS, BASE_URL, Fixtures, advance, iso, USER = (
    _helpers[key] for key in ("BASE_MS", "BASE_URL", "Fixtures", "advance", "iso", "USER")
)
WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"


def open_recovery_fixture(context, fixture):
    page = context.new_page()
    page.set_viewport_size({"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", fixture.route)
    page.route_web_socket("**/*", fixture.websocket)
    page.add_init_script('localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'
                         + json.dumps(json.dumps(USER, ensure_ascii=False)) + ');')
    page.clock.install(time=datetime.fromtimestamp(BASE_MS / 1000, timezone.utc))
    page.goto(BASE_URL + "/agents?session=41")
    page.wait_for_load_state("networkidle")
    fixture.now += 3000
    page.clock.pause_at(datetime.fromtimestamp(fixture.now / 1000, timezone.utc))
    advance(page, fixture, 2)
    expect(page.get_by_role("heading", name="내 에이전트", exact=True)).to_have_count(1)
    assert fixture.sockets
    return page, errors


class RecoveryFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.recovery_available = True
        self.reconnects = []
        self.launch_status = "ready"
        self.reconnect_error = ""
        self.malicious_url = False
        self.unexpected_writes = []

    def session(self):
        result = super().session()
        result.update(connected=self.connected, mode="live", testnet=False,
                      recovery={"available": self.recovery_available and not self.connected,
                                "reason": "이 실행에는 복구 정보가 없어요. 최신 실행기를 사용해 주세요."})
        return result

    def route(self, route):
        path = urlparse(route.request.url).path
        if path.endswith("/sessions/41/reconnect"):
            self.reconnects.append(path)
            if self.reconnect_error:
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": self.reconnect_error}))
            else:
                url = "https://example.invalid/untrusted" if self.malicious_url else "ggparrot://launch/?v=2&env=local&ticket=" + "a" * 43
                route.fulfill(content_type="application/json", body=json.dumps({
                    "launch_id": len(self.reconnects), "launch_url": url,
                    "expires_at": iso(self.now + 120_000), "status": "ready"}))
        elif "/runner/launch-tickets/" in path:
            route.fulfill(content_type="application/json", body=json.dumps({"status": self.launch_status}))
        else:
            if route.request.method in {"POST", "PUT", "DELETE"} and not path.endswith("/stream-token"):
                self.unexpected_writes.append(path)
            super().route(route)


def dismiss_notes(page):
    notes = page.get_by_role("dialog", name="껄무새가 이렇게 바뀌었어요")
    if notes.is_visible():
        notes.get_by_role("button", name="닫기", exact=True).click()


def main():
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"),
            args=["--no-sandbox"])
        for width in (1440, 390):
            context = browser.new_context(user_agent=WINDOWS_UA)
            fixture = RecoveryFixtures()
            page, errors = open_recovery_fixture(context, fixture)
            page.set_viewport_size({"width": width, "height": 1000})
            dismiss_notes(page)
            button = page.get_by_role("button", name="실행기 다시 연결", exact=True)
            button.click()
            dialog = page.get_by_role("dialog", name="중단된 실행 다시 연결")
            link = dialog.get_by_role("link", name="실행기 열기", exact=True)
            expect(link).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=f"/tmp/ggp-web-recovery-{width}.png")
            assert link.get_attribute("href").startswith("ggparrot://launch/?v=2&env=local&ticket=")
            assert fixture.reconnects == ["/api/me/runner/sessions/41/reconnect"]
            # Observe the click without asking the host OS to launch an app.
            link.evaluate("node => node.addEventListener('click', event => event.preventDefault())")
            link.click()
            fixture.launch_status = "claimed"
            advance(page, fixture, 3)
            expect(dialog).to_contain_text("실행기가 연결 요청을 받았어요")
            assert fixture.session()["session_id"] == 41 and not fixture.connected
            fixture.connected = True
            fixture.push_heartbeat()
            expect(button).to_have_count(0)
            expect(dialog).to_have_count(0)
            fixture.connected = False
            fixture.push_heartbeat()
            expect(button).to_be_visible()
            expect(dialog).to_have_count(0)
            assert not errors and not fixture.unexpected_writes, (errors, fixture.unexpected_writes)
            reports.append({"width": width, "same_session": 41, "ticket_is_not_resume": "passed"})
            context.close()

        context = browser.new_context(user_agent=WINDOWS_UA)
        fixture = RecoveryFixtures()
        fixture.reconnect_error = "실행기가 이미 연결되어 있어요."
        page, errors = open_recovery_fixture(context, fixture)
        dismiss_notes(page)
        page.get_by_role("button", name="실행기 다시 연결", exact=True).click()
        dialog = page.get_by_role("dialog", name="중단된 실행 다시 연결")
        expect(dialog.get_by_role("alert")).to_contain_text(fixture.reconnect_error)
        expect(dialog.get_by_role("link", name="실행기 열기", exact=True)).to_have_count(0)
        fixture.reconnect_error = ""
        fixture.malicious_url = True
        dialog.get_by_role("button", name="연결 다시 준비").click()
        expect(dialog.get_by_role("alert")).to_contain_text("연결 주소를 확인하지 못했어요")
        fixture.malicious_url = False
        fixture.launch_status = "expired"
        dialog.get_by_role("button", name="연결 다시 준비").click()
        advance(page, fixture, 3)
        expect(dialog).to_contain_text("연결 요청이 만료됐어요")
        expect(dialog.get_by_role("link", name="실행기 열기", exact=True)).to_have_count(0)
        dialog.get_by_role("button", name="닫기", exact=True).click()
        fixture.recovery_available = False
        fixture.push_heartbeat()
        before = len(fixture.reconnects)
        page.get_by_role("button", name="실행기 다시 연결", exact=True).click()
        expect(dialog).to_contain_text("이 실행에는 복구 정보가 없어요")
        assert len(fixture.reconnects) == before
        assert not errors and not fixture.unexpected_writes, (errors, fixture.unexpected_writes)
        reports.append({"server_conflict_bad_link_expiry_legacy": "passed"})
        context.close()

        context = browser.new_context(user_agent=WINDOWS_UA)
        fixture = RecoveryFixtures()
        fixture.stopped = True
        page, errors = open_recovery_fixture(context, fixture)
        dismiss_notes(page)
        page.get_by_role("button", name="실행기 다시 연결", exact=True).click()
        expect(page.get_by_role("dialog", name="중단된 실행 다시 연결")
               .get_by_role("link", name="실행기 열기", exact=True)).to_be_visible()
        assert fixture.reconnects == ["/api/me/runner/sessions/41/reconnect"] and not errors
        reports.append({"automatically_ended_session_history": "passed"})
        context.close()

        context = browser.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1")
        fixture = RecoveryFixtures()
        page, errors = open_recovery_fixture(context, fixture)
        dismiss_notes(page)
        page.get_by_role("button", name="실행기 다시 연결", exact=True).click()
        expect(page.get_by_role("dialog", name="중단된 실행 다시 연결")).to_contain_text("원래 PC에서 같은 계정")
        assert not fixture.reconnects and not errors
        reports.append({"mobile_requires_original_pc": "passed"})
        context.close()
        browser.close()
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    main()
