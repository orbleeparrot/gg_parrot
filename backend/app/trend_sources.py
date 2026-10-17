"""Allowlisted regional news metadata adapters, not a request-time crawler.

Collectors call ``collect_source``; public readers consume their prepared store.
Domestic feeds use bounded in-process caches. Global feeds reuse the existing
news loaders (including their existing durable caches), never a second fetcher.
No AI call, new DB write, full article body, login, or browser bypass occurs here.
``publisher_region`` describes the publisher, not an article's language/topic.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import re
import threading
import time
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

from .http_runtime import SingleFlightGroup, get_http_client

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 50
_ROBOTS_BYTES = 64 * 1024
_CACHE_MS = 300_000
_ERROR_MS = 60_000
_STALE_MS = 6 * 60 * 60_000
_ROBOTS_MS = 24 * 60 * 60_000
_USER_AGENT = 'gg-parrot-news-collector/1.0'
_cache: dict[str, tuple[dict, int]] = {}
_robots_cache: dict[str, tuple[RobotFileParser, int]] = {}
_lock = threading.Lock()
_flights = SingleFlightGroup()
_robots_flights = SingleFlightGroup()

_SOURCES = {
    'blockmedia_rss': dict(name='블록미디어', url='https://www.blockmedia.co.kr/feed',
                          publisher_region='kr', format='rss', hosts=('www.blockmedia.co.kr', 'blockmedia.co.kr')),
    'bloomingbit_rss': dict(name='블루밍비트', url='https://bloomingbit.io/rss.xml',
                          publisher_region='kr', format='rss', hosts=('bloomingbit.io',)),
    'digitalasset_rss': dict(name='디지털애셋', url='https://cdn.digitalasset.works/rss/gn_rss_allArticle.xml',
                           publisher_region='kr', format='rss', hosts=('www.digitalasset.works', 'digitalasset.works'),
                           fetch_hosts=('cdn.digitalasset.works',)),
    'tokenpost_news': dict(name='토큰포스트', url='https://www.tokenpost.kr/news/cryptocurrency',
                          publisher_region='kr', format='html', hosts=('www.tokenpost.kr', 'tokenpost.kr')),
    'coindesk_rss': dict(name='CoinDesk', url='https://www.coindesk.com/arc/outboundfeeds/rss/',
                         publisher_region='global', format='shared', hosts=('www.coindesk.com', 'coindesk.com')),
    'decrypt_rss': dict(name='Decrypt', url='https://decrypt.co/feed', publisher_region='global',
                       format='shared', hosts=('decrypt.co', 'www.decrypt.co')),
    'cryptoslate_rss': dict(name='CryptoSlate', url='https://cryptoslate.com/feed/',
                          publisher_region='global', format='shared', hosts=('cryptoslate.com', 'www.cryptoslate.com')),
}


class SourceError(ValueError):
    def __init__(self, code: str, *, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def source_registry(region: str | None = None) -> list[dict]:
    """Return an independent copy; arbitrary URLs are never accepted as sources."""
    if region not in (None, 'kr', 'global'):
        raise ValueError('unknown publisher region')
    return [dict(id=key, name=value['name'], url=value['url'],
                 publisher_region=value['publisher_region'], content_type='article',
                 format=value['format']) for key, value in _SOURCES.items()
            if region is None or value['publisher_region'] == region]


def _source(source_id: str) -> dict:
    if source_id not in _SOURCES:
        raise ValueError('unknown trend source')
    return _SOURCES[source_id]


def clear_caches() -> None:
    """Lifecycle/test hook. Keys and retained item counts are otherwise fixed."""
    with _lock:
        _cache.clear()
        _robots_cache.clear()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit: int) -> str:
    parser = _Text()
    parser.feed(str(value or '')[:16_000])
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', ' '.join(parser.parts))
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _published(value, now_ms: int) -> tuple[str, int] | None:
    raw = str(value or '').strip()
    if not raw or len(raw) > 100:
        return None
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', raw):
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        else:
            stamp = parsedate_to_datetime(raw)
        # A source's missing timezone/date is not collection time. Do not guess.
        if stamp.tzinfo is None:
            return None
        stamp = stamp.astimezone(timezone.utc)
        millis = int(stamp.timestamp() * 1000)
        if not 1_230_768_000_000 <= millis <= now_ms + 5 * 60_000:
            return None
        return stamp.isoformat(), millis
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _safe_url(value: str, hosts: tuple[str, ...]) -> bool:
    try:
        parts = urlsplit(value)
        return (len(value) <= 2000 and not re.search(r'[\s\x00-\x1f\x7f]', value)
                and parts.scheme == 'https' and parts.hostname in hosts
                and parts.username is None and parts.password is None
                and parts.port in (None, 443))
    except ValueError:
        return False


def _article_url(source_id: str, value) -> str:
    from .news_archive import _normalized_url
    descriptor = _source(source_id)
    raw = str(value or '')
    if not raw or re.search(r'[\s\x00-\x1f\x7f]', raw):
        return ''
    url = urljoin(descriptor['url'], raw)
    if not _safe_url(url, descriptor['hosts']):
        return ''
    parts = urlsplit(url)
    path = parts.path.rstrip('/')
    valid = False
    if source_id == 'blockmedia_rss':
        valid = bool(re.fullmatch(r'/archives/\d+', path))
    elif source_id == 'bloomingbit_rss':
        valid = bool(re.fullmatch(r'/feed/news/\d+', path))
    elif source_id == 'digitalasset_rss':
        valid = path == '/news/articleView.html' and bool(re.fullmatch(
            r'\d+', (parse_qs(parts.query).get('idxno') or [''])[0]))
    elif source_id == 'tokenpost_news':
        valid = bool(re.fullmatch(r'/news/(?:cryptocurrency|blockchain|tech|economy|market|policy|'
                                 r'politics|insight|briefing|breaking|general)/\d+', path))
    else:
        valid = bool(path and not re.match(r'^/(?:feed|arc/outboundfeeds|tag|search)(?:/|$)', path))
    return _normalized_url(url) if valid else ''


def validate_item_url(source_id: str, url: str) -> bool:
    """Validate an absolute article URL against its known publisher and paths.

    The store can use this boundary without accepting a caller-supplied host or
    making an outbound request. Relative hrefs are supported only by parsers.
    """
    if source_id not in _SOURCES or not isinstance(url, str) or not url.startswith('https://'):
        return False
    return bool(_article_url(source_id, url))


def _crypto_article(item: dict) -> bool:
    from . import news
    title = item['title']
    # Advertorial gambling/giveaway bait never becomes news even with a coin keyword.
    if re.search(r'\b(?:casino|gambling|free spins|promo code|sweepstakes)\b|'
                 r'카지노|토토|무료\s*리딩|수익\s*보장|가입\s*코드|^\s*\[(?:광고|AD|PR)\]', title, re.I):
        return False
    if not news._is_news_article_candidate(item):
        return False
    context = ' '.join((title, item.get('excerpt', ''), ' '.join(item.get('categories') or [])))
    if re.search(news._CRYPTO_NEWS_CONTEXT + r'|(?<![A-Za-z0-9_])(?:USDT|USDC|PYUSD|RLUSD)(?![A-Za-z0-9_])|'
                 r'디지털\s*자산|BNB\s*체인|'
                 r'비트코인|이더리움|솔라나|스테이블코인|하이퍼리퀴드|체인링크|바이낸스|업비트|빗썸|'
                 r'스트래티지|크립토퀀트|주기영|렛저|서클|지캐시|헤데라|아비트럼|레이어제로', context, re.I):
        return True
    if re.search(r'제미니|\bGemini\b', context, re.I) and re.search(r'거래소|\b(?:CFTC|exchange)\b', context, re.I):
        return True
    # Publisher feeds can describe a named network without the word "coin".
    # Only explicit project names are used, not ambiguous short tickers (ICP/SC/T).
    aliases = ('solana', 'dogecoin', 'hyperliquid', 'avalanche blockchain', 'arbitrum',
               'chainlink', 'bnb chain', 'xrp ledger', 'ripple', 'aave', 'uniswap',
               'celestia blockchain', 'internet computer', 'threshold network', 'cryptoquant')
    return any(re.search(r'(?<![\w])' + re.escape(alias) + r'(?![\w])', context, re.I)
               for alias in aliases)


def _normalize(source_id: str, raw: dict, now_ms: int) -> dict | None:
    descriptor = _source(source_id)
    title = _text(raw.get('title'), 500)
    url = _article_url(source_id, raw.get('url'))
    published = _published(raw.get('published'), now_ms)
    if not title or not url or not published:
        return None
    from .news_identity import news_identity
    item = dict(title=title, url=url, source=descriptor['name'], source_id=source_id,
                feed_source=source_id, publisher_region=descriptor['publisher_region'],
                content_type='article', published=published[0], published_ms=published[1])
    categories = [_text(category, 120) for category in (raw.get('categories') or [])[:20]]
    if categories:
        item['categories'] = categories
    excerpt = _text(raw.get('excerpt'), 1200)
    if excerpt:
        item['excerpt'] = excerpt
    if not _crypto_article(item):
        return None
    item['id'] = sha256(news_identity(item).encode('utf-8')).hexdigest()
    return item


def _unique(source_id: str, raw: list[dict], now_ms: int) -> list[dict]:
    result, seen = [], set()
    for candidate in raw[:200]:
        item = _normalize(source_id, candidate, now_ms)
        if item and item['id'] not in seen:
            seen.add(item['id'])
            result.append(item)
            if len(result) >= MAX_ITEMS:
                break
    return sorted(result, key=lambda item: (-item['published_ms'], item['id']))


def _rss_rows(xml_text: str) -> list[dict]:
    if len(xml_text.encode('utf-8')) > MAX_RESPONSE_BYTES or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', xml_text, re.I):
        raise SourceError('invalid_feed')
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError('invalid_feed') from exc
    if root.tag.rsplit('}', 1)[-1] != 'rss' or root.find('channel') is None:
        raise SourceError('invalid_feed')
    result = []
    for entry in root.iterfind('.//item'):
        result.append(dict(title=entry.findtext('title'), url=entry.findtext('link'),
                           published=entry.findtext('pubDate'), excerpt=entry.findtext('description'),
                           categories=[value.text or '' for value in entry.findall('category')]))
        if len(result) >= 200:
            break
    return result


def parse_rss(source_id: str, xml_text: str, *, now_ms: int | None = None) -> list[dict]:
    """Normalize RSS title/date/link/description, never content:encoded bodies."""
    _source(source_id)
    try:
        return _unique(source_id, _rss_rows(xml_text), _now(now_ms))
    except SourceError:
        return []


class _Tokenpost(HTMLParser):
    """Parse the observed article_card markup, not arbitrary anchor text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.card = None
        self.capture = None
        self.capture_tag = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'article' and 'article_card' in classes:
            self.card = dict(title='', url='', published='', excerpt='', categories=[])
        if self.card is None:
            return
        if tag == 'a' and 'article_card_title_link' in classes:
            self.card['url'] = attrs.get('href', '')
        if tag == 'time' and 'article_card_date' in classes:
            self.card['published'] = attrs.get('datetime', '')
        for class_name, key in (('article_card_title', 'title'), ('article_card_summary', 'excerpt'),
                                ('article_card_category', 'categories')):
            if class_name in classes:
                self.capture, self.capture_tag = key, tag

    def handle_data(self, data):
        if self.card is not None and self.capture and not self.hidden:
            if self.capture == 'categories':
                self.card['categories'].append(data)
            else:
                self.card[self.capture] += data[:2000]

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if tag == self.capture_tag:
            self.capture = self.capture_tag = None
        if tag == 'article' and self.card is not None:
            if len(self.cards) < 200:
                self.cards.append(self.card)
            self.card = None


