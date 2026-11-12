"""Recovery is initiated in the desktop runner, with no web recovery action.

All API, telemetry and WebSocket traffic is served by fixtures.
"""

import json, os, runpy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import expect, sync_playwright

mod = runpy.run_path(str(Path(__file__).with_name("agentNotificationBrowser.smoke.py")))
Fixtures, BASE_MS, BASE_URL, USER, advance = (
    mod[k] for k in ("Fixtures", "BASE_MS", "BASE_URL", "USER", "advance")
)


class Disconnected(Fixtures):
    def __init__(self):
        super().__init__()
        self.unexpected = []
        self.api_requests = []

    def session(self):
        r = super().session()
        r.update(
            connected=False,
            mode="live",
            testnet=False,
            recovery={"available": True, "reason": "fixture-capability"},
        )
        return r

    def route(self, r):
        path = urlparse(r.request.url).path
        if path.startswith("/api/"):
            self.api_requests.append((r.request.method, path))
            if path == "/api/visit":
                r.fulfill(status=204, body="")
                return
            if r.request.method in (
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            ) and not path.endswith("/stream-token"):
                self.unexpected.append(path)
        super().route(r)


reports = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        executable_path=os.environ.get(
            "BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"
        ),
        args=["--no-sandbox"],
    )
    for width in (1440, 390):
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36",
            service_workers="block",
            viewport={"width": width, "height": 1000},
        )
        page = ctx.new_page()
        f = Disconnected()
        errors = []
        scripts = {}
        page.on("pageerror", lambda e: errors.append(str(e)))

        def response(res):
            if "/assets/" in res.url and res.url.endswith(".js"):
                try:
                    scripts[res.url] = res.text()
                except Exception:
                    pass

        page.on("response", response)
        page.route("**/*", f.route)
        page.route_web_socket("**/*", f.websocket)
        page.add_init_script(
            'localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'
            + json.dumps(json.dumps(USER, ensure_ascii=False))
            + ");"
        )
        page.clock.install(time=datetime.fromtimestamp(BASE_MS / 1000, timezone.utc))
        for history in (False, True):
            f.stopped = history
            page.goto(BASE_URL + "/agents?session=41", wait_until="networkidle")
            advance(page, f, 2)
            expect(
                page.get_by_role("heading", name="내 에이전트", exact=True)
            ).to_have_count(1)
            expect(
                page.get_by_role("button", name="실행기 다시 연결", exact=True)
            ).to_have_count(0)
            expect(
                page.get_by_role("dialog", name="중단된 실행 다시 연결", exact=True)
            ).to_have_count(0)
            assert not f.unexpected, f.unexpected
        offenders = [
            u
            for u, s in scripts.items()
            if "중단된 실행 다시 연결" in s or "runner-reconnect-button" in s
        ]
        assert not offenders, offenders
        assert not errors, errors
        page.screenshot(path=f"/tmp/ggp-web-no-reconnect-{width}.png")
        reports.append(
            {
                "width": width,
                "active_and_history_reconnect_button": "absent",
                "loaded_script_count": len(scripts),
                "old_reconnect_bundle": "absent",
                "real_api_writes": 0,
                "real_websockets": 0,
                "errors": errors,
            }
        )
        ctx.close()
    browser.close()
print(json.dumps(reports, ensure_ascii=False))
