"""Run real Leaderboard pagination/navigation against a local, bounded fixture API.

Requires frontend npm dependencies, Python Playwright and Chromium. Optional:
NODE_BINARY, BROWSER_EXECUTABLE_PATH, LEADERBOARD_BROWSER_REPORT.
No production backend or external network is used.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


FRONTEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)


def entry(entry_id, rank):
    return {"id": entry_id, "rank": rank, "symbol": "BTCUSDT", "username": f"author-{entry_id}",
            "created_kst": "16:00", "return_pct": rank / 100, "likes": 0, "dislikes": 0,
            "locked": True, "unlock_price": 100}


class Fixtures:
    def __init__(self, target_available=True):
        self.total = 120
        self.target_available = target_available
        self.queries = []
        self.responses = []
        self.unexpected = []

    def route(self, route):
        parsed = urlparse(route.request.url)
        if parsed.path == "/api/leaderboard":
            query = parse_qs(parsed.query)
            self.queries.append(query)
            requested = int(query.get("page", [1])[0])
            size = int(query.get("page_size", [50])[0])
            assert size == 50, query
            target = int(query.get("entry_id", [0])[0])
            location = {"entry_id": 1, "rank": 111, "page": 3} if target == 1 and self.target_available else None
            page = location["page"] if location else min(requested, max(1, (self.total + 49) // 50))
            ranks = range((page - 1) * 50 + 1, min(self.total, page * 50) + 1)
            rows = [entry(1 if rank == 111 and self.target_available else rank + 1000, rank) for rank in ranks]
            payload = {"items": rows, "total": self.total, "page": page, "page_size": size,
                       "has_more": page * size < self.total, "seconds_to_reset": 1000,
                       "snapshot_id": f"fixture-{self.total}", "preparing": False,
                       "entry_location": location}
            self.responses.append(payload)
            route.fulfill(json=payload)
        elif parsed.path == "/api/chat":
            route.fulfill(json={"items": [], "mode": "metadata", "latest_id": 0,
                                "day_start_ms": 1789311600000, "seen_id": None,
                                "unseen_count": 0, "has_more": False})
        elif parsed.hostname in {"localhost", "127.0.0.1"} and not parsed.path.startswith("/api/"):
            if parsed.path.startswith("/brand/") or parsed.path == "/favicon.ico":
                route.fulfill(status=204)
            else:
                route.continue_()
        elif route.request.url == "https://bin.bnbstatic.com/static/assets/logos/BTC.png":
            route.fulfill(status=204)
        else:
            self.unexpected.append(route.request.url)
            route.abort()


def build_harness(directory):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        raise RuntimeError("Node is required; set NODE_BINARY")
    source = f"""
import React from {json.dumps(str(FRONTEND / 'node_modules/react/index.js'))};
import {{createRoot}} from {json.dumps(str(FRONTEND / 'node_modules/react-dom/client.js'))};
import {{MemoryRouter}} from 'react-router-dom';
import Leaderboard from {json.dumps(str(FRONTEND / 'src/pages/Leaderboard.jsx'))};
const state = window.auditRegistered ? {{registeredId: 1, justRegistered: true}} : null;
createRoot(document.getElementById('root')).render(
  <MemoryRouter initialEntries={{[{{pathname:'/leaderboard', state}}]}}><Leaderboard /></MemoryRouter>
);
"""
    (directory / "entry.jsx").write_text(source)
    build = """
const esbuild = require(process.argv[1]);
esbuild.buildSync({entryPoints: [process.argv[2]], outfile: process.argv[3],
 bundle: true, format: 'iife', jsx: 'automatic', nodePaths: [process.argv[4]],
 define: {'import.meta.env': '{}'}, logLevel: 'error'});
"""
    subprocess.run([node, "-e", build, str(FRONTEND / "node_modules/esbuild"),
                    str(directory / "entry.jsx"), str(directory / "app.js"),
                    str(FRONTEND / "node_modules")], check=True)
    (directory / "index.html").write_text("""<!doctype html><html lang="ko"><meta charset="utf-8">
