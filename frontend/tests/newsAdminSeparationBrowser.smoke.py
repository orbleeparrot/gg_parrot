"""Fixture-only browser checks. No production API, account creation, or AI requests.

NEWS_ADMIN_TEST_URL defaults to a local Vite server on port 5183.
"""
import json
import os
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('NEWS_ADMIN_TEST_URL', 'http://127.0.0.1:5183')
USER = {'id': 1, 'username': '로컬 점검', 'email': 'qa@example.invalid', 'is_admin': True}
ARTICLE = {'title': '정책 기관의 공식 발표 기사', 'source': 'CoinDesk', 'url': 'https://fixture.invalid/article', 'published': '2026-09-30T02:00:00Z'}
OPINION = {'title': '작성자의 매수 주장 게시글', 'source': 'Binance Square', 'content_type': 'community',
           'community_post_id': '123', 'url': 'https://fixture.invalid/opinion', 'author': '개인',
           'community_summary_status': 'ready', 'community_summary': '작성자는 가격이 오를 것이라는 개인 의견을 밝혔어요.'}


class Fixture:
    def __init__(self):
        self.user_queries = []
        self.beacons = []

    def route(self, route):
        req = route.request
        parsed = urlparse(req.url)
        if '/api/' not in parsed.path:
            if parsed.hostname == urlparse(BASE).hostname:
                return route.continue_()
            return route.abort()
        data = {}
        path = parsed.path
        if path.endswith('/auth/me'):
            data = {'user': USER}
        elif path.endswith('/hot-coins'):
            data = {'coins': [{'symbol': 'QNTUSDT', 'last_price': 70, 'change_pct': 12, 'quote_volume': 10000}]}
        elif '/news/coin/' in path or path.endswith('/news/market'):
            data = {'items': [ARTICLE, OPINION], 'as_of': '2026-09-30', 'translation': {'status': 'ready'}, 'collection': {'status': 'ready'}}
        elif path.endswith('/admin/users'):
            include = parse_qs(parsed.query).get('include_internal') == ['true']
            self.user_queries.append(include)
            data = {'days': 7, 'generated_at': '2026-09-30T03:00:00Z', 'kpis': {'dau': 22 if include else 2, 'logged_in_accounts': 1},
                    'traffic': {'include_internal': include}, 'series': {}, 'daily': [], 'channels': [], 'devices': [], 'pages': []}
        elif path.endswith('/admin/news'):
            data = {'generated_at': '2026-09-30T03:00:00Z', 'kpis': {}, 'pipeline': [
                {'key': 'collection', 'label': '기사 수집', 'status_label': '수집 기록 있음', 'last_success_ms': 1790737200000,
                 'completed_today': 3, 'count_unit': '저장 행', 'failures_today': 0},
                {'key': 'translation', 'label': '제목 번역', 'status_label': '처리 중', 'last_success_ms': 1790737200000,
                 'completed_today': 80, 'processing': 1, 'pending': 12, 'errors': 3, 'failures_today': 4},
                {'key': 'summary', 'label': '커뮤니티 본문 요약', 'status_label': '대기 작업 있음', 'last_success_ms': 1790737200000,
                 'completed_today': 20, 'processing': 0, 'pending': 30, 'errors': 5, 'failures_today': 6},
                {'key': 'market_summary', 'label': '오늘의 시장 요약', 'status_label': '오늘 요약 없음',
                 'completed_today': 0, 'processing': None, 'pending': None, 'errors': None, 'failures_today': 1}],
                'engines': [{'engine': 'article_enrichment', 'label': '구 보강 엔진', 'status': 'delayed', 'status_label': '지연'}]}
        elif path.endswith('/visit'):
            self.beacons.append(req.post_data_json)
            return route.fulfill(status=204)
        elif path.endswith('/visit/leave'):
            return route.fulfill(status=204)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data, ensure_ascii=False))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    results = []
    for width in (1440, 390):
        fixture = Fixture()
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', fixture.route)
        page.add_init_script('localStorage.setItem("ggp_token","fixture");localStorage.setItem("ggp_user",'
                             + json.dumps(json.dumps(USER, ensure_ascii=False)) + ');')
        page.goto(BASE + '/news?qa=1')
        page.wait_for_load_state('networkidle')
        notice = page.get_by_role('button', name='확인했어요', exact=True)
        if notice.count():
            notice.click()
        # 시장·규제 헤드라인은 거르지 않는다 — 기사와 커뮤니티 글이 함께 보이고, 커뮤니티 글에는 '사실 확인 안 됨' 표시가 붙는다.
        market = page.locator('section.is-market')
        expect(market.get_by_text(ARTICLE['title'], exact=True).first).to_be_attached()
        expect(market.get_by_text(OPINION['title'], exact=True).first).to_be_attached()
        # 콘텐츠 유형은 '경주마 동향' 제목 오른쪽 segmented 버튼으로만 거른다(기본 보도 기사).
        racers = page.locator('section.is-racers')
        filters = racers.get_by_role('group', name='경주마 뉴스 콘텐츠 유형')
        expect(filters.get_by_role('button', name='보도 기사', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(racers.get_by_text(ARTICLE['title'], exact=True).first).to_be_attached()
        expect(racers.get_by_text(OPINION['title'], exact=True)).to_have_count(0)
        filters.get_by_role('button', name='커뮤니티', exact=True).click()
        expect(filters.get_by_role('button', name='커뮤니티', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(racers.get_by_text(OPINION['title'], exact=True).first).to_be_attached()
        expect(racers.get_by_text(ARTICLE['title'], exact=True)).to_have_count(0)
        expect(racers.get_by_text('커뮤니티 글은 작성자의 주장·매매 의견이며, 보도 기사나 검증된 투자 정보가 아니에요.')).to_be_visible()
        filters.get_by_role('button', name='전체', exact=True).click()
        expect(racers.get_by_text(ARTICLE['title'], exact=True).first).to_be_attached()
        expect(racers.get_by_text(OPINION['title'], exact=True).first).to_be_attached()
        page.locator('section.is-racers').screenshot(path=os.environ.get('NEWS_FILTER_SHOT', '/tmp/news-filter') + f'-{width}.png')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.goto(BASE + '/admin?tab=users&days=7')
        page.wait_for_load_state('networkidle')
        if notice.count():
            notice.click()
        toggle = page.get_by_role('checkbox', name='관리자·QA 방문 포함')
        expect(toggle).not_to_be_checked()
        toggle.check()
        expect(toggle).to_be_checked()
        page.wait_for_function('new URLSearchParams(location.search).get("include_internal") === "true"')
        page.wait_for_load_state('networkidle')
        assert fixture.user_queries[0] is False and fixture.user_queries[-1] is True
        expect(page.get_by_text('로그인 계정 (기간)', exact=True)).to_be_visible()
        page.get_by_role('button', name='30일', exact=True).click()
        expect(page.get_by_role('button', name='30일', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(toggle).to_be_checked()
        assert parse_qs(urlparse(page.url).query).get('days') is None
        page.goto(BASE + '/admin?tab=news')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('수집 · 번역 · 요약 단계', exact=True)).to_be_visible()
        expect(page.get_by_role('cell', name='제목 번역', exact=True)).to_be_visible()
        expect(page.get_by_role('cell', name='커뮤니티 본문 요약', exact=True)).to_be_visible()
        expect(page.get_by_role('cell', name='오늘 요약 없음', exact=True)).to_be_visible()
        expect(page.get_by_text('구 보강 엔진', exact=True)).to_have_count(0)
        assert not errors, errors
        assert fixture.beacons and all(b.get('is_internal') is True for b in fixture.beacons)
        results.append({'width': width, 'passed': True, 'page_errors': errors, 'user_filter_requests': fixture.user_queries})
        page.close()
    print(json.dumps(results, ensure_ascii=False))
    browser.close()
