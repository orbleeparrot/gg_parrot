"""Filled profile/activity mobile regressions against a real build and local fixtures.

  FRONTEND_BUILD=/tmp/profile-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
    python frontend/tests/profileMobileBrowser.smoke.py
PROFILE_MOBILE_OUTPUT selects screenshots/report (default /tmp/ggp-profile-mobile-check).
Reuses the profile edit/account fixtures. External requests are blocked; no real
account, backend, trade, or production API is used.
"""

import importlib.util
import json
import os
import re
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


SPEC = importlib.util.spec_from_file_location("profile_avatar_fixture", Path(__file__).with_name("profileAvatarBrowser.smoke.py"))
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
OUTPUT = Path(os.environ.get("PROFILE_MOBILE_OUTPUT", "/tmp/ggp-profile-mobile-check")).resolve()
SUMMARY = "RSI가 낮아지면 분할 매수하고, 목표 수익에 도달하면 매도하는 전략"
AT = "2026-09-10T01:30:00Z"
AT_MS = 1789003800000
TABS = {"created": "만든 매크로", "purchased": "구매한 매크로", "sales": "판매 내역", "ledger": "포인트 내역", "posts": "게시글"}
SECTIONS = {"매크로": ("created", "purchased"), "포인트·판매": ("sales", "ledger"), "게시글": ("posts",)}
MACRO = {"symbol": "BTCUSDT", "position_side": "long", "rule_type": "E", "candle_interval": "1d", "params": {"activation_profit": 5, "trail_percent": 3, "initial_capital": 1000}, "risk": {"invest_ratio": 1}}


class FilledFixtures(profile.Fixtures):
    def __init__(self):
        super().__init__()
        self.users["fixture-a"]["bio"] = "거래량과 추세를 함께 보는 매크로를 만들어요."
        self.total_sales = 12
        self.total_earned = 8400
        self.blocked_external = []

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname not in {"127.0.0.1", "localhost"}:
            # Deliberately exercise the coin logo fallback without contacting a CDN.
            self.blocked_external.append(route.request.url)
            route.abort()
            return
        super().route(route)

    def dashboard(self, user):
        data = super().dashboard(user)
        data["totals"] = {"created": 2, "sales": self.total_sales, "earned": self.total_earned, "purchased": 2}
        data["created"] = [
            {"entry_id": 11, "symbol": "BTCUSDT", "human_summary": SUMMARY, "sales": 8, "earned": 5600, "created_ms": AT_MS, "macro": MACRO},
            {"entry_id": 12, "symbol": "ETHUSDT", "human_summary": "이동평균선 교차를 보고 매수하는 전략", "sales": 4, "earned": 2800, "created_ms": AT_MS - 86400000, "macro": None},
        ]
        data["purchased"] = [
            {"entry_id": 21, "symbol": "SOLUSDT", "seller": "추세를기록하는매크로연구자", "human_summary": SUMMARY, "price": 1000, "unlocked_at": AT, "macro": {**MACRO, "symbol": "SOLUSDT"}},
            {"entry_id": 22, "symbol": "1000PEPEUSDT", "seller": "고래", "human_summary": "거래량이 늘어나는 구간에서 진입하는 전략", "price": 100, "unlocked_at": AT, "macro": None},
        ]
        data["sales"] = [
            {"entry_id": 11, "buyer": "매일꾸준하게기록하는초보투자자", "symbol": "BTCUSDT", "earned": 700, "at": AT},
            {"entry_id": 12, "buyer": "파란고래", "symbol": "ETHUSDT", "earned": 70, "at": AT},
        ]
        data["ledger"] = [
            {"created_at": AT, "delta": 700, "balance_after": 12500, "reason": "unlock_earn", "ref": "entry:11"},
            {"created_at": AT, "delta": -1000, "balance_after": 11800, "reason": "unlock_spend", "ref": "entry:21"},
        ]
        data["my_posts"] = [
            {"id": 13, "title": "첫 매크로를 만들면서 배운 점과 매수 조건을 수정한 이유", "created_ms": AT_MS, "comment_count": 12, "has_image": True},
            {"id": 14, "title": "분할 매수 기록", "created_ms": AT_MS, "comment_count": 0, "has_image": False},
        ]
        return data


def section_button(page, name):
    return page.get_by_role("navigation", name="프로필 메뉴").get_by_role("button", name=name, exact=True)