def _tokenpost_rows(html_text: str) -> list[dict]:
    if len(html_text.encode('utf-8')) > MAX_RESPONSE_BYTES:
        raise SourceError('response_too_large')
    parser = _Tokenpost()
    parser.feed(html_text)
    if not parser.cards:
        # A bot/challenge/error page is not a successfully collected empty feed.
        raise SourceError('article_markup_missing')
    return parser.cards


def parse_tokenpost(html_text: str, *, now_ms: int | None = None) -> list[dict]:
    try:
        return _unique('tokenpost_news', _tokenpost_rows(html_text), _now(now_ms))
    except SourceError:
        return []


def _now(now_ms) -> int:
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def _fetch(descriptor: dict, url: str, *, byte_limit: int = MAX_RESPONSE_BYTES,
           allow_not_found: bool = False) -> tuple[str, int]:
    hosts = descriptor.get('fetch_hosts', descriptor['hosts'])
    for _ in range(3):
        if not _safe_url(url, hosts):
            raise SourceError('unsafe_redirect')
        with get_http_client().stream('GET', url, follow_redirects=False, timeout=10.0,
                headers={'User-Agent': _USER_AGENT,
                         'Accept': 'application/rss+xml, application/xml, text/html;q=0.8, text/plain;q=0.5'}) as response:
            status = response.status_code
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get('location', '')
                if not location:
                    raise SourceError('unsafe_redirect')
                url = urljoin(url, location)
                continue
            if allow_not_found and status in (404, 410):
                return '', status
            if status < 200 or status >= 300:
                raise SourceError('http_error', http_status=status)
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(body) + len(chunk) > byte_limit:
                    raise SourceError('response_too_large')
                body.extend(chunk)
            return body.decode(response.encoding or 'utf-8', errors='replace'), status
    raise SourceError('redirect_limit')


