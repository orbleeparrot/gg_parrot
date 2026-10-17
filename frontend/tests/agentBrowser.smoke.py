"""Run with a local Vite server; all API responses and credentials are fixtures.

  npx vite --port 5178 --strictPort --host 127.0.0.1
  BROWSER_EXECUTABLE_PATH=... python frontend/tests/agentBrowser.smoke.py

껄무새 중계(서버 말풍선) · 고래 동향 탭 · 손실 말풍선의 [청산하고 종료] → 종료 확인 창 → 결과 화면까지 본다.
"""
import json, os, re, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright, expect

phase = {'stopped': False, 'sessions': 0, 'stop_requests': 0, 'agent': 0}
# 사이트에 들어오면 한 번 뜨는 개발자 노트 — 이 점검과 상관없으니 '이 탭에서 닫음'으로 시작한다.
DEVNOTE_ID = re.search(r'id: "([^"]+)"', (Path(__file__).resolve().parents[1] / 'src/lib/devNotes.js').read_text()).group(1)
now = int(time.time() * 1000)
user = {'id': 1, 'username': '브라우저 검증', 'email': 'browser@example.invalid'}
macro = {'symbol': 'LINKUSDT', 'position_side': 'long', 'rule_type': 'A', 'candle_interval': '1m',
         'params': {'take_profit_pct': 3, 'initial_capital': 1000}, 'risk': {}}
session = {'session_id': 41, 'user_macro_id': 1, 'symbol': 'LINKUSDT', 'status': 'running', 'connected': True,
           'in_position': True, 'position_side': 'long', 'market': 'spot', 'testnet': True, 'entry_price': 100,
           'last_price': 96.8, 'position_qty': 2, 'unrealized_pct': -3.2, 'macro': macro, 'started_kst': '09/07 12:00',
           'stopping': False}
sayings = [
    {'id': 1, 'kind': 'start', 'at_ms': now - 30 * 60000, 'face': 'focused', 'tone': 'warn', 'hl': 'LINK 테스트넷 시작했어요',
     'nums': '', 'nums_tone': '', 'subs': [{'b': '손절 기준 없음', 't': ' · 떨어져도 매크로는 안 팔아요'}],
     'chips': [{'kind': 'macro', 'label': '내 매크로'}], 'post': None, 'actions': []},
    {'id': 2, 'kind': 'fill', 'at_ms': now - 29 * 60000, 'face': 'signal', 'tone': '', 'hl': '샀어요!',
     'nums': '100.00 USDT · 2개 · 200.00 USDT', 'nums_tone': '', 'subs': [{'t': '시작하자마자 사는 매크로예요'}],
     'chips': [{'kind': 'macro', 'label': '체결', 'event_id': 7}], 'post': None, 'actions': []},
    {'id': 3, 'kind': 'post', 'at_ms': now - 10 * 60000, 'face': 'curious', 'tone': '', 'hl': '이런 글이 올라왔네요?',
     'nums': '', 'nums_tone': '', 'subs': [],
     'chips': [{'kind': 'comm', 'label': 'Binance Square', 'href': 'https://www.binance.com/en/square/post/1'}],
     'post': {'kind': 'community', 'title': 'LINK 15분 미결제약정 6.07% 증가', 'summary': '미결제약정이 6.07% 늘었다고 전한다.',
              'author': '검증 작성자', 'source': 'Binance Square', 'url': 'https://www.binance.com/en/square/post/1',
              'published_ms': now - 11 * 60000}, 'actions': []},
    {'id': 4, 'kind': 'loss', 'at_ms': now - 60000, 'face': 'warning', 'tone': 'warn',
     'hl': '어어? 손절 기준이 없는데, 이대로 괜찮으세요?', 'nums': '-3.2% · -6.40 USDT', 'nums_tone': 'down',
     'subs': [{'t': '손절 기준이 없어 매크로는 계속 들고 있어요'}, {'t': '다음엔 -5%에서 말할게요'}],
     'chips': [{'kind': 'chart', 'label': '차트'}], 'post': None, 'actions': []},
]


