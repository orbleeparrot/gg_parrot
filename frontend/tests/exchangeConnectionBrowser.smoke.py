"""Local built UI only. No production credentials/backend/exchange requests.

Run after npm run build with Playwright installed; screenshots stay in /tmp.
The jsQR dev dependency independently decodes the browser's actual SVG QR.
"""
import os
import importlib.util
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
_spec = importlib.util.spec_from_file_location("runner_device_fixture", Path(__file__).with_name("runnerDeviceBrowser.smoke.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
Handler, Fixture, BUILD = _fixture.Handler, _fixture.Fixture, _fixture.BUILD


class WizardFixture(Fixture):
    def __init__(self, exchange):
        super().__init__()
        self.exchange = exchange

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path == "/api/me/macros":
            self.requests.append(parsed.path)
            macro = {"exchange": self.exchange, "symbol": "KRW-BTC", "position_side": "long", "leverage": 1, "rule_type": "A", "candle_interval": "1h", "params": {"initial_capital": 50000}, "risk": {}}
            route.fulfill(json={"items": [{"id": 1, "symbol": "KRW-BTC", "name": "로컬 검사 예시", "rule_type": "A", "source_type": "created", "macro": macro}]})
            return
        super().route(route)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors = []
    checks = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"], **(
                {"executable_path": os.environ["BROWSER_EXECUTABLE_PATH"]} if os.environ.get("BROWSER_EXECUTABLE_PATH") else {}))
            for width in [320, 390, 1440]:
                for reduced in ["reduce", "no-preference"]:
                    context = browser.new_context(viewport={"width": width, "height": 1000}, reduced_motion=reduced, color_scheme="dark" if reduced == "reduce" else "light")
                    fixture = Fixture()
                    context.route("**/*", fixture.route)
                    context.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => {window.copied = value;}}});")
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    for exchange, name in [("upbit", "업비트"), ("bithumb", "빗썸")]:
                        page.goto(origin + f"/exchange-connect?exchange={exchange}&step=prepare&member_key=fixture-secret&launch_id=fixture-ticket", wait_until="networkidle")
                        guide = page.get_by_role("region", name=f"{name} 단계별 연결 도우미")
                        expect(guide).to_be_visible()
                        expect(page.get_by_role("dialog")).to_have_count(0)
                        assert guide.locator("input").count() == 0
                        guide.get_by_role("button", name="다음 안내 →").click()
                        expect(guide.locator("h3")).to_have_text("필요한 권한 셋만 켜기")
                        assert page.evaluate("document.activeElement.tagName") == "H3"
                        guide.get_by_role("button", name="다음 안내 →").click()
                        expect(guide.locator("h3")).to_have_text("실행기 PC의 IPv4 등록하기")
                        guide.get_by_text("다른 기기에서 안내만 이어보기 · QR").click()
                        expect(guide.get_by_role("img", name="현재 단계의 공개 연결 안내 QR")).to_be_visible()
                        guide.get_by_role("button", name="안내 주소 복사").click()
                        address = f"https://gg-parrot.vercel.app/exchange-connect?exchange={exchange}&step=ip"
                        assert page.evaluate("window.copied") == address
                        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "node_modules/jsqr/dist/jsQR.js"))
                        decoded = page.evaluate("""async () => {
                          const svg = document.querySelector('.exchange-connect-qr');
                          const img = new Image();
                          img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg));
                          await img.decode();
                          const canvas = document.createElement('canvas'); canvas.width=canvas.height=360;
                          const ctx=canvas.getContext('2d'); ctx.drawImage(img,0,0,360,360);
                          return jsQR(ctx.getImageData(0,0,360,360).data,360,360)?.data;
                        }""")
                        assert decoded == address, decoded
                        guide.get_by_role("button", name="정지 화면 3").click()
                        expect(guide.get_by_role("button", name="정지 화면 3")).to_have_attribute("aria-pressed", "true")
                        caption = guide.locator("figcaption").inner_text()
                        page.wait_for_timeout(600 if reduced == "reduce" else 4200)
                        assert guide.locator("figcaption").inner_text() == caption
                        if reduced == "reduce":
                            expect(guide.get_by_role("button", name="일시정지", exact=True)).to_have_count(0)
                        else:
                            guide.get_by_role("button", name="반복 재생", exact=True).click()
                            expect(guide.get_by_role("button", name="일시정지", exact=True)).to_be_visible()
                            guide.get_by_role("button", name="일시정지", exact=True).click()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        # No exchange/member/launch requests are needed for a public guide.
                        assert not any("runner/key" in x or "launch" in x for x in fixture.requests), fixture.requests
                        if os.environ.get("EXCHANGE_CONNECT_SCREENSHOT_DIR") and exchange == "upbit" and width in [390, 1440]:
                            output = Path(os.environ["EXCHANGE_CONNECT_SCREENSHOT_DIR"])
                            output.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(output / f"guide-{width}-{reduced}.png"), full_page=True)
                        checks += 1
                        print(f"{width}px {reduced} {exchange} passed", flush=True)
                    context.close()
            for exchange, name in [("upbit", "업비트"), ("bithumb", "빗썸")]:
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                fixture = WizardFixture(exchange)
                context.route("**/*", fixture.route)
                context.add_init_script("""Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});
                  Object.defineProperty(navigator,'userAgentData',{get:()=>undefined});
                  localStorage.setItem('ggp_token','fixture-only-token');
                  localStorage.setItem('ggp_user',JSON.stringify({id:101,username:'로컬 검사',email:'fixture@example.invalid'}));
                  sessionStorage.setItem('devnote:closed','2026-10-08');""")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/runner?step=2", wait_until="networkidle")
                if page.get_by_role("dialog").count():
                    page.get_by_role("button", name="확인했어요", exact=True).click()
                guide = page.get_by_role("region", name=f"{name} 단계별 연결 도우미")
                expect(guide).to_be_visible()
                expect(page.get_by_role("checkbox")).to_have_count(1)  # Manual guide acknowledgment only.
                assert guide.locator("input").count() == 0
                assert not any("launch-ticket" in x for x in fixture.requests)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                print(f"Windows wizard {exchange} passed", flush=True)
                checks += 1
                context.close()
            browser.close()
        assert not errors, errors
        print(f"exchange connection browser: {checks} cases passed; page errors 0")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