def assert_activity(page, key):
    section = next(label for label, keys in SECTIONS.items() if key in keys)
    for label in SECTIONS:
        expect(section_button(page, label)).to_have_attribute("aria-pressed", "true" if label == section else "false")
    panel = page.locator(".me-panel")
    expect(panel).to_be_visible()
    tabs = page.get_by_role("tablist", name="내 활동 종류")
    if key == "posts":
        expect(tabs).to_have_count(0)
        expect(panel).to_have_attribute("role", "region")
        expect(panel).to_have_attribute("aria-labelledby", "me-workspace-title")
    else:
        expect(tabs).to_be_visible()
        expect(tabs.get_by_role("tab")).to_have_count(2)
        selected = tabs.get_by_role("tab", name=re.compile(TABS[key]))
        expect(selected).to_have_attribute("aria-selected", "true")
        expect(selected).to_have_attribute("tabindex", "0")
        expect(tabs.locator('[role="tab"][tabindex="0"]')).to_have_count(1)
        expect(tabs.locator('[role="tab"][aria-selected="false"][tabindex="-1"]')).to_have_count(1)
        expect(panel).to_have_attribute("role", "tabpanel")
        expect(panel).to_have_attribute("aria-labelledby", f"me-tab-{key}")
        for tab_key in SECTIONS[section]:
            expect(tabs.get_by_role("tab", name=re.compile(TABS[tab_key]))).to_contain_text("2")
    create = page.locator(".me-create-link")
    if section == "매크로":
        expect(create).to_have_count(1)
        expect(create).to_be_visible()
        expect(create).to_have_attribute("href", "/builder")
        expect(create).to_have_attribute("aria-label", "매크로 만들기")
        expect(create).to_have_text("매크로 만들기")
        if page.viewport_size["width"] < 600:
            button = create.bounding_box()
            assert button["width"] >= 44 and button["height"] >= 44, button
    elif section == "게시글":
        expect(create).to_have_count(1)
        expect(create).to_have_text("글쓰기")
        expect(create).to_have_attribute("href", "/board/write")
    else:
        expect(create).to_have_count(0)
    if key == "created": expect(page).to_have_url(re.compile(r"/mypage(?:\?tab=created)?$"))
    else: expect(page).to_have_url(re.compile(rf"/mypage\?tab={key}$"))


def select_activity(page, key):
    section = next(label for label, keys in SECTIONS.items() if key in keys)
    section_button(page, section).click()
    if key != "posts":
        page.get_by_role("tablist", name="내 활동 종류").get_by_role("tab", name=re.compile(TABS[key])).click()
    assert_activity(page, key)


def assert_no_overflow(page, root=".me-page"):
    result = page.locator(root).evaluate("""root => {
      const outside = [...root.querySelectorAll('*')].filter(el => {
        if (!el.checkVisibility() || el.classList.contains('sr-only')) return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && (rect.left < -1 || rect.right > innerWidth + 1);
      }).map(el => ({tag:el.tagName, cls:el.className, text:el.textContent.slice(0,80)}));
      return {width:innerWidth, scroll:document.documentElement.scrollWidth, outside};
    }""")
    assert result["scroll"] <= result["width"] + 1 and not result["outside"], result


def assert_stats(page, stacked=False):
    metrics = page.locator(".me-stats").evaluate("""root => [...root.children].map(el=>{
      const label=el.querySelector('dt'), value=el.querySelector('dd');
      return {label:label.getBoundingClientRect().top, value:value.getBoundingClientRect().top,
        left:label.getBoundingClientRect().left, right:value.getBoundingClientRect().right,
        font:getComputedStyle(value).fontSize, family:getComputedStyle(value).fontFamily};
    })""")
    assert len(metrics) == 3, metrics
    assert len({(x["font"], x["family"]) for x in metrics}) == 1, metrics
    for field in (["left", "right"] if stacked else ["label", "value"]):
        assert max(x[field] for x in metrics) - min(x[field] for x in metrics) < 1, metrics
    assert page.locator(".me-stat.is-points dd").evaluate("""el => {
      const probe=document.createElement('span');probe.style.color='rgb(var(--c-brand-line))';el.append(probe);
      const equal=getComputedStyle(el).color===getComputedStyle(probe).color;probe.remove();return equal;
    }""")


