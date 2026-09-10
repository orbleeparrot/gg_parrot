"""Header-to-profile navigation, member key controls and profile logout.

FRONTEND_BUILD and BROWSER_EXECUTABLE_PATH select a local build and Chromium.
PROFILE_ACCOUNT_OUTPUT selects screenshots/reports. All APIs use local fixtures.
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

spec = importlib.util.spec_from_file_location('profile', Path(__file__).with_name('profileAvatarBrowser.smoke.py'))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
OUTPUT = Path(os.environ.get('PROFILE_ACCOUNT_OUTPUT', '/tmp/ggp-profile-account-check'))

class Fixture(profile.Fixtures):
    def __init__(self):
        super().__init__()
        self.key_calls = []
        self.generation = 0
        self.fail_key = False
        self.hold_key = False
        self.held_keys = []

    def route(self, route):
        path = urlsplit(route.request.url).path
        if path not in ('/api/me/runner/key', '/api/me/runner/key/regenerate'):
            return super().route(route)
        token = route.request.headers.get('authorization', '').removeprefix('Bearer ')
        self.key_calls.append((path, token))
        if self.fail_key:
            route.fulfill(status=503, json={'detail': 'Fixture unavailable'})
            return
        if path.endswith('/regenerate'):
            assert route.request.method == 'POST'
            self.generation += 1
        payload = {'key': f'fixture-only-{token}-key-{self.generation}'}
        if self.hold_key:
            self.held_keys.append((route, payload))
        else:
            route.fulfill(json=payload)

def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    profile.OUTPUT = OUTPUT
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(profile.Handler, directory=str(profile.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ['BROWSER_EXECUTABLE_PATH'], args=['--no-sandbox'])
            suite = profile.Suite(browser, f'http://127.0.0.1:{server.server_port}')
            for width in (320, 390, 1440):
                for theme in ('light', 'dark'):
                    fixture = Fixture()
                    context, page = suite.open(fixture, width, theme, '/mypage/settings')
                    header = page.locator('.account-trigger')
                    expect(header).to_have_attribute('href', '/mypage')
                    header.click()
                    expect(page).to_have_url(re.compile(r'/mypage$'))
                    expect(page.get_by_role('dialog', name='계정 메뉴')).to_have_count(0)
                    toggle = page.get_by_role('button', name='회원 키', exact=True)
                    expect(toggle).to_be_visible()
                    assert not fixture.key_calls
                    suite.screenshot(page, f'profile-{width}-{theme}')
                    toggle.click()
                    key = page.get_by_label('회원 키', exact=True)
                    expect(key).to_have_attribute('type', 'password')
                    expect(key).to_have_value('fixture-only-fixture-a-key-0')
                    page.get_by_role('button', name='보기', exact=True).click()
                    expect(key).to_have_attribute('type', 'text')
                    page.get_by_role('button', name='숨기기', exact=True).click()
                    page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.fixtureCopied=value}}})")
                    page.get_by_role('button', name='복사', exact=True).click()
                    expect(page.get_by_role('button', name='복사됨', exact=True)).to_be_visible()
                    assert page.evaluate('window.fixtureCopied') == 'fixture-only-fixture-a-key-0'
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    page.get_by_role('button', name='재발급', exact=True).click()
                    assert fixture.generation == 0
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.get_by_role('button', name='재발급', exact=True).click()
                    expect(key).to_have_value('fixture-only-fixture-a-key-1')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    suite.screenshot(page, f'member-key-{width}-{theme}')
                    page.get_by_role('button', name='로그아웃', exact=True).click()
                    expect(page).to_have_url(re.compile(r'/login$'))
                    assert page.evaluate("localStorage.getItem('ggp_token')") is None
                    expect(page.locator('#me-member-key')).to_have_count(0)
                    suite.record(f'header-key-logout-{width}-{theme}', fixture)
                    context.close()

            fixture = Fixture(); fixture.fail_key = True
            context, page = suite.open(fixture, 390)
            toggle = page.get_by_role('button', name='회원 키', exact=True)
            toggle.click()
            expect(page.locator('#me-member-key [role="alert"]')).to_be_visible()
            toggle.click(); fixture.fail_key = False; toggle.click()
            key = page.get_by_label('회원 키', exact=True)
            expect(key).to_have_attribute('type', 'password')
            page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('denied')}}})")
            page.get_by_role('button', name='복사', exact=True).click()
            expect(key).to_have_attribute('type', 'text')
            expect(page.locator('#me-member-key [role="alert"]')).to_contain_text('직접 복사')
            suite.record('key-fetch-retry-and-clipboard-fallback', fixture); context.close()

            fixture = Fixture(); fixture.hold_key = True
            context, page = suite.open(fixture, 390)
            page.get_by_role('button', name='회원 키', exact=True).click()
            expect(page.locator('#me-member-key [role="status"]')).to_be_visible()
            page.evaluate("user=>{localStorage.setItem('ggp_token','fixture-b');localStorage.setItem('ggp_user',JSON.stringify(user));window.dispatchEvent(new StorageEvent('storage',{key:'ggp_token'}))}", fixture.users['fixture-b'])
            expect(page.locator('.me-name')).to_have_text('다른회원')
            expect(page.locator('#me-member-key')).to_have_count(0)
            fixture.hold_key = False
            for route, payload in fixture.held_keys: route.fulfill(json=payload)
            page.get_by_role('button', name='회원 키', exact=True).click()
            expect(page.get_by_label('회원 키', exact=True)).to_have_value('fixture-only-fixture-b-key-0')
            suite.record('late-key-response-cannot-cross-accounts', fixture); context.close()
            browser.close()
            (OUTPUT / 'report.json').write_text(json.dumps({'passed': True, 'checks': suite.checks, 'page_errors': suite.errors}, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()

if __name__ == '__main__':
    main()
