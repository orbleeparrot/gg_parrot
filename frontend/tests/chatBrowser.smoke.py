"""Browser regressions for the real ChatBox with an entirely local fixture API.

Requires frontend npm dependencies, Python Playwright and Chromium. Run with:
  BROWSER_EXECUTABLE_PATH=/path/to/chrome python frontend/tests/chatBrowser.smoke.py
Optional NODE_BINARY selects Node; CHAT_BROWSER_REPORT writes JSON results.
The script bundles a temporary component harness, serves localhost, and blocks
all external requests. It neither starts nor contacts the production backend.
"""

import json
import os
import re
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
NOW = datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc)
DAY_START = int(datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 86_400_000


def message(message_id, user_id=9, username="other", day_start=DAY_START):
    return {
        "id": message_id,
        "user_id": user_id,
        "username": username,
        "text": f"message-{message_id}",
        "created_at": datetime.fromtimestamp((day_start + 1000 * message_id) / 1000, timezone.utc).isoformat(),
        "created_ms": day_start + 1000 * message_id,
        "created_kst": "16:00",
    }


class Fixtures:
    def __init__(self, items, seen=1, page_size=200):
        self.items = list(items)
        self.seen = {1: seen, 2: 0}
        self.day_start = DAY_START
        self.page_size = page_size
        self.hold = False
        self.held = []
        self.fail = False
        self.fail_reads = False
        self.get_count = 0
        self.post_bodies = []
        self.read_bodies = []
        self.unexpected_requests = []

    def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/"):
            if parsed.path not in {"/api/chat", "/api/chat/read"}:
                self.unexpected_requests.append(request.url)
                route.fulfill(status=404, json={"detail": "Unexpected fixture API"})
                return
            token = request.headers.get("authorization", "")
            match = re.fullmatch(r"Bearer fixture-member-(\d+)", token)
            user_id = int(match[1]) if match else None
            if parsed.path == "/api/chat/read":
                assert request.method == "PUT", request.method
                if user_id is None:
                    route.fulfill(status=401, json={"detail": "로그인이 필요해요."})
                    return
                body = request.post_data_json
                self.read_bodies.append((user_id, body))
                if self.fail_reads:
                    route.fulfill(status=503, json={"detail": "읽음 상태를 저장하지 못했어요."})
                    return
                self.seen[user_id] = max(self.seen.get(user_id) or 0, body["last_seen_id"])
                route.fulfill(json={"seen_id": self.seen[user_id]})
                return
            if request.method == "POST":
                body = request.post_data_json
                self.post_bodies.append(body)
                if user_id is None:
                    route.fulfill(status=401, json={"detail": "로그인이 필요해요."})
                    return
                row = message(max([item["id"] for item in self.items] + [0]) + 1,
                              user_id, "shared-name", self.day_start)
                row["text"] = body["text"]
                self.items.append(row)
                route.fulfill(json={"message": row})
                return
            assert request.method == "GET", request.method
            self.get_count += 1
            if self.fail:
                route.fulfill(status=503, json={"detail": "채팅을 불러오지 못했어요."})
                return
            query = parse_qs(parsed.query)
            before = int(query["before_id"][0]) if "before_id" in query else None
            if user_id is not None and self.seen.get(user_id) is None:
                self.seen[user_id] = max([item["id"] for item in self.items] + [0])
            server_cursor = self.seen.get(user_id)
            cursor = server_cursor
            if "seen_id" in query:
                cursor = max(cursor or 0, int(query["seen_id"][0]))
            current = [dict(item) for item in self.items if item["created_ms"] >= self.day_start]
            eligible = [item for item in current if before is None or item["id"] < before]
            selected = eligible[-self.page_size:]
            payload = {
                "items": selected,
                "has_more": len(eligible) > len(selected),
                "oldest_id": selected[0]["id"] if selected else None,
                "latest_id": max([item["id"] for item in current] + [0]),
                "day_start_ms": self.day_start,
                "seen_id": cursor,
                "server_seen_id": server_cursor,
                "unseen_count": sum(item["id"] > cursor and (user_id is None or item["user_id"] != user_id)
                                    for item in current) if cursor is not None else 0,
            }
            if self.hold and before is None:
                self.held.append((route, payload))
            else:
                route.fulfill(json=payload)
            return
        if parsed.hostname in {"127.0.0.1", "localhost"}:
            if parsed.path.startswith("/brand/") or parsed.path == "/favicon.ico":
                route.fulfill(status=204)
            else:
                route.continue_()
        else:
            self.unexpected_requests.append(request.url)
            route.abort()

    def release(self):
        pending, self.held = self.held, []
        for route, payload in pending:
            route.fulfill(json=payload)


def build_harness(directory):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        raise RuntimeError("Node is required; set NODE_BINARY or add node to PATH")
    entry = directory / "entry.jsx"
    source = f"""
import React, {{ useState }} from {json.dumps(str(FRONTEND / 'node_modules/react/index.js'))};
import {{ createRoot }} from {json.dumps(str(FRONTEND / 'node_modules/react-dom/client.js'))};
import ChatBox from {json.dumps(str(FRONTEND / 'src/components/ChatBox.jsx'))};
import {{ setAuth, clearAuth }} from {json.dumps(str(FRONTEND / 'src/lib/auth.js'))};
function Harness() {{
  const [mounted, setMounted] = useState(true);
  window.auditMount = setMounted;
  window.auditAuth = (id) => setAuth(`fixture-member-${{id}}`, {{id, username: 'shared-name'}});
  window.auditLogout = clearAuth;
  return mounted ? <ChatBox /> : <p>Another route</p>;
}}
createRoot(document.getElementById('root')).render(<Harness />);
"""
    entry.write_text(source)
    build = """
const esbuild = require(process.argv[1]);
esbuild.buildSync({entryPoints: [process.argv[2]], outfile: process.argv[3],
 bundle: true, format: 'iife', jsx: 'automatic', nodePaths: [process.argv[4]],
 define: {'import.meta.env': '{}'}, logLevel: 'error'});
"""
    subprocess.run([node, "-e", build, str(FRONTEND / "node_modules/esbuild"),
                    str(entry), str(directory / "app.js"), str(FRONTEND / "node_modules")], check=True)
    (directory / "index.html").write_text("""<!doctype html><html lang="ko"><meta charset="utf-8"><link rel="stylesheet" href="/app.css">
<style>
.chat-sheet>.chat-log{flex:none;height:180px;overflow:auto;overflow-anchor:none}.chat-row{min-height:45px}
.chat-float{width:430px}.chat-fab img{width:32px}.chat-sticker-tray img{width:40px}
svg{width:16px;height:16px}
</style><body><div id="root"></div><script src="/app.js"></script></body></html>""")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def settle(page):
    page.clock.run_for(50)
    page.wait_for_timeout(50)


def advance_poll(page):
    page.clock.run_for(3500)
    page.wait_for_timeout(80)


def open_chat(page):
    page.locator(".chat-fab").click()
    settle(page)


def close_chat(page):
    page.locator(".chat-close").click()
    settle(page)


def assert_badge(page, count):
    badge = page.locator(".chat-fab-badge")
    if count:
        expect(badge).to_have_text(f"{'99+' if count > 99 else count} new")
    else:
        expect(badge).to_have_count(0)


class Suite:
    def __init__(self, browser, base):
        self.browser, self.base = browser, base
        self.contexts = []
        self.errors = []
        self.fixtures = []

    def page(self, fixture, context=None, authenticated=True):
        if context is None:
            context = self.browser.new_context()
            self.contexts.append(context)
            context.route("**/*", fixture.route)
            if authenticated:
                context.add_init_script("""if (!localStorage.getItem('ggp_token')) {
localStorage.setItem('ggp_token', 'fixture-member-1');
localStorage.setItem('ggp_user', JSON.stringify({id:1, username:'shared-name'}));
}""")
        page = context.new_page()
        page.set_default_timeout(4000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.clock.install(time=NOW)
        page.goto(self.base)
        page.wait_for_function("window.auditMount !== undefined")
        settle(page)
        self.fixtures.append(fixture)
        return page, context

    def cleanup(self):
        for context in self.contexts:
            context.close()
        assert not self.errors, self.errors
        unexpected = [url for fixture in self.fixtures for url in fixture.unexpected_requests]
        assert not unexpected, unexpected


def identity(suite):
    fixture = Fixtures([message(1, 1, "shared-name"), message(2, 2, "shared-name"),
                        message(3, None, "shared-name")], seen=3)
    page, _ = suite.page(fixture)
    open_chat(page)
    expect(page.locator(".chat-row.is-mine")).to_have_count(1)
    expect(page.locator(".chat-row.is-mine")).to_contain_text("message-1")
    expect(page.locator(".chat-row.is-continued")).to_have_count(0)
    page.get_by_role("textbox", name="채팅 메시지", exact=True).fill("private draft")
    close_chat(page)
    fixture.items.append(message(4))
    page.evaluate("auditAuth(2)")
    settle(page)
    assert_badge(page, 3)
    open_chat(page)
    expect(page.get_by_role("textbox", name="채팅 메시지", exact=True)).to_have_value("")
    expect(page.locator(".chat-row.is-mine")).to_have_count(1)
    expect(page.locator(".chat-row.is-mine")).to_contain_text("message-2")
    close_chat(page)
    page.evaluate("auditAuth(1)")
    settle(page)
    assert_badge(page, 1)


def cross_tab_auth(suite):
    fixture = Fixtures([message(1, 1, "shared-name"), message(2, 2, "shared-name")], seen=2)
    page, context = suite.page(fixture)
    open_chat(page)
    page.get_by_role("textbox", name="채팅 메시지", exact=True).fill("private draft")
    other, _ = suite.page(fixture, context=context)
    other.evaluate("auditAuth(2)")
    settle(other)
    settle(page)
    expect(page.get_by_role("dialog", name="리더보드 채팅")).to_have_count(0)
    open_chat(page)
    expect(page.get_by_role("textbox", name="채팅 메시지", exact=True)).to_have_value("")
    expect(page.locator(".chat-row.is-mine")).to_have_count(1)
    expect(page.locator(".chat-row.is-mine")).to_contain_text("message-2")


def remount(suite):
    fixture = Fixtures([message(1), message(2)])
    page, _ = suite.page(fixture)
    assert_badge(page, 1)
    page.evaluate("auditMount(false)")
    expect(page.locator(".chat-fab")).to_have_count(0)
    fixture.hold = True
    page.evaluate("auditMount(true)")
    settle(page)
    assert fixture.held, "Returning to the page should refresh messages"
    assert_badge(page, 1)
    fixture.hold = False
    fixture.release()
    settle(page)
    assert_badge(page, 1)


def cross_tab(suite):
    fixture = Fixtures([message(1), message(2)])
    page, context = suite.page(fixture)
    assert_badge(page, 1)
    other, _ = suite.page(fixture, context=context)
    open_chat(other)
    assert fixture.seen[1] == 2
    # Do not advance the first tab's poll clock: storage notifications must suffice.
    assert_badge(page, 0)


def empty_arrival(suite):
    fixture = Fixtures([], seen=None)
    page, _ = suite.page(fixture)
    assert_badge(page, 0)
    fixture.items.append(message(1))
    advance_poll(page)
    assert_badge(page, 1)


def scroll_unread(suite):
    fixture = Fixtures([message(index) for index in range(1, 31)], seen=30)
    page, _ = suite.page(fixture)
    open_chat(page)
    page.locator(".chat-log").evaluate("el => {el.scrollTop=0; el.dispatchEvent(new Event('scroll'));}")
    fixture.items.append(message(31))
    advance_poll(page)
    position = page.locator(".chat-log").evaluate("el => ({top:el.scrollTop,height:el.clientHeight,scrollHeight:el.scrollHeight})")
    assert position["scrollHeight"] - position["top"] - position["height"] > 100, position
    assert (fixture.seen[1] or 0) < 31, "A message below the viewport must remain unread"
    close_chat(page)
    assert_badge(page, 1)


def load_failure(suite):
    fixture = Fixtures([message(1)])
    fixture.fail = True
    page, _ = suite.page(fixture)
    open_chat(page)
    expect(page.get_by_role("alert")).to_be_visible()
    fixture.fail = False
    page.get_by_role("button", name=re.compile("다시|재시도")).click()
    settle(page)
    expect(page.locator(".chat-bubble")).to_have_text("message-1")
    expect(page.get_by_role("alert")).to_have_count(0)


def post_get_race(suite):
    fixture = Fixtures([message(1)])
    page, _ = suite.page(fixture)
    open_chat(page)
    fixture.hold = True
    advance_poll(page)
    assert fixture.held
    page.get_by_role("textbox", name="채팅 메시지", exact=True).fill("sent-success")
    page.get_by_role("button", name="전송", exact=True).click()
    settle(page)
    bubble = page.locator(".chat-bubble").filter(has_text="sent-success")
    expect(bubble).to_have_count(1)
    assert fixture.post_bodies == [{"text": "sent-success"}], fixture.post_bodies
    page.evaluate("""() => {
      window.auditPostDisappeared = false;
      window.auditPostObserver = new MutationObserver(() => {
        if (![...document.querySelectorAll('.chat-bubble')].some(el => el.textContent === 'sent-success')) {
          window.auditPostDisappeared = true;
        }
      });
      window.auditPostObserver.observe(document.querySelector('.chat-log'), {childList:true, subtree:true});
    }""")
    fixture.hold = False
    fixture.release()
    settle(page)
    expect(bubble).to_have_count(1)
    assert not page.evaluate("window.auditPostDisappeared"), "A stale GET briefly erased a successful POST"
    advance_poll(page)
    expect(bubble).to_have_count(1)


def read_sync_after_reload(suite):
    fixture = Fixtures([message(1), message(2)])
    fixture.fail_reads = True
    page, _ = suite.page(fixture)
    open_chat(page)
    expect(page.get_by_role("alert")).to_contain_text("읽음 상태를 저장하지 못했어요.")
    assert fixture.seen[1] == 1
    assert fixture.read_bodies
    fixture.fail_reads = False
    # A full reload clears the memory cache while keeping the unsynced local cursor.
    # GET returns effective seen_id=2 but server_seen_id=1; the PUT must still run.
    page.reload()
    page.wait_for_function("window.auditMount !== undefined")
    settle(page)
    for _ in range(40):
        if fixture.seen[1] == 2:
            break
        page.wait_for_timeout(50)
    assert fixture.seen[1] == 2, "An unsynced local read cursor was never persisted after reload"
    assert_badge(page, 0)


def own_post_does_not_read_unfetched_messages(suite):
    fixture = Fixtures([message(1)])
    page, _ = suite.page(fixture)
    open_chat(page)
    fixture.hold = True
    advance_poll(page)
    assert fixture.held
    # Another member's message is on the server but absent from our held GET.
    fixture.items.append(message(2))
    page.get_by_role("textbox", name="채팅 메시지", exact=True).fill("my-third-message")
    page.get_by_role("button", name="전송", exact=True).click()
    settle(page)
    expect(page.locator(".chat-bubble").filter(has_text="my-third-message")).to_have_count(1)
    expect(page.locator(".chat-bubble").filter(has_text=re.compile(r"^message-2$"))).to_have_count(0)
    assert fixture.seen[1] == 1, "Our POST jumped the read cursor over an unfetched message"
    close_chat(page)
    fixture.hold = False
    fixture.release()
    settle(page)
    assert_badge(page, 1)
    assert fixture.seen[1] == 1
    open_chat(page)
    expect(page.locator(".chat-bubble").filter(has_text=re.compile(r"^message-2$"))).to_have_count(1)
    for _ in range(40):
        if fixture.seen[1] == 3:
            break
        page.wait_for_timeout(50)
    assert fixture.seen[1] == 3, "The cursor should advance after missing messages are fetched and visible"


def read_sync_retry_on_poll(suite):
    fixture = Fixtures([message(1), message(2)])
    fixture.fail_reads = True
    page, _ = suite.page(fixture)
    open_chat(page)
    expect(page.get_by_role("alert")).to_contain_text("읽음 상태를 저장하지 못했어요.")
    assert fixture.seen[1] == 1
    failed_attempts = len(fixture.read_bodies)
    fixture.fail_reads = False
    # Recovery must happen on the next poll, without navigation or manual retry.
    advance_poll(page)
    expect(page.get_by_role("alert")).to_have_count(0)
    assert len(fixture.read_bodies) > failed_attempts, "The failed read write was not retried"
    assert fixture.seen[1] == 2
    assert_badge(page, 0)


def delayed_divider(suite):
    fixture = Fixtures([message(1), message(2)])
    fixture.hold = True
    page, _ = suite.page(fixture)
    open_chat(page)
    fixture.hold = False
    fixture.release()
    settle(page)
    expect(page.get_by_role("separator", name="여기부터 새 메시지")).to_have_count(1)


def pagination(suite):
    fixture = Fixtures([message(index) for index in range(1, 7)], seen=6, page_size=3)
    page, _ = suite.page(fixture)
    open_chat(page)
    expect(page.locator(".chat-row")).to_have_count(3)
    page.get_by_role("button", name=re.compile("이전.*(대화|메시지)|(대화|메시지).*더" )).click()
    settle(page)
    expect(page.locator(".chat-row")).to_have_count(6)
    assert page.locator(".chat-bubble").all_text_contents() == [f"message-{index}" for index in range(1, 7)]
    advance_poll(page)
    expect(page.locator(".chat-row")).to_have_count(6)


def history_gap_after_absence(suite):
    fixture = Fixtures([message(index) for index in range(1, 6)], seen=5, page_size=3)
    page, _ = suite.page(fixture)
    open_chat(page)
    page.get_by_role("button", name="이전 메시지 더 보기", exact=True).click()
    settle(page)
    expect(page.locator(".chat-row")).to_have_count(5)
    expect(page.get_by_role("button", name="이전 메시지 더 보기", exact=True)).to_have_count(0)
    close_chat(page)
    page.evaluate("auditMount(false)")
    expect(page.locator(".chat-fab")).to_have_count(0)
    fixture.page_size = 200
    fixture.items = [message(index) for index in range(1, 406)]
    page.evaluate("auditMount(true)")
    settle(page)
    open_chat(page)
    expect(page.locator(".chat-bubble").filter(has_text=re.compile(r"^message-405$"))).to_have_count(1)
    # The stale cached first page must not anchor the next request at before_id=1.
    # All intervening rows 6..205 must remain reachable after a long absence.
    for _ in range(3):
        button = page.get_by_role("button", name="이전 메시지 더 보기", exact=True)
        if not button.count():
            break
        button.click()
        settle(page)
    expect(page.locator(".chat-row")).to_have_count(405)
    assert page.locator(".chat-bubble").all_text_contents() == [f"message-{index}" for index in range(1, 406)]
    expect(page.get_by_role("button", name="이전 메시지 더 보기", exact=True)).to_have_count(0)


def day_rollover(suite):
    fixture = Fixtures([message(1), message(2)])
    page, _ = suite.page(fixture)
    assert_badge(page, 1)
    fixture.day_start += DAY_MS
    fixture.items = [message(3, day_start=fixture.day_start)]
    page.clock.set_system_time(datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc))
    advance_poll(page)
    assert_badge(page, 1)
    open_chat(page)
    expect(page.locator(".chat-bubble")).to_have_text("message-3")


def anonymous(suite):
    fixture = Fixtures([message(1)], seen=None)
    page, _ = suite.page(fixture, authenticated=False)
    open_chat(page)
    expect(page.locator(".chat-bubble")).to_have_text("message-1")
    expect(page.get_by_role("button", name="전송", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="로그인", exact=True)).to_be_visible()
    assert not fixture.post_bodies


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix="ggp-chat-browser-") as temporary:
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
                for test in (identity, remount, cross_tab, cross_tab_auth, empty_arrival, scroll_unread,
                             load_failure, post_get_race, own_post_does_not_read_unfetched_messages,
                             read_sync_after_reload, read_sync_retry_on_poll,
                             delayed_divider, pagination, history_gap_after_absence,
                             day_rollover, anonymous):
                    suite = Suite(browser, f"http://127.0.0.1:{server.server_port}")
                    try:
                        test(suite)
                        suite.cleanup()
                        results[test.__name__] = {"passed": True}
                    except Exception as error:
                        results[test.__name__] = {"passed": False, "error": str(error)}
                        for context in suite.contexts:
                            context.close()
                    print(json.dumps({test.__name__: results[test.__name__]}, ensure_ascii=False), flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    report = {"passed": all(result["passed"] for result in results.values()), "scenarios": results}
    if os.environ.get("CHAT_BROWSER_REPORT"):
        Path(os.environ["CHAT_BROWSER_REPORT"]).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
