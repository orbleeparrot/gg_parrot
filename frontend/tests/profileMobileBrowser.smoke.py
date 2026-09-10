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


def assert_no_overflow(page):
    result = page.locator(".me-page").evaluate("""root => {
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
    assert len({(x["font"], x["family"]) for x in metrics}) == 1, metrics
    for field in (["left", "right"] if stacked else ["label", "value"]):
        assert max(x[field] for x in metrics) - min(x[field] for x in metrics) < 1, metrics
    assert page.locator(".me-stat.is-points dd").evaluate("""el => {
      const probe=document.createElement('span');probe.style.color='rgb(var(--c-brand-line))';el.append(probe);
      const equal=getComputedStyle(el).color===getComputedStyle(probe).color;probe.remove();return equal;
    }""")


def assert_account_state(page, opened):
    toggle = page.get_by_role("button", name="계정 설정", exact=True)
    settings = page.locator("#me-account-settings")
    expect(toggle).to_have_attribute("aria-controls", "me-account-settings")
    expect(toggle).to_have_attribute("aria-expanded", "true" if opened else "false")
    if opened:
        expect(settings).to_be_visible()
        expect(page.get_by_role("button", name="로그아웃", exact=True)).to_be_visible()
    else:
        expect(settings).not_to_be_visible()
        expect(page.get_by_role("button", name="로그아웃", exact=True)).not_to_be_visible()


def main():
    if not (profile.BUILD / "index.html").is_file(): raise SystemExit(f"Build missing: {profile.BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Reuse the existing suite's screenshot/report helpers in this test's output directory.
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
                    compact = width < 1100
                    selector = page.get_by_role("combobox", name="내 활동 종류")
                    if compact:
                        expect(selector).to_be_visible()
                        assert selector.evaluate("e=>parseFloat(getComputedStyle(e).fontSize)") >= 16
                        assert selector.bounding_box()["height"] >= 44
                        expect(page.get_by_role("tablist", name="내 활동 종류")).not_to_be_visible()
                    else:
                        expect(selector).not_to_be_visible()
                        expect(page.get_by_role("tablist", name="내 활동 종류")).to_be_visible()
                    assert_account_state(page, False)
                    assert_stats(page)
                    for key, label in TABS.items():
                        if compact: selector.select_option(key)
                        else: page.get_by_role("tab", name=re.compile(label)).click()
                        expect(page.locator(".me-panel")).to_have_attribute("aria-label" if compact else "aria-labelledby", label if compact else f"me-tab-{key}")
                        if key != "created": expect(page).to_have_url(re.compile(rf"\?tab={key}$"))
                        rows = page.locator(".me-posts .board-item" if key == "posts" else ".me-table .me-row:not(.me-table-head)")
                        expect(rows).to_have_count(2)
                        if key in ("created", "purchased"):
                            buttons = rows.get_by_role("button")
                            expect(buttons.first).to_be_enabled(); expect(buttons.last).to_be_disabled()
                            if compact:
                                assert buttons.first.bounding_box()["height"] >= 44
                                expect(rows.first.get_by_text("판매" if key == "created" else "구매 금액", exact=True)).to_be_visible()
                            else:
                                # Separate list-row grids must share their header's columns.
                                columns = [".coin-icon", ".me-main"] + ([".me-row-sales", ".me-row-earned"] if key == "created" else [".me-row-price"]) + [".me-row-date", "button"]
                                alignment = page.locator(".me-table").evaluate("""(el, selectors) => {
                                  const head=[...el.querySelector('.me-table-head').children];
                                  const data=el.querySelector('.me-row:not(.me-table-head)');
                                  const row=selectors.map(selector=>data.querySelector(selector));
                                  return head.map((cell,i)=>Math.abs(cell.getBoundingClientRect().right-row[i].getBoundingClientRect().right));
                                }""", columns)
                                assert max(alignment[:-1]) < 1, alignment
                        if key == "ledger" and compact:
                            expect(rows.first.get_by_text("잔액", exact=True)).to_be_visible()
                            expect(rows.last).to_contain_text("−1,000P")
                        if key == "posts":
                            expect(rows.first.get_by_role("link")).to_have_attribute("href", "/board/13")
                            expect(rows.first.get_by_role("link")).to_have_attribute("aria-label", re.compile("댓글 12개.*사진 첨부"))
                        assert_no_overflow(page)
                        suite.screenshot(page, f"{key}-{width}-{theme}")
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator(".me-posts .board-item")).to_have_count(2)
                    if compact: expect(selector).to_have_value("posts")
                    profile.open_account(page)
                    assert_account_state(page, True)
                    expect(page.get_by_role("button", name="비밀번호 변경", exact=True)).to_be_visible()
                    page.get_by_role("button", name="비밀번호 변경", exact=True).click()
                    expect(page.get_by_role("dialog", name="비밀번호 변경")).to_be_visible(); page.keyboard.press("Escape")
                    page.get_by_role("button", name="회원 탈퇴", exact=True).click()
                    expect(page.get_by_role("dialog", name="회원 탈퇴")).to_be_visible(); page.keyboard.press("Escape")
                    assert_no_overflow(page)
                    suite.screenshot(page, f"account-{width}-{theme}")
                    suite.record(f"filled-all-activities-account-{width}-{theme}", fixture); context.close()

            for points in (999999, 1000000, 2147483647):
                fixture = FilledFixtures(); fixture.users["fixture-a"].update({"username": "매크로를연구하는이십글자이름입니다확인중", "points_balance": points, "bio": ""})
                fixture.total_sales = 12345; fixture.total_earned = points
                context, page = suite.open(fixture, 320)
                page.evaluate("document.fonts.ready")
                assert_no_overflow(page); assert_stats(page, stacked=True)
                expect(page.locator(".me-bio")).to_have_count(0)
                expect(page.locator(".me-stats")).to_contain_text(f"{points:,}P")
                expect(page.locator(".me-stats")).to_contain_text("12345건")
                expect(page.get_by_role("button", name="프로필 편집", exact=True)).to_be_visible()
                suite.screenshot(page, f"large-values-{points}-320")
                suite.record(f"long-name-large-values-{points}", fixture); context.close()

            fixture = FilledFixtures(); context, page = suite.open(fixture, 1099)
            toggle = page.get_by_role("button", name="계정 설정", exact=True)
            for open_state in (False, True):
                if open_state:
                    toggle.focus(); page.keyboard.press("Enter")
                    expect(toggle).to_be_focused()
                assert_account_state(page, open_state)
                page.set_viewport_size({"width":1440,"height":900})
                assert_account_state(page, open_state)
                page.set_viewport_size({"width":390,"height":844})
                assert_account_state(page, open_state)
            toggle.press("Space"); assert_account_state(page, False)
            profile.open_account(page)
            page.get_by_role("button", name="로그아웃", exact=True).click()
            expect(page).to_have_url(re.compile(r"/login$"))
            assert page.evaluate("localStorage.getItem('ggp_token')") is None
            suite.record("account-collapse-resize-restoration-and-logout", fixture); context.close()

            fixture = FilledFixtures(); context, page = suite.open(fixture, 1440)
            page.get_by_role("tab", name=re.compile("만든 매크로")).focus()
            for key, selected in [("ArrowRight", "purchased"), ("End", "posts"), ("ArrowRight", "created"), ("ArrowLeft", "posts"), ("Home", "created")]:
                page.keyboard.press(key)
                current = page.get_by_role("tab", name=re.compile(TABS[selected]))
                expect(current).to_be_focused()
                expect(current).to_have_attribute("aria-selected", "true")
                expect(current).to_have_attribute("tabindex", "0")
                expect(page.locator('.me-tabs [role="tab"][tabindex="0"]')).to_have_count(1)
                expect(page.locator('.me-tabs [role="tab"][aria-selected="false"][tabindex="-1"]')).to_have_count(4)
                expect(page.locator(".me-panel")).to_have_attribute("aria-labelledby", f"me-tab-{selected}")
                if selected != "created": expect(page).to_have_url(re.compile(rf"\?tab={selected}$"))
                else: expect(page).to_have_url(re.compile(r"/mypage$"))
            page.keyboard.press("Tab")
            expect(page.locator(".me-panel")).to_be_focused()
            suite.record("desktop-activity-keyboard-navigation", fixture); context.close()

            fixture = FilledFixtures(); fixture.users["fixture-a"]["can_change_password"] = False
            context, page = suite.open(fixture, 390); profile.open_account(page)
            expect(page.get_by_role("button", name="비밀번호 변경", exact=True)).to_have_count(0)
            expect(page.get_by_text("Google 로그인", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="회원 탈퇴", exact=True)).to_be_visible()
            suite.record("google-account-keeps-supported-actions", fixture); context.close()
            browser.close()
            report = {"passed": True, "build": str(profile.BUILD), "checks": suite.checks, "page_errors": suite.errors, "screenshots": suite.screenshots}
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"passed": True, "checks": len(suite.checks)}))
    finally: server.shutdown()


if __name__ == "__main__": main()
