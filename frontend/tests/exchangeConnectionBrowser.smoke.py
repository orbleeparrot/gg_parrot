"""Runner-first guide against a real local build and read-only API fixtures.

No production credentials, backend, exchange requests, or native orders.
Optional screenshots are written only to EXCHANGE_CONNECT_SCREENSHOT_DIR in /tmp.
"""
import os
import importlib.util
import re
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

_spec = importlib.util.spec_from_file_location("runner_device_fixture", Path(__file__).with_name("runnerDeviceBrowser.smoke.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
Handler, Fixture, BUILD = _fixture.Handler, _fixture.Fixture, _fixture.BUILD


class WizardFixture(Fixture):
    def __init__(self, exchange):
        super().__init__()
        self.exchange = exchange
        self.launch_payloads = []

    def route(self, route):
        parsed = urlsplit(route.request.url)
        if parsed.hostname in {"localhost", "127.0.0.1"}:
            if parsed.path == "/api/me/runner/launch-tickets":
                self.requests.append(parsed.path)
                self.launch_payloads.append(route.request.post_data_json)
                # Opening the native process is unavailable in this browser fixture;
                # the manual connection screen must remain usable instead.
                route.fulfill(status=503, json={"detail": "fixture native handoff unavailable"})
                return
            if parsed.path == "/api/me/macros":
                self.requests.append(parsed.path)
                symbol = "BTCUSDT" if self.exchange == "binance" else "KRW-BTC"
                macro = {"exchange": self.exchange, "symbol": symbol, "position_side": "long", "leverage": 1, "rule_type": "A", "candle_interval": "1h", "params": {"initial_capital": 50000}, "risk": {}}
                route.fulfill(json={"items": [{"id": 1, "symbol": symbol, "name": "로컬 검사 예시", "rule_type": "A", "source_type": "created", "macro": macro}]})
                return
            if parsed.path == "/api/runner/download/info":
                self.requests.append(parsed.path)
                route.fulfill(json={"available": True, "url": "https://fixture.invalid/runner.exe", "version": "12", "min_runner_version": "6", "domestic_min_runner_version": "10", "supports_launch": True, "size": 8388608})
                return
        super().route(route)


def assert_no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def assert_key_choice_visible(guide):
    styles = guide.locator('.exchange-connect-key-mode button[aria-pressed="true"]').evaluate("element => { const s=getComputedStyle(element); return [parseFloat(s.borderTopWidth), s.borderTopStyle, s.borderTopColor]; }")
    other_color = guide.locator('.exchange-connect-key-mode button[aria-pressed="false"]').evaluate("element => getComputedStyle(element).borderTopColor")
    assert styles[0] >= 1 and styles[1] == "solid" and styles[2] != other_color, (styles, other_color)


def windows_context(browser, exchange, errors, ready=False):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    fixture = WizardFixture(exchange)
    context.route("**/*", fixture.route)
    context.add_init_script("""Object.defineProperty(navigator,'platform',{get:()=> 'Win32'});
      Object.defineProperty(navigator,'userAgentData',{get:()=>undefined});
      localStorage.setItem('ggp_token','fixture-only-token');
      localStorage.setItem('ggp_user',JSON.stringify({id:101,username:'로컬 검사',email:'fixture@example.invalid'}));
      sessionStorage.setItem('devnote:closed','2026-10-08');""")
    if ready:
        context.add_init_script("localStorage.setItem('ggparrot:runner-opened-version','12');")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, fixture


def close_intro(page):
    if page.get_by_role("dialog").count():
        page.get_by_role("button", name="확인했어요", exact=True).click()


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
                    theme = "dark" if reduced == "reduce" else "light"
                    context = browser.new_context(viewport={"width": width, "height": 1000}, reduced_motion=reduced, color_scheme=theme)
                    fixture = Fixture()
                    context.route("**/*", fixture.route)
                    context.add_init_script(f"localStorage.setItem('ggp_theme', '{theme}');")
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    for exchange, name in [("upbit", "업비트"), ("bithumb", "빗썸")]:
                        page.goto(origin + f"/exchange-connect?exchange={exchange}&step=ip&member_key=fixture-secret&launch_id=fixture-ticket", wait_until="networkidle")
                        guide = page.get_by_role("region", name=f"{name} 단계별 연결 도우미")
                        expect(guide).to_be_visible()
                        expect(page.get_by_role("dialog")).to_have_count(0)
                        expect(guide.locator("h3")).to_have_text("실행기에서 PC의 공인 IP 확인하기")
                        assert parse_qs(urlsplit(page.url).query) == {"exchange": [exchange], "step": ["prepare"]}
                        assert guide.locator("input,video,figure,.exchange-connect-qr,.exchange-connect-example").count() == 0
                        expect(guide.get_by_text("ipify", exact=False)).to_be_visible()
                        assert "고정 IP인지 판정하지 않아요" in guide.inner_text()
                        assert "현재 배포된 v10" in guide.inner_text()
                        assert page.locator('a[href*="fixture-secret"],a[href*="fixture-ticket"]').count() == 0
                        assert_key_choice_visible(guide)
                        capture = os.environ.get("EXCHANGE_CONNECT_SCREENSHOT_DIR") and exchange == "upbit" and width == 1440 and reduced == "reduce"
                        if capture:
                            output = Path(os.environ["EXCHANGE_CONNECT_SCREENSHOT_DIR"])
                            output.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(output / "guide-new.png"), full_page=True)
                        for button in guide.locator("button").all():
                            bounds = button.bounding_box()
                            assert bounds["height"] >= 44 and bounds["width"] >= 44, bounds
                        guide.get_by_role("button", name="다음 안내", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("공식 거래소에서 권한·IP를 등록하고 발급하기")
                        expect(guide.locator("h3")).to_be_focused()
                        assert parse_qs(urlsplit(page.url).query)["step"] == ["permissions"]
                        if exchange == "upbit":
                            assert "업비트 공식 QR 로그인" in guide.inner_text()
                            assert "API 키 전송이나 껄무새 매매 승인 기능이 아니에요" in guide.inner_text()
                        else:
                            expect(guide.get_by_role("link", name="공식 발급 안내", exact=True)).to_have_attribute("href", "https://support.bithumb.com/hc/ko/articles/52815899880345")
                            assert "점유 인증" in guide.inner_text()
                        guide.get_by_role("button", name="다음 안내", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("발급한 키로 실행기에서 검사하기")
                        expect(guide.locator("h3")).to_be_focused()
                        assert "Windows 계정으로 암호화" in guide.inner_text()
                        assert "검사 전용 버튼이 아니므로" in guide.inner_text()
                        guide.get_by_role("button", name="이전 안내", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("공식 거래소에서 권한·IP를 등록하고 발급하기")
                        guide.get_by_role("button", name="1 실행기·IP", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("실행기에서 PC의 공인 IP 확인하기")
                        expect(guide.locator("h3")).to_be_focused()
                        guide.get_by_role("button", name="기존 키 사용", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("기존 키로 실행기에서 검사하기")
                        expect(guide.locator("h3")).to_be_focused()
                        expect(guide.get_by_role("button", name="기존 키 사용", exact=True)).to_have_attribute("aria-pressed", "true")
                        assert_key_choice_visible(guide)
                        assert parse_qs(urlsplit(page.url).query) == {"exchange": [exchange], "step": ["keys"]}
                        assert "Secret Key를 분실" in guide.inner_text()
                        assert "거래소별" in guide.inner_text()
                        if capture:
                            page.evaluate("window.scrollTo(0, 0)")
                            page.screenshot(path=str(output / "guide-existing.png"), full_page=True)
                        guide.get_by_role("button", name="2 권한·IP 확인", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("기존 키의 권한·허용 IP 확인하기")
                        assert "IP 변경만으로 정상 키를 새로 발급할 필요는 없어요" in guide.inner_text()
                        if exchange == "upbit":
                            assert "기존 키의 ‘변경’" in guide.inner_text()
                        else:
                            assert "권한은 발급 후 바꿀 수 없어" in guide.inner_text()
                        guide.get_by_role("button", name="새 키 발급", exact=True).click()
                        expect(guide.locator("h3")).to_have_text("실행기에서 PC의 공인 IP 확인하기")
                        expect(guide.get_by_role("button", name="새 키 발급", exact=True)).to_have_attribute("aria-pressed", "true")
                        assert parse_qs(urlsplit(page.url).query)["step"] == ["prepare"]
                        assert_no_overflow(page)
                        assert not any("runner/key" in x or "launch" in x for x in fixture.requests), fixture.requests
                        checks += 1
                        print(f"{width}px {reduced} {exchange} passed", flush=True)
                    context.close()
            for exchange, name in [("upbit", "업비트"), ("bithumb", "빗썸"), ("binance", "바이낸스")]:
                context, page, fixture = windows_context(browser, exchange, errors)
                page.goto(origin + "/runner?step=2", wait_until="networkidle")
                close_intro(page)
                if exchange == "binance":
                    expect(page.get_by_role("checkbox")).to_have_count(1)
                    expect(page.get_by_role("button", name="키를 준비한 뒤 확인해 주세요")).to_be_disabled()
                    page.goto(origin + "/?run=1&step=3", wait_until="networkidle")
                    expect(page.get_by_role("checkbox")).to_have_count(1)
                    expect(page.get_by_role("button", name="키를 준비한 뒤 확인해 주세요")).to_be_disabled()
                else:
                    guide = page.get_by_role("region", name=f"{name} 단계별 연결 도우미")
                    expect(guide).to_be_visible()
                    expect(page.get_by_role("checkbox")).to_have_count(0)
                    expect(page.get_by_role("button", name="실행기부터 준비하기", exact=True)).to_be_enabled()
                    page.get_by_role("button", name="실행기부터 준비하기", exact=True).click()
                    expect(page.locator("#runner-wizard-title")).to_contain_text("실행기를 준비하면")
                    assert parse_qs(urlsplit(page.url).query)["step"] == ["3"]
                    assert page.evaluate("Object.keys(localStorage).filter(key => key.startsWith('ggparrot:domestic-key-ready:')).length") == 0
                    page.get_by_role("button", name="이전 화면", exact=True).click()
                    expect(page.get_by_role("button", name="실행기부터 준비하기", exact=True)).to_be_enabled()
                    page.goto(origin + "/?run=1&step=3", wait_until="networkidle")
                    expect(page.locator("#runner-wizard-title")).to_contain_text("실행기를 준비하면")
                assert not any("launch-ticket" in x for x in fixture.requests), fixture.requests
                assert_no_overflow(page)
                print(f"Windows wizard {exchange} passed", flush=True)
                checks += 1
                context.close()
            # Reuse 1-2: a manually recorded ready runner skips the download scene
            # for each domestic exchange, even if native launch is unavailable.
            for exchange, name in [("upbit", "업비트"), ("bithumb", "빗썸")]:
                context, page, fixture = windows_context(browser, exchange, errors, ready=True)
                page.goto(origin + "/runner?step=2", wait_until="networkidle")
                close_intro(page)
                guide = page.get_by_role("region", name=f"{name} 단계별 연결 도우미")
                guide.get_by_role("button", name="기존 키 사용", exact=True).click()
                expect(guide.locator("h3")).to_have_text("기존 키로 실행기에서 검사하기")
                expect(page.get_by_role("button", name="실행기부터 준비하기", exact=True)).to_have_count(0)
                page.get_by_role("button", name="실행기 연결로 바로가기", exact=True).click()
                expect(page.locator("#runner-wizard-title")).to_contain_text("회원 키를")
                assert parse_qs(urlsplit(page.url).query)["step"] == ["4"]
                expect(page.get_by_role("button", name="회원 키 복사", exact=True)).to_be_visible()
                assert fixture.launch_payloads == [{"user_macro_id": 1, "testnet": True}], fixture.launch_payloads
                assert page.evaluate("localStorage.getItem('ggparrot:runner-opened-version')") == "12"
                assert_no_overflow(page)
                context.close()
                checks += 1
                print(f"reuse ready {exchange} skipped download passed", flush=True)
            # Reuse 3: readiness must not bypass Binance's separate API attestation.
            context, page, fixture = windows_context(browser, "binance", errors, ready=True)
            page.goto(origin + "/runner?step=2", wait_until="networkidle")
            close_intro(page)
            expect(page.get_by_role("button", name="키를 준비한 뒤 확인해 주세요", exact=True)).to_be_disabled()
            assert fixture.launch_payloads == []
            page.get_by_role("checkbox").check()
            page.get_by_role("button", name="키 준비 확인 · 실행기 연결로 바로가기", exact=True).click()
            expect(page.locator("#runner-wizard-title")).to_contain_text("회원 키를")
            assert parse_qs(urlsplit(page.url).query)["step"] == ["4"]
            assert fixture.launch_payloads == [{"user_macro_id": 1, "testnet": True}], fixture.launch_payloads
            context.close()
            checks += 1
            print("reuse ready Binance still gated passed", flush=True)
            # Reuse 4: no browser record? The explicit manual confirmation remains
            # available, and is never represented as installation detection.
            context, page, fixture = windows_context(browser, "upbit", errors)
            page.goto(origin + "/runner?step=3", wait_until="networkidle")
            close_intro(page)
            page.get_by_role("button", name="이 PC에서 실행기를 이미 열었어요", exact=True).click()
            expect(page.locator("#runner-wizard-title")).to_contain_text("회원 키를")
            assert parse_qs(urlsplit(page.url).query)["step"] == ["4"]
            assert fixture.launch_payloads == []
            assert page.evaluate("localStorage.getItem('ggparrot:runner-opened-version')") == "12"
            page.get_by_role("button", name="이전 화면", exact=True).click()
            expect(page.get_by_text("사용자가 준비했다고 확인함 · 실제 설치·실행 여부를 감지한 것은 아님", exact=True)).to_be_visible()
            context.close()
            checks += 1
            print("reuse manual readiness is not detection passed", flush=True)
            # Reuse 5: the install page prefers connection over repeat download,
            # retaining download as an optional secondary action and recovery links.
            context, page, fixture = windows_context(browser, "upbit", errors, ready=True)
            page.goto(origin + "/runner/install", wait_until="networkidle")
            close_intro(page)
            fast = page.get_by_role("link", name="준비한 실행기로 빠른 연결", exact=True)
            expect(fast).to_have_class(re.compile(r"\bbtn-primary\b"))
            expect(page.get_by_role("link", name="실행기 내려받기", exact=True)).to_have_class(re.compile(r"\bbtn-secondary\b"))
            expect(page.get_by_text("이 브라우저에서 준비를 확인한 기록이에요. 실제 설치나 실행 중 여부를 감지한 것은 아니에요.", exact=True)).to_be_visible()
            page.get_by_role("link", name="빗썸 기존 키 안내", exact=True).click()
            guide = page.get_by_role("region", name="빗썸 단계별 연결 도우미")
            expect(guide.locator("h3")).to_have_text("기존 키로 실행기에서 검사하기")
            assert parse_qs(urlsplit(page.url).query) == {"exchange": ["bithumb"], "step": ["keys"]}
            assert "JWT" in guide.inner_text() and "연장은 안 돼요" in guide.inner_text()
            assert guide.locator("input").count() == 0
            assert fixture.launch_payloads == []
            assert_no_overflow(page)
            context.close()
            checks += 1
            print("reuse install primary and existing-key recovery passed", flush=True)
            browser.close()
        assert not errors, errors
        print(f"exchange connection browser: {checks} cases passed; page errors 0")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
