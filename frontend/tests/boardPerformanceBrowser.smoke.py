"""Real build + local fixtures: editor splitting, list cache, mutation freshness.

FRONTEND_BUILD, BROWSER_EXECUTABLE_PATH, BOARD_BROWSER_OUTPUT configure the run.
No real account, database or external API is contacted.
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

spec = importlib.util.spec_from_file_location("profile", Path(__file__).with_name("profileAvatarBrowser.smoke.py"))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
OUTPUT = Path(os.environ.get("BOARD_BROWSER_OUTPUT", "/tmp/ggp-board-browser-check"))


class Fixture(profile.Fixtures):
    def __init__(self):
        super().__init__()
        self.list_calls = 0
        self.comments = []
        self.likes = 0
        self.fail_comment = False

    def post(self, pid):
        return {"id": pid, "title": f"게시글 {pid}", "body_html": "<p>본문</p>", "author_name": "작성자",
                "author_user_id": 202, "author_avatar_url": None, "created_ms": 1788998400000,
                "created_kst": "2026-09-10 09:00", "views": 1, "likes": self.likes, "dislikes": 0,
                "my_vote": 1 if self.likes else 0, "comments": self.comments if pid == 11 else [],
                "comment_count": len(self.comments) if pid == 11 else 0, "has_image": False}

    def route(self, route):
        path = urlsplit(route.request.url).path
        method = route.request.method
        if path == "/api/board/posts":
            self.list_calls += 1
            route.fulfill(json={"items": [self.post(11), self.post(12)], "page": 1, "pages": 1, "total": 2}); return
        if re.fullmatch(r"/api/board/posts/\d+", path):
            route.fulfill(json=self.post(int(path.rsplit("/", 1)[1]))); return
        if path == "/api/board/posts/11/comments" and method == "POST":
            if self.fail_comment:
                route.fulfill(status=503, json={"detail": "다시 시도해 주세요"}); return
            c = {"id": 1, "post_id": 11, "text": route.request.post_data_json["text"], "username": "껄무새",
                 "author_user_id": 101, "author_avatar_url": None, "created_ms": 1788998400000,
                 "created_kst": "2026-09-10 09:00", "parent_id": None, "replies": []}
            self.comments.append(c); route.fulfill(json={"comment": c}); return
        if path == "/api/board/posts/11/vote":
            self.likes = 1
            route.fulfill(json={"post_id": 11, "likes": 1, "dislikes": 0, "my_vote": 1}); return
        return super().route(route)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    profile.OUTPUT = OUTPUT
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(profile.Handler, directory=str(profile.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ["BROWSER_EXECUTABLE_PATH"], args=["--no-sandbox"])
            suite = profile.Suite(browser, f"http://127.0.0.1:{server.server_port}")
            for width in (390, 1440):
                fixture = Fixture()
                context, page = suite.open(fixture, width, path="/board")
                expect(page.locator('.board-row[href="/board/11"]')).to_be_visible()
                page.locator('.board-row[href="/board/11"]').click()
                expect(page.locator('.board-post-title')).to_have_text("게시글 11")
                expect(page.locator('.board-post-list .board-row').first).to_be_visible()
                requests = fixture.list_calls
                page.get_by_role("link", name="목록 전체", exact=True).click()
                expect(page.locator('.board-row[href="/board/11"]')).to_be_visible()
                assert fixture.list_calls == requests, "return to list should use fresh cache"
                assert not page.evaluate("performance.getEntriesByType('resource').some(r=>/BoardWrite|BoardBodyEditor|tiptap/i.test(r.name))")
                page.locator('.board-row[href="/board/11"]').click()
                expect(page.locator('.board-post-title')).to_have_text("게시글 11")
                fixture.fail_comment = True
                page.get_by_role("textbox", name="댓글", exact=True).fill("등록 확인")
                page.get_by_role("button", name="댓글 등록", exact=True).click()
                expect(page.get_by_role("form", name="댓글 쓰기").get_by_role("alert")).to_contain_text("다시 시도")
                expect(page.get_by_role("textbox", name="댓글", exact=True)).to_have_value("등록 확인")
                fixture.fail_comment = False
                page.get_by_role("button", name="댓글 등록", exact=True).click()
                expect(page.locator('.board-comments')).to_contain_text("등록 확인")
                expect(page.locator('.board-post-list .board-count').first).to_have_text("1")
                assert fixture.list_calls > requests, "successful mutation should refresh visible list"
                page.get_by_role("button", name=re.compile(r"^추천")).click()
                expect(page.locator('.board-react-btn').first).to_have_attribute("aria-pressed", "true")
                page.get_by_role("link", name="목록 전체", exact=True).click()
                expect(page.locator('.board-row[href="/board/11"] .board-count')).to_have_text("1")
                expect(page.locator('.board-row[href="/board/11"] .board-likes')).to_have_text("1")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                suite.screenshot(page, f"board-{width}")
                page.get_by_role("link", name="글쓰기", exact=True).click()
                expect(page.locator('.tiptap')).to_be_visible()
                assert page.evaluate("performance.getEntriesByType('resource').some(r=>/BoardWrite/.test(r.name))")
                suite.record(f"cache-mutations-editor-split-{width}", fixture)
                context.close()
            browser.close()
            (OUTPUT / "report.json").write_text(json.dumps({"passed": True, "checks": suite.checks}, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
