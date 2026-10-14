"""Real development React StrictMode receipt-hook lifecycle regression.

Build the adjacent JSX with esbuild --jsx=automatic and NODE_ENV=development, then run this
script in the Playwright image. All API requests are local intercepted fixtures;
there is no backend, production account, AI call, exchange order or screenshot.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


BUNDLE = Path(__file__).resolve().parents[1] / "dist" / "session-logs-strict-probe.js"
HTML = b"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Local receipt StrictMode regression</title><div id="root"></div>
<script type="module" src="/session-logs-strict-probe.js"></script></html>"""
PROBE_INIT = """
localStorage.setItem('ggp_token', 'strict-browser-fixture');
localStorage.setItem('ggp_user', JSON.stringify({id: 42, username: 'StrictMode fixture', email: 'strict@example.invalid'}));
window.__strictReceiptFetches = [];
const originalFetch = window.fetch.bind(window);
window.fetch = (input, options = {}) => {
  const token = JSON.parse(options.body || '{}').receipt_token;
  const entry = {token, method: options.method, aborted: !!options.signal?.aborted, fulfilled: false};
  window.__strictReceiptFetches.push(entry);
  options.signal?.addEventListener('abort', () => { entry.aborted = true; }, {once: true});
  return originalFetch(input, options).then(response => {entry.fulfilled = true; return response;});
};
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            body, content_type = HTML, "text/html; charset=utf-8"
        elif path == "/session-logs-strict-probe.js":
            body, content_type = BUNDLE.read_bytes(), "text/javascript; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


def settle_render(page):
    # Two rendered frames allow passive React effects to run without a long
    # arbitrary sleep or accidentally testing before a rerender has committed.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def main():
    assert BUNDLE.is_file(), "Build the development StrictMode JSX probe with esbuild first"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    receipts, held, external = [], [], []

    def route_fixture(route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            external.append(parsed.hostname)
            route.abort()
            return
        if parsed.path.startswith("/api/"):
            assert parsed.path == "/api/me/agents/sessions/41/news-receipts", parsed.path
            assert route.request.method == "POST"
            assert route.request.headers.get("authorization") == "Bearer strict-browser-fixture"
            token = route.request.post_data_json["receipt_token"]
            receipts.append(token)
            if token == "fixture-held":
                held.append(route)
                return
            route.fulfill(json={"ok": True, "recorded": True, "event_id": len(receipts)})
            return
        route.continue_()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
                **({"executable_path": os.environ["BROWSER_EXECUTABLE_PATH"]} if os.environ.get("BROWSER_EXECUTABLE_PATH") else {}))
            context = browser.new_context()
            context.route("**/*", route_fixture)
            context.add_init_script(PROBE_INIT)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.clock.install()
            page.goto(origin, wait_until="networkidle")
            expect(page.locator("[data-auth-user]")).to_have_text("42")
            expect(page.locator("[data-receipt-state]")).to_have_text("strict-initial: ready")
            effects = page.evaluate("window.__strictReceiptEffects")
            assert effects == {"setups": 2, "cleanups": 1}, effects
            fetches = page.evaluate("window.__strictReceiptFetches")
            assert len([item for item in fetches if item["token"] == "fixture-initial" and item["fulfilled"]]) == 1, fetches
            assert any(item["token"] == "fixture-initial" and item["aborted"] for item in fetches), fetches

            before = len(fetches)
            page.get_by_role("button", name="Rerender same key", exact=True).click()
            expect(page.locator("[data-render-revision]")).to_have_text("1")
            settle_render(page)
            assert page.evaluate("window.__strictReceiptFetches.length") == before

            page.get_by_role("button", name="Change receipt key", exact=True).click()
            expect(page.locator("[data-receipt-state]")).to_have_text("strict-changed: ready")
            assert receipts.count("fixture-changed") == 1, receipts
            before = page.evaluate("window.__strictReceiptFetches.length")
            page.get_by_role("button", name="Rerender same key", exact=True).click()
            expect(page.locator("[data-render-revision]")).to_have_text("2")
            settle_render(page)
            assert page.evaluate("window.__strictReceiptFetches.length") == before

            page.get_by_role("button", name="Begin held receipt", exact=True).click()
            expect(page.locator("[data-receipt-state]")).to_have_text("strict-held: saving")
            page.wait_for_function("window.__strictReceiptFetches.some(item => item.token === 'fixture-held')")
            assert len(held) == 1, receipts
            page.get_by_role("button", name="Unmount receipt hook", exact=True).click()
            expect(page.locator("[data-unmounted]")).to_be_visible()
            page.wait_for_function("window.__strictReceiptFetches.some(item => item.token === 'fixture-held' && item.aborted)")
            effects = page.evaluate("window.__strictReceiptEffects")
            assert effects == {"setups": 2, "cleanups": 2}, effects
            before = page.evaluate("window.__strictReceiptFetches.length")
            page.clock.run_for(10_000)
            settle_render(page)
            assert page.evaluate("window.__strictReceiptFetches.length") == before, "Unmount must cancel, not retry"
            assert not errors, errors
            assert not external, external
            # Finish the intercepted route after proving the caller cancelled
            # it, so Playwright does not leave a route-handler future pending
            # when the fixture browser context is closed.
            for route in held:
                route.abort()
            held.clear()
            print(json.dumps({"passed": True, "development_strictmode": True, "effects": effects,
                "initial_ready": True, "changed_key_ready": True, "same_key_duplicates": 0,
                "unmount_aborted": True, "production_requests": 0, "orders": 0}))
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
