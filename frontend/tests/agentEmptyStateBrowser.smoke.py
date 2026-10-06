"""GG-012: local Vite only; every API response and identity is a fixture.

Run with AGENT_EMPTY_BASE_URL=http://127.0.0.1:5178 and Playwright/Chromium.
No production users, external requests, screenshots or paid AI calls are made.
"""
import json
import os
import time
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("AGENT_EMPTY_BASE_URL", "http://127.0.0.1:5178")
assert urlparse(BASE).hostname in {"localhost", "127.0.0.1"}
USER = {"id": 1, "username": "local fixture", "email": "fixture@example.invalid"}
NOW = int(time.time() * 1000)
SESSION = {"session_id": 41, "symbol": "LINKUSDT", "status": "running", "connected": True,
           "in_position": False, "market": "spot", "testnet": True,
           "macro": {"symbol": "LINKUSDT", "rule_type": "A", "position_side": "long", "candle_interval": "1m",
                     "params": {"take_profit_pct": 3, "initial_capital": 1000}, "risk": {"stop_loss_pct": 2, "invest_ratio": .5}}}


def run_case(browser, case):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
    page = context.new_page()
    errors = []
    console_errors = []
    held = []
    state = {"hold": case == "loading", "fail": True}
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    if case != "anonymous":
        context.add_init_script(
            'localStorage.setItem("ggp_token", "local-fixture");'
            'localStorage.setItem("ggp_user", ' + json.dumps(json.dumps(USER)) + ');'
        )

    def route_handler(route):
        parsed = urlparse(route.request.url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            route.abort()
            return
        if not parsed.path.startswith("/api/"):
            if parsed.path == "/src/pages/Agents.jsx" and os.environ.get("AGENT_REPLAY_LEGACY") == "1":
                response = route.fetch()
                body = response.text()
                fixed = "activeSessionChart(chartSnapshot, selected, exchange)"
                assert fixed in body
                legacy = 'chartSnapshot?.symbol === selected?.symbol && (chartSnapshot.exchange || "binance") === exchange ? chartSnapshot : null'
                route.fulfill(response=response, body=body.replace(fixed, legacy))
                return
            if parsed.path == "/src/pages/Agents.jsx" and case == "render-error":
                route.fulfill(content_type="application/javascript", body='export default function Agents() { if (!window.__ggRecovered) throw new Error("fixture-render-failure"); return "에이전트 오류 복구 확인"; }')
                return
            route.continue_()
            return
        payload, status = {"items": []}, 200
        if parsed.path.endswith("/auth/me"):
            payload = {"user": USER}
        elif parsed.path.endswith("/runner/sessions/stream-token"):
            payload, status = {"detail": "local stream unavailable"}, 503
        elif parsed.path.endswith("/runner/sessions"):
            if state["hold"]:
                held.append(route)
                return
            if case == "request-failed" and state["fail"]:
                payload, status = {"detail": "fixture session request failed"}, 500
            elif case == "null-response" and state["fail"]:
                payload = None
            else:
                payload = {"active": [SESSION] if case == "running" else [], "recent": []}
        elif parsed.path.endswith("/candles") or parsed.path.endswith("/candles/live"):
            payload = {"candles": [{"t": NOW - (30 - i) * 60000, "o": 100, "h": 103, "l": 96, "c": 99, "v": 1000, "closed": True} for i in range(30)],
                       "server_time": NOW, "fetched_at_ms": NOW, "source": "fixture", "data_source": "fixture", "market": "spot"}
        elif parsed.path.endswith("/runner/download/info"):
            payload = {"version": "9", "supports_launch": True}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    context.route("**/*", route_handler)
    page.goto(BASE + "/?qa=1", wait_until="networkidle")
    dismiss = page.get_by_role("button", name="닫기", exact=True)
    if dismiss.is_visible():
        dismiss.click()
    page.get_by_role("link", name="내 에이전트", exact=True).click()
    if os.environ.get("AGENT_REPLAY_LEGACY") == "1":
        expect(page.get_by_role("heading", name="화면을 불러오지 못했어요", exact=True)).to_be_visible()
        assert any("Cannot read properties of null (reading 'exchange')" in message for message in errors + console_errors), errors + console_errors
        context.close()
        return {"legacy_failure_confirmed": True, "error": "Cannot read properties of null (reading 'exchange')"}
    if case == "render-error":
        expect(page.get_by_role("heading", name="화면을 불러오지 못했어요", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="내 에이전트", exact=True)).to_be_visible()
        page.go_back()
        expect(page).to_have_url(BASE + "/?qa=1")
        expect(page.get_by_role("heading", name="화면을 불러오지 못했어요", exact=True)).not_to_be_visible()
        page.get_by_role("link", name="내 에이전트", exact=True).click()
        expect(page.get_by_role("heading", name="화면을 불러오지 못했어요", exact=True)).to_be_visible()
        page.evaluate("window.__ggRecovered = true")
        page.get_by_role("button", name="다시 시도", exact=True).click()
        expect(page.get_by_text("에이전트 오류 복구 확인", exact=True)).to_be_visible()
        page.get_by_role("link", name="껄무새 메인", exact=True).first.click()
        expect(page).to_have_url(BASE + "/")
        assert all("fixture-render-failure" in error for error in errors), errors
        context.close()
        return {"case": case, "expected_render_failure_isolated": True}
    if case == "anonymous":
        expect(page).to_have_url(BASE + "/login?next=%2Fagents")
    elif case == "loading":
        expect(page.get_by_role("status").filter(has_text="실행 중인 매크로를 불러오는 중")).to_be_visible()
        assert held, "session fixture must actually be awaiting a response"
        state["hold"] = False
        for route in held:
            route.fulfill(content_type="application/json", body='{"active": [], "recent": []}')
        expect(page.get_by_role("heading", name="지금 돌아가는 에이전트가 없어요", exact=True)).to_be_visible()
    elif case in {"request-failed", "null-response"}:
        expect(page.get_by_role("alert").filter(has_text="실행 상태 오류")).to_be_visible()
        expect(page.get_by_role("button", name="다시 불러오기", exact=True)).to_be_visible()
        state["fail"] = False
        page.get_by_role("button", name="다시 불러오기", exact=True).click()
        expect(page.get_by_role("heading", name="지금 돌아가는 에이전트가 없어요", exact=True)).to_be_visible()
        expect(page.get_by_role("alert").filter(has_text="실행 상태 오류")).not_to_be_visible()
    elif case == "running":
        expect(page.locator(".agent-workspace")).to_be_visible()
        expect(page.locator(".agent-chart-pane")).to_be_visible()
        expect(page.get_by_role("combobox", name="매크로 세션 선택")).to_have_value("41")
    else:
        expect(page.get_by_role("heading", name="지금 돌아가는 에이전트가 없어요", exact=True)).to_be_visible()

    # Both history navigation and the sidebar must recover without a document reload.
    page.go_back()
    expect(page).to_have_url(BASE + "/?qa=1")
    expect(page.get_by_role("link", name="내 에이전트", exact=True)).to_be_visible()
    page.get_by_role("link", name="내 에이전트", exact=True).click()
    if case == "anonymous":
        expect(page).to_have_url(BASE + "/login?next=%2Fagents")
    else:
        expect(page.get_by_role("link", name="껄무새 메인", exact=True).first).to_be_visible()
        page.get_by_role("link", name="껄무새 메인", exact=True).first.click()
        expect(page).to_have_url(BASE + "/")
        expect(page.get_by_role("link", name="내 에이전트", exact=True)).to_be_visible()
    assert not errors, {"case": case, "page_errors": errors}
    context.close()
    return {"case": case, "page_errors": errors}


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    cases = ("anonymous",) if os.environ.get("AGENT_REPLAY_LEGACY") == "1" else ("anonymous", "loading", "empty", "request-failed", "null-response", "running", "render-error")
    results = [run_case(browser, case) for case in cases]
    browser.close()
    print(json.dumps({"passed": True, "results": results}, ensure_ascii=False))
