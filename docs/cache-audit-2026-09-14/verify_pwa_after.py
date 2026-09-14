"""Actual service worker lifecycle with a local build; all APIs are fixtures."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
BUILD = Path(os.environ["FRONTEND_BUILD"]).resolve()
CHROME = os.environ["BROWSER_EXECUTABLE_PATH"]
OUTPUT = HERE / "after-ui/pwa"
OUTPUT.mkdir(parents=True, exist_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        try:
            return super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Navigation can cancel a static download.

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = f"http://127.0.0.1:{server.server_port}"
blocked = []


def route(request):
    url = urlsplit(request.request.url)
    if url.hostname != "127.0.0.1":
        blocked.append(request.request.url)
        request.abort()
    elif url.path.startswith("/api/"):
        if url.path == "/api/me/runner/key":
            request.fulfill(json={"key": "fixture-private-key"})
        elif url.path == "/api/news/market":
            request.fulfill(json={"items": [], "overview": None})
        else:
            request.fulfill(json={"items": [], "coins": [], "enabled": False})
    else:
        request.continue_()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.route("**/*", route)
        context.route_web_socket("**/*", lambda socket: socket.close())
        page = context.new_page()
        page.goto(origin + "/guide", wait_until="load")
        page.wait_for_function("Boolean(navigator.serviceWorker.controller)", timeout=20_000)
        assets = sorted(path for path in (BUILD / "assets").glob("index-*.js"))
        assert assets
        asset_path = "/assets/" + assets[0].name
        page.evaluate("path => fetch(path, {cache:'reload'}).then(r => r.text())", asset_path)
        page.wait_for_function("async path => (await caches.keys()).some(Boolean) && Boolean(await caches.match(path))", arg=asset_path)
        page.evaluate("fetch('/api/me/runner/key').then(r=>r.json())")
        stored = page.evaluate("async () => {const keys=await caches.keys();return (await Promise.all(keys.map(async name=>(await (await caches.open(name)).keys()).map(r=>new URL(r.url).pathname)))).flat();}")
        assert "/offline.html" in stored and asset_path in stored
        assert not any(path.startswith("/api/") for path in stored)
        context.set_offline(True)
        # Use an actual navigation (not SPA history), proving the installed
        # worker supplies the offline page when the network fails.
        page.goto(origin + "/mypage?offline-check=1", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="인터넷 연결이 끊겼어요")).to_be_visible()
        page.screenshot(path=str(OUTPUT / "offline-mobile.png"))
        context.set_offline(False)
        page.get_by_role("button", name="다시 시도").click()
        expect(page.get_by_role("heading", name="인터넷 연결이 끊겼어요")).not_to_be_visible()
        page.wait_for_load_state("load")
        result = {"build": str(BUILD), "registered_from_app": True, "asset_cached": True,
                  "api_response_cached": False, "offline_navigation": True, "online_recovery": True,
                  "cached_paths": stored, "blocked_external_requests": len(blocked),
                  "external_api_calls": 0}
        (OUTPUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        context.unroute_all(behavior="wait")
        browser.close()
finally:
    server.shutdown()
    server.server_close()
