"""GG-001/002/011 regressions; fixture-only, no accounts, AI or screenshots."""
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("profile", Path(__file__).with_name("profileAvatarBrowser.smoke.py"))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
BASE = os.environ.get("QA_TEST_BASE_URL", "http://127.0.0.1:5178")


class Fixture(profile.Fixtures):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.list_calls = 0
        self.list_queries = []

    def post(self):
        return {"id": 11, "title": "대비 검증 게시글", "author_name": "fixture", "author_user_id": 101,
                "created_ms": 1788998400000, "views": 0, "likes": 0, "dislikes": 0, "comments": [],
                "body_html": '<p style="color:rgb(221,225,231)!important;background-color:#fff">기존 인라인 색 본문 <strong>강조</strong></p>'
                             '<p style="text-align:right;color:#eee">정렬 보존 <a href="https://example.invalid">링크</a></p>'}

    def route(self, route):
        path = urlsplit(route.request.url).path
        if path == "/api/board/posts":
            self.list_calls += 1
            self.list_queries.append(urlsplit(route.request.url).query)
            if self.failure == "network":
                route.abort(); return
            if self.failure:
                route.fulfill(status=int(self.failure), content_type="text/html", body="<html>Gateway error</html>"); return
            route.fulfill(json={"items": [self.post()], "page": 2, "pages": 2, "total": 20}); return
        if path == "/api/board/posts/11":
            route.fulfill(json=self.post()); return
        if path == "/api/auth/login":
            route.fulfill(status=401, json={"detail": "검증용 로그인 실패"}); return
        if "devnote" in path:
            route.fulfill(json={"note": None}); return
        if route.request.method not in ("GET", "HEAD"):
            route.fulfill(status=204); return  # no telemetry/paid calls can escape
        return super().route(route)


def open_page(browser, width, path, fixture, logged_in=False):
    context = browser.new_context(viewport={"width": width, "height": 900}, service_workers="block", reduced_motion="reduce")
    context.route("**/*", fixture.route)
    if logged_in:
        context.add_init_script("localStorage.setItem('ggp_token','fixture-a');localStorage.setItem('ggp_user'," + json.dumps(json.dumps(fixture.users["fixture-a"])) + ");")
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE + path, wait_until="domcontentloaded")
    hide_note = page.get_by_role("button", name="오늘 하루 보지 않기", exact=True)
    expect(hide_note).to_be_visible()
    hide_note.click()
    return context, page, errors


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    for width in (390, 1440):
        fixture = Fixture()
        context, page, errors = open_page(browser, width, "/board/11", fixture)
        expect(page.locator(".board-post-body")).to_be_visible()
        for dark in (False, True):
            page.evaluate("value => document.documentElement.classList.toggle('dark', value)", dark)
            result = page.locator(".board-post-body").evaluate("""root => {
                const rgb = s => (s.match(/[\\d.]+/g)||[]).slice(0,3).map(Number);
                const lum = s => rgb(s).map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((s,c,i)=>s+c*[.2126,.7152,.0722][i],0);
                const p = root.querySelector('p'), style = getComputedStyle(p);
                let parent=p, bg; while(parent){bg=getComputedStyle(parent).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')break;parent=parent.parentElement;}
                const a=lum(style.color), b=lum(bg);
                return {contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05), color:p.style.color,
                    alignment:root.querySelectorAll('p')[1].style.textAlign, bold:root.querySelector('strong')!==null, link:root.querySelector('a')!==null};
            }""")
            assert result["contrast"] >= 4.5 and result["color"] == "", result
            assert result["alignment"] == "right" and result["bold"] and result["link"], result
        assert not errors, errors
        context.close()
        print(json.dumps({"width": width, "contrast": "passed"}), flush=True)
        for failure, expected_calls in (("200", 1), ("503", 3), ("network", 1)):
            fixture = Fixture(failure)
            context, page, errors = open_page(browser, width, "/board?page=2&sort=likes&q=코인", fixture)
            retry = page.get_by_role("button", name="글 목록 다시 불러오기", exact=True)
            expect(retry).to_be_visible(timeout=20000)
            assert fixture.list_calls == expected_calls, fixture.list_calls
            fixture.failure = None
            retry.click()
            expect(page.locator('.board-row[href="/board/11"]')).to_be_visible()
            expect(retry).to_have_count(0)
            assert fixture.list_calls == expected_calls + 1
            assert fixture.list_queries[-1] == fixture.list_queries[0], fixture.list_queries
            assert not errors, errors
            context.close()
            print(json.dumps({"width": width, "board_failure": failure, "recovery": "passed"}), flush=True)
        fixture = Fixture()
        context, page, errors = open_page(browser, width, "/", fixture)
        ask = page.locator("[data-home-ask-trigger]").first
        expect(ask).to_contain_text("로그인 후 이용할 수 있어요")
        ask.click()
        expect(page).to_have_url(re.compile(r"/login\?next=%2Fbuilder%3Fask%3D1"))
        page.get_by_role("textbox", name="아이디", exact=True).fill("fixture@example.invalid")
        page.locator('input[type="password"]').fill("fixture-password")
        page.get_by_role("button", name="로그인", exact=True).click()
        expect(page.get_by_text("검증용 로그인 실패", exact=True)).to_be_visible()
        expect(page.get_by_role("textbox", name="아이디", exact=True)).to_have_value("fixture@example.invalid")
        page.go_back()
        expect(ask).to_be_visible()
        assert not errors, errors
        context.close()
        fixture = Fixture()
        context, page, errors = open_page(browser, width, "/", fixture, logged_in=True)
        ask = page.locator("[data-home-ask-trigger]").first
        expect(ask).to_be_visible()
        expect(ask).not_to_contain_text("로그인 후 이용할 수 있어요")
        ask.click()
        expect(page).to_have_url(re.compile(r"/builder\?ask=1"))
        assert not errors, errors
        context.close()
        print(json.dumps({"width": width, "contrast_light_dark": "passed", "board_html_network_retry": "passed", "ask_login_return": "passed"}), flush=True)
    browser.close()
