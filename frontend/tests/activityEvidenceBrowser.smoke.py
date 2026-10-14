"""Canonical activity-event links against the actual build; all APIs are local fixtures.

No production accounts, order submission or AI provider calls. No screenshots.
"""
import importlib.util
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("logs", Path(__file__).with_name("agentSessionLogsBrowser.smoke.py"))
logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logs)


class EvidenceFixture(logs.LogsFixture):
    def __init__(self):
        super().__init__()
        self.observations = []
        self.saved = {}
        self.fail_observation = False

    def route(self, route):
        parsed = urlsplit(route.request.url)
        path = parsed.path
        if path == "/api/me/notifications/unread":
            route.fulfill(json={"unread": 2, "latest_id": 402})
            return
        if path == "/api/me/notifications":
            route.fulfill(json={"unread": 2, "items": [
                {"id": 402, "kind": "agent", "title": "연결된 실행 알림", "body": "실제 저장 사건 참조",
                 "session_id": 41, "data": {"session_id": 41, "activity_event_id": 9}, "link": "/agents", "created_ms": self.now, "read": False},
                {"id": 401, "kind": "agent", "title": "과거 실행 알림", "body": "추정 연결하지 않음",
                 "session_id": 41, "data": {}, "link": "/agents", "created_ms": self.now, "read": False},
            ]})
            return
        if path == "/api/me/notifications/read":
            route.fulfill(json={"unread": 0})
            return
        if path.endswith("/observations"):
            sid = int(path.split("/sessions/")[1].split("/")[0])
            payload = route.request.post_data_json
            self.observations.append((sid, payload))
            if self.fail_observation:
                route.fulfill(status=503, json={"detail": "fixture storage unavailable"})
                return
            key = (sid, payload["client_event_id"])
            if key not in self.saved:
                event = self.event(201 + len(self.saved), "info", payload["details"]["title"], "execution")
                event.update(session_id=sid, event_name=payload["event_name"],
                    event_key="browser:" + payload["client_event_id"], component="browser_observation",
                    details={**payload["details"], "verification": "browser_observation_not_exchange_event"})
                self.saved[key] = event
                self.rows[sid].append(event)
            event = self.saved[key]
            route.fulfill(json={"ok": True, "recorded": True, "event_id": event["id"], "event": event})
            return
        if path.endswith("/news-receipts"):
            sid = int(path.split("/sessions/")[1].split("/")[0])
            self.receipts.append((sid, route.request.post_data_json["receipt_token"]))
            if not any(row["id"] == 25 for row in self.rows[sid]):
                self.rows[sid].append(self.event(25, "news", "뉴스 응답 수신 원본", "news"))
            route.fulfill(json={"ok": True, "recorded": True, "event_id": 25,
                "receipt_key": f"stable-{sid}", "article_ids": [item["id"] for item in self.news_items]})
            return
        if path.endswith("/logs"):
            sid = int(path.split("/sessions/")[1].split("/")[0])
            anchor = int(parse_qs(parsed.query).get("context_id", ["0"])[0])
            if anchor and not any(row["id"] == anchor for row in self.rows[sid]):
                self.log_requests.append((sid, parse_qs(parsed.query)))
                route.fulfill(status=404, json={"detail": "원본 사건을 찾을 수 없어요."})
                return
        if path.endswith("/events"):
            sid = int(path.split("/sessions/")[1].split("/")[0])
            route.fulfill(json={"session_id": sid, "events": [row for row in self.rows[sid] if row["category"] != "news"]})
            return
        super().route(route)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(logs.static.Handler, directory=str(logs.static.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    checks = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            for width in [1440, 390]:
                context, page, fixture, errors = logs.open_page(browser, origin, width=width, fixture=EvidenceFixture())
                page.get_by_role("link", name="이 기사 포함 응답 수신 로그 · 사건 #25", exact=True).click()
                logs.settle(page)
                expect(page).to_have_url(origin + "/agents?session=41&panel=logs&event=25")
                expect(page.locator("[data-log-id='25'][data-context-anchor='true']")).to_be_visible()
                assert fixture.log_requests[-1][1].get("context_id") == ["25"]
                page.get_by_role("button", name="조회 조건 해제", exact=True).click()
                logs.settle(page)
                assert "event=" not in page.url
                assert "context_id" not in fixture.log_requests[-1][1]
                assert not errors, errors
                context.close(); checks += 1

            context, page, fixture, errors = logs.open_page(browser, origin, fixture=EvidenceFixture())
            page.locator(".header-bell-button").click()
            expect(page.get_by_text("원본 연결 없음 · 이전 형식 알림", exact=True)).to_be_visible()
            page.locator(".header-bell-item").filter(has_text="연결된 실행 알림").click()
            logs.settle(page)
            expect(page).to_have_url(origin + "/agents?session=41&panel=logs&event=9")
            expect(page.locator("[data-log-id='9'][data-context-anchor='true']")).to_be_visible()
            assert not errors, errors
            context.close(); checks += 1

            context, page, fixture, errors = logs.open_page(browser, origin, fixture=EvidenceFixture())
            page.get_by_role("link", name="해당 로그 보기 · 사건 #9", exact=True).click()
            logs.settle(page)
            expect(page.locator("[data-log-id='9'][data-context-anchor='true']")).to_be_visible()
            assert not fixture.observations, fixture.observations
            assert not errors, errors
            context.close(); checks += 1

            context, page, fixture, errors = logs.open_page(browser, origin, fixture=EvidenceFixture())
            fixture.holding = False
            fixture.push_heartbeat(); logs.settle(page)
            expect(page.get_by_text("포지션 청산", exact=True)).to_be_visible()
            expect(page.get_by_role("link", name="해당 로그 보기 · 사건 #201", exact=True)).to_be_visible()
            assert fixture.observations[0][1]["event_name"] == "browser.execution_state_observed"
            assert "operation_id" not in fixture.observations[0][1]
            assert len(fixture.observations) == 1
            page.get_by_role("link", name="해당 로그 보기 · 사건 #201", exact=True).click()
            logs.settle(page)
            expect(page.locator("[data-log-id='201'][data-context-anchor='true']")).to_be_visible()
            expect(page.get_by_text("browser.execution_state_observed", exact=True).first).to_be_visible()
            assert not errors, errors
            context.close(); checks += 1

            fixture = EvidenceFixture(); fixture.fail_observation = True
            context, page, fixture, errors = logs.open_page(browser, origin, fixture=fixture)
            fixture.holding = False
            fixture.push_heartbeat(); logs.settle(page)
            logs.advance(page, fixture, 2)
            expect(page.get_by_text("관측 기록 저장 실패", exact=False).first).to_be_visible()
            assert len(fixture.observations) == 2
            logs.advance(page, fixture, 20)
            assert len(fixture.observations) == 2
            fixture.fail_observation = False
            page.get_by_role("button", name="기록 다시 저장", exact=True).click()
            logs.settle(page)
            expect(page.get_by_role("link", name="해당 로그 보기 · 사건 #201", exact=True)).to_be_visible()
            assert len(fixture.observations) == 3
            assert len({payload["client_event_id"] for _, payload in fixture.observations}) == 1
            assert not errors, errors
            context.close(); checks += 1

            for sid, event in [(41, "9999"), (42, "9")]:
                context, page, fixture, errors = logs.open_page(browser, origin, fixture=EvidenceFixture(),
                    path=f"/agents?session={sid}&panel=logs&event={event}")
                expect(page.get_by_text("다른 사건으로 대체하지 않습니다.", exact=False).first).to_be_visible()
                expect(page.locator("[data-log-id]")).to_have_count(0)
                assert all(query.get("context_id") == [event] for _, query in fixture.log_requests)
                assert not errors, errors
                context.close(); checks += 1

            context, page, fixture, errors = logs.open_page(browser, origin, fixture=EvidenceFixture(),
                path="/agents?session=41&panel=logs&event=not-an-event")
            expect(page.get_by_text("요청한 활동 로그의 세션 또는 사건 ID를 확인하지 못했어요.", exact=False)).to_be_visible()
            assert not fixture.log_requests
            assert not errors, errors
            context.close(); checks += 1
            browser.close()
    finally:
        server.shutdown(); server.server_close()
    print(f"Passed {checks} canonical activity-evidence browser scenarios. Production calls: 0; exchange orders: 0.")


if __name__ == "__main__":
    main()
