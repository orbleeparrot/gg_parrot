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
    before = page.locator('.studio-work').bounding_box()
    chart_before = page.locator('.studio-chart').bounding_box()
    saved = page.evaluate("localStorage.getItem('ggp_studio_condition_width')")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 180)
    page.mouse.down()
    for step in range(1,13):
        delta = (target-current)*step/12
        page.mouse.move(box["x"] + box["width"] / 2 + delta, box["y"] + 180)
        settle(page)
        panel = page.locator('.studio-cond').bounding_box()
        chart = page.locator('.studio-chart').bounding_box()
        work = page.locator('.studio-work').bounding_box()
        assert abs(panel['width']-current-delta) <= 1, (step,current,target,panel)
        assert abs(chart['height']-chart_before['height']) <= 1, (step,chart,chart_before)
        assert abs(work['y']-before['y']) <= 1 and abs(work['height']-before['height']) <= 1, (step,work,before)
        assert page.evaluate("localStorage.getItem('ggp_studio_condition_width')") == saved, 'Drag persisted before release'
    page.mouse.up()
    settle(page)
    assert not page.locator('.studio-work.is-resizing').count()


def check_collapse(page, checks):
    panel = page.locator('.studio-cond')
    divider = page.locator('.studio-splitter')
    reopen = page.get_by_role('button', name='조건 펼치기')
    values = page.locator('.builder-dense input,.builder-dense select').evaluate_all('nodes => nodes.map(n => [n.type,n.value,n.checked])')

    def start_drag():
        box = divider.bounding_box()
        width = float(divider.get_attribute('aria-valuenow'))
        x, y = box['x'] + box['width']/2, box['y'] + 180
        page.mouse.move(x,y); page.mouse.down()
        return lambda target: (page.mouse.move(x+target-width,y), settle(page))

    def assert_closed():
        expect(reopen).to_be_visible()
        expect(panel).to_have_attribute('inert','')
        expect(panel).to_have_attribute('aria-hidden','true')
        geometry = page.evaluate("""() => {
          const box = s => document.querySelector(s).getBoundingClientRect();
          const work=box('.studio-work'), panel=box('.studio-cond'), chart=box('.studio-chart'), button=box('.studio-conditions-reopen');
          return {panel:panel.width, chart:chart.width, work:work.width, center:button.y+button.height/2-work.y-work.height/2, overflow:document.documentElement.scrollWidth>innerWidth};
        }""")
        assert geometry['panel']==0 and abs(geometry['chart']+24-geometry['work'])<=1,geometry
        assert abs(geometry['center'])<=1 and not geometry['overflow'],geometry

    # Overshooting the minimum by up to 96px must still leave the panel open.
    drag_to(page,280)
    for target in (265,240,216,184):
        move = start_drag(); move(target)
        expect(divider).to_have_attribute('aria-valuenow','280')
        expect(reopen).to_be_hidden()
        page.mouse.up(); settle(page)
        expect(divider).to_have_attribute('aria-valuenow','280')
        expect(panel).not_to_have_attribute('inert','')
        assert page.evaluate("localStorage.getItem('ggp_studio_conditions_collapsed')") == 'false'
        assert page.evaluate("localStorage.getItem('ggp_studio_condition_width')") == '280'
    checks.append({'minimumWidthStableOnOvershoot':True,'collapseDeadZonePx':96})

    # Only a deliberate further drag collapses; reversing is possible before release.
    move = start_drag()
    move(184); expect(divider).to_have_attribute('aria-valuenow','280')
    expect(reopen).to_be_hidden()
    move(180); assert_closed()
    assert page.evaluate("localStorage.getItem('ggp_studio_conditions_collapsed')") == 'false'
    move(270); assert_closed()
    move(320); expect(divider).to_have_attribute('aria-valuenow','320')
    expect(panel).not_to_have_attribute('inert','')
    page.mouse.up(); settle(page)
    expect(divider).to_have_attribute('aria-valuenow','320')
    checks.append({'collapseThreshold':True,'reverseBeforeRelease':True})

    # A cancelled collapse preview restores the committed width and keyboard access.
    drag_to(page,560)
    move=start_drag(); move(180); assert_closed()
    divider.dispatch_event('pointercancel',{'pointerId':1})
    page.mouse.up(); settle(page)
    expect(divider).to_have_attribute('aria-valuenow','560')
    expect(panel).not_to_have_attribute('inert','')
    assert not page.locator('.studio-work.is-resizing').count()
    checks.append({'cancelCollapseRestoresWidth':True})

    # Commit collapse, restore with the centered button, then persist across navigation.
    move=start_drag(); move(180); page.mouse.up(); settle(page)
    assert_closed(); expect(reopen).to_be_focused()
    assert page.evaluate("localStorage.getItem('ggp_studio_condition_width')") == '560'
    assert page.evaluate("localStorage.getItem('ggp_studio_conditions_collapsed')") == 'true'
    reopen.click(); settle(page)
    expect(divider).to_have_attribute('aria-valuenow','560')
    expect(divider).to_be_focused()
    assert values == page.locator('.builder-dense input,.builder-dense select').evaluate_all('nodes => nodes.map(n => [n.type,n.value,n.checked])')
    checks.append({'restorePreviousWidth':True,'preserveInputsOnCollapse':True,'focusRestored':True})

    move=start_drag(); move(180); page.mouse.up(); settle(page)
    page.reload(); expect(page.locator('.studio-chart canvas').first).to_be_visible(); settle(page)
    assert_closed()
    reopen.evaluate('n=>n.blur()'); page.mouse.move(10,10)
    page.screenshot(path=str(OUTPUT/'conditions-collapsed.png'))
    page.set_viewport_size({'width':390,'height':844}); settle(page)
    expect(panel).to_be_visible(); expect(panel).not_to_have_attribute('inert','')
    expect(panel).not_to_have_attribute('aria-hidden','true')
    expect(reopen).to_be_hidden()
    assert not inspect(page)['overflow']
    page.set_viewport_size({'width':1440,'height':1000}); settle(page)
    assert_closed()
    reopen.focus(); reopen.press('Enter'); settle(page)
    expect(divider).to_have_attribute('aria-valuenow','560')
    checks.append({'collapsedReload':True,'mobileAlwaysShowsConditions':True,'keyboardRestore':True})

    divider.press('Home'); divider.press('ArrowLeft'); settle(page)
    assert_closed(); expect(reopen).to_be_focused()
    reopen.press('Enter'); settle(page)
    expect(divider).to_have_attribute('aria-valuenow','280')
    expect(panel).not_to_have_attribute('inert','')
    checks.append({'keyboardCollapse':True})


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

        # A click or rapid repeated clicks must never reset the split.
        drag_to(page,560)
        before_click=inspect(page)
        divider.click(); settle(page)
        assert inspect(page)==before_click, 'Click changed the layout'
        divider.dblclick(); settle(page)
        assert inspect(page)==before_click, 'Repeated clicks reset the split'
        checks.append({'clickStable':True,'repeatedClickStable':True,'continuousPointerTracking':True})
        # Keyboard, limits, persisted preference and window resizing.
        divider.focus()
        divider.press('Home'); expect(divider).to_have_attribute('aria-valuenow','280')
        divider.press('ArrowRight'); expect(divider).to_have_attribute('aria-valuenow','296')
        divider.press('Shift+ArrowRight'); expect(divider).to_have_attribute('aria-valuenow','344')
        divider.press('End'); expect(divider).to_have_attribute('aria-valuenow','680')
        divider.dblclick(); expect(divider).to_have_attribute('aria-valuenow','680')
        drag_to(page,560)
        page.reload(); expect(divider).to_have_attribute('aria-valuenow','560')
        page.set_viewport_size({'width':1100,'height':900}); settle(page)
        state=inspect(page)
        assert state['chart'] >= 480 and not state['overflow'],state
        page.set_viewport_size({'width':1440,'height':1000}); settle(page)
        expect(divider).to_have_attribute('aria-valuenow','560')
        check_collapse(page,checks)
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
        mobile.add_init_script("localStorage.setItem('ggp_theme','dark');localStorage.setItem('ggp_studio_condition_width','680');localStorage.setItem('ggp_studio_conditions_collapsed','true');")
        mobile.goto(BASE+'/builder')
        expect(mobile.locator('.studio-chart canvas').first).to_be_visible()
        expect(mobile.locator('.studio-cond')).to_be_visible()
        expect(mobile.locator('.studio-cond')).not_to_have_attribute('inert','')
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