def _check_robots(descriptor: dict, now_ms: int) -> None:
    parts = urlsplit(descriptor['url'])
    origin = f'{parts.scheme}://{parts.netloc}'
    def load():
        with _lock:
            hit = _robots_cache.get(origin)
        if hit and hit[1] > now_ms:
            return hit[0]
        text, _ = _fetch(descriptor, origin + '/robots.txt', byte_limit=_ROBOTS_BYTES, allow_not_found=True)
        parser = RobotFileParser()
        parser.parse(text.splitlines() if text else ['User-agent: *', 'Allow: /'])
        with _lock:
            _robots_cache[origin] = (parser, now_ms + _ROBOTS_MS)
        return parser
    parser = _robots_flights.run(origin, load)[0]
    if not parser.can_fetch(_USER_AGENT, descriptor['url']):
        raise SourceError('robots_disallowed')


def _global_rows(source_id: str) -> list[dict]:
    from . import news
    if source_id == 'coindesk_rss':
        return news._fetch_coindesk_news(strict=True)
    return news._fetch_shared_publisher_rss(source_id, strict=True)


def _report(source_id: str, now_ms: int) -> dict:
    descriptor = _source(source_id)
    return dict(id=source_id, name=descriptor['name'], url=descriptor['url'],
                publisher_region=descriptor['publisher_region'], content_type='article',
                fetch_mode='shared_news_cache' if descriptor['format'] == 'shared' else 'https',
                status='ready', attempted=True, cached=False, stale=False,
                collected_at_ms=now_ms, last_success_ms=None, fetched_count=0, item_count=0)


