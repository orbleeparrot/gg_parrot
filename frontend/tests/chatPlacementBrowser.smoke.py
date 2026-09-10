"""Fixed mobile chat and remembered desktop dragging with local API fixtures.

FRONTEND_BUILD=/tmp/chat-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  NODE_BINARY=/path/to/node python frontend/tests/chatPlacementBrowser.smoke.py
CHAT_PLACEMENT_OUTPUT overrides /tmp/ggp-chat-placement-check.
The real ChatBox is bundled in the existing local test harness; no backend runs.
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


spec = importlib.util.spec_from_file_location("chat_layout", Path(__file__).with_name("chatLayoutBrowser.smoke.py"))
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)
chat = layout.chat
BUILD = Path(os.environ.get("FRONTEND_BUILD", chat.FRONTEND / "dist")).resolve()
OUTPUT = Path(os.environ.get("CHAT_PLACEMENT_OUTPUT", "/tmp/ggp-chat-placement-check")).resolve()
SAVED = {"right": 280, "bottom": 180}


def placement(page):
    return page.locator(".chat-float").evaluate("""el => {
      const r = el.getBoundingClientRect();
      return {right: innerWidth-r.right, bottom: innerHeight-r.bottom};
    }""")


def saved_placement(page):
    return page.evaluate("JSON.parse(localStorage.getItem('chat:placement'))")


def assert_position(actual, expected):
    assert all(abs(actual[key] - expected[key]) <= 1 for key in ("right", "bottom")), (actual, expected)


def mobile_swipe(page):
    rect = page.locator(".chat-fab").bounding_box()
    start = {"x": rect["x"] + rect["width"] / 2, "y": rect["y"] + rect["height"] / 2}
    session = page.context.new_cdp_session(page)
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [start]})
    for distance in (20, 50, 100):
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
            {"x": start["x"] - distance, "y": start["y"] - distance}
        ]})
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    session.detach()
    chat.settle(page)


def mobile_case(browser, origin, width, theme):
    fixture = layout.LayoutFixtures([chat.message(index) for index in range(1, 12)], seen=11)
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  has_touch=True, is_mobile=True, color_scheme=theme)
    context.route("**/*", fixture.route)
    context.add_init_script("localStorage.setItem('ggp_token','fixture-member-1');"
                            "localStorage.setItem('ggp_user',JSON.stringify({id:1,username:'회원'}));"
                            "localStorage.setItem('chat:placement'," + json.dumps(json.dumps(SAVED)) + ");")
    suite = chat.Suite(browser, origin)
    suite.contexts.append(context)
    try:
        page, _ = suite.page(fixture, context=context)
        page.evaluate("theme => document.documentElement.classList.toggle('dark',theme==='dark')", theme)
        expect(page.locator(".chat-float")).to_have_class("chat-float is-mobile-fixed")
        assert_position(placement(page), {"right": 16, "bottom": 16})
        assert saved_placement(page) == SAVED
        assert page.locator(".chat-fab").get_attribute("title") is None
        assert page.locator(".chat-fab").evaluate("el => getComputedStyle(el).touchAction") == "manipulation"
        mobile_swipe(page)
        assert_position(placement(page), {"right": 16, "bottom": 16})
        assert saved_placement(page) == SAVED
        expect(page.locator(".chat-sheet")).to_have_count(0)
        page.locator(".chat-fab").tap()
        chat.settle(page)
        expect(page.get_by_role("dialog", name="리더보드 채팅")).to_be_visible()
        layout.inspect_layout(page)
        page.locator(".chat-fab").tap()
        chat.settle(page)
        expect(page.locator(".chat-sheet")).to_have_count(0)
        page.locator(".chat-fab").tap()
        chat.settle(page)
        # Simulate the VisualViewport reported when an onscreen keyboard opens.
        page.evaluate("""() => {
          Object.defineProperty(visualViewport, 'height', {configurable:true, value:500});
          Object.defineProperty(visualViewport, 'offsetTop', {configurable:true, value:0});
          visualViewport.dispatchEvent(new Event('resize'));
        }""")
        chat.settle(page)
        bounds = page.locator(".chat-sheet").bounding_box()
        composer = page.locator(".chat-composer").bounding_box()
        assert bounds["y"] >= 0 and composer["y"] + composer["height"] < 500, (bounds, composer)
        assert_position(placement(page), {"right": 16, "bottom": 844 - 500 + 16})
        assert saved_placement(page) == SAVED
        page.screenshot(path=str(OUTPUT / f"mobile-{width}-{theme}-keyboard.png"), full_page=True)
        page.evaluate("""() => {
          delete visualViewport.height; delete visualViewport.offsetTop;
          visualViewport.dispatchEvent(new Event('resize'));
        }""")
        chat.settle(page)
        page.locator(".chat-close").tap()
        chat.settle(page)
        page.evaluate("auditMount(false)")
        chat.settle(page)
        page.evaluate("auditMount(true)")
        chat.settle(page)
        assert_position(placement(page), {"right": 16, "bottom": 16})
        assert saved_placement(page) == SAVED
        page.screenshot(path=str(OUTPUT / f"mobile-{width}-{theme}-fixed.png"), full_page=True)
    finally:
        suite.cleanup()


def desktop_case(browser, origin):
    fixture = layout.LayoutFixtures([chat.message(1)], seen=1)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.route("**/*", fixture.route)
    context.add_init_script("localStorage.setItem('chat:placement'," + json.dumps(json.dumps(SAVED)) + ");")
    suite = chat.Suite(browser, origin)
    suite.contexts.append(context)
    try:
        page, _ = suite.page(fixture, context=context)
        assert_position(placement(page), SAVED)
        fab = page.locator(".chat-fab").bounding_box()
        page.mouse.move(fab["x"] + fab["width"] / 2, fab["y"] + fab["height"] / 2)
        page.mouse.down()
        page.mouse.move(fab["x"] + fab["width"] / 2 - 80, fab["y"] + fab["height"] / 2 - 60, steps=8)
        page.mouse.up()
        chat.settle(page)
        remembered = saved_placement(page)
        assert remembered != SAVED
        assert_position(placement(page), remembered)
        expect(page.locator(".chat-sheet")).to_have_count(0)
        # Entering mobile must not clamp or overwrite the desktop preference.
        page.set_viewport_size({"width": 375, "height": 844})
        chat.settle(page)
        assert_position(placement(page), {"right": 16, "bottom": 16})
        assert saved_placement(page) == remembered
        page.locator(".chat-fab").click()
        chat.settle(page)
        expect(page.locator(".chat-sheet")).to_be_visible()
        page.locator(".chat-close").click()
        chat.settle(page)
        page.set_viewport_size({"width": 1440, "height": 1000})
        chat.settle(page)
        assert_position(placement(page), remembered)
        assert saved_placement(page) == remembered
        page.screenshot(path=str(OUTPUT / "desktop-restored.png"), full_page=True)
    finally:
        suite.cleanup()


def main():
    styles = sorted((BUILD / "assets").glob("index-*.css"))
    if len(styles) != 1:
        raise SystemExit(f"Build missing or ambiguous index CSS: {BUILD}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": [], "build": str(BUILD)}
    with tempfile.TemporaryDirectory(prefix="ggp-chat-placement-") as temporary:
        directory = Path(temporary)
        chat.build_harness(directory)
        (directory / "assets").symlink_to(BUILD / "assets", target_is_directory=True)
        (directory / "brand").symlink_to(chat.FRONTEND / "public/brand", target_is_directory=True)
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
                origin = f"http://127.0.0.1:{server.server_port}"
                for width, theme in ((375, "dark"), (375, "light"), (820, "dark")):
                    mobile_case(browser, origin, width, theme)
                    report["checks"].append(f"fixed-swipe-tap-keyboard-remount-{width}-{theme}")
                desktop_case(browser, origin)
                report["checks"].append("desktop-drag-resize-mobile-restore")
                browser.close()
            report["passed"] = True
        finally:
            server.shutdown()
            server.server_close()
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