def route_handler(route):
    url = urlparse(route.request.url)
    path = url.path
    if '/api/' not in path:
        if url.hostname in ('127.0.0.1', 'localhost'):
            route.continue_()
        else:
            route.abort()
        return
    status = 200
    payload = {}
    if path.endswith('/auth/me'):
        payload = {'user': user}
    elif path.endswith('/devnote/current'):
        status, payload = 404, {'detail': 'none'}
    elif path.endswith('/runner/sessions/stream-token'):
        status, payload = 503, {'detail': 'Simulated WebSocket outage'}
    elif path.endswith('/runner/sessions'):
        phase['sessions'] += 1
        final = {**session, 'status': 'stopped', 'connected': False, 'in_position': False, 'position_qty': 0,
                 'realized_pnl': 10, 'note': '청산 완료 후 종료'}
        payload = {'active': [] if phase['stopped'] else [session], 'recent': [final] if phase['stopped'] else [], 'poll_seconds': 3}
    elif path.endswith('/agent'):
        phase['agent'] += 1
        after = int(parse_qs(url.query).get('after', ['0'])[0])
        rows = [item for item in sayings if item['id'] > after]
        payload = {'session_id': 41, 'status': 'stopped' if phase['stopped'] else 'running', 'items': rows,
                   'cursor': rows[-1]['id'] if rows else after}
    elif path.endswith('/request-stop'):
        assert route.request.post_data_json['mode'] == 'close_and_stop'
        phase['stop_requests'] += 1
        phase['stopped'] = True
        payload = {'ok': True}
    elif path.endswith('/whale-activity'):
        payload = {'status': 'ready', 'symbol': 'LINKUSDT', 'market': 'spot', 'quote_asset': 'USDT',
                   'items': [{'id': 'spot:LINKUSDT:7', 'side': 'buy', 'notional': 150000, 'quantity': 1500, 'price': 100,
                              'occurred_at': now}]}
    elif path.endswith('/candles') or path.endswith('/candles/live'):
        bars = [{'t': now - (30 - i) * 60000, 'o': 100, 'h': 103, 'l': 96, 'c': 97, 'v': 1000, 'closed': True} for i in range(30)]
        payload = {'candles': bars, 'server_time': now, 'refresh_seconds': 30, 'source': 'fixture', 'data_source': 'fixture', 'market': 'spot'}
    elif path.endswith('/hot-coins'):
        payload = {'items': []}
    route.fulfill(status=status, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get('BROWSER_EXECUTABLE_PATH'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', route_handler)
    page.add_init_script('localStorage.setItem("ggp_token","browser-fixture");localStorage.setItem("ggp_user",'
                         + json.dumps(json.dumps(user, ensure_ascii=False)) + ');'
                         + f'sessionStorage.setItem("devnote:closed",{json.dumps(DEVNOTE_ID)});')
    page.goto('http://127.0.0.1:5178/agents')
    page.wait_for_load_state('networkidle')
    # 중계: 서버 말풍선이 그대로 — 브라우저가 따로 만든 '평가손실 주의' 같은 관측은 없다.
    expect(page.get_by_text('어어? 손절 기준이 없는데, 이대로 괜찮으세요?', exact=True)).to_be_visible(timeout=10000)
    expect(page.get_by_text('“LINK 15분 미결제약정 6.07% 증가”', exact=True)).to_be_visible()
    expect(page.get_by_text('평가손실 주의', exact=True)).to_have_count(0)
    expect(page.get_by_text('실시간 연결 복구 중', exact=False)).to_have_count(0)
    page.screenshot(path='/tmp/gg-parrot-agent-running.png', full_page=True)
    # 고래 동향 탭은 그대로 따로.
    page.get_by_role('button', name='고래 동향, PRO 구독 기능').click()
    expect(page.get_by_text('LINKUSDT 대규모 매수 체결', exact=True)).to_be_visible(timeout=10000)
    page.get_by_role('button', name='중계', exact=True).click()
    # 말풍선에는 청산 버튼이 없다(실수로 누르면 실제 포지션이 정리된다) — 종료는 위 실행 바에서만.
    expect(page.locator('.agent-say').get_by_role('button')).to_have_count(0)
    page.get_by_role('button', name='청산 후 종료', exact=True).click()
    dialog = page.get_by_role('alertdialog')
    expect(dialog.get_by_text('청산 후 종료할까요?', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='청산하고 종료', exact=True).click()
    expect(page.get_by_role('heading', name='청산 완료 후 종료했어요')).to_be_visible(timeout=10000)
    page.screenshot(path='/tmp/gg-parrot-agent-stopped.png', full_page=True)
    assert phase['stop_requests'] == 1, phase
    assert phase['agent'] >= 1, phase
    assert not errors, errors
    print(json.dumps({'passed': True, 'requests': phase, 'page_errors': errors}, ensure_ascii=False))
    browser.close()
