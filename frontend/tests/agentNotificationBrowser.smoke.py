"""Browser regression for notification identity; all APIs and WS are fixtures.

Run against Vite on port 5178. BROWSER_EXECUTABLE_PATH may select local Chromium.
AGENT_TEST_BASE_URL can select a deployed frontend; only its static assets pass through.
The Playwright clock advances poll timers without waiting minutes in real time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

BASE_MS = int(datetime(2026, 9, 7, 3, tzinfo=timezone.utc).timestamp() * 1000)
BASE_URL = os.environ.get("AGENT_TEST_BASE_URL", "http://127.0.0.1:5178")
USER = {"id": 1, "username": "알림 회귀 검증", "email": "browser@example.invalid"}
MACRO = {
    "symbol": "LINKUSDT", "position_side": "long", "rule_type": "E", "candle_interval": "1m",
    "params": {"activation_profit": 5, "trail_percent": 5, "initial_capital": 1000},
    "risk": {"invest_ratio": .5},
}


def iso(millis):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()


class Fixtures:
    def __init__(self, news_status="ready"):
        self.now = BASE_MS
        self.news_status = news_status
        self.holding = True
        self.stopped = False
        self.news_items = [{
            "id": "link-news-one", "url": "https://fixture.example/news/link-one",
            "title": "체인링크 네트워크 업데이트 발표", "source": "검증 뉴스",
            "position_effect": "neutral",
            # Missing publication date deliberately exercises the mutable
            # snapshot timestamp fallback without changing the article itself.
            "summary": "체인링크가 네트워크 업데이트를 발표했습니다. 기존 기사와 발생 시각을 유지하며 새 소식만 추가하는지 확인합니다.",
        }]
        self.trades = []
        self.sockets = []
        self.calls = {"sessions": 0, "news": 0, "whales": 0, "candles": 0, "stop": 0}
        self.external_blocked = []

    def session(self):
        return {
            "session_id": 41, "user_macro_id": 1, "symbol": "LINKUSDT", "macro": MACRO,
            "status": "stopped" if self.stopped else "running", "connected": not self.stopped,
            "in_position": self.holding and not self.stopped, "position_side": "long", "market": "spot",
            "testnet": True, "entry_price": 100 if self.holding else 0,
            "last_price": 100.1, "position_qty": 0 if self.stopped or not self.holding else 2,
            "unrealized_pct": .1, "realized_pnl": 0,
            "started_at": iso(BASE_MS - 60_000), "started_kst": "09/07 12:00",
            "last_heartbeat_at": iso(self.now), "stopping": False,
            "stopped_at": iso(self.now) if self.stopped else None,
            "note": "청산 완료 후 종료" if self.stopped else "",
        }

    def sessions(self):
        return {"active": [] if self.stopped else [self.session()],
                "recent": [self.session()] if self.stopped else [], "poll_seconds": 3}

    def push_heartbeat(self):
        for socket in self.sockets:
            socket.send(json.dumps({"type": "sessions.snapshot", "data": self.sessions()}))

    def websocket(self, socket):
        parsed = urlparse(socket.url)
        if parsed.path.endswith("/api/me/runner/sessions/stream"):
            self.sockets.append(socket)
            self.push_heartbeat()
        elif parsed.hostname in {"127.0.0.1", "localhost"}:
            socket.connect_to_server()  # Vite's local development connection.
        else:
            self.external_blocked.append(socket.url)
            socket.close()

    def route(self, route):
        parsed = urlparse(route.request.url)
        path = parsed.path
        if "/api/" not in path:
            if parsed.hostname in {"127.0.0.1", "localhost", urlparse(BASE_URL).hostname}:
                route.continue_()
            else:
                self.external_blocked.append(route.request.url)
                route.abort()
            return
        payload = {}
        if path.endswith("/auth/me"):
            payload = {"user": USER}
        elif path.endswith("/runner/sessions/stream-token"):
            payload = {"token": "browser-fixture-stream-token"}
        elif path.endswith("/runner/sessions"):
            self.calls["sessions"] += 1
            payload = self.sessions()
        elif path.endswith("/request-stop"):
            assert route.request.post_data_json["mode"] == "close_and_stop"
            self.calls["stop"] += 1
            self.stopped = True
            self.holding = False
            payload = {"ok": True}
        elif path.endswith("/position-news"):
            self.calls["news"] += 1
            payload = {
                "context": {"session_id": 41, "asset_symbol": "LINK", "coin_name": "체인링크"},
                "analysis_status": self.news_status, "updated_at": iso(self.now),
                "collection": {"status": self.news_status, "freshness": "fresh", "last_attempt_at": iso(self.now)},
                "items": self.news_items if self.news_status == "ready" else [],
            }
        elif path.endswith("/whale-activity"):
            self.calls["whales"] += 1
            payload = {"status": "ready" if self.trades else "empty", "symbol": "LINKUSDT", "market": "spot",
                       "quote_asset": "USDT", "observed_at": iso(self.now), "items": self.trades}
        elif path.endswith("/candles") or path.endswith("/candles/live"):
            self.calls["candles"] += 1
            last_open = self.now // 60_000 * 60_000
            candles = [{"t": last_open - (30 - index) * 60_000,
                        "o": 100.1, "h": 100.1, "l": 100.1, "c": 100.1, "v": 1000, "closed": True}
                       for index in range(30)]
            payload = {"candles": candles, "server_time": self.now, "refresh_seconds": 5,
                       "source": "fixture", "data_source": "fixture", "market": "spot"}
        elif path.endswith("/hot-coins"):
            payload = {"items": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


def messages(page):
    return page.locator(".agent-chat-log .agent-message").evaluate_all("""nodes => nodes.map(node => ({
      title: node.querySelector('.agent-message-primary')?.textContent,
      summary: node.querySelector('.agent-message-summary')?.textContent || '',
      occurredAt: node.querySelector('time')?.getAttribute('datetime'),
      source: node.querySelector('footer a')?.getAttribute('href') || '',
    }))""")


def new_observations(page):
    return page.locator(".agent-new-message").all_text_contents()


def advance(page, fixture, seconds):
    # Small increments let mocked HTTP responses and React effects settle
    # between successive timers, while also delivering fresh WS heartbeats.
    for _ in range(seconds):
        fixture.now += 1000
        page.clock.run_for(1000)
        fixture.push_heartbeat()
        page.wait_for_timeout(15)
    page.clock.run_for(50)
    page.wait_for_timeout(50)


def open_fixture(browser, fixture):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", fixture.route)
    page.route_web_socket("**/*", fixture.websocket)
    page.add_init_script('localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'
                         + json.dumps(json.dumps(USER, ensure_ascii=False)) + ');')
    page.clock.install(time=datetime.fromtimestamp(BASE_MS / 1000, timezone.utc))
    page.goto(BASE_URL + "/agents")
    page.wait_for_load_state("networkidle")
    fixture.now += 3000
    page.clock.pause_at(datetime.fromtimestamp(fixture.now / 1000, timezone.utc))
    advance(page, fixture, 2)
    expect(page.get_by_role("log", name="에이전트 관측 기록")).to_be_visible()
    assert fixture.sockets, "Fixture WebSocket did not connect"
    return page, errors


def assert_quiet_refresh(page, fixture, seconds=35):
    before, badge = messages(page), new_observations(page)
    before_calls = dict(fixture.calls)
    advance(page, fixture, seconds)
    assert messages(page) == before, {"before": before, "after": messages(page)}
    assert new_observations(page) == badge
    if not fixture.stopped:
        assert fixture.calls["news"] > before_calls["news"], fixture.calls
        assert fixture.calls["whales"] > before_calls["whales"], fixture.calls
        assert fixture.calls["candles"] > before_calls["candles"], fixture.calls


def main():
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"),
            args=["--no-sandbox"])
        # Pending/empty are routine states and must never create chat bubbles.
        for initial_status in ("pending", "empty"):
            fixture = Fixtures(initial_status)
            page, errors = open_fixture(browser, fixture)
            assert messages(page) == [], {"initial_status": initial_status, "messages": messages(page)}
            advance(page, fixture, 35)
            assert messages(page) == [] and new_observations(page) == []
            fixture.news_status = "ready"
            advance(page, fixture, 35)
            assert len(messages(page)) == 1
            assert not errors, errors
            reports.append({"initial_status": initial_status, "passed": True, "requests": fixture.calls})
            page.close()

        fixture = Fixtures()
        page, errors = open_fixture(browser, fixture)
        baseline = messages(page)
        assert len(baseline) == 1, baseline
        assert baseline[0]["title"] == fixture.news_items[0]["title"]
        assert baseline[0]["occurredAt"], baseline
        # Put the reader above the bottom so actual new events have a visible
        # badge; routine polls must not increment that badge or move messages.
        page.get_by_role("log", name="에이전트 관측 기록").evaluate("""node => {
          node.style.height = '40px'; node.style.maxHeight = '40px'; node.style.minHeight = '0';
          node.scrollTop = 0; node.dispatchEvent(new Event('scroll', { bubbles: true }));
        }""")
        assert_quiet_refresh(page, fixture, 70)  # includes a newly closed flat candle

        fixture.news_items.append({
            "id": "link-news-two", "url": "https://fixture.example/news/link-two",
            "title": "체인링크 새 파트너십 발표", "source": "검증 뉴스", "published": fixture.now,
            "position_effect": "neutral", "summary": "실제 새 기사 한 건입니다.",
        })
        advance(page, fixture, 35)
        after_news = messages(page)
        assert len(after_news) == 2 and after_news[0] == baseline[0], after_news
        assert after_news[-1]["title"] == fixture.news_items[-1]["title"]
        expect(page.locator(".agent-new-message")).to_have_text("새 관측 1개 ↓")
        assert_quiet_refresh(page, fixture)

        fixture.trades.append({"id": "spot:LINKUSDT:7", "side": "buy", "notional": 150000,
                               "quantity": 1500, "price": 100, "occurred_at": fixture.now})
        advance(page, fixture, 35)
        after_trade = messages(page)
        assert len(after_trade) == 3 and after_trade[:2] == after_news, after_trade
        assert "대규모 매수 체결" in after_trade[-1]["title"]
        assert_quiet_refresh(page, fixture)

        # Initial holding is quiet, but real exit and re-entry each emit once.
        fixture.holding = False
        advance(page, fixture, 2)
        after_exit = messages(page)
        assert len(after_exit) == len(after_trade) + 1 and after_exit[:-1] == after_trade, after_exit
        fixture.holding = True
        advance(page, fixture, 2)
        after_entry = messages(page)
        assert len(after_entry) == len(after_exit) + 1 and after_entry[:-1] == after_exit, after_entry
        assert "진입" in after_entry[-1]["title"]
        assert_quiet_refresh(page, fixture)

        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="청산 후 종료", exact=True).click()
        advance(page, fixture, 2)
        stopped = messages(page)
        assert len(stopped) == len(after_entry) + 1 and stopped[:-1] == after_entry, stopped
        assert "종료" in stopped[-1]["title"]
        expect(page.get_by_role("button", name="청산 후 종료", exact=True)).to_be_disabled()
        assert_quiet_refresh(page, fixture)
        assert fixture.calls["stop"] == 1
        assert not errors, errors
        page.get_by_role("log", name="에이전트 관측 기록").evaluate("""node => {
          node.style.height = ''; node.style.maxHeight = ''; node.style.minHeight = '';
          node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll', { bubbles: true }));
        }""")
        page.screenshot(path="/tmp/gg-parrot-agent-notification-regression.png", full_page=True)
        reports.append({"initial_status": "ready", "passed": True, "requests": fixture.calls,
                        "final_messages": stopped, "page_errors": errors})
        print(json.dumps({"passed": True, "scenarios": reports}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