def assert_profile_layout(page, width):
    rail = page.locator(".me-profile-rail").bounding_box()
    content = page.locator(".me-content").bounding_box()
    photo = page.locator(".me-avatar").bounding_box()
    name = page.locator(".me-name").bounding_box()
    if width >= 1100:
        assert rail["x"] + rail["width"] <= content["x"], (rail, content)
    else:
        assert abs(photo["x"] + photo["width"] / 2 - width / 2) < 1, photo
        assert photo["y"] + photo["height"] <= name["y"] + 1, (photo, name)
        assert content["y"] >= rail["y"] + rail["height"] - 1, (rail, content)
    expect(page.get_by_role("button", name="프로필 편집", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="계정 설정", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="프로필 편집", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="프로필 설정", exact=True)).to_have_attribute("href", "/mypage/settings?tab=security")
    expect(page.get_by_role("link", name="프로필 사진 변경", exact=True)).to_have_attribute("href", "/mypage/settings")


def assert_filled_rows(page, key, width):
    if key in ("created", "purchased"):
        cards = page.locator(".me-macro-card")
        expect(cards).to_have_count(2)
        expect(cards.first.locator(".me-macro-description")).to_have_text(SUMMARY)
        expect(cards.first.get_by_role("button")).to_be_enabled()
        expect(cards.last.get_by_role("button")).to_be_disabled()
        first, last = cards.first.bounding_box(), cards.last.bounding_box()
        assert abs(first["width"] - last["width"]) < 1, (first, last)
        if width < 600:
            assert abs(first["x"] - last["x"]) < 1 and last["y"] >= first["y"] + first["height"], (first, last)
        elif width >= 1440:
            assert abs(first["y"] - last["y"]) < 1 and last["x"] >= first["x"] + first["width"], (first, last)
        if key == "created":
            expect(cards.first).to_contain_text("8건")
            expect(cards.first).to_contain_text("5,600P")
        else:
            expect(cards.first).to_contain_text("추세를기록하는매크로연구자")
            expect(cards.first).to_contain_text("1,000P")
    elif key == "posts":
        rows = page.locator(".me-posts > .me-post")
        expect(rows).to_have_count(2)
        expect(rows.first.get_by_role("link")).to_have_attribute("href", "/board/13")
        expect(rows.first.get_by_role("link")).to_have_attribute("aria-label", re.compile("댓글 12개.*사진 첨부"))
    else:
        rows = page.locator(".me-transactions > .me-transaction")
        expect(rows).to_have_count(2)
        if key == "ledger":
            expect(rows.first).to_contain_text("12,500P")
            expect(rows.last).to_contain_text("−1,000P")
        else:
            expect(rows.first).to_contain_text("매일꾸준하게기록하는초보투자자")
            expect(rows.first).to_contain_text("700P")


