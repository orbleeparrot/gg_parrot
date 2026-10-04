"""프로 빌더(/builder/pro) 화면 회귀 — 두 검증 API 를 가로채 화면이 실제로 부르고 그리는지 본다.

파일 내용만 보는 단위 시험은 빈 컴포넌트도 통과시키므로, 이 시험이 렌더 수준의 증거다.
로컬 Vite 에 붙인다: STUDIO_PRO_TEST_URL (기본 http://127.0.0.1:5173).
브라우저는 BROWSER_EXECUTABLE_PATH 가 있으면 그것을, 없으면 playwright 기본 크로미움을 쓴다.
"""
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('STUDIO_PRO_TEST_URL', 'http://127.0.0.1:5173')
CHROMIUM = os.environ.get('BROWSER_EXECUTABLE_PATH') or None
spec = importlib.util.spec_from_file_location('chart_fixtures', Path(__file__).with_name('chartMigrationBrowser.smoke.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

WINDOWS = [
    {'index': 1, 'start': '2025-10-01', 'end': '2025-12-31', 'return_pct': 0, 'trades': 2},
    {'index': 2, 'start': '2026-01-01', 'end': '2026-03-31', 'return_pct': None, 'trades': 0, 'error': 'ValueError'},
]


def validate_payload(**over):
    body = {
        'result': {'final_return_pct': 12.3, 'buy_hold_return_pct': 5.5, 'mdd_pct': 9.1, 'total_trades': 14,
                   'sharpe': 1.7, 'profit_factor': 1.9, 'max_consecutive_losses': 3, 'top_trade_share_pct': 41.2},
        'monthly': [{'month': '2025-11', 'pct': 3.2}],
        'concentration': {'top_month_share_pct': 64.5, 'months': 3},
        'drawdown': {'start': '2025-11-03T00:00:00', 'trough': '2025-12-01T00:00:00', 'recovered': None,
                     'depth_pct': 9.1, 'recovery_days': None},
        'sortino': None, 'calmar': None,
        'windows': WINDOWS, 'warnings': ['한_구간_집중', '모르는_코드'],
    }
    body.update(over)
    return body


EVIDENCE = [
    {'date': '2025-11-12', 'change_pct': -8.2, 'volume_ratio': 2.1, 'btc_change_pct': -3.0, 'verdict': '종목 요인',
     'headlines': {'items': [], 'found': False, 'reason': '보존_범위_밖'}},
    {'date': '2025-12-01', 'change_pct': 6.4, 'volume_ratio': 1.4, 'btc_change_pct': 2.0, 'verdict': '시장 요인',
     'headlines': {'items': [{'title': '서버가 보낸 기사', 'url': 'https://example.com/a', 'source': '예시일보'}],
                   'found': True, 'reason': ''}},
]


def main():
    state = {'source': 'fallback', 'hold_validate': False}
    validate_calls, explain_calls, held, errors = [], [], [], []
    validate_response = {}

    def route(route):
        parsed = urlparse(route.request.url)
        if parsed.path == '/api/validate' and route.request.method == 'POST':
            validate_calls.append(route.request.post_data_json)
            if state['hold_validate']:
                held.append(route)
                return
            route.fulfill(status=200, content_type='application/json', body=json.dumps(validate_response['body']))
            return
        if parsed.path == '/api/validate/explain' and route.request.method == 'POST':
            explain_calls.append(route.request.post_data_json)
            route.fulfill(status=200, content_type='application/json', body=json.dumps(
                {'text': '검증용 해설이에요.', 'source': state['source'], 'evidence': EVIDENCE}))
            return
        fixtures.route_handler(route)

    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROMIUM, headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', route)

        def dismiss_note():
            page.wait_for_timeout(400)
            if page.locator('.devnote, [role="dialog"]').count():
                page.keyboard.press('Escape')

        def run_validation():
            page.get_by_role('button', name='검증하기', exact=True).click()

        # 1. 요청 모양 — buildMacro 출력 + windows 4, 해설에는 검증 응답이 summary 로 간다.
        validate_response['body'] = validate_payload()
        page.goto(BASE + '/builder/pro')
        dismiss_note()
        run_validation()
        expect(page.locator('.pro-analysis')).to_contain_text('검증용 해설이에요.')
        assert len(validate_calls) == 1 and len(explain_calls) == 1, (validate_calls, explain_calls)
        sent = validate_calls[0]
        assert sent['windows'] == 4, sent
        macro = sent['macro']
        assert macro['exchange'] == 'binance' and macro['symbol'] == 'BTCUSDT' and macro['rule_type'] == 'A', macro
        assert macro['period']['preset'] == '1y' and macro['quote_currency'] == 'USDT', macro
        assert explain_calls[0]['macro'] == macro
        assert explain_calls[0]['summary'] == validate_response['body'], explain_calls[0]['summary']
        checks.append('검증하기 → /api/validate(macro, windows=4) → /api/validate/explain(summary=검증 응답)')

        # 2. 0% 구간과 실패 구간, 못 잰 지표.
        report = page.locator('.pro-report')
        report_text = report.inner_text()
        assert 'null' not in report_text and 'undefined' not in report_text and 'NaN' not in report_text, report_text
        zero_row, failed_row = report.locator('.pro-window').nth(0), report.locator('.pro-window').nth(1)
        expect(zero_row).to_contain_text('0.00%')
        assert zero_row.locator('.pro-window-track i').count() == 1, '진짜 0% 는 막대를 그린다'
        expect(failed_row).to_contain_text('데이터 없음')
        assert failed_row.locator('.pro-window-track i').count() == 0, '실패한 구간에는 막대가 없다'
        assert not re.search(r'(?<![\d.])0\.00%', failed_row.inner_text()), failed_row.inner_text()
        assert len(re.findall(r'(?<![\d.])0\.00%', report_text)) == 1, report_text
        for label in ('소르티노', '칼마'):
            value = report.locator('.pro-metric', has_text=label).locator('dd')
            expect(value).to_have_text('—')
        expect(report.locator('.pro-metric', has_text='샤프').locator('dd')).to_have_text('1.70')
        expect(report.locator('.pro-metric', has_text='회복').locator('small')).to_contain_text('아직')
        expect(report.locator('.pro-warnings li')).to_have_count(1)
        checks.append('0% 구간은 0.00% + 막대, 실패 구간은 데이터 없음(막대·null·0.00% 없음), 소르티노·칼마는 —, 모르는 경고 코드는 숨김')

        # 3. 라벨 — 폴백은 자동 요약, 정확히 ai 일 때만 AI 분석.
        expect(page.locator('.pro-analysis h3')).to_have_text('자동 요약')
        assert 'AI 분석' not in page.content()
        state['source'] = 'ai'
        run_validation()
        expect(page.locator('.pro-analysis h3')).to_have_text('AI 분석')
        state['source'] = 'something-else'
        run_validation()
        expect(page.locator('.pro-analysis h3')).to_have_text('자동 요약')
        assert 'AI 분석' not in page.content()
        checks.append('source=fallback/모르는 값 → 자동 요약(DOM 에 AI 분석 없음), source=ai → AI 분석')

        # 4. 근거 펼침.
        first = page.locator('.pro-evidence button').nth(0)
        expect(first).to_have_attribute('aria-expanded', 'false')
        first.click()
        expect(first).to_have_attribute('aria-expanded', 'true')
        body = page.locator('.pro-evidence-body').first
        expect(body).to_contain_text('찾지 못했')
        assert body.locator('a').count() == 0
        first.click()
        expect(first).to_have_attribute('aria-expanded', 'false')
        second = page.locator('.pro-evidence button').nth(1)
        second.click()
        link = page.locator('.pro-evidence-body a')
        expect(link).to_have_count(1)
        expect(link).to_have_attribute('href', 'https://example.com/a')
        expect(page.locator('.pro-evidence-body')).not_to_contain_text('찾지 못했')
        checks.append('근거 행 aria-expanded 토글, found:false/보존_범위_밖 은 찾지 못했 + 링크 없음, found:true 는 서버가 준 링크만')

        # 5. 검증이 도는 중에 조건을 고치면 도착한 결과는 지난 결과로 표시된다.
        state['hold_validate'] = True
        run_validation()
        expect(page.get_by_role('button', name='검증 중…')).to_be_visible()
        page.get_by_placeholder(re.compile('BTCUSDT 또는')).fill('ETHUSDT')
        assert held, '검증 요청이 아직 서버에 걸려 있어야 한다'
        held.pop().fulfill(status=200, content_type='application/json', body=json.dumps(validate_response['body']))
        expect(page.get_by_role('button', name='검증하기', exact=True)).to_be_enabled()
        expect(page.locator('.pro-stale')).to_contain_text('조건을 바꿨어요')
        state['hold_validate'] = False
        run_validation()
        expect(page.locator('.pro-stale')).to_have_count(0)
        assert validate_calls[-1]['macro']['symbol'] == 'ETHUSDT', validate_calls[-1]['macro']
        checks.append('검증 중 조건 변경 → 도착한 결과에 조건을 바꿨어요 표시, 다시 검증하면 사라짐')

        # 6. 검증 요청이 실패하면 서버 문구를 보이고 버튼은 다시 눌린다. 포트폴리오는 보내지 않는다.
        before = len(validate_calls)
        page.get_by_placeholder(re.compile('BTCUSDT 또는')).fill('BTCUSDT, ETHUSDT')
        run_validation()
        expect(page.locator('.pro-error')).to_contain_text('종목 하나로')
        assert len(validate_calls) == before, '포트폴리오 매크로는 요청을 보내지 않는다'
        checks.append('여러 종목은 요청 없이 안내 문구')

        # 7. 기본 빌더의 프로로 열기 — 지금 조건이 따라온다.
        page.goto(BASE + '/builder')
        dismiss_note()
        page.locator('[data-tour="interval"] select').select_option('1h')
        page.locator('.studio-mode-btn').click()
        page.get_by_role('menuitem', name=re.compile('프로로 열기')).click()
        page.wait_for_url('**/builder/pro')
        expect(page.locator('[data-field="candle_interval"] select, select').filter(has=page.locator('option[value="1h"]')).first).to_have_value('1h')
        run_validation()
        expect(page.locator('.pro-report')).to_be_visible()
        assert validate_calls[-1]['macro']['candle_interval'] == '1h', validate_calls[-1]['macro']

        # 8. 손댄 · 낡은 history.state 로 열어도 화면이 비지 않는다(새로고침해도 state 가 남는다).
        for bad in ({}, [], {'rule_type': 'Z'}):
            errors.clear()
            page.goto(BASE + '/builder/pro')
            page.evaluate('macro => window.history.replaceState({usr: {macro}, key: "bad", idx: 0}, "")', bad)
            page.reload()
            expect(page.get_by_role('button', name='검증하기', exact=True)).to_be_visible()
            assert not errors, (bad, errors)
        checks.append('손댄 history.state({} · [] · rule_type Z) 로 새로고침해도 프로 빌더가 그려지고 기본 조건으로 시작')
        browser.close()
        checks.append('/builder 의 프로로 열기 → /builder/pro, 고른 봉 간격(1h)이 따라와 검증 요청에 실린다')
    assert not errors, errors
    print('\n'.join('OK ' + c for c in checks))


if __name__ == '__main__':
    main()