<link rel="stylesheet" href="/app.css"><style>
.lb-row{display:flex;min-height:85px;gap:10px}.lb-row>div{flex:1}svg{width:16px;height:16px}
.chat-fab{position:fixed;bottom:10px;right:10px}.chat-fab img{width:32px}
</style><body><div id="root"></div><script src="/app.js"></script></body></html>""")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def settle(page, elapsed=100):
    page.clock.run_for(elapsed)
    page.wait_for_timeout(100)


def shrinking_snapshot(page, fixture):
    nav = page.get_by_role("navigation", name="리더보드 페이지")
    expect(nav).to_contain_text("1 / 3")
    nav.get_by_role("button", name="다음").click()
    settle(page)
    expect(nav).to_contain_text("2 / 3")
    nav.get_by_role("button", name="다음").click()
    settle(page)
    expect(nav).to_contain_text("3 / 3")
    expect(page.locator(".lb-row[id]")).to_have_count(20)
    fixture.total = 3
    settle(page, 6000)
    settle(page)  # A changed effective page starts a fresh bounded poll.
    expect(page.locator(".lb-row[id]")).to_have_count(3)
    expect(nav).to_have_count(0)
    expect(page.get_by_text("아직 등록된 매크로가 없어요", exact=True)).to_have_count(0)
    assert any(q.get("page") == ["3"] and r["page"] == 1 and len(r["items"]) == 3
               for q, r in zip(fixture.queries, fixture.responses))
    return {"effective_page": 1, "visible_rows": 3}


def assert_target_focused(page, fixture):
    target = page.locator("#leaderboard-entry-1")
    expect(target).to_be_focused()
    expect(target).to_be_in_viewport()
    expect(page.get_by_role("navigation", name="리더보드 페이지")).to_contain_text("3 / 3")
    assert any(query.get("entry_id") == ["1"] for query in fixture.queries)
    assert all(len(response["items"]) <= 50 for response in fixture.responses)
    return {"entry_id": 1, "effective_page": 3, "focused": True, "max_response_rows": max(len(r["items"]) for r in fixture.responses)}


def off_page_macro_focus(page, fixture):
    expect(page.locator("#leaderboard-entry-1")).to_have_count(0)
    page.evaluate("window.dispatchEvent(new CustomEvent('ggp:leaderboard-focus-entry', {detail:{entryId:1}}))")
    settle(page)
    settle(page)
    return assert_target_focused(page, fixture)


def newly_registered_entry_waits_for_snapshot(page, fixture):
    assert any(query.get("entry_id") == ["1"] for query in fixture.queries)
    expect(page.locator("#leaderboard-entry-1")).to_have_count(0)
    fixture.target_available = True
    settle(page, 6000)
    settle(page)
    return assert_target_focused(page, fixture)


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix="ggp-leaderboard-browser-") as temporary:
        directory = Path(temporary)
        build_harness(directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(directory)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
                if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                    launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
                browser = playwright.chromium.launch(**launch)
                for test in (shrinking_snapshot, off_page_macro_focus, newly_registered_entry_waits_for_snapshot):
                    registered = test is newly_registered_entry_waits_for_snapshot
                    fixture = Fixtures(target_available=not registered)
                    errors = []
                    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
                    context.route("**/*", fixture.route)
                    context.add_init_script(f"window.auditRegistered = {json.dumps(registered)};")
                    page = context.new_page()
                    page.set_default_timeout(4000)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.clock.install(time=NOW)
                    try:
                        page.goto(f"http://127.0.0.1:{server.server_port}")
                        settle(page)
                        expect(page.locator(".lb-row[id]")).to_have_count(50)
                        evidence = test(page, fixture)
                        assert not errors, errors
                        assert not fixture.unexpected, fixture.unexpected
                        results[test.__name__] = {"passed": True, **evidence}
                    except Exception as error:
                        results[test.__name__] = {"passed": False, "error": str(error)}
                    finally:
                        context.close()
                    print(json.dumps({test.__name__: results[test.__name__]}, ensure_ascii=False), flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    report = {"passed": all(result["passed"] for result in results.values()), "scenarios": results}
    if os.environ.get("LEADERBOARD_BROWSER_REPORT"):
        Path(os.environ["LEADERBOARD_BROWSER_REPORT"]).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
