"""Exercise the real Studio divider and container layout with intercepted APIs."""
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("STUDIO_SPLIT_TEST_URL", "http://127.0.0.1:5178")
OUTPUT = ROOT / "docs/ui-checks/studio-split"
spec = importlib.util.spec_from_file_location("fixtures", Path(__file__).with_name("chartMigrationBrowser.smoke.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def settle(page):
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def inspect(page):
    return page.evaluate("""() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const builder = document.querySelector('.builder-dense');
      const columns = getComputedStyle(builder.querySelector('.bd-grid')).gridTemplateColumns.split(' ').length;
      const money = builder.querySelector('input[aria-label="시작 자금 (USDT)"]')?.closest('.bd-field').querySelector('.bd-hint');
      const b = builder.getBoundingClientRect();
      const fields = [...builder.querySelectorAll('.field')].filter(n => n.getBoundingClientRect().height);
      return {
        panel: box('.studio-cond').width, chart: box('.studio-chart').width,
        work: box('.studio-work').width, columns,
        moneyLines: money ? Math.round(money.getBoundingClientRect().height / parseFloat(getComputedStyle(money).lineHeight)) : 0,
        overflow: document.documentElement.scrollWidth > innerWidth || fields.some(n => {const r=n.getBoundingClientRect(); return r.left < b.left-1 || r.right > b.right+1}),
        chartCanvas: document.querySelector('.studio-chart canvas')?.getBoundingClientRect().width,
      };
    }""")


def drag_to(page, target):
    divider = page.get_by_role("separator", name="조건 패널 너비 조절")
    box = divider.bounding_box()
    current = float(divider.get_attribute("aria-valuenow"))
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 180)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + target - current, box["y"] + 180, steps=12)
    page.mouse.up()
    settle(page)
    assert not page.locator('.studio-work.is-resizing').count()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    checks, errors, backtests = [], [], []
    def route(request):
        if urlparse(request.request.url).path == '/api/backtest':
            backtests.append(request.request.post_data_json)
            payload = {'result': {'initial_capital': 1000000, 'final_equity': 1030000, 'final_return_pct': 3, 'mdd_pct': 2, 'win_rate_pct': 60, 'total_trades': 10, 'buy_hold_return_pct': 2, 'sharpe': 1.1, 'profit_factor': 1.4, 'max_consecutive_losses': 2, 'equity_curve': [{'t':'2025-09-11','equity':1000000},{'t':'2026-09-11','equity':1030000}]}, 'per_symbol': [], 'human_summary': '검증용 결과', 'data_source': 'fixture', 'period_label': '최근 1년'}
            request.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        else:
            fixtures.route_handler(request)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=fixtures.CHROMIUM, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440, 'height':1000})
        page.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('ggp_theme','dark');localStorage.setItem('ggp_token','fixture');")
        page.goto(BASE+'/builder')
        expect(page.locator('.studio-chart canvas').first).to_be_visible()
        page.evaluate('() => document.fonts.ready')
        form_before = page.locator('.builder-dense input,.builder-dense select').evaluate_all('nodes => nodes.map(n => [n.type,n.value,n.checked])')
        divider = page.get_by_role('separator', name='조건 패널 너비 조절')
        for target, columns in [(280,1), (400,1), (460,2), (560,2), (680,2), (336,1)]:
            drag_to(page, target)
            state = inspect(page)
            assert abs(state['panel']-target) <= 1 and state['columns'] == columns, state
            assert not state['overflow'] and state['chart'] >= 480 and state['chartCanvas'] > 200, state
            assert abs(state['panel']+state['chart']+1-state['work']) < 1, state
            assert state['moneyLines'] == 1, state
            checks.append({'target':target, **state})
            if target in (336,560):
                page.locator('.studio-cond-body').evaluate('n=>n.scrollTop=0')
                divider.evaluate('n=>n.blur()'); page.mouse.move(10,10)
                page.screenshot(path=str(OUTPUT/f'conditions-{target}.png'))
        form_after = page.locator('.builder-dense input,.builder-dense select').evaluate_all('nodes => nodes.map(n => [n.type,n.value,n.checked])')
        assert form_before == form_after, 'Resizing changed form values'

        # Keyboard, limits, reset, persisted preference and window resizing.
        divider.focus()
        divider.press('Home'); expect(divider).to_have_attribute('aria-valuenow','280')
        divider.press('ArrowRight'); expect(divider).to_have_attribute('aria-valuenow','296')
        divider.press('Shift+ArrowRight'); expect(divider).to_have_attribute('aria-valuenow','344')
        divider.press('End'); expect(divider).to_have_attribute('aria-valuenow','680')
        divider.dblclick(); expect(divider).to_have_attribute('aria-valuenow','336')
        drag_to(page,560)
        page.reload(); expect(divider).to_have_attribute('aria-valuenow','560')
        page.set_viewport_size({'width':1100,'height':900}); settle(page)
        state=inspect(page)
        assert state['chart'] >= 480 and not state['overflow'],state
        page.set_viewport_size({'width':1440,'height':1000}); settle(page)
        expect(divider).to_have_attribute('aria-valuenow','560')
        for width in (280,460,680):
            drag_to(page,width)
            for rule in 'ABCDEFGHIJK':
                page.get_by_label('매매 방식',exact=True).select_option(rule)
                while page.locator('.builder-dense details:not([open]) summary').count():
                    page.locator('.builder-dense details:not([open]) summary').first.click()
                settle(page)
                state=inspect(page)
                assert not state['overflow'],(rule,width,state)
                checks.append({'rule':rule,'target':width,**state})
        assert not backtests, 'Layout changes triggered a backtest'
        page.get_by_label('매매 방식',exact=True).select_option('A')
        page.locator('.studio-cond-foot > button').click()
        expect(page.locator('.sd-kpis')).to_be_visible()
        for width, kpi_columns in [(280,6),(680,3)]:
            drag_to(page,width)
            assert page.locator('.sd-kpis').evaluate("n=>getComputedStyle(n).gridTemplateColumns.split(' ').length") == kpi_columns
            assert not inspect(page)['overflow']
            if width==680:
                geometry=page.locator('.sd-two').evaluate("""n=>{
                  const chart=n.querySelector('.sd-eq').getBoundingClientRect();
                  const caption=n.querySelector('.equity-caption').getBoundingClientRect();
                  const table=n.querySelector('.sd-table').getBoundingClientRect();
                  return {height:chart.height,bottom:chart.bottom,captionBottom:caption.bottom,tableTop:table.top};
                }""")
                assert geometry['height']>=220 and geometry['captionBottom']<=geometry['bottom']+1 and geometry['tableTop']>=geometry['bottom'], geometry
        assert len(backtests)==1, 'Resizing reran an existing result'
        # A cancelled pointer gesture must release the resizing state.
        box=divider.bounding_box()
        page.mouse.move(box['x'],box['y']+180); page.mouse.down()
        divider.dispatch_event('pointercancel',{'pointerId':1})
        page.mouse.up()
        assert not page.locator('.studio-work.is-resizing').count()
        page.evaluate("document.documentElement.classList.remove('dark')")
        drag_to(page,560)
        page.locator('.studio-cond-body').evaluate('n=>n.scrollTop=0')
        divider.evaluate('n=>n.blur()'); page.mouse.move(10,10)
        page.screenshot(path=str(OUTPUT/'conditions-result-light.png'))
        checks.append({'resultReflow':True,'noRepeatBacktest':True,'pointerCancel':True})
        page.close()

        # On phones the divider is absent and conditions remain in the page flow.
        mobile=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        mobile.route('**/*', fixtures.route_handler)
        mobile.on('pageerror',lambda error:errors.append(str(error)))
        mobile.add_init_script("localStorage.setItem('ggp_theme','dark');localStorage.setItem('ggp_studio_condition_width','680');")
        mobile.goto(BASE+'/builder')
        expect(mobile.locator('.studio-chart canvas').first).to_be_visible()
        expect(mobile.get_by_role('separator',name='조건 패널 너비 조절')).to_have_count(0)
        for width in (320,390,768):
            mobile.set_viewport_size({'width':width,'height':844}); settle(mobile)
            state=inspect(mobile)
            assert state['columns']==(2 if width==768 else 1) and not state['overflow'] and state['moneyLines']==1,state
            checks.append({'mobile':width,**state})
        mobile.set_viewport_size({'width':390,'height':844})
        mobile.locator('.studio-cond').evaluate('n=>n.scrollIntoView({block:"start"})')
        mobile.evaluate('window.scrollBy(0,-116)')
        mobile.screenshot(path=str(OUTPUT/'conditions-mobile.png'))
        browser.close()
    assert not errors,errors
    (OUTPUT/'report.json').write_text(json.dumps({'passed':True,'checks':checks,'errors':errors},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'cases':len(checks),'errors':errors}))


if __name__ == '__main__':
    main()
