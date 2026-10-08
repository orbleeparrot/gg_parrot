"""Agent connection states and re-entry; every API and WebSocket is a fixture.

Run with AGENT_TEST_BASE_URL pointing to a local Vite/preview server and
BROWSER_EXECUTABLE_PATH pointing to Chromium. No production data is accessed.
"""
from __future__ import annotations

import json
import os
import runpy
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

_helpers = runpy.run_path(str(Path(__file__).with_name("agentNotificationBrowser.smoke.py")))
BASE_MS, BASE_URL, Fixtures, advance, iso, messages, open_fixture = (
    _helpers[key] for key in ("BASE_MS", "BASE_URL", "Fixtures", "advance", "iso", "messages", "open_fixture")
)


class DomesticFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.news_transport_failed = False
        self.news_items = []

    def session(self):
        result = super().session()
        result["symbol"] = "KRW-ORCA"
        result["macro"] = {**result["macro"], "symbol": "KRW-ORCA", "exchange": "upbit"}
        return result

    def route(self, route):
        path = urlparse(route.request.url).path
        payload = None
        status = 200
        if path.endswith("/position-news"):
            self.calls["news"] += 1
            if self.news_transport_failed:
                status, payload = 503, {"detail": "fixture temporary storage failure"}
            else:
                payload = {"context": {"session_id": 41, "asset_symbol": "ORCA"},
                           "items": [], "analysis_status": "ready", "cursor": 1,
                           "translation": {"status": "partial", "pending_count": 673},
                           "collection": {"status": "ready", "freshness": "fresh"}}
        elif path.endswith("/runner/sessions/41/events"):
            payload = {"session_id": 41, "events": [{"id": 1, "kind": "signal",
                "ts": iso(BASE_MS - 60_000),
                "message": "서버 연결 재시도 중 — 신호 대기(진입 없음, 손절만 로컬에서 봅니다)"}]}
        if payload is None:
            super().route(route)
        else:
            route.fulfill(status=status, content_type="application/json",
                          body=json.dumps(payload, ensure_ascii=False))


def assert_domestic_status(page, fixture):
    expect(page.locator("[data-whale-status]")).to_contain_text("아직 지원하지 않")
    expect(page.locator("[data-news-status]")).to_contain_text("673건의 번역·요약 처리 대기")
    assert "번역 중" not in page.locator("[data-news-status]").inner_text()
    titles = [item["title"] for item in messages(page)]
    assert "대규모 체결 연결 확인 중" not in titles, titles
    assert "서버 연결 복구 · 이전 신호 대기 기록" in titles, titles
    assert fixture.calls["whales"] == 0, fixture.calls


def show_activity(page, viewport):
    update_dialog = page.get_by_role("dialog", name="껄무새가 이렇게 바뀌었어요")
    if update_dialog.is_visible():
        update_dialog.get_by_role("button", name="닫기", exact=True).click()
    if viewport["width"] < 768:
        page.get_by_role("button", name="에이전트", exact=True).click()
    expect(page.get_by_role("log", name="에이전트 관측 기록")).to_be_visible()


def main():
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"),
            args=["--no-sandbox"])
        for viewport in [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}]:
            fixture = DomesticFixtures()
            page, errors = open_fixture(browser, fixture)
            page.set_viewport_size(viewport)
            show_activity(page, viewport)
            assert_domestic_status(page, fixture)
            advance(page, fixture, 35)
            assert_domestic_status(page, fixture)

            page.goto(BASE_URL + "/faq")
            page.goto(BASE_URL + "/agents?session=41")
            show_activity(page, viewport)
            advance(page, fixture, 3)
            assert_domestic_status(page, fixture)

            fixture.news_transport_failed = True
            advance(page, fixture, 5)
            expect(page.locator("[data-news-status]")).to_contain_text("서버 연결이 지연")
            assert any(item["title"] == "뉴스 서버 연결 지연" for item in messages(page))
            fixture.news_transport_failed = False
            advance(page, fixture, 35)
            assert_domestic_status(page, fixture)
            assert any(item["title"] == "뉴스 연결 복구" for item in messages(page))
            assert not any(item["title"] == "뉴스 서버 연결 지연" for item in messages(page))
            assert not errors, errors
            reports.append({"viewport": viewport, "whale_requests": fixture.calls["whales"],
                            "reentry_and_recovery": "passed"})
            page.close()
        browser.close()
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    main()
