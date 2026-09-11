"""Real Studio regression: backtest limits never block live chart/strategy updates.

All APIs are intercepted. Run against local Vite via STUDIO_BUDGET_TEST_URL.
"""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'docs/chart-migration-preview/integration'
BASE = os.environ.get('STUDIO_BUDGET_TEST_URL', 'http://127.0.0.1:5182')
spec = importlib.util.spec_from_file_location('chart_fixtures', Path(__file__).with_name('chartMigrationBrowser.smoke.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    calls, chart_intervals, errors = [], [], []
    response_mode = {"success": False}
    checks = []
    def route(route):
        parsed = urlparse(route.request.url)
        if parsed.path == '/api/backtest' and route.request.method == 'POST':
            calls.append(route.request.post_data_json)
            if response_mode['success']:
                payload = {'result': {'initial_capital': 1000000, 'final_equity': 1030000, 'final_return_pct': 3, 'mdd_pct': 2, 'win_rate_pct': 60, 'total_trades': 10, 'buy_hold_return_pct': 2, 'sharpe': 1.1, 'profit_factor': 1.4, 'max_consecutive_losses': 2, 'equity_curve': [{'t':'2025-09-11','equity':1000000},{'t':'2026-09-11','equity':1030000}]}, 'per_symbol': [], 'human_summary': '검증용 결과', 'data_source': 'fixture', 'period_label': '최근 1년'}
                route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))
                return
            route.fulfill(status=400, content_type='application/json', body=json.dumps({'detail':'검증용 백테스트 오류'}))
            return
        if parsed.path == '/api/candles':
            chart_intervals.append(parse_qs(parsed.query)['interval'][0])
        fixtures.route_handler(route)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=fixtures.CHROMIUM, headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':960})
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.route('**/*',route)
        page.goto(BASE+'/builder')
        chart = page.locator('.studio-chart')
        canvas = chart.locator('canvas').first
        expect(canvas).to_be_visible()
        expect(page.locator('.studio-budget')).to_contain_text('365개')
        interval = page.locator('[data-tour="interval"] select')
        period = page.locator('[data-tour="period"] select')
        run = page.locator('.studio-cond-foot > button')
        for value,label,count in [('1m','1분','525,600'),('5m','5분','105,120')]:
            chart.get_by_role('button',name=label,exact=True).click()
            expect(interval).to_have_value(value)
            expect(period).to_have_value('1y')
            expect(page.locator('.studio-budget')).to_contain_text(count+'개')
            expect(run).to_be_disabled()
            expect(canvas).to_be_visible()
            expect(chart.get_by_text('익절선',exact=True)).to_be_visible()
            expect(chart.get_by_text('손절선',exact=True)).to_be_visible()
            page.keyboard.press('Control+Enter')
            page.wait_for_timeout(150)
            assert not calls, calls
            assert value in chart_intervals, chart_intervals
        checks.append('1년·1분/5분 한도 초과에도 해당 봉 간격의 차트와 전략선 유지; 백테스트 요청 없음')
        chart.get_by_role('button',name='1시간',exact=True).click()
        expect(interval).to_have_value('1h')
        expect(page.locator('.studio-budget')).to_contain_text('8,760개')
        expect(run).to_be_enabled()
        run.click()
        expect(page.get_by_text('오류: 검증용 백테스트 오류',exact=True)).to_be_visible()
        assert calls[-1]['macro']['candle_interval']=='1h'
        assert calls[-1]['macro']['period']['preset']=='1y'
        expect(canvas).to_be_visible()
        expect(chart.get_by_text('익절선',exact=True)).to_be_visible()
        checks.append('1시간봉은 선택한 1년 그대로 요청; 백테스트 서버 오류 후에도 차트 유지')
        chart.get_by_role('button',name='1분',exact=True).click()
        expect(page.locator('.studio-budget')).to_contain_text('525,600개')
        before=len(calls)
        page.get_by_role('button',name='1분봉 유지 · 최근 1주로 변경',exact=True).click()
        expect(interval).to_have_value('1m')
        expect(period).to_have_value('1w')
        expect(page.locator('.studio-budget')).to_contain_text('10,080개')
        expect(run).to_be_enabled()
        assert len(calls)==before
        run.click()
        expect(page.get_by_text('오류: 검증용 백테스트 오류',exact=True)).to_be_visible()
        assert calls[-1]['macro']['candle_interval']=='1m'
        assert calls[-1]['macro']['period']['preset']=='1w'
        checks.append('사용자가 누른 뒤에만 1분봉 유지·최근 1주 적용; 원래 봉 간격으로 요청')
        period.select_option('1y')
        page.get_by_role('button',name='기간 유지 · 1시간봉으로 변경',exact=True).click()
        expect(interval).to_have_value('1h')
        expect(period).to_have_value('1y')
        expect(canvas).to_be_visible()
        checks.append('기간 유지 선택은 1년을 그대로 두고 봉 간격만 명시적으로 변경')
        response_mode['success'] = True
        run.click()
        expect(page.locator('.sd-bt')).to_be_visible()
        before = len(calls)
        for label in ('1분', '5분'):
            chart.get_by_role('button', name=label, exact=True).click()
            expect(run).to_be_disabled()
            page.wait_for_timeout(900)  # Exceeds the actual 700ms automatic backtest debounce.
            assert len(calls) == before
            expect(canvas).to_be_visible()
            expect(chart.get_by_text('익절선', exact=True)).to_be_visible()
        chart.get_by_role('button', name='1시간', exact=True).click()
        expect(run).to_be_enabled()
        checks.append('성공 결과가 있어 자동 테스트가 켜져 있어도 한도 초과 전환은 차트만 갱신')
        page.screenshot(path=str(OUTPUT/'studio-backtest-budget-desktop.png'))
        page.set_viewport_size({'width':390,'height':844})
        chart.get_by_role('button',name='1분',exact=True).click()
        expect(canvas).to_be_visible()
        expect(page.locator('.studio-budget')).to_contain_text('525,600개')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.locator('.studio-cond-foot').screenshot(path=str(OUTPUT/'studio-backtest-budget-mobile.png'))
        checks.append('390px 모바일에서 한도 및 변경 버튼 줄바꿈, 가로 넘침 없음')
        assert not errors, errors
        browser.close()
    report={'passed':len(checks),'checks':checks,'backtest_requests':calls,'chart_intervals':chart_intervals,'page_errors':errors}
    (OUTPUT/'studio-backtest-budget-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':len(checks),'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
