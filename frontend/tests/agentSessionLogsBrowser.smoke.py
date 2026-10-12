"""Session log menu against a real build and local read/write receipt fixtures.

No backend/production accounts, provider requests or exchange orders. Browser
clock advances polling without wall-clock waits. No screenshots are retained.
"""
import importlib.util
import json
import os
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def load_fixture(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".smoke.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notifications = load_fixture("agentNotificationBrowser")
static = load_fixture("runnerDeviceBrowser")


class LogsFixture(notifications.Fixtures):
    def __init__(self):
        super().__init__()
        self.log_requests = []
        self.receipts = []
        self.fail_logs = False
        self.fail_receipts = False
        self.clear_receipt_after_first = False
        self.hold_session = None
        self.hold_before = False
        self.held = []
        self.rows = {41: [self.event(10, "news", "첫 뉴스 응답 수신", "news"),
                          self.event(9, "fill", "매수 2 LINK 체결", "trade"),
                          self.event(8, "start", "매크로 시작", "execution"),
                          self.event(7, "news_error", "뉴스 수집 연결 오류", "news")],
                     42: [self.event(12, "order", "다른 매크로의 주문", "trade")],
                     43: [self.event(5, "stop", "종료된 매크로 기록", "execution")]}

    def event(self, event_id, kind, message, category):
        news = category == "news"
        return {"id": event_id, "kind": kind, "category": category, "message": message,
                "source": "news_receipt" if news else "execution",
                "source_label": "뉴스 응답 수신 확인" if news else "실행 기록",
                "ts": notifications.iso(notifications.BASE_MS - 60_000),
                "received_at": notifications.iso(notifications.BASE_MS),
                "details": {"count": 1 if kind == "news" else 0,
                            "collection_status": "error" if kind == "news_error" else "ready",
                            "examples": [{"title": "체인링크 네트워크 업데이트 발표", "source": "검증 뉴스",
                                          "url": "https://fixture.example/news/link-one"}] if kind == "news" else []}}

    def sessions(self):
        first = self.session()
        second = {**first, "session_id": 42, "symbol": "BTCUSDT", "macro": {**notifications.MACRO, "symbol": "BTCUSDT"}}
        stopped = {**first, "session_id": 43, "status": "stopped", "connected": False,
                   "in_position": False, "stopped_at": notifications.iso(self.now)}
        return {"active": [first, second], "recent": [stopped], "poll_seconds": 3}

    def log_response(self, sid, query):
        category = query.get("category", ["all"])[0]
        before = int(query.get("before_id", ["999999"])[0])
        rows = [row for row in self.rows[sid] if row["id"] < before and
                (category == "all" or row["category"] == category or category == "error" and row["kind"] == "news_error")]
        rows = sorted(rows, key=lambda row: row["id"], reverse=True)
        page = rows[:2]
        return {"session_id": sid, "status": "stopped" if sid == 43 else "running", "items": page,
                "has_more": len(rows) > 2, "next_before_id": page[-1]["id"] if len(rows) > 2 else None,
                "retention": {"event_cap": 500, "news_cap": 100, "history_days": 30, "history_keep": 30, "pin_limit": 10}}

    def route(self, route):
        parsed = urlsplit(route.request.url)
        path = parsed.path
        if "/api/" in path and parsed.hostname not in {"localhost", "127.0.0.1"}:
            route.abort()
            return
        if "/api/me/agents/sessions/" in path:
            sid = int(path.split("/sessions/")[1].split("/")[0])
            if path.endswith("/logs"):
                query = parse_qs(parsed.query)
                self.log_requests.append((sid, query))
                if self.hold_session == sid or self.hold_before and query.get("before_id"):
                    self.held.append((route, self.log_response(sid, query)))
                    return
                if self.fail_logs:
                    route.fulfill(status=500, json={"detail": "fixture read failure"})
                else:
                    route.fulfill(json=self.log_response(sid, query))
                return
            if path.endswith("/news-receipts"):
                assert route.request.method == "POST"
                token = route.request.post_data_json["receipt_token"]
                self.receipts.append((sid, token))
                if self.fail_receipts:
                    route.fulfill(status=500, json={"detail": "fixture write failure"})
                else:
                    if not any(row["id"] == 25 for row in self.rows[sid]):
                        self.rows[sid].append(self.event(25, "news", "새 뉴스 응답 수신 저장", "news"))
                    route.fulfill(json={"ok": True, "recorded": True, "event_id": 25})
                return
            if path.endswith("/position-news"):
                self.calls["news"] += 1
                clear = self.clear_receipt_after_first and self.calls["news"] > 1
                route.fulfill(json={"context": {"session_id": sid, "asset_symbol": "LINK"},
                    "analysis_status": "ready", "updated_at": notifications.iso(self.now),
                    "collection": {"status": "ready", "freshness": "fresh"}, "items": [] if clear else self.news_items,
                    "receipt_key": None if clear else f"stable-{sid}",
                    "receipt_token": None if clear else f"opaque-{sid}-{self.calls['news']}"})
                return
        super().route(route)


def advance(page, fixture, seconds):
    notifications.advance(page, fixture, seconds)


def open_page(browser, origin, width=1440, theme="dark", fixture=None, path="/agents?session=41"):
    fixture = fixture or LogsFixture()
    context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme, reduced_motion="reduce")
    context.route("**/*", fixture.route)
    context.route_web_socket("**/*", fixture.websocket)
    context.add_init_script("localStorage.setItem('ggp_token','browser-fixture');"
        + "localStorage.setItem('ggp_user'," + json.dumps(json.dumps(notifications.USER)) + ");"
        + f"localStorage.setItem('ggp_theme','{theme}');")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install(time=datetime.fromtimestamp(notifications.BASE_MS / 1000, timezone.utc))
    page.goto(origin + path, wait_until="networkidle")
    if page.get_by_role("dialog").count():
        page.get_by_role("button", name="확인했어요", exact=True).click()
    page.clock.pause_at(datetime.fromtimestamp((notifications.BASE_MS + 3000) / 1000, timezone.utc))
    advance(page, fixture, 2)
    if width <= 800:
        page.locator(".agent-workspace-tabs").get_by_role("button", name="에이전트", exact=True).click()
    return context, page, fixture, errors


