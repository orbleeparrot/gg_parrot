"""Public regional publisher adapters: real metadata only, bounded offline I/O."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import httpx
import pytest

from app import trend_sources as sources


NOW = int(datetime(2026, 10, 17, 3, tzinfo=timezone.utc).timestamp() * 1000)


def feed(*items):
    return '<rss><channel>' + ''.join(items) + '</channel></rss>'


def entry(title='비트코인 ETF 자금 유입', url='https://www.blockmedia.co.kr/archives/123',
          date='Sat, 17 Oct 2026 00:57:46 +0000', description='', category=''):
    from xml.sax.saxutils import escape
    return ('<item><title>' + escape(title) + '</title><link>' + escape(url) + '</link>'
            '<pubDate>' + escape(date) + '</pubDate><description>' + escape(description) +
            '</description><category>' + escape(category) + '</category></item>')


@pytest.fixture(autouse=True)
def clear_caches():
    sources.clear_caches()
    yield
    sources.clear_caches()


def test_registry_is_copied_and_regions_are_explicit():
    domestic = sources.source_registry('kr')
    assert {s['id'] for s in domestic} == {'blockmedia_rss', 'bloomingbit_rss',
                                         'digitalasset_rss', 'tokenpost_news'}
    assert all(s['content_type'] == 'article' and s['publisher_region'] == 'kr' for s in domestic)
    assert {s['id'] for s in sources.source_registry('global')} == {
        'coindesk_rss', 'decrypt_rss', 'cryptoslate_rss'}
    domestic[0]['url'] = 'http://localhost/'
    assert sources.source_registry('kr')[0]['url'].startswith('https://')
    with pytest.raises(ValueError):
        sources.source_registry('anything')


def test_rss_canonical_url_identity_excerpt_and_real_time():
    items = sources.parse_rss('blockmedia_rss', feed(
        entry(url='https://www.blockmedia.co.kr/archives/123?utm_source=rss#x',
              description='<p>실제 RSS 설명 <b>200억원</b></p><script>evil()</script>'),
        entry(title='동일 URL 제목 수정', url='https://www.blockmedia.co.kr/archives/123'),
    ), now_ms=NOW)
    assert len(items) == 1
    item = items[0]
    assert item['url'] == 'https://www.blockmedia.co.kr/archives/123'
    assert item['published'] == '2026-10-17T00:57:46+00:00'
    assert item['published_ms'] == 1792198666000
    assert item['excerpt'] == '실제 RSS 설명 200억원'
    assert item['publisher_region'] == 'kr' and item['content_type'] == 'article'
    assert item['source_id'] == 'blockmedia_rss'
    again = sources.parse_rss('blockmedia_rss', feed(entry()), now_ms=NOW)[0]
    assert again['id'] == item['id']


def test_rss_does_not_invent_bloomingbit_excerpt_or_use_full_body():
    xml = feed(entry(title='BNB체인 토큰화 주식 거래 증가',
                     url='https://bloomingbit.io/feed/news/122396'))
    xml = xml.replace('</item>', '<encoded>기사 전문을 대신 쓰면 안됨</encoded></item>')
    item = sources.parse_rss('bloomingbit_rss', xml, now_ms=NOW)[0]
    assert 'excerpt' not in item
    assert item['source'] == '블루밍비트'


@pytest.mark.parametrize('date', ['', '10월 17일', '09:34', '2026-10-17',
                                  'Sat, 17 Oct 2026 00:00:00', 'nonsense',
                                  'Sat, 17 Oct 2099 00:00:00 +0000'])
def test_undated_partial_naive_and_future_entries_are_not_fabricated(date):
    assert sources.parse_rss('blockmedia_rss', feed(entry(date=date)), now_ms=NOW) == []


@pytest.mark.parametrize('url', ['http://127.0.0.1/a', 'https://www.blockmedia.co.kr.evil/archives/1',
    'https://user:password@www.blockmedia.co.kr/archives/1', 'https://www.blockmedia.co.kr:8443/archives/1',
    'javascript:alert(1)', 'https://www.blockmedia.co.kr/feed',
    'https://www.blockmedia.co.kr/archives/1\x00'])
def test_untrusted_article_urls_rejected(url):
    assert sources.parse_rss('blockmedia_rss', feed(entry(url=url)), now_ms=NOW) == []


def test_store_url_validator_is_absolute_exact_and_does_not_fetch():
    assert sources.validate_item_url('blockmedia_rss', 'https://www.blockmedia.co.kr/archives/123')
    assert sources.validate_item_url('digitalasset_rss', 'https://www.digitalasset.works/news/articleView.html?idxno=43646')
    for source_id, url in (
        ('missing', 'https://www.blockmedia.co.kr/archives/123'),
        ('blockmedia_rss', '/archives/123'),
        ('blockmedia_rss', 'https://www.blockmedia.co.kr/archives/123\n'),
        ('blockmedia_rss', 'https://www.blockmedia.co.kr.evil/archives/123'),
        ('tokenpost_news', 'https://www.tokenpost.kr/forum/crypto/123'),
    ):
        assert not sources.validate_item_url(source_id, url)


def test_noncrypto_stock_and_promotional_articles_are_filtered():
    result = sources.parse_rss('blockmedia_rss', feed(
        entry(title='삼성전자 반도체 수출 반등', url='https://www.blockmedia.co.kr/archives/1'),
        entry(title='Stake.us casino promo code 무료 가입 비트코인 증정',
              url='https://www.blockmedia.co.kr/archives/2'),
        entry(title='비트코인 ETF 자금 유입', url='https://www.blockmedia.co.kr/archives/3'),
        entry(title='강력한 ICP가 있어도 CRM 도입이 어려운 이유',
              url='https://www.blockmedia.co.kr/archives/4'),
    ), now_ms=NOW)
    assert [i['url'] for i in result] == ['https://www.blockmedia.co.kr/archives/3']


@pytest.mark.parametrize('title', [
    '비자, USDC로 24시간 지급자금 충전…이뇨와 상용화',
    '서클, 신흥시장 50곳으로 사업 확대 추진',
    '렛저, 사고 수개월 전 기기 변조 신고 있었다',
    '스트래티지, 11월부터 우선주 STRC 배당 매일 지급 추진',
    '美 법원, CFTC·제미니 합의 취소 요청 기각',
])
def test_known_crypto_companies_and_stablecoins_without_coin_word_are_retained(title):
    xml = feed(entry(title=title, url='https://bloomingbit.io/feed/news/122396'))
    assert sources.parse_rss('bloomingbit_rss', xml, now_ms=NOW)[0]['title'] == title


def test_ai_gemini_and_explicit_paid_promotion_are_not_crypto_news():
    xml = feed(entry(title='Google Gemini AI 모델 출시', url='https://bloomingbit.io/feed/news/1'),
               entry(title='[광고] 비트코인 수수료 프로모션', url='https://bloomingbit.io/feed/news/2'))
    assert sources.parse_rss('bloomingbit_rss', xml, now_ms=NOW) == []


def test_xml_invalid_dtd_and_size_cap():
    assert sources.parse_rss('blockmedia_rss', '<rss>', now_ms=NOW) == []
    assert sources.parse_rss('blockmedia_rss', '<!DOCTYPE rss [<!ENTITY a "x">]><rss/>', now_ms=NOW) == []
    assert sources.parse_rss('blockmedia_rss', 'x' * (sources.MAX_RESPONSE_BYTES + 1), now_ms=NOW) == []


def tokenpost_card(href='/news/cryptocurrency/423599', date='2026-10-17T10:02:44+09:00',
                   title='솔라나 선전 행사 참가 증가'):
    return f'''<article class="article_card article_card_list">
      <a class="article_card_title_link" href="{href}"><h3 class="article_card_title">{title}</h3></a>
      <a class="article_card_summary_link" href="{href}"><p class="article_card_summary">실제 목록 설명</p></a>
      <span class="article_card_category">암호화폐</span>
      <time class="article_card_date" datetime="{date}">24분 전</time></article>'''


def test_tokenpost_real_article_card_shape_and_exact_paths():
    html = ('<a href="/news/cryptocurrency/123">메뉴</a>' + tokenpost_card() + tokenpost_card()
            + tokenpost_card('/forum/crypto/123')
            + tokenpost_card('https://evil.test/news/cryptocurrency/123')
            + tokenpost_card('/news/cryptocurrency/123evil'))
    items = sources.parse_tokenpost(html, now_ms=NOW)
    assert len(items) == 1
    assert items[0]['title'] == '솔라나 선전 행사 참가 증가'
    assert items[0]['published'] == '2026-10-17T01:02:44+00:00'
    assert items[0]['excerpt'] == '실제 목록 설명'
    assert items[0]['source_id'] == 'tokenpost_news'


def test_tokenpost_does_not_guess_relative_time_and_accepts_valid_empty_list():
    assert sources.parse_tokenpost(tokenpost_card(date=''), now_ms=NOW) == []
    assert sources.parse_tokenpost(tokenpost_card(date='2026-10-17'), now_ms=NOW) == []


def mock_client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(sources, 'get_http_client', lambda: client)
    return client


def test_collect_caches_per_source_and_copies_public_results(monkeypatch):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text='User-agent: *\nAllow: /' if request.url.path == '/robots.txt'
                              else feed(entry()))
    with mock_client(monkeypatch, handler):
        result = sources.collect_source('blockmedia_rss', now_ms=NOW)
        assert result['status'] == 'ready' and result['source']['attempted']
        assert result['source']['last_success_ms'] == NOW
        result['items'][0]['title'] = 'mutated'
        cached = sources.collect_source('blockmedia_rss', now_ms=NOW + 1)
        assert cached['source']['cached'] and not cached['source']['attempted']
        assert cached['items'][0]['title'] == '비트코인 ETF 자금 유입'
        assert requests == ['https://www.blockmedia.co.kr/robots.txt', 'https://www.blockmedia.co.kr/feed']


def test_failure_report_is_safe_backed_off_and_keeps_bounded_stale(monkeypatch):
    state = {'fail': False}
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(429, text='SECRET RESPONSE BODY') if state['fail'] else httpx.Response(200, text=feed(entry()))
    with mock_client(monkeypatch, handler):
        first = sources.collect_source('blockmedia_rss', now_ms=NOW)
        state['fail'] = True
        failed = sources.collect_source('blockmedia_rss', now_ms=NOW + 300_001)
        assert failed['status'] == 'error' and failed['items'] == first['items']
        assert failed['source']['http_status'] == 429 and failed['source']['stale']
        assert failed['source']['last_success_ms'] == NOW
        assert 'SECRET' not in str(failed)
        retry = sources.collect_source('blockmedia_rss', now_ms=NOW + 310_000)
        assert not retry['source']['attempted']
        expired = sources.collect_source('blockmedia_rss', now_ms=NOW + 6 * 3600_000 + 1)
        assert expired['items'] == []


def test_redirects_cannot_escape_source_allowlist(monkeypatch):
    paths = []
    def handler(request):
        paths.append(str(request.url))
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(302, headers={'location': 'http://169.254.169.254/latest/meta-data'})
    with mock_client(monkeypatch, handler):
        result = sources.collect_source('blockmedia_rss', now_ms=NOW)
    assert result['status'] == 'error' and not result['items']
    assert len(paths) == 2


def test_same_publisher_redirect_is_bounded_and_can_succeed(monkeypatch):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        if request.url.path == '/feed':
            return httpx.Response(302, headers={'location': '/feed/'})
        return httpx.Response(200, text=feed(entry()))
    with mock_client(monkeypatch, handler):
        assert sources.collect_source('blockmedia_rss', now_ms=NOW)['status'] == 'ready'
    assert paths == ['/robots.txt', '/feed', '/feed/']
    sources.clear_caches()
    def loop(request):
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(
            302, headers={'location': '/feed'})
    with mock_client(monkeypatch, loop):
        assert sources.collect_source('blockmedia_rss', now_ms=NOW)['source']['error_code'] == 'redirect_limit'


def test_robots_failure_is_not_treated_as_permission_and_markup_failure_is_not_ready(monkeypatch):
    with mock_client(monkeypatch, lambda request: httpx.Response(403)):
        result = sources.collect_source('blockmedia_rss', now_ms=NOW)
        assert result['status'] == 'error' and result['source']['http_status'] == 403
    sources.clear_caches()
    def bot_wall(request):
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(
            200, text='<html>Verify you are human</html>')
    with mock_client(monkeypatch, bot_wall):
        assert sources.collect_source('tokenpost_news', now_ms=NOW)['source']['error_code'] == 'article_markup_missing'
        assert sources.collect_source('blockmedia_rss', now_ms=NOW)['source']['error_code'] == 'invalid_feed'


def test_feed_fetched_count_distinguishes_filtered_items(monkeypatch):
    xml = feed(entry(), entry(title='삼성전자 수출 증가', url='https://www.blockmedia.co.kr/archives/2'))
    def handler(request):
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(200, text=xml)
    with mock_client(monkeypatch, handler):
        result = sources.collect_source('blockmedia_rss', now_ms=NOW)
    assert result['source']['fetched_count'] == 2 and result['source']['item_count'] == 1


def test_robots_deny_blocks_feed_and_unknown_source_never_makes_requests(monkeypatch):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, text='User-agent: *\nDisallow: /feed')
    with mock_client(monkeypatch, handler):
        result = sources.collect_source('blockmedia_rss', now_ms=NOW)
        assert result['source']['error_code'] == 'robots_disallowed'
        with pytest.raises(ValueError):
            sources.collect_source('https://evil.test/rss', now_ms=NOW)
    assert paths == ['/robots.txt']


def test_collect_singleflight_has_one_feed_request(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if request.url.path == '/feed':
            entered.set()
            assert release.wait(5)
            return httpx.Response(200, text=feed(entry()))
        return httpx.Response(404)
    with mock_client(monkeypatch, handler), ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(sources.collect_source, 'blockmedia_rss', now_ms=NOW)
        assert entered.wait(5)
        two = pool.submit(sources.collect_source, 'blockmedia_rss', now_ms=NOW)
        release.set()
        assert one.result()['items'] == two.result()['items']
    assert requests.count('/feed') == 1


def test_global_feeds_reuse_existing_loader_without_new_http(monkeypatch):
    from app import news
    calls = []
    monkeypatch.setattr(news, '_fetch_coindesk_news', lambda **kw: calls.append(kw) or [{
        'title': 'Bitcoin ETF inflows grow', 'url': 'https://www.coindesk.com/markets/2026/10/17/bitcoin',
        'published': '2026-10-17T01:00:00+00:00', 'excerpt': 'Actual description'}])
    monkeypatch.setattr(sources, 'get_http_client', lambda: pytest.fail('duplicate HTTP'))
    result = sources.collect_source('coindesk_rss', now_ms=NOW)
    assert calls == [{'strict': True}]
    assert result['items'][0]['publisher_region'] == 'global'
    assert result['items'][0]['source_id'] == 'coindesk_rss'


def test_response_bytes_and_item_counts_are_bounded(monkeypatch):
    def handler(request):
        return httpx.Response(404) if request.url.path == '/robots.txt' else httpx.Response(
            200, content=b'x' * (sources.MAX_RESPONSE_BYTES + 1))
    with mock_client(monkeypatch, handler):
        assert sources.collect_source('blockmedia_rss', now_ms=NOW)['source']['error_code'] == 'response_too_large'
    xml = feed(*(entry(url=f'https://www.blockmedia.co.kr/archives/{n}') for n in range(90)))
    assert len(sources.parse_rss('blockmedia_rss', xml, now_ms=NOW)) == sources.MAX_ITEMS
