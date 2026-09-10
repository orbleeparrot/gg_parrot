"""HTTP 400 moderation UX on the real components; all APIs use local fixtures.

Build frontend first. Set BROWSER_EXECUTABLE_PATH and optionally FRONTEND_BUILD,
NODE_BINARY, MODERATION_BROWSER_OUTPUT. No production backend is contacted.
"""
import importlib.util
import json
import os
import tempfile
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


HERE = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("MODERATION_BROWSER_OUTPUT", "/tmp/ggp-moderation-browser"))


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


chat = load("chat_fixture", "chatBrowser.smoke.py")
board = load("board_fixture", "boardPerformanceBrowser.smoke.py")
profile = board.profile


def block(route, field):
    route.fulfill(status=400, json={"detail": f"{field}에 비속어가 포함되어 있어요. 표현을 수정해 주세요."})


class ChatFixture(chat.Fixtures):
    def route(self, route):
        if (urlsplit(route.request.url).path == "/api/chat" and route.request.method == "POST"
                and "씨발" in route.request.post_data_json["text"]):
            block(route, "메시지")
            return
        super().route(route)


class BoardFixture(board.Fixture):
    def __init__(self):
        super().__init__()
        self.saved = None
        self.reject_post = True

    def post(self, pid):
        if self.saved and pid == 13:
            return self.saved
        return {**super().post(pid), "author_user_id": 101}

    def route(self, route):
        path, method = urlsplit(route.request.url).path, route.request.method
        if path == "/api/board/posts" and method == "POST":
            if self.reject_post:
                block(route, "본문")
            else:
                self.saved = {**self.post(13), "title": "수정한 제목", "body_html": "<p>수정한 본문</p>"}
                route.fulfill(json=self.saved)
            return
        if path == "/api/board/posts/11/comments" and method == "POST":
            if "씨발" in route.request.post_data_json["text"]:
                block(route, "댓글")
                return
        if path == "/api/board/comments/1" and method == "PUT":
            text = route.request.post_data_json["text"]
            if "씨발" in text:
                block(route, "댓글")
            else:
                self.comments[0] = {**self.comments[0], "text": text, "edited": True}
                route.fulfill(json={"comment": self.comments[0]})
            return
        super().route(route)


def check_board(suite, width):
    fixture = BoardFixture()
    context, page = suite.open(fixture, width, path="/board/11")
    draft = page.get_by_role("textbox", name="댓글", exact=True)
    draft.fill("씨발")
    page.get_by_role("button", name="댓글 등록", exact=True).click()
    expect(page.get_by_role("form", name="댓글 쓰기").get_by_role("alert")).to_contain_text("표현을 수정")
    expect(draft).to_have_value("씨발")
    assert not fixture.comments
    draft.fill("전략 공유 감사합니다")
    page.get_by_role("button", name="댓글 등록", exact=True).click()
    expect(draft).to_have_value("")
    expect(page.locator(".board-comments")).to_contain_text("전략 공유 감사합니다")
    page.locator(".board-comment").get_by_role("button", name="편집", exact=True).click()
    editing = page.get_by_role("textbox", name="댓글 고치기", exact=True)
    editing.fill("씨발")
    page.locator(".board-comment-edit").get_by_role("button", name="저장", exact=True).click()
    expect(editing).to_have_value("씨발")
    expect(page.locator(".board-comment-edit").get_by_role("alert")).to_contain_text("표현을 수정")
    assert fixture.comments[0]["text"] == "전략 공유 감사합니다"
    editing.fill("수정한 댓글")
    page.locator(".board-comment-edit").get_by_role("button", name="저장", exact=True).click()
    expect(page.locator(".board-comments")).to_contain_text("수정한 댓글")
    page.locator(".board-post-list").get_by_role("button", name="글쓰기", exact=True).click()
    title, editor = page.get_by_role("textbox", name="제목", exact=True), page.locator(".tiptap")
    title.fill("기록 제목")
    editor.fill("씨발")
    page.get_by_role("button", name="등록", exact=True).click()
    expect(page.locator(".board-form-error")).to_contain_text("표현을 수정")
    expect(title).to_have_value("기록 제목")
    expect(editor).to_have_text("씨발")
    assert fixture.saved is None
    suite.screenshot(page, f"post-rejected-{width}")
    fixture.reject_post = False
    title.fill("수정한 제목")
    editor.fill("수정한 본문")
    page.get_by_role("button", name="등록", exact=True).click()
    expect(page.locator(".board-post-title")).to_have_text("수정한 제목")
    suite.record(f"comment-create-edit-and-post-correction-{width}", fixture)
    context.close()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    profile.OUTPUT = OUTPUT
    servers = []
    with tempfile.TemporaryDirectory(prefix="ggp-moderation-harness-") as temp:
        chat.build_harness(Path(temp))
        for handler, directory in ((chat.QuietHandler, temp), (profile.Handler, profile.BUILD)):
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(directory)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ["BROWSER_EXECUTABLE_PATH"], args=["--no-sandbox"])
                chat_suite = chat.Suite(browser, f"http://127.0.0.1:{servers[0].server_port}")
                board_suite = profile.Suite(browser, f"http://127.0.0.1:{servers[1].server_port}")
                for width in (390, 1440):
                    fixture = ChatFixture([], seen=0)
                    page, _ = chat_suite.page(fixture)
                    page.set_viewport_size({"width": width, "height": 900})
                    chat.open_chat(page)
                    draft = page.get_by_role("textbox", name="채팅 메시지", exact=True)
                    draft.fill("씨발")
                    page.get_by_role("button", name="전송", exact=True).click()
                    expect(page.get_by_role("alert")).to_contain_text("표현을 수정")
                    expect(draft).to_have_value("씨발")
                    assert not fixture.items
                    draft.fill("시바이누 매매 기록")
                    page.get_by_role("button", name="전송", exact=True).click()
                    expect(draft).to_have_value("")
                    expect(page.locator(".chat-row.is-mine")).to_contain_text("시바이누 매매 기록")
                    check_board(board_suite, width)
                chat_suite.cleanup()
                browser.close()
                (OUTPUT / "report.json").write_text(json.dumps({"passed": True, "chat_widths": [390, 1440],
                                                               "board_checks": board_suite.checks}, ensure_ascii=False, indent=2))
        finally:
            for server in servers:
                server.shutdown()


if __name__ == "__main__":
    main()