def _cached_result(hit: tuple[dict, int], now_ms: int) -> dict:
    result = deepcopy(hit[0])
    source = result['source']
    source.update(attempted=False, cached=True)
    success = source.get('last_success_ms')
    if success is None or now_ms - success > _STALE_MS:
        result['items'] = []
        source['item_count'] = 0
        source['stale'] = False
    return result


def collect_source(source_id: str, *, now_ms: int | None = None) -> dict:
    """Return {items, source, status}; failures are isolated and never leak bodies.

    ``attempted`` distinguishes an adapter load from its cache hit, not a count
    of upstream HTTP requests. Global ``fetch_mode=shared_news_cache`` delegates
    to existing cached loaders. ``collected_at_ms`` is the real adapter attempt
    and ``last_success_ms`` never advances on failure.
    Failed refreshes preserve at most six hours of explicitly stale metadata.
    The registry bounds this cache to seven keys, each at most fifty items.
    """
    descriptor = _source(source_id)
    now_ms = _now(now_ms)
    def load():
        with _lock:
            hit = _cache.get(source_id)
        if hit and hit[1] > now_ms:
            return _cached_result(hit, now_ms)
        report = _report(source_id, now_ms)
        try:
            if descriptor['format'] == 'shared':
                raw = _global_rows(source_id)
            else:
                _check_robots(descriptor, now_ms)
                text, _ = _fetch(descriptor, descriptor['url'])
                raw = _rss_rows(text) if descriptor['format'] == 'rss' else _tokenpost_rows(text)
            items = _unique(source_id, raw, now_ms)
            report.update(last_success_ms=now_ms, fetched_count=len(raw), item_count=len(items))
            expires = now_ms + _CACHE_MS
        except Exception as exc:
            safe = exc if isinstance(exc, SourceError) else SourceError('source_unavailable')
            report.update(status='error', error_code=safe.code)
            if safe.http_status is not None:
                report['http_status'] = safe.http_status
            last_success = hit[0]['source'].get('last_success_ms') if hit else None
            items = deepcopy(hit[0]['items']) if last_success is not None and now_ms - last_success <= _STALE_MS else []
            report.update(last_success_ms=last_success, item_count=len(items), stale=bool(items), cached=bool(items))
            expires = now_ms + _ERROR_MS
        result = dict(items=items, source=report, status=report['status'])
        with _lock:
            _cache[source_id] = (deepcopy(result), expires)
        return result
    result, state = _flights.run(source_id, load)
    result = deepcopy(result)
    if state == 'shared':
        result['source'].update(attempted=False, cached=True)
    return result
