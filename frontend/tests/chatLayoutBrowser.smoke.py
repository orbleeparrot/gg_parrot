"""Check ChatBox layout against actual built application CSS, using local APIs.

Run `npm run build` in frontend first, then run this script with Playwright.
CHAT_BUILT_ASSETS can point to another Vite build's assets directory.
BROWSER_EXECUTABLE_PATH and NODE_BINARY have the same meaning as chatBrowser.
CHAT_LAYOUT_REPORT defaults to /tmp/ggp-chat-layout-report.json.
"""

import importlib.util
import json
import os
import tempfile
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


spec = importlib.util.spec_from_file_location("chat_browser", Path(__file__).with_name("chatBrowser.smoke.py"))
chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat)


class LayoutFixtures(chat.Fixtures):
    def route(self, route):
        if "/brand/" in route.request.url:
            route.continue_()
        else:
            super().route(route)


def inspect_layout(page):
    metrics = page.evaluate("""() => {
      const rect = selector => {
        const node = document.querySelector(selector);
        const box = node.getBoundingClientRect();
        return {x:box.x, y:box.y, width:box.width, height:box.height, bottom:box.bottom,
                right:box.right, clientWidth:node.clientWidth, scrollWidth:node.scrollWidth};
      };
      return {viewport:{width:innerWidth,height:innerHeight}, sheet:rect('.chat-sheet'),
        log:rect('.chat-log'), composer:rect('.chat-composer'),
        input:rect('.chat-composer input'), bodyWidth:document.documentElement.scrollWidth,
        display:getComputedStyle(document.querySelector('.chat-sheet')).display};
    }""")
    viewport = metrics["viewport"]
    assert metrics["display"] == "flex", metrics
    for name in ("sheet", "log", "composer", "input"):
        box = metrics[name]
        assert box["width"] > 0 and box["height"] > 0, (name, metrics)
        assert box["x"] >= -1 and box["right"] <= viewport["width"] + 1, (name, metrics)
        assert box["y"] >= -1 and box["bottom"] <= viewport["height"] + 1, (name, metrics)
    assert metrics["log"]["height"] >= 60, metrics
    assert metrics["log"]["bottom"] <= metrics["composer"]["y"] + 1, metrics
    assert metrics["composer"]["bottom"] <= metrics["sheet"]["bottom"] + 1, metrics
    assert metrics["bodyWidth"] <= viewport["width"], metrics
    assert metrics["log"]["scrollWidth"] <= metrics["log"]["clientWidth"] + 1, metrics
    expect(page.get_by_role("textbox", name="채팅 메시지", exact=True)).to_be_visible()
    return metrics


def main():
    assets = Path(os.environ.get("CHAT_BUILT_ASSETS", chat.FRONTEND / "dist/assets")).resolve()
    styles = sorted(assets.glob("index-*.css"))
    if len(styles) != 1:
        raise RuntimeError(f"Expected one built index CSS in {assets}; run the Vite build first")
    report = {"passed": False, "styles": str(styles[0]), "scenarios": {}, "screenshots": []}
    with tempfile.TemporaryDirectory(prefix="ggp-chat-layout-") as temporary:
        directory = Path(temporary)
        chat.build_harness(directory)
        (directory / "assets").symlink_to(assets, target_is_directory=True)
        (directory / "brand").symlink_to(chat.FRONTEND / "public/brand", target_is_directory=True)
        # All sizes, resets, fonts and theme tokens come from production CSS.
        # app.css is the latest bundled ChatBox.css, loaded after global styles.
        (directory / "index.html").write_text(f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/assets/{styles[0].name}"><link rel="stylesheet" href="/app.css">
</head><body><div id="root"></div><script src="/app.js"></script></body></html>""")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(chat.QuietHandler, directory=str(directory)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
                if os.environ.get("BROWSER_EXECUTABLE_PATH"):
                    launch["executable_path"] = os.environ["BROWSER_EXECUTABLE_PATH"]
                browser = playwright.chromium.launch(**launch)
                for device, viewport in (("desktop", {"width": 1440, "height": 1000}),
                                         ("mobile", {"width": 390, "height": 844})):
                    for theme in ("light", "dark"):
                        name = f"{device}-{theme}"
                        fixture = LayoutFixtures([chat.message(index, (index % 3) + 1, "회원 이름")
                                                  for index in range(1, 41)], seen=40)
                        for item in fixture.items:
                            item["text"] = f"{item['id']}번 메시지: 회원별 읽음 상태와 채팅 화면을 확인하고 있어요."
                        context = browser.new_context(viewport=viewport, color_scheme=theme)
                        context.route("**/*", fixture.route)
                        context.add_init_script("""localStorage.setItem('ggp_token','fixture-member-1');
localStorage.setItem('ggp_user',JSON.stringify({id:1,username:'긴 이름을 가진 회원 계정'}));""")
                        suite = chat.Suite(browser, f"http://127.0.0.1:{server.server_port}")
                        suite.contexts.append(context)
                        try:
                            page, _ = suite.page(fixture, context=context)
                            page.evaluate("theme => document.documentElement.classList.toggle('dark',theme==='dark')", theme)
                            page.evaluate("document.fonts.ready")
                            chat.open_chat(page)
                            page.clock.run_for(250)
                            states = {"normal": inspect_layout(page)}
                            fixture.fail = True
                            chat.advance_poll(page)
                            expect(page.get_by_role("alert")).to_be_visible()
                            states["error"] = inspect_layout(page)
                            fixture.fail = False
                            page.get_by_role("button", name="다시 시도", exact=True).click()
                            chat.settle(page)
                            expect(page.get_by_role("alert")).to_have_count(0)
                            page.locator(".chat-log").evaluate("el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));}")
                            fixture.items.append(chat.message(41))
                            chat.advance_poll(page)
                            expect(page.locator(".chat-jump")).to_be_visible()
                            states["scroll_jump"] = inspect_layout(page)
                            page.get_by_role("button", name="스티커 열기", exact=True).click()
                            chat.settle(page)
                            expect(page.get_by_role("group", name="스티커 고르기")).to_be_visible()
                            states["sticker_tray"] = inspect_layout(page)
                            fixture.fail = True
                            chat.advance_poll(page)
                            expect(page.get_by_role("alert")).to_be_visible()
                            states["error_jump_stickers"] = inspect_layout(page)
                            if (device, theme) in (("desktop", "light"), ("mobile", "dark")):
                                screenshot = f"/tmp/ggp-chat-layout-{name}.png"
                                page.screenshot(path=screenshot, full_page=True)
                                report["screenshots"].append(screenshot)
                            suite.cleanup()
                            report["scenarios"][name] = {"passed": True, "states": states}
                        except Exception as error:
                            report["scenarios"][name] = {"passed": False, "error": str(error)}
                            context.close()
                        outcome = report["scenarios"][name]
                        summary = ({"passed": True, "states": list(outcome["states"]),
                                    "min_log_height": min(state["log"]["height"] for state in outcome["states"].values())}
                                   if outcome["passed"] else outcome)
                        print(json.dumps({name: summary}, ensure_ascii=False), flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    report["passed"] = all(result["passed"] for result in report["scenarios"].values())
    target = Path(os.environ.get("CHAT_LAYOUT_REPORT", "/tmp/ggp-chat-layout-report.json"))
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": report["passed"], "report": str(target), "screenshots": report["screenshots"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
