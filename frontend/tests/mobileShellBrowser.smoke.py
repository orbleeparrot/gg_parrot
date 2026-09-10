"""Mobile navigation, touch help, theme sync and responsive route smoke checks.

FRONTEND_BUILD=/tmp/mobile-build BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  python frontend/tests/mobileShellBrowser.smoke.py
Uses a local static server and fixture APIs only; external requests are blocked.
"""
import importlib.util
import json
import os
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location('news_fixture', Path(__file__).with_name('newsMobileBrowser.smoke.py'))
news = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news)
OUTPUT = Path(os.environ.get('MOBILE_SHELL_OUTPUT', '/tmp/ggp-mobile-shell-check'))
USER = {'id': 101, 'username': '모바일회원', 'email': 'mobile@example.com', 'points_balance': 12500, 'avatar_url': None}
IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36'

class Fixture(news.Fixture):
    def route(self, route):
        path = urlsplit(route.request.url).path
        if path == '/api/auth/me':
            route.fulfill(json={'user': USER})
        elif path == '/api/auth/google/config':
            route.fulfill(json={'enabled': False})
        else:
            super().route(route)


def bounds(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(news.Handler, directory=str(news.BUILD)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    checks, errors = [], []
    try:
        with sync_playwright() as p:
            launch = {'headless': True, 'args': ['--no-sandbox']}
            if os.environ.get('BROWSER_EXECUTABLE_PATH'): launch['executable_path'] = os.environ['BROWSER_EXECUTABLE_PATH']
            browser = p.chromium.launch(**launch)
            for width in (320, 390, 768, 1099, 1440):
                for theme in ('dark', 'light'):
                    mobile = width < 1100
                    context = browser.new_context(viewport={'width': width, 'height': 844}, has_touch=mobile, is_mobile=mobile,
                                                  user_agent=IPHONE if mobile else WINDOWS, color_scheme=theme)
                    context.route('**/*', Fixture().route)
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origin + '/guide', wait_until='networkidle')
                    bounds(page)
                    header = page.locator('.site-header')
                    expect(header.locator('.account-trigger')).to_be_visible()
                    if mobile:
                        expect(header.locator('.header-resources')).to_be_hidden()
                        expect(header.locator('.header-theme')).to_be_hidden()
                        # A touch click opens help once, then the next closes it.
                        header.get_by_role('button', name='시장 참고 지표 상세 열기').tap()
                        info = page.locator('#market-context-panel .info-trigger')
                        for index in range(3):
                            button = info.nth(index)
                            size = button.bounding_box()
                            assert size['height'] == size['width'] == 20, size
                            button.tap()
                            expect(button).to_have_attribute('aria-expanded', 'true')
                            tip = page.get_by_role('tooltip')
                            expect(tip).to_be_visible()
                            rect = tip.bounding_box()
                            assert rect['x'] >= 0 and rect['x'] + rect['width'] <= width, rect
                            button.tap()
                            expect(button).to_have_attribute('aria-expanded', 'false')
                        page.screenshot(path=str(OUTPUT / f'briefing-{width}-{theme}.png'), animations='disabled')
                        header.get_by_role('button', name='시장 참고 지표 상세 닫기').tap()
                        menu = header.get_by_role('button', name='페이지 메뉴 열기')
                        menu.tap()
                        drawer = page.get_by_role('dialog', name='모바일 페이지 메뉴')
                        expect(drawer).to_be_visible()
                        expect(page.locator('.site-frame')).to_have_attribute('inert', '')
                        for label in ('사용법', '실행기 설치'):
                            link = drawer.get_by_role('link', name=label, exact=False)
                            expect(link).to_be_visible()
                            assert link.bounding_box()['height'] >= 44
                        toggle = drawer.get_by_role('switch')
                        toggle.tap()
                        expect(toggle).to_have_attribute('aria-checked', 'false' if theme == 'dark' else 'true')
                        assert page.evaluate('document.documentElement.classList.contains("dark")') == (theme == 'light')
                        assert page.locator('.header-tooltip:visible').count() == 0
                        page.screenshot(path=str(OUTPUT / f'menu-{width}-{theme}.png'), animations='disabled')
                        drawer.get_by_role('link', name='실행기 설치', exact=False).tap()
                        expect(page).to_have_url(origin + '/runner/install')
                        expect(drawer).to_be_hidden()
                        expect(page.locator('.runner-device-copy')).to_be_visible()
                        bounds(page)
                        # Resizing an open drawer must never strand an inert desktop.
                        menu.tap()
                        page.set_viewport_size({'width': 1440, 'height': 900})
                        expect(page.locator('.site-mobile-drawer')).to_be_hidden()
                        expect(page.locator('.site-frame')).not_to_have_attribute('inert', '')
                        expect(page.locator('.site-header .header-theme')).to_have_attribute('aria-checked', 'false' if theme == 'dark' else 'true')
                        assert page.evaluate('document.body.style.overflow') != 'hidden'
                        page.set_viewport_size({'width': width, 'height': 844})
                    else:
                        expect(header.locator('.header-resources')).to_be_visible()
                        controls = header.locator('.header-resource').all()
                        assert controls[0].bounding_box()['width'] == controls[1].bounding_box()['width']
                    # Guest profile opens login directly on every breakpoint.
                    header.locator('.account-trigger').click()
                    expect(page).to_have_url(__import__('re').compile(r'/login\?next='))
                    checks.append(f'navigation-help-theme-guest-{width}-{theme}')
                    context.close()

            # Signed-in account popup stays in bounds; portrait + short landscape.
            for width, height in ((320, 740), (844, 390)):
                context = browser.new_context(viewport={'width': width, 'height': height}, has_touch=True, is_mobile=True, user_agent=IPHONE, color_scheme='dark')
                context.add_init_script("localStorage.setItem('ggp_token', 'fixture'); localStorage.setItem('ggp_user', " + json.dumps(json.dumps(USER)) + ");")
                context.route('**/*', Fixture().route)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/guide', wait_until='networkidle')
                page.locator('.account-trigger').tap()
                panel = page.get_by_role('dialog', name='계정 메뉴', exact=True)
                expect(panel).to_be_visible()
                box = panel.bounding_box()
                assert box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] + box['height'] <= height
                page.screenshot(path=str(OUTPUT / f'account-{width}.png'), animations='disabled')
                page.keyboard.press('Escape')
                expect(panel).to_be_hidden()
                page.get_by_role('button', name='페이지 메뉴 열기').tap()
                drawer = page.get_by_role('dialog', name='모바일 페이지 메뉴')
                drawer.get_by_role('switch').tap()  # must remain reachable on a short screen
                drawer.get_by_role('link', name='사용법', exact=True).tap()
                expect(drawer).to_be_hidden()  # even when navigating to the same URL
                for path in ('/', '/news', '/guide', '/runner/install', '/board', '/leaderboard', '/builder'):
                    page.goto(origin + path, wait_until='networkidle')
                    bounds(page)
                    if path not in ('/', '/builder'):
                        expect(page.locator('.site-marquee')).to_be_hidden()
                    if path == '/news':
                        expect(page.locator('.news-racer-map')).to_be_hidden()
                        expect(page.get_by_role('list', name='경주마 상승률 순위')).to_be_visible()
                checks.append(f'member-landscape-route-smoke-{width}')
                context.close()
            browser.close()
        assert not errors, errors
        (OUTPUT / 'report.json').write_text(json.dumps({'passed': True, 'checks': checks, 'errors': errors}, ensure_ascii=False, indent=2))
        print(json.dumps({'passed': True, 'checks': len(checks), 'report': str(OUTPUT / 'report.json')}))
    finally:
        server.shutdown()

if __name__ == '__main__':
    main()