def open_logs(page):
    page.get_by_role("group", name="매크로 화면 선택").get_by_role("button", name="활동 로그", exact=True).click()
    settle(page)
    expect(page.get_by_role("heading", name="활동 로그", exact=True)).to_be_visible()


def settle(page):
    page.clock.run_for(100)
    page.wait_for_timeout(100)


def release_held(fixture):
    for route, payload in fixture.held:
        route.fulfill(json=payload)
    fixture.held.clear()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(static.Handler, directory=str(static.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    checks = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
                **({"executable_path": os.environ["BROWSER_EXECUTABLE_PATH"]} if os.environ.get("BROWSER_EXECUTABLE_PATH") else {}))
            for width, theme in [(1440, "dark"), (1440, "light"), (390, "dark"), (320, "light")]:
                context, page, fixture, errors = open_page(browser, origin, width, theme)
                assert not fixture.log_requests, "Closed menu must not poll logs"
                advance(page, fixture, 15)
                assert len(fixture.receipts) == 1, fixture.receipts
                open_logs(page)
                expect(page.get_by_text("새 뉴스 응답 수신 저장", exact=True)).to_be_visible()
                expect(page.get_by_text("2026.09.07 11:59:00 KST", exact=True).first).to_be_visible()
                page.get_by_role("button", name="이전 기록 더 보기", exact=True).click()
                expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
                ids = page.locator("[data-log-id]").evaluate_all("nodes => nodes.map(node => node.dataset.logId)")
                assert len(ids) == len(set(ids)) and ids == ["25", "10", "9", "8"], ids
                filters = page.get_by_role("group", name="활동 로그 종류")
                filters.get_by_role("button", name="매매", exact=True).click()
                settle(page)
                expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
                expect(page.locator("[data-log-id]")).to_have_count(1)
                filters.get_by_role("button", name="오류", exact=True).click()
                settle(page)
                expect(page.get_by_text("뉴스 수집 연결 오류", exact=True)).to_be_visible()
                expect(page.get_by_text("뉴스 수집 오류", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                fixture.fail_logs = True
                page.get_by_role("button", name="새로고침", exact=True).click()
                settle(page)
                expect(page.get_by_text("활동 로그 갱신에 실패했어요. 마지막 확인 기록은 유지해요.", exact=True)).to_be_visible()
                expect(page.get_by_text("뉴스 수집 연결 오류", exact=True)).to_be_visible()
                fixture.fail_logs = False
                page.get_by_role("button", name="다시 시도", exact=True).click()
                settle(page)
                expect(page.locator(".agent-session-log-error")).to_have_count(0)
                before = len(fixture.log_requests)
                page.get_by_role("group", name="매크로 화면 선택").get_by_role("button", name="에이전트", exact=True).click()
                advance(page, fixture, 12)
                assert len(fixture.log_requests) == before
                assert len(fixture.receipts) == 1, fixture.receipts
                assert not errors, errors
                context.close()
                checks += 1

            # A delayed old-session response must not leak into the next run.
            fixture = LogsFixture()
            fixture.hold_session = 41
            context, page, fixture, errors = open_page(browser, origin, fixture=fixture)
            open_logs(page)
            expect(page.get_by_text("저장된 활동 로그를 확인하고 있어요.", exact=True)).to_be_visible()
            page.locator(".agent-macro-select-wrap select").select_option("42")
            settle(page)
            expect(page.get_by_text("다른 매크로의 주문", exact=True)).to_be_visible()
            release_held(fixture)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_have_count(0)
            page.reload(wait_until="networkidle")
            expect(page.get_by_text("다른 매크로의 주문", exact=True)).to_be_visible()
            assert not errors, errors
            context.close()
            checks += 1

            # Closing during older pagination aborts it; reopening even after a
            # head failure must leave Retry enabled, not an eternal loading flag.
            context, page, fixture, errors = open_page(browser, origin)
            open_logs(page)
            expect(page.get_by_text("새 뉴스 응답 수신 저장", exact=True)).to_be_visible()
            fixture.hold_before = True
            page.get_by_role("button", name="이전 기록 더 보기", exact=True).click()
            expect(page.get_by_role("button", name="이전 기록 확인 중…", exact=True)).to_be_disabled()
            page.get_by_role("group", name="매크로 화면 선택").get_by_role("button", name="에이전트", exact=True).click()
            fixture.fail_logs = True
            open_logs(page)
            expect(page.get_by_role("button", name="다시 시도", exact=True)).to_be_enabled()
            fixture.fail_logs = False
            fixture.hold_before = False
            page.get_by_role("button", name="다시 시도", exact=True).click()
            settle(page)
            expect(page.get_by_role("button", name="이전 기록 더 보기", exact=True)).to_be_enabled()
            release_held(fixture)
            assert not errors, errors
            context.close()
            checks += 1

            # Ended history has existing logs, never a new news-receipt write.
            context, page, fixture, errors = open_page(browser, origin, path="/agents?session=43&panel=logs")
            expect(page.get_by_text("종료된 매크로 기록", exact=True)).to_be_visible()
            advance(page, fixture, 22)
            assert len(fixture.log_requests) == 1 and not fixture.receipts
            assert not errors, errors
            context.close()
            checks += 1

            # Failed receipt retries twice, then stops even across many polls.
            fixture = LogsFixture()
            fixture.fail_receipts = True
            fixture.clear_receipt_after_first = True
            context, page, fixture, errors = open_page(browser, origin, fixture=fixture)
            advance(page, fixture, 15)
            assert len(fixture.receipts) == 2, fixture.receipts
            open_logs(page)
            expect(page.get_by_role("button", name="기록 다시 저장", exact=True)).to_be_visible()
            fixture.fail_receipts = False
            page.get_by_role("button", name="기록 다시 저장", exact=True).click()
            expect(page.get_by_role("button", name="기록 다시 저장", exact=True)).to_have_count(0)
            assert len(fixture.receipts) == 3
            assert not errors, errors
            context.close()
            checks += 1
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({"passed": True, "browser_scenarios": checks, "production_requests": 0, "orders": 0}))


if __name__ == "__main__":
    main()
