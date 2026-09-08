"""Fixture-only archive rendering, publication dates and new-event badges.

Run against Vite with AGENT_TEST_BASE_URL. External APIs never pass through.
"""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("agent_fixture", Path(__file__).with_name("agentNotificationBrowser.smoke.py"))
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)

ARCHIVE = {"id": "historical-chip", "title": "CHIP 프로젝트 출시 소식", "is_historical": True,
           "published": "2022-01-02T01:30:00Z", "source": "검증 뉴스",
           "url": "https://fixture.invalid/historical-chip"}


class NewsFixtures(fixture_module.Fixtures):
    def __init__(self, phase="ready"):
        super().__init__()
        self.phase = phase

    def route(self, route):
        if not urlparse(route.request.url).path.endswith("/position-news"):
            return super().route(route)
        self.calls["news"] += 1
        payload = {"context": {"session_id": 41, "asset_symbol": "LINK"},
                   "analysis_status": "pending" if self.phase == "pending" else "ready",
                   "collection": {"status": "pending" if self.phase == "pending" else "ready", "freshness": "fresh"},
                   "translation": {"status": "partial" if self.phase == "translating" else "ready",
                                   "pending_count": 1 if self.phase == "translating" else 0},
                   "content_scope": "archive" if self.phase == "archive" else "mixed",
                   "items": [] if self.phase in {"pending", "translating", "empty"} else self.news_items}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True,
        executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", "/opt/google/chrome/chrome"), args=["--no-sandbox"])
    fixture = NewsFixtures("pending")
    page, errors = fixture_module.open_fixture(browser, fixture)
    expect(page.locator("[data-news-status]")).to_have_text("관련 기사를 찾고 있어요.")
    assert fixture_module.messages(page) == []
    fixture.phase = "translating"
    fixture_module.advance(page, fixture, 5)
    expect(page.locator("[data-news-status]")).to_have_text("뉴스 1건을 한국어로 번역 중이에요. 완료되면 표시해요.")
    assert fixture_module.messages(page) == []
    fixture.phase = "archive"
    fixture.news_items = [dict(ARCHIVE)]
    fixture_module.advance(page, fixture, 35)
    expect(page.locator("[data-news-status]")).to_have_text("최근 기사가 없어 과거 관련 기사를 보여드려요.")
    expect(page.get_by_text("과거 기사", exact=True)).to_be_visible()
    expect(page.get_by_text("게시 2022.01.02 10:30 KST", exact=True)).to_be_visible()
    assert len(fixture_module.messages(page)) == 1
    assert fixture_module.new_observations(page) == []
    assert not errors, errors
    page.close()

    fixture = NewsFixtures()
    page, errors = fixture_module.open_fixture(browser, fixture)
    expect(page.get_by_text("게시일 확인 불가", exact=True)).to_be_visible()
    assert fixture_module.messages(page)[0]["occurredAt"] is None
    page.get_by_role("log", name="에이전트 관측 기록").evaluate("""node => {
        node.style.height = '40px'; node.style.maxHeight = '40px'; node.style.minHeight = '0';
        node.scrollTop = 0; node.dispatchEvent(new Event('scroll', {bubbles:true}));
    }""")
    fixture.news_items.append(dict(ARCHIVE))
    fixture_module.advance(page, fixture, 35)
    assert len(fixture_module.messages(page)) == 2
    assert fixture_module.new_observations(page) == []
    expect(page.get_by_text("과거 기사", exact=True)).to_have_count(1)
    fixture.news_items[-1]["title"] = "CHIP 프로젝트 출시 관련 과거 소식"
    fixture_module.advance(page, fixture, 35)
    assert len(fixture_module.messages(page)) == 2
    assert fixture_module.new_observations(page) == []
    fixture.news_items.append({"id": "new-live", "title": "오늘 새 파트너십 발표", "published": fixture.now,
                               "source": "검증 뉴스", "url": "https://fixture.invalid/new-live"})
    fixture_module.advance(page, fixture, 35)
    assert len(fixture_module.messages(page)) == 3
    expect(page.locator(".agent-new-message")).to_have_text("새 관측 1개 ↓")
    fixture_module.assert_quiet_refresh(page, fixture, 35)
    before_community_badge = fixture_module.new_observations(page)
    fixture.news_items.append({
        "content_type": "community", "community_post_id": "123456789", "author": "시장기록자",
        "source": "Binance Square", "url": "https://www.binance.com/en/square/post/123456789",
        "title": "CHIP에 대한 커뮤니티 작성자의 의견", "original_title": "A personal CHIP outlook",
        "summary": "Original English community body", "position_effect": "favorable",
        "is_historical": True, "published": "2022-01-02T01:30:00Z",
    })
    fixture_module.advance(page, fixture, 35)
    assert len(fixture_module.messages(page)) == 4
    assert fixture_module.new_observations(page) == before_community_badge
    expect(page.get_by_text("커뮤니티 · 과거 게시글", exact=True)).to_have_count(1)
    expect(page.get_by_text("커뮤니티 · Binance Square · 시장기록자", exact=False)).to_have_count(1)
    expect(page.get_by_text("Original English community body", exact=True)).to_have_count(0)
    expect(page.get_by_text("A personal CHIP outlook", exact=True)).to_have_count(0)
    post = page.locator(".agent-message").filter(has=page.get_by_text("CHIP에 대한 커뮤니티 작성자의 의견", exact=True))
    assert "is-info" in post.get_attribute("class")
    fixture.news_items[-1]["title"] = "한국어 표현을 수정한 커뮤니티 의견"
    fixture.news_items[-1]["id"] = "changed-title-hash"
    fixture_module.advance(page, fixture, 35)
    assert len(fixture_module.messages(page)) == 4
    assert fixture_module.new_observations(page) == before_community_badge
    assert not errors, errors
    page.get_by_role("log", name="에이전트 관측 기록").evaluate("""node => {
        node.style.height = ''; node.style.maxHeight = ''; node.style.minHeight = '';
    }""")
    page.screenshot(path="/tmp/gg-parrot-agent-historical-news.png", full_page=True)
    print(json.dumps({"passed": True, "archive_count": 1, "archive_new_badges": 0,
                      "live_new_badges": 1, "messages": fixture_module.messages(page), "page_errors": errors}, ensure_ascii=False))
    browser.close()
