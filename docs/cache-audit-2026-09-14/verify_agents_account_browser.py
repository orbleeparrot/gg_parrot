"""Browser reproduction of stale private sessions after an account switch.

Requires Playwright, a current local frontend build and local Chromium.
Set FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH as needed. Only a loopback static
server is contacted. Every API and WebSocket is mocked; external URLs are blocked.
This reuses the repository's browser fixtures and does not start the backend.
"""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import importlib.util
import json
import os
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path(os.environ.get("FRONTEND_BUILD", "/tmp/ggp-performance-build")).resolve()
CHROME = os.environ.get("BROWSER_EXECUTABLE_PATH", "/data/team/clcleh123/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
assert (BUILD / "index.html").is_file(), "Build the current frontend and set FRONTEND_BUILD"
os.environ["FRONTEND_BUILD"] = str(BUILD)


def import_fixture(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile_fixture = import_fixture("profile_cache_audit_fixture", "frontend/tests/profileAvatarBrowser.smoke.py")
agent_fixture = import_fixture("agent_cache_audit_fixture", "frontend/tests/agentNotificationBrowser.smoke.py")
server = ThreadingHTTPServer(("127.0.0.1", 0), partial(profile_fixture.Handler, directory=str(BUILD)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
agent_fixture.BASE_URL = base
fixture = agent_fixture.Fixtures()
second_user = {"id": 2, "username": "SecondAccount", "email": "b@example.invalid"}
second_account_requests = []
unexpected_external = []


def route(request):
    parsed = urlsplit(request.request.url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        unexpected_external.append(request.request.url)
        request.abort()
        return
    path = parsed.path
    token = request.request.headers.get("authorization", "")
    if token == "Bearer audit-b" and path == "/api/auth/me":
        request.fulfill(json={"user": second_user})
        return
    if token == "Bearer audit-b" and path.startswith("/api/me/runner/sessions"):
        second_account_requests.append(path)
        request.fulfill(status=503, json={"detail": "Isolated B load failure"})
        return
    fixture.route(request)


def websocket(socket):
    # No connect_to_server: all WebSockets are fixture-owned or closed.
    if urlsplit(socket.url).path.endswith("/api/me/runner/sessions/stream"):
        fixture.sockets.append(socket)
        fixture.push_heartbeat()
    else:
        socket.close()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", route)
        page.route_web_socket("**/*", websocket)
        page.add_init_script(
            'localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'
            + json.dumps(json.dumps(agent_fixture.USER, ensure_ascii=False)) + ");"
        )
        page.goto(base + "/agents")
        expect(page.locator(".agent-workspace")).to_be_visible()
        expect(page.locator(".agent-chart-pane")).to_have_attribute("aria-label", "LINKUSDT 실시간 차트")
        page.evaluate(
            'user=>{localStorage.setItem("ggp_token","audit-b");localStorage.setItem("ggp_user",JSON.stringify(user));'
            'window.dispatchEvent(new StorageEvent("storage",{key:"ggp_token"}));}', second_user
        )
        expect(page.get_by_text("실행 상태 오류: Isolated B load failure")).to_be_visible()
        observed = {
            "current_account_id": page.evaluate('JSON.parse(localStorage.getItem("ggp_user")).id'),
            "previous_account_chart_visible": page.locator(".agent-chart-pane").is_visible(),
            "previous_account_chart_label": page.locator(".agent-chart-pane").get_attribute("aria-label"),
        }
        assert observed["current_account_id"] == 2
        assert observed["previous_account_chart_visible"], "Audited defect no longer reproduces"
        report = {
            "audit": "agents-stale-account-session-state",
            "scope": "Loopback static server; all API and WebSocket responses mocked; external URLs blocked",
            "frontend_build": str(BUILD),
            "source": "frontend/src/pages/Agents.jsx",
            "source_sha256": hashlib.sha256((ROOT / "frontend/src/pages/Agents.jsx").read_bytes()).hexdigest(),
            "expected_previous_account_chart_visible": False,
            "observed": observed,
            "second_account_requests": second_account_requests,
            "blocked_external_requests": unexpected_external,
            "defect_reproduced": True,
        }
        Path(__file__).with_name("agents-account-browser-result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
        browser.close()
finally:
    server.shutdown()
    server.server_close()