def main():
    if not (profile.BUILD / "index.html").is_file(): raise SystemExit(f"Build missing: {profile.BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    profile.OUTPUT = OUTPUT
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(profile.Handler, directory=str(profile.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if os.environ.get("BROWSER_EXECUTABLE_PATH"): launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
            browser = playwright.chromium.launch(**launch)
            suite = profile.Suite(browser, f"http://127.0.0.1:{server.server_port}")
            for width, height in [(320, 740), (375, 812), (390, 844), (414, 896), (768, 1024), (844, 390), (1440, 900)]:
                for theme in ("light", "dark"):
                    fixture = FilledFixtures(); context, page = suite.open(fixture, width, theme)
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("document.fonts.ready")
                    assert_profile_layout(page, width)
                    assert_stats(page)
                    if width == 390:
                        bronze = page.locator(".me-tier-toggle .me-tier-icon.is-bronze").evaluate("el => getComputedStyle(el).color")
                        page.locator(".me-tier-toggle").click()
                        tier = page.get_by_role("dialog", name="판매 등급", exact=True)
                        expect(tier).to_be_visible()
                        assert tier.locator(".me-tier-icon.is-bronze").evaluate("el => getComputedStyle(el).color") == bronze
                        page.keyboard.press("Escape")
                        expect(tier).not_to_be_visible()
                    for key in TABS:
                        select_activity(page, key)
                        assert_filled_rows(page, key, width)
                        assert_no_overflow(page)
                        suite.screenshot(page, f"{key}-{width}-{theme}")
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator(".me-posts > .me-post")).to_have_count(2)
                    assert_activity(page, "posts")
                    profile.open_account(page)
                    expect(page.get_by_text("parrot@example.com", exact=True)).to_be_visible()
                    expect(page.get_by_role("button", name="비밀번호 변경", exact=True)).to_be_visible()
                    page.get_by_role("button", name="비밀번호 변경", exact=True).click()
                    expect(page.get_by_role("dialog", name="비밀번호 변경")).to_be_visible(); page.keyboard.press("Escape")
                    page.get_by_role("button", name="회원 탈퇴", exact=True).click()
                    expect(page.get_by_role("dialog", name="회원 탈퇴")).to_be_visible(); page.keyboard.press("Escape")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    suite.screenshot(page, f"account-{width}-{theme}")
                    suite.record(f"workspace-activities-and-settings-{width}-{theme}", fixture); context.close()

            for points in (999999, 1000000, 2147483647):
                fixture = FilledFixtures(); fixture.users["fixture-a"].update({"username": "매크로를연구하는이십글자이름입니다확인중", "points_balance": points, "bio": ""})
                fixture.total_sales = 12345; fixture.total_earned = points
                context, page = suite.open(fixture, 320)
                page.evaluate("document.fonts.ready")
                assert_no_overflow(page); assert_stats(page, stacked=True)
                expect(page.locator(".me-bio")).to_have_count(0)
                expect(page.locator(".me-stats")).to_contain_text(f"{points:,}P")
                expect(page.locator(".me-stats")).to_contain_text("12345건")
                assert_profile_layout(page, 320)
                suite.screenshot(page, f"large-values-{points}-320")
                suite.record(f"long-name-large-values-{points}", fixture); context.close()

            for width in (390, 768, 1440):
                fixture = FilledFixtures(); context, page = suite.open(fixture, width)
                for key in TABS:
                    page.goto(suite.origin + f"/mypage?tab={key}", wait_until="domcontentloaded")
                    assert_activity(page, key)
                    assert_filled_rows(page, key, width)
                    assert_no_overflow(page)
                for section, keys in list(SECTIONS.items())[:2]:
                    select_activity(page, keys[0])
                    page.get_by_role("tab", name=re.compile(TABS[keys[0]])).focus()
                    for key, selected in [("ArrowRight", keys[1]), ("End", keys[1]), ("ArrowRight", keys[0]), ("ArrowLeft", keys[1]), ("Home", keys[0])]:
                        before = page.evaluate("scrollY")
                        page.keyboard.press(key)
                        expect(page.get_by_role("tab", name=re.compile(TABS[selected]))).to_be_focused()
                        assert_activity(page, selected)
                        assert abs(page.evaluate("scrollY") - before) <= 1, (width, key, before, page.evaluate("scrollY"))
                suite.record(f"section-deeplinks-filter-keyboard-{width}", fixture); context.close()

            fixture = FilledFixtures(); context, page = suite.open(fixture, 390)
            profile.open_account(page)
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 900})
                expect(page.get_by_role("navigation", name="설정 메뉴").get_by_role("link", name="계정 및 보안", exact=True)).to_have_attribute("aria-current", "page")
                expect(page.get_by_role("button", name="로그아웃", exact=True)).to_be_visible()
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="로그아웃", exact=True)).to_be_visible()
            page.get_by_role("button", name="로그아웃", exact=True).click()
            expect(page).to_have_url(re.compile(r"/login$"))
            assert page.evaluate("localStorage.getItem('ggp_token')") is None
            suite.record("security-page-resize-reload-and-logout", fixture); context.close()

            fixture = FilledFixtures(); fixture.users["fixture-a"]["can_change_password"] = False
            context, page = suite.open(fixture, 390); profile.open_account(page)
            expect(page.get_by_role("button", name="비밀번호 변경", exact=True)).to_have_count(0)
            expect(page.get_by_text("Google 로그인", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="회원 탈퇴", exact=True)).to_be_visible()
            suite.record("google-account-keeps-supported-actions", fixture); context.close()

            fixture = profile.Fixtures(); context, page = suite.open(fixture, 390)
            expect(page.locator(".me-macro-card")).to_have_count(0)
            expect(page.locator(".me-create-link")).to_have_count(1)
            expect(page.locator(".me-content").get_by_role("link", name="매크로 만들기", exact=True)).to_have_count(1)
            expect(page.locator(".me-create-link")).to_have_attribute("href", "/builder")
            assert_no_overflow(page)
            suite.screenshot(page, "empty-created-390")
            suite.record("empty-macros-have-one-create-action", fixture); context.close()
            browser.close()
            report = {"passed": True, "build": str(profile.BUILD), "checks": suite.checks, "page_errors": suite.errors, "screenshots": suite.screenshots}
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"passed": True, "checks": len(suite.checks)}))
    finally: server.shutdown()


if __name__ == "__main__": main()
