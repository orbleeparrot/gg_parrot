"""Local built UI with synthetic news/model responses; never a real model benchmark.

FRONTEND_BUILD=/tmp/ggp-semif-build BROWSER_EXECUTABLE_PATH=/path/to/chrome python this_file
"""
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import hashlib
from playwright.sync_api import sync_playwright, expect

BUILD = Path(os.environ.get("FRONTEND_BUILD", "/tmp/ggp-semif-build"))
OUTPUT = Path(__file__).resolve().parents[2] / "docs/semif-news-test-2026-09-23"
USER = {"id": 1, "username": "테스트 관리자", "is_admin": True, "points_balance": 0}
NEWS = [dict(id=f"{i:020x}", scope="BTC", title=title, source="UI 검증용 기사", excerpt="이 기사와 판단 시간은 화면 동작 검증을 위한 테스트 데이터입니다.", published="2026-09-23T07:00:00Z", url="https://example.com") for i, title in enumerate([
    "비트코인 현물 ETF, 순유입 규모 확대", "거래소 보안 사고로 일시적인 출금 중단", "주요 거래소, 신규 서비스 출시 일정 공개"], 1)]

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not (BUILD / urlsplit(self.path).path.lstrip("/")).is_file():
            self.path = "/index.html"
        super().do_GET()
    def log_message(self, *args):
        pass

class Fixtures:
    def __init__(self):
        self.snapshot = {"model": "semif-test:0.1.1", "enabled": True, "connected": True, "detail": "", "current": None,
                         "history": [], "stats": {"completed": 0, "pending": 2, "failed": 0, "average_ms": None}}
        self.unexpected = []
        self.reads = 0
        self.unchanged = 0
    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != "127.0.0.1":
            route.abort(); return
        path = url.path
        if not path.startswith("/api/"):
            route.continue_(); return
        if path == "/api/admin/news-test/results":
            assert route.request.method == "GET", "Browser must never schedule inference"
            self.reads += 1
            version = hashlib.md5(json.dumps(self.snapshot, sort_keys=True).encode()).hexdigest()
            if parse_qs(url.query).get('after') == [version]:
                self.unchanged += 1
                route.fulfill(json={"unchanged": True, "version": version}); return
            route.fulfill(json={**self.snapshot, "version": version, "server_now_ms": int(__import__('time').time() * 1000)}); return
        if path == "/api/me/notifications/stream-token":
            route.fulfill(status=503, json={"detail": "No stream in UI fixture"}); return
        fixtures = {"/api/auth/me": {"user": USER}, "/api/kimchi-premium": {"ok": True, "premium_pct": .82},
                    "/api/fear-greed": {"ok": True, "value": 64}, "/api/hangang-temp": {"ok": True, "temperature": 24}, "/api/hot-coins": {"items": []},
                    "/api/me/notifications/unread": {"count": 0}, "/api/visit": {"ok": True}}
        if path not in fixtures:
            self.unexpected.append(path)
        route.fulfill(json=fixtures.get(path, {}))
    def processing(self, index):
        previous = self.snapshot['current']
        if previous and previous.get('result'):
            self.snapshot['history'].insert(0, previous)
        self.snapshot['current'] = {'article': NEWS[index], 'status': 'processing', 'started_at': int(__import__('time').time()*1000), 'result': None}
    def finish(self, verdict="bullish", elapsed=1234):
        self.snapshot['current'] = {**self.snapshot['current'], 'status': 'ready', 'result': {"verdict": verdict, "elapsed_ms": elapsed, "model_ms": 1200, "load_ms": 30, "probabilities": {"bullish": .8 if verdict == "bullish" else .1, "bearish": .8 if verdict == "bearish" else .1, "neutral": .1}}}
        self.snapshot['stats']['completed'] += 1
        self.snapshot['stats']['pending'] -= 1
        self.snapshot['stats']['average_ms'] = elapsed

def until(page, predicate):
    for _ in range(100):
        if predicate(): return
        page.wait_for_timeout(100)
    raise AssertionError("UI condition timed out")

def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ["BROWSER_EXECUTABLE_PATH"], headless=True, args=["--no-sandbox"])
            for width, theme in [(1440, "dark"), (390, "dark"), (320, "light")]:
                fixture = Fixtures()
                context = browser.new_context(viewport={"width": width, "height": 1000}, reduced_motion="reduce", color_scheme=theme)
                context.route("**/*", fixture.route)
                context.add_init_script("localStorage.setItem('ggp_token','fixture');localStorage.setItem('ggp_user'," + json.dumps(json.dumps(USER)) + ");localStorage.setItem('ggp_theme'," + json.dumps(theme) + ");")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/admin/news-test")
                if page.get_by_role("button", name="확인했어요", exact=True).count():
                    page.get_by_role("button", name="확인했어요", exact=True).click()
                expect(page.get_by_role("button", name="테스트 시작", exact=True)).to_have_count(0)
                # Sidebar entry is shared by desktop and mobile, with active styling.
                if width < 768:
                    page.get_by_role("button", name="페이지 메뉴 열기").click()
                    nav = page.locator("#site-mobile-navigation")
                else:
                    nav = page.locator(".site-sidebar")
                menu = nav.get_by_role("link", name="뉴스 판단 테스트", exact=True)
                expect(menu).to_be_visible()
                expect(menu).to_have_attribute("aria-current", "page")
                menu.click()
                if width < 768:
                    expect(nav).to_have_attribute("aria-hidden", "true")
                # Server starts work while the browser merely observes.
                fixture.processing(0)
                expect(page.locator(".st-verdict strong")).to_have_text("판단 중")
                first_clock = page.locator(".st-timing strong").inner_text()
                page.wait_for_timeout(150)
                assert page.locator(".st-timing strong").inner_text() != first_clock
                until(page, lambda: fixture.unchanged > 0)
                fixture.finish()
                expect(page.locator(".st-verdict strong")).to_have_text("호재")
                expect(page.locator(".st-timing strong")).to_contain_text("1.23")
                fixture.processing(1)
                expect(page.locator(".st-history li")).to_have_count(1)
                fixture.finish("bearish", 2456)
                expect(page.locator(".st-verdict strong")).to_have_text("악재")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"overflow: {width}"
                page.screenshot(path=str(OUTPUT / f"news-test-{width}-{theme}.png"), full_page=True)
                page.locator(".st-history li button").click()
                expect(page.locator(".st-verdict strong")).to_have_text("호재")
                page.get_by_role("button", name="현재 뉴스로 돌아가기").click()
                expect(page.locator(".st-verdict strong")).to_have_text("악재")
                # Reload preserves server results; no start request is sent.
                page.reload()
                expect(page.locator(".st-verdict strong")).to_have_text("악재")
                expect(page.locator(".st-history li")).to_have_count(1)
                fixture.snapshot['enabled'] = False
                fixture.snapshot['detail'] = '자동 판단 서버 연결이 필요해요.'
                expect(page.locator(".st-error")).to_contain_text("서버 연결")
                assert fixture.reads > 2
                assert not errors, errors
                assert not fixture.unexpected, fixture.unexpected
                checks.append({"width": width, "theme": theme, "passed": True})
                context.close()
            browser.close()
    finally:
        server.shutdown()
    (OUTPUT / "browser-results.json").write_text(json.dumps({"data": "synthetic fixtures, not real inference", "checks": checks}, indent=2))
    print(json.dumps(checks))

if __name__ == "__main__":
    main()
