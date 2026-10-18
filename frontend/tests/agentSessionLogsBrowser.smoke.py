"""Session log menu against a real build and local read/write receipt fixtures.

No backend/production accounts, provider requests or exchange orders. Browser
clock advances polling without wall-clock waits. No screenshots are retained.
"""
import importlib.util
import json
import os
import re
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
        self.hold_query = None
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
                "severity": "ERROR" if kind == "news_error" else "INFO",
                "component": "news_delivery" if news else "engine", "event_name": "news.received" if news else f"execution.{kind}",
                "operation_id": "order:fixture-one" if kind in {"order", "fill"} else "",
                "occurred_at": notifications.iso(notifications.BASE_MS - 60_000), "time_basis": "event",
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
        context_id = int(query.get("context_id", ["0"])[0])
        q = query.get("q", [""])[0].lower()
        severity = query.get("severity", [""])[0]
        component = query.get("component", [""])[0]
        operation_id = query.get("operation_id", [""])[0]
        since_ms = int(query.get("since_ms", ["0"])[0])
        rows = [row for row in self.rows[sid] if row["id"] < before and
                (context_id or ((category == "all" or row["category"] == category or category == "error" and row["kind"] == "news_error")
                 and (not q or q in " ".join(str(row.get(key, "")) for key in ["message", "kind", "event_name", "operation_id"]).lower())
                 and (not severity or row.get("severity", "UNKNOWN") == severity)
                 and (not component or row.get("component", "") == component)
                 and (not operation_id or row.get("operation_id", "") == operation_id)
                 and (not since_ms or datetime.fromisoformat(row["received_at"].replace("Z", "+00:00")).timestamp() * 1000 >= since_ms)))]
        rows = sorted(rows, key=lambda row: row["id"], reverse=True)
        page = [{**row, "context_anchor": row["id"] == context_id} for row in rows] if context_id else rows[:2]
        return {"session_id": sid, "status": "stopped" if sid == 43 else "running", "items": page,
                "context": {"session_id": sid, "exchange": "binance", "symbol": "LINKUSDT", "mode": "testnet"},
                "query": query, "has_more": not context_id and len(rows) > 2,
                "next_before_id": page[-1]["id"] if not context_id and len(rows) > 2 else None,
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
                if self.hold_session == sid or self.hold_before and query.get("before_id") or self.hold_query == query.get("q", [""])[0]:
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
    expect(page.get_by_role("group", name="매크로 화면 선택")).to_have_count(0)
    page.get_by_role("group", name="에이전트 대화 종류").get_by_role("button", name="활동 로그", exact=True).click()
    settle(page)
    expect(page.get_by_role("heading", name="활동 로그", exact=True)).to_be_visible()


def settle(page):
    page.clock.run_for(100)
    page.wait_for_timeout(100)


def drain_initial_clock_until_visible(page, locator):
    # Reload retains the paused Playwright clock. React can install the first
    # setTimeout(0) poll AFTER networkidle, so a wall-clock expect alone never
    # starts that request. Drain only until the expected row is visible, with a
    # strict 1s virtual-time budget: no sleeps, manual refresh or 10s poll retry.
    for _ in range(100):
        if locator.is_visible():
            return
        page.clock.run_for(10)


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
                assert page.locator(".agent-session-log-body").bounding_box()["height"] >= 200
                # Terminal output is the default on every viewport. Search is
                # always available, while the three advanced controls collapse.
                expect(page.get_by_role("combobox", name="활동 로그 수준", exact=True)).not_to_be_visible()
                expect(page.locator(".agent-session-log-columns")).to_have_count(0)
                assert page.locator(".agent-session-log-line").first.evaluate("node => getComputedStyle(node).display") == "block"
                page.get_by_role("button", name="상세 필터", exact=True).click()
                expect(page.get_by_role("combobox", name="활동 로그 수준", exact=True)).to_be_visible()
                page.get_by_role("button", name="상세 필터", exact=True).click()
                expect(page.get_by_text("새 뉴스 응답 수신 저장", exact=True)).to_be_visible()
                expect(page.locator("time[title='2026.09.07 11:59:00 KST']").first).to_be_visible()
                expect(page.locator(".agent-session-log-level").first).to_have_text("INFO")
                requests_before_search = len(fixture.log_requests)
                search = page.get_by_role("searchbox", name="활동 로그 검색")
                search.fill("일치하지않는검색어")
                settle(page)
                advance(page, fixture, 1)
                expect(page.get_by_text("검색 조건과 일치하는 로그가 없어요.", exact=True)).to_be_visible()
                expect(page.locator("[data-log-id]")).to_have_count(0)
                search.fill("")
                settle(page)
                advance(page, fixture, 1)
                expect(page.get_by_text("새 뉴스 응답 수신 저장", exact=True)).to_be_visible()
                assert len(fixture.log_requests) > requests_before_search
                assert any(query.get("q") == ["일치하지않는검색어"] for _, query in fixture.log_requests)
                page.get_by_role("button", name="줄바꿈", exact=True).click()
                expect(page.locator(".agent-session-log")).not_to_have_class(re.compile(r".*is-wrapped.*"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.get_by_role("button", name="줄바꿈", exact=True).click()
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
                expect(page.locator(".agent-session-log-kind").first).to_have_attribute("title", "뉴스 수집 오류")
                expect(page.locator(".agent-session-log-kind").first).to_have_text("news.received")
                expect(page.locator(".agent-session-log-level").first).to_have_text("ERROR")
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
                page.get_by_role("group", name="에이전트 대화 종류").get_by_role("button", name="전체", exact=True).click()
                advance(page, fixture, 12)
                assert len(fixture.log_requests) == before
                assert len(fixture.receipts) == 1, fixture.receipts
                assert not errors, errors
                context.close()
                checks += 1

            # Server-backed filters search ALL retained rows, including rows
            # outside the loaded first page. Related views reuse the same GET.
            context, page, fixture, errors = open_page(browser, origin)
            open_logs(page)
            page.get_by_role("button", name="상세 필터", exact=True).click()
            page.get_by_role("combobox", name="활동 로그 수준", exact=True).select_option("ERROR")
            settle(page)
            expect(page.get_by_text("뉴스 수집 연결 오류", exact=True)).to_be_visible()
            assert fixture.log_requests[-1][1]["severity"] == ["ERROR"]
            page.get_by_role("combobox", name="활동 로그 수준", exact=True).select_option("")
            page.get_by_role("combobox", name="활동 로그 주체", exact=True).select_option("engine")
            settle(page)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
            assert fixture.log_requests[-1][1]["component"] == ["engine"]
            page.get_by_role("combobox", name="활동 로그 주체", exact=True).select_option("")
            selected_at = page.evaluate("Date.now()")
            page.get_by_role("combobox", name="활동 로그 서버 수신 기간", exact=True).select_option("24")
            settle(page)
            assert int(fixture.log_requests[-1][1]["since_ms"][0]) == int(selected_at - 86_400_000)
            page.get_by_role("combobox", name="활동 로그 서버 수신 기간", exact=True).select_option("all")
            settle(page)
            search = page.get_by_role("searchbox", name="활동 로그 검색")
            search.fill("매수")
            advance(page, fixture, 1)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
            assert len(page.locator("[data-log-id]").all()) == 1
            row = page.locator("[data-log-id='9']")
            row.locator("summary.agent-session-log-line").click()
            row.get_by_role("button", name="같은 작업 보기", exact=True).click()
            settle(page)
            assert fixture.log_requests[-1][1]["operation_id"] == ["order:fixture-one"]
            assert "q" not in fixture.log_requests[-1][1]
            expect(page.get_by_text("같은 작업: order:fixture-one", exact=True)).to_be_visible()
            row = page.locator("[data-log-id='9']")
            if not row.locator("details.agent-session-log-row").get_attribute("open") == "":
                row.locator("summary.agent-session-log-line").click()
            row.get_by_role("button", name="전후 기록 보기", exact=True).click()
            settle(page)
            assert fixture.log_requests[-1][1]["context_id"] == ["9"]
            assert "operation_id" not in fixture.log_requests[-1][1]
            expect(page.locator("[data-log-id='9']")).to_have_class(re.compile(r".*is-anchor.*"))
            expect(page.get_by_text("뉴스 수집 연결 오류", exact=True)).to_be_visible()
            page.get_by_role("button", name="조회 조건 해제", exact=True).click()
            settle(page)
            page.get_by_role("button", name="화면 일시정지", exact=True).click()
            expect(page.get_by_text("화면만 일시정지 · 매크로 계속 실행", exact=True)).to_be_visible()
            before = len(fixture.log_requests)
            fixture.rows[41].append(fixture.event(32, "fill", "일시정지 이후 새 체결", "trade"))
            advance(page, fixture, 25)
            assert len(fixture.log_requests) == before, fixture.log_requests
            expect(page.get_by_text("일시정지 이후 새 체결", exact=True)).to_have_count(0)
            page.get_by_role("button", name="자동 갱신 재개", exact=True).click()
            settle(page)
            expect(page.get_by_text("일시정지 이후 새 체결", exact=True)).to_be_visible()
            assert not errors, errors
            context.close()
            checks += 1

            # Changing filters invalidates an already in-flight response just
            # like switching sessions; old search results cannot leak through.
            fixture = LogsFixture()
            fixture.hold_query = "지연"
            context, page, fixture, errors = open_page(browser, origin, fixture=fixture)
            open_logs(page)
            search = page.get_by_role("searchbox", name="활동 로그 검색")
            search.fill("지연")
            advance(page, fixture, 1)
            assert fixture.held
            search.fill("매수")
            advance(page, fixture, 1)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
            release_held(fixture)
            settle(page)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_be_visible()
            expect(page.get_by_text("검색 조건과 일치하는 로그가 없어요.", exact=True)).to_have_count(0)
            assert not errors, errors
            context.close()
            checks += 1

            # Legacy rows never synthesize INFO or occurrence timestamps from
            # missing fields. Server-reception fallback is explicitly labeled.
            fixture = LogsFixture()
            old = fixture.event(39, "error", "구 형식 진단 기록", "error")
            old.update(severity="UNKNOWN", component="", event_name="", occurred_at="", time_basis="server_reception")
            fixture.rows[41].append(old)
            context, page, fixture, errors = open_page(browser, origin, fixture=fixture)
            open_logs(page)
            row = page.locator("[data-log-id='39']")
            expect(row.locator(".agent-session-log-level")).to_have_text("UNKNOWN")
            expect(row.locator(".agent-session-log-time").first).to_contain_text("수신")
            row.locator("summary.agent-session-log-line").click()
            expect(row.get_by_text("기록되지 않음 · 서버 수신 시각으로 표시", exact=True)).to_be_visible()
            assert not errors, errors
            context.close()
            checks += 1

            # Provenance stays event-local, not inferred from the testnet
            # session header. Stored legacy text remains visible with an
            # explicit evidence caveat; server failures are not receipts.
            for width in [1440, 390]:
                fixture = LogsFixture()
                simulated = fixture.event(61, "fill", "모의 체결 결과", "trade")
                simulated.update(event_name="runner.order_result", component="runner", details={
                    "mode": "mock", "executed_qty": 2, "fill_price": 100,
                    "fees_known": True, "reconciliation_scope": "simulated_fee_model"})
                legacy = fixture.event(60, "order", "구 실행기 체결 보고 · 0 @ 0", "trade")
                legacy.update(event_name="command.execution_reported", details={"executed_qty": 0, "fill_price": 0})
                server_failure = fixture.event(59, "news_error", "서버 뉴스 응답 생성 실패 · HTTP 429", "news")
                server_failure.update(event_name="news.request_failed", component="server", source="news_request",
                    source_label="서버 뉴스 응답 생성 실패", details={"http_status": 429,
                        "verification": "server_response_not_browser_receipt", "outcome": "response_not_created"})
                receipt = fixture.event(58, "news", "뉴스 브라우저 응답 수신", "news")
                receipt.update(event_name="news.response_received")
                fixture.rows[41] = [simulated, legacy, server_failure, receipt]
                context, page, fixture, errors = open_page(browser, origin, width=width, fixture=fixture)
                open_logs(page)
                while page.get_by_role("button", name="이전 기록 더 보기", exact=True).count():
                    page.get_by_role("button", name="이전 기록 더 보기", exact=True).click()
                    settle(page)
                mock_row = page.locator("[data-log-id='61']")
                expect(mock_row.locator(".agent-session-log-observation")).to_contain_text("모의 · 실제 주문 없음")
                mock_row.locator("summary.agent-session-log-line").click()
                expect(mock_row.locator("dt", has_text=re.compile(r"^fees_known$")).locator("..")
                    .locator("dd")).to_contain_text("true")
                expect(mock_row.locator("dt", has_text=re.compile(r"^fees_known$")).locator("..")
                    .locator("dd")).to_contain_text("수수료 금액 확인 아님")
                expect(mock_row.locator("dt", has_text=re.compile(r"^reconciliation_scope$")).locator("..")
                    .locator("dd")).to_contain_text("실제 거래소 수수료 아님")
                old_row = page.locator("[data-log-id='60']")
                expect(old_row.locator(".agent-session-log-message")).to_have_text("구 실행기 체결 보고 · 0 @ 0")
                expect(old_row.locator(".agent-session-log-observation")).to_contain_text("체결 수량·가격 확인 정보 없음")
                expect(old_row.locator(".agent-session-log-observation")).not_to_contain_text("테스트넷")
                failed_row = page.locator("[data-log-id='59']")
                expect(failed_row.locator(".agent-session-log-kind")).to_have_attribute("title", "뉴스 요청 실패")
                expect(failed_row.locator(".agent-session-log-observation")).to_have_text("# 서버 요청 실패 기록 · 브라우저 수신 확인 아님")
                expect(page.locator("[data-log-id='58'] .agent-session-log-observation")).to_have_text(
                    "# 브라우저 응답 수신 · 수집 완료/사용자 열람 증명 아님")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                context.close()
                checks += 1

            # A delayed old-session response must not leak into the next run.
            fixture = LogsFixture()
            fixture.hold_session = 41
            context, page, fixture, errors = open_page(browser, origin, fixture=fixture)
            open_logs(page)
            expect(page.get_by_text("저장된 활동 로그를 확인하고 있어요.", exact=True)).to_be_visible()
            page.get_by_role("combobox", name="매크로 세션 선택").click()
            page.get_by_role("option").filter(has_text="BTC").click()
            settle(page)
            expect(page.get_by_text("다른 매크로의 주문", exact=True)).to_be_visible()
            release_held(fixture)
            expect(page.get_by_text("매수 2 LINK 체결", exact=True)).to_have_count(0)
            page.reload(wait_until="networkidle")
            drain_initial_clock_until_visible(page, page.get_by_text("다른 매크로의 주문", exact=True))
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
            page.get_by_role("group", name="에이전트 대화 종류").get_by_role("button", name="전체", exact=True).click()
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
