"""Run with a local Vite server; all API responses and credentials are fixtures."""
import json,time,os
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

phase={'stopped':False,'news':0,'sessions':0,'stop_requests':0}
now=int(time.time()*1000)
user={'id':1,'username':'브라우저 검증','email':'browser@example.invalid'}
macro={'symbol':'LINKUSDT','position_side':'long','rule_type':'A','candle_interval':'1m','params':{'take_profit_pct':3,'initial_capital':1000},'risk':{'stop_loss_pct':2,'invest_ratio':.5}}
session={'session_id':41,'user_macro_id':1,'symbol':'LINKUSDT','status':'running','connected':True,'in_position':True,'position_side':'long','market':'spot','testnet':True,'entry_price':100,'last_price':97,'position_qty':2,'unrealized_pct':-3,'macro':macro,'started_kst':'09/07 12:00','stopping':False}

def route_handler(route):
    path=urlparse(route.request.url).path
    if '/api/' not in path:
        if urlparse(route.request.url).hostname in ('127.0.0.1','localhost'):
            route.continue_()
        else:
            route.abort()
        return
    status=200
    payload={}
    if path.endswith('/auth/me'):
        payload={'user':user}
    elif path.endswith('/runner/sessions/stream-token'):
        status,payload=503,{'detail':'Simulated WebSocket outage'}
    elif path.endswith('/runner/sessions'):
        phase['sessions']+=1
        final={**session,'status':'stopped','connected':False,'in_position':False,'position_qty':0,'realized_pnl':10,'note':'청산 완료 후 종료'}
        payload={'active':[] if phase['stopped'] else [session],'recent':[final] if phase['stopped'] else [],'poll_seconds':3}
    elif path.endswith('/request-stop'):
        assert route.request.post_data_json['mode']=='close_and_stop'
        phase['stop_requests']+=1
        phase['stopped']=True
        payload={'ok':True}
    elif path.endswith('/position-news'):
        phase['news']+=1
        pending=phase['news']==1
        payload={'context':{'session_id':41,'asset_symbol':'LINK','coin_name':'체인링크'},'analysis_status':'pending' if pending else 'ready','collection':{'status':'pending' if pending else 'ready','freshness':'fresh'},'items':[] if pending else [{'id':'link-news','title':'체인링크 네트워크 업데이트 발표','source':'검증 RSS','published':now,'position_effect':'neutral','summary':'새 티커 뉴스 도착 검증'}]}
    elif path.endswith('/whale-activity'):
        payload={'status':'ready','symbol':'LINKUSDT','market':'spot','quote_asset':'USDT','items':[{'id':'spot:LINKUSDT:7','side':'buy','notional':150000,'quantity':1500,'price':100,'occurred_at':now}]}
    elif path.endswith('/candles') or path.endswith('/candles/live'):
        bars=[{'t':now-(30-i)*60000,'o':100,'h':103,'l':96,'c':97,'v':1000,'closed':True} for i in range(30)]
        payload={'candles':bars,'server_time':now,'refresh_seconds':30,'source':'fixture','data_source':'fixture','market':'spot'}
    elif path.endswith('/hot-coins'):
        payload={'items':[]}
    route.fulfill(status=status,content_type='application/json',body=json.dumps(payload,ensure_ascii=False))

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ.get('BROWSER_EXECUTABLE_PATH'),args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/*',route_handler)
    page.add_init_script('localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'+json.dumps(json.dumps(user,ensure_ascii=False))+');')
    page.goto('http://127.0.0.1:5178/agents')
    page.wait_for_load_state('networkidle')
    expect(page.get_by_text('체인링크 네트워크 업데이트 발표',exact=True)).to_be_visible(timeout=10000)
    expect(page.get_by_text('평가손실 주의',exact=True)).to_be_visible()
    expect(page.get_by_text('LINKUSDT 대규모 매수 체결',exact=True)).to_be_visible()
    expect(page.get_by_text('실시간 연결 복구 중 · 5초마다 실행 상태 확인',exact=True)).to_be_visible()
    page.screenshot(path='/tmp/gg-parrot-agent-running.png',full_page=True)
    # Let fallback polling run with the websocket unavailable.
    page.wait_for_timeout(5200)
    assert phase['sessions']>=2,phase
    page.on('dialog',lambda dialog:dialog.accept())
    page.get_by_role('button',name='청산 후 종료',exact=True).click()
    expect(page.get_by_text('청산 완료 후 종료',exact=True)).to_be_visible(timeout=10000)
    expect(page.get_by_role('button',name='청산 후 종료',exact=True)).to_be_disabled()
    page.screenshot(path='/tmp/gg-parrot-agent-stopped.png',full_page=True)
    assert phase['stop_requests']==1,phase
    assert not errors,errors
    print(json.dumps({'passed':True,'requests':phase,'page_errors':errors},ensure_ascii=False))
    browser.close()
