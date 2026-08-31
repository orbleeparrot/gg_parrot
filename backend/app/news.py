"""'오늘의 코인동향' — 무료 RSS 헤드라인 수집과 요약.

정보 제공용이며 투자자문이 아니다. 설계 가드레일(사용자 합의):
  1) 중립·사실 위주: 실제 헤드라인을 그대로 링크로 보여준다(팩트).
  2) 저작권: 기사 전문을 복제하지 않는다 — 제목·매체·시각 + '원문 링크'만.
  3) 환각 방지: AI 개요는 '주어진 헤드라인'에서만 요약하게 하고(모델 지식으로
     새 사실 생성 금지), 실패하면 개요 없이 헤드라인만 보여준다.
  4) 비용: KST 기준 하루 1회만 요약해 메모리에 캐시(방문마다 호출 X).

Google News RSS의 한국어 검색 결과와 CoinDesk 공식 RSS를 함께 사용한다.
기사 본문은 저장하지 않고 제목·매체·원문 링크·발행 시각만 다룬다.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from .http_runtime import SingleFlightGroup, get_http_client, run_parallel
from .ai_runtime import ai_cache_key, get_ai_runtime, get_anthropic_client

_GOOGLE_NEWS = "https://news.google.com/rss/search"
_COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
_OPENEDEN_RSS = "https://openeden.com/news/feed/"
_HTTP_TIMEOUT = 10.0
_MAX_ITEMS = 8
_MAX_COIN_ITEMS = 10
_COIN_CACHE_SECONDS = max(60, int(os.environ.get("COIN_NEWS_CACHE_SECONDS", "300")))
_OPENEDEN_CACHE_SECONDS = 60 * 60
_OPENEDEN_MAX_AGE_DAYS = 30

# 시장·규제 전반 쿼리. Google News 검색 연산자 when:2d 로 최근 이틀로 제한.
_MARKET_QUERY = "암호화폐 OR 가상자산 OR 비트코인 규제 OR 동향 when:2d"

# 코린이가 아는 흔한 티커의 한글명 — 한국어 뉴스 적중률을 높인다. 없으면 티커 그대로.
_COIN_KO = {
    "BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "SOL": "솔라나",
    "DOGE": "도지코인", "ADA": "에이다", "TRX": "트론", "AVAX": "아발란체",
    "LINK": "체인링크", "DOT": "폴카닷", "MATIC": "폴리곤", "SHIB": "시바이누",
    "BCH": "비트코인캐시", "LTC": "라이트코인", "ATOM": "코스모스", "ETC": "이더리움클래식",
    "APT": "앱토스", "ARB": "아비트럼", "OP": "옵티미즘", "SUI": "수이",
    "PEPE": "페페", "USDT": "테더", "BNB": "바이낸스코인",
    "UNI": "유니스왑", "AAVE": "에이브", "MKR": "메이커", "SAND": "샌드박스",
    "MANA": "디센트럴랜드", "AXS": "엑시인피니티", "GRT": "더그래프", "ALGO": "알고랜드",
    "FIL": "파일코인", "ICP": "인터넷컴퓨터", "NEAR": "니어프로토콜", "INJ": "인젝티브",
    "RUNE": "토르체인", "STX": "스택스", "IMX": "이뮤터블", "ONDO": "온도파이낸스",
    "ZEC": "지캐시", "XLM": "스텔라루멘", "HBAR": "헤데라", "VET": "비체인",
    "EDEN": "오픈에덴",
}

# 영문 RSS 제목·CoinDesk category 태그에서 자산 관련성을 판별할 때 사용한다.
# 나머지 자산은 티커와 _COIN_KO 한글명을 기본 별칭으로 사용한다.
_COIN_ALIASES = {
    "BTC": ("bitcoin", "비트코인"),
    "ETH": ("ethereum", "ether", "이더리움"),
    "XRP": ("xrp", "ripple", "리플"),
    "SOL": ("sol", "solana", "솔라나"),
    "EDEN": ("openeden", "open eden", "eden token", "오픈에덴"),
}

# 영문권에서만 다뤄지는 소형 자산은 한글 검색 하나로 기사가 고갈된다.
# EDEN은 검증된 브랜드/티커 표현만 사용해 일반적인 'Eden' 동명이인 오탐을 막는다.
_COIN_GOOGLE_QUERIES = {
    "EDEN": (
        ('(OpenEden OR 오픈에덴 OR "EDEN 코인") when:30d', "ko"),
        ('(OpenEden OR "Open Eden") when:30d', "en"),
        (
            '(EDEN coin OR EDEN crypto OR EDEN token OR $EDEN OR EDEN USDT OR '
            'EDEN listing OR EDEN price) when:30d',
            "en",
        ),
    ),
}

_EDEN_STRONG_TERMS = (
    "openeden",
    "open eden",
    "오픈에덴",
    "$eden",
    "eden coin",
    "eden crypto",
    "eden token",
    "eden usdt",
)
_EDEN_CRYPTO_CONTEXT = (
    "coin",
    "crypto",
    "token",
    "usdt",
    "listing",
    "listed",
    "exchange",
    "upbit",
    "bithumb",
    "binance",
    "altcoin",
    "airdrop",
    "rwa",
    "treasury",
    "tokenized",
    "defi",
    "web3",
    "price",
    "rally",
    "surge",
    "jump",
    "trading",
    "market",
    "코인",
    "토큰",
    "상장",
    "거래소",
    "급등",
)
_EDEN_NOISE_TERMS = (
    "magic eden",
    "eden research",
    "eden prairie",
    "east of eden",
    "eden housing",
    "eden center",
    "eden park",
    "eden street",
    "another eden",
    "eden project",
    "eden innovations",
    "concrete market",
    "ishares msci denmark",
    "edenmagnet",
    "liquidity mapping around (eden)",
    "price today",
    "live price",
)

_GOOGLE_LOCALES = {
    "ko": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}

# 요약(개요)은 Anthropic 키가 있을 때만. 시장 페이지에 하루 1회.
_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
_SUMMARY_PROMPT_VERSION = "market-news-summary-v2"
_MARKET_SUMMARY_RETRY_SECONDS = 30.0

_DISCLAIMER = (
    "정보 제공용이며 투자자문이 아닙니다. 요약은 AI가 생성했을 수 있으니 "
    "반드시 원문을 확인하세요."
)

# 일 1회 캐시: key -> (envelope, kst_date_str). 인스턴스 재시작 시 재생성(허용).
_cache: dict[str, tuple[dict, str]] = {}
# 공개 뉴스 화면의 종목 조회용 호환 캐시. 에이전트 API는 이 캐시를 읽지 않고
# 중앙 워커가 DB에 영속화한 공용 스냅샷만 읽는다.
_coin_cache: dict[str, tuple[dict, float]] = {}
_coindesk_cache: tuple[list[dict], float] | None = None
_coindesk_error_cache: tuple[str, float] | None = None
_openeden_cache: tuple[list[dict], float] | None = None
_openeden_error_cache: tuple[str, float] | None = None
_market_summary_retry_at = 0.0
_rss_refreshes = SingleFlightGroup()


class NewsFetchError(RuntimeError):
    """Raised when every configured RSS source is unavailable."""


_MARKET_QUOTES = ("FDUSD", "USDT", "BUSD", "USDC", "TUSD", "USD")


def canonical_asset_symbol(symbol: str) -> str:
    """Validate an already-derived asset ticker without stripping suffixes."""
    value = str(symbol or "").strip().upper()
    return value if re.fullmatch(r"[A-Z0-9]{2,15}", value) else ""


def canonical_market_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    return value if re.fullmatch(r"[A-Z0-9]{2,21}", value) else ""


def asset_from_market_symbol(symbol: str) -> str:
    """Convert a market-pair input to its asset exactly once at ingress."""
    value = canonical_market_symbol(symbol)
    if not value:
        return value
    if value in _MARKET_QUOTES:
        return value
    for quote in _MARKET_QUOTES:
        if value.endswith(quote) and len(value) > len(quote):
            return canonical_asset_symbol(value[: -len(quote)])
    return canonical_asset_symbol(value)


def position_news_collection_universe() -> frozenset[str]:
    """Return the complete built-in asset universe collected by the worker."""
    return frozenset(_COIN_KO)


def _kst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


def _kst_date() -> str:
    return _kst_now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# RSS 파싱 (순수 함수 — 테스트 가능, I/O 없음)
# ---------------------------------------------------------------------------
def _clean_title(title: str, source: Optional[str]) -> str:
    """Google News 제목은 보통 '기사제목 - 매체명' 형태 — 매체명 꼬리를 떼어낸다."""
    t = (title or "").strip()
    if source and t.endswith(" - " + source):
        t = t[: -(len(source) + 3)].strip()
    elif " - " in t:
        # 매체 원소가 없을 때의 폴백: 마지막 ' - 매체' 조각 제거
        head, _, _tail = t.rpartition(" - ")
        if head:
            t = head.strip()
    return t


def _fmt_published(dt: Optional[datetime]) -> str:
    """KST 기준 상대 시각(예: '3시간 전', '어제')."""
    if dt is None:
        return ""
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 0:
        secs = 0
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    days = int(secs // 86400)
    if days == 1:
        return "어제"
    return f"{days}일 전"


def _parse_rss(xml_text: str, *, limit: int = _MAX_ITEMS) -> list[dict]:
    """RSS XML -> [{title, source, url, published, published_display}]. 중복 제거."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for item in root.iterfind(".//item"):
        link = (item.findtext("link") or "").strip()
        raw_title = (item.findtext("title") or "").strip()
        if not link or not raw_title:
            continue
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        title = _clean_title(raw_title, source)
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        pub_raw = (item.findtext("pubDate") or "").strip()
        published_dt = None
        if pub_raw:
            try:
                published_dt = parsedate_to_datetime(pub_raw)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published_dt = None
        items.append(
            {
                "title": title,
                "source": source,
                "url": link,
                "published": published_dt.astimezone(timezone.utc).isoformat() if published_dt else None,
                "published_display": _fmt_published(published_dt),
            }
        )
        if len(items) >= limit:
            break
    return items


def _parse_coindesk_rss(
    xml_text: str,
    *,
    limit: int = 25,
) -> list[dict]:
    """Parse CoinDesk's official headline-link RSS without article bodies."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for item in root.iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        key = title.casefold()
        if not title or not link or key in seen:
            continue
        seen.add(key)
        pub_raw = (item.findtext("pubDate") or "").strip()
        published_dt = None
        if pub_raw:
            try:
                published_dt = parsedate_to_datetime(pub_raw)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published_dt = None
        categories = [
            str(category.text or "").strip()
            for category in item.findall("category")
            if str(category.text or "").strip()
        ]
        items.append({
            "title": title,
            "source": "CoinDesk",
            "url": link,
            "published": (
                published_dt.astimezone(timezone.utc).isoformat()
                if published_dt
                else None
            ),
            "published_display": _fmt_published(published_dt),
            "categories": categories,
            "feed_source": "coindesk_rss",
        })
        if len(items) >= limit:
            break
    return items


def _parse_openeden_rss(xml_text: str) -> list[dict]:
    """Parse recent metadata from OpenEden's official RSS, never article bodies."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_OPENEDEN_MAX_AGE_DAYS)
    items = []
    for raw in _parse_rss(xml_text, limit=20):
        url = str(raw.get("url") or "")
        published = raw.get("published")
        if not url.startswith("https://openeden.com/news/") or not published:
            continue
        try:
            published_at = datetime.fromisoformat(str(published))
        except ValueError:
            continue
        if published_at < cutoff:
            continue
        item = dict(raw)
        item["source"] = "OpenEden"
        item["feed_source"] = "openeden_official_rss"
        items.append(item)
        if len(items) >= _MAX_COIN_ITEMS:
            break
    return items


def _matches_asset(item: dict, asset_symbol: str, coin_name: str) -> bool:
    categories = item.get("categories") or []
    searchable = " ".join([
        str(item.get("title") or ""),
        *[str(category or "") for category in categories],
    ]).casefold()
    if asset_symbol == "EDEN":
        if any(term in searchable for term in _EDEN_NOISE_TERMS):
            return False
        if any(term in searchable for term in _EDEN_STRONG_TERMS):
            return True
        has_ticker = re.search(r"(?<![a-z0-9])eden(?![a-z0-9])", searchable)
        return bool(
            has_ticker
            and any(term in searchable for term in _EDEN_CRYPTO_CONTEXT)
        )
    aliases = {
        asset_symbol,
        coin_name,
        *_COIN_ALIASES.get(asset_symbol, ()),
    }
    for alias in aliases:
        normalized = str(alias or "").strip().casefold()
        if not normalized:
            continue
        if re.search(r"[a-z0-9]", normalized):
            if re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                searchable,
            ):
                return True
        elif normalized in searchable:
            return True
    return False


def _relevant_items(
    items: list[dict],
    *,
    asset_symbol: str,
    coin_name: str,
    feed_source: str,
) -> list[dict]:
    relevant = []
    for raw in items:
        if not _matches_asset(raw, asset_symbol, coin_name):
            continue
        item = dict(raw)
        item["feed_source"] = feed_source
        relevant.append(item)
    return relevant


def _merge_news_items(*sources: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    positions = [0 for _items in sources]
    while len(merged) < _MAX_COIN_ITEMS:
        advanced = False
        for source_index, items in enumerate(sources):
            while positions[source_index] < len(items):
                item = items[positions[source_index]]
                positions[source_index] += 1
                advanced = True
                key = re.sub(
                    r"\s+",
                    " ",
                    str(item.get("title") or "").strip().casefold(),
                )
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                break
            if len(merged) >= _MAX_COIN_ITEMS:
                return merged
        if not advanced:
            break
    return merged


# ---------------------------------------------------------------------------
# 네트워크 + 요약
# ---------------------------------------------------------------------------
def _fetch_news(
    query: str,
    *,
    limit: int = _MAX_ITEMS,
    strict: bool = False,
    locale: str = "ko",
) -> list[dict]:
    params = {"q": query, **_GOOGLE_LOCALES.get(locale, _GOOGLE_LOCALES["ko"])}
    try:
        def load():
            resp = get_http_client().get(_GOOGLE_NEWS, params=params, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            return _parse_rss(resp.text, limit=limit)

        items, _state = _rss_refreshes.run(("google", query, locale, limit), load)
        return [dict(item) for item in items]
    except Exception as exc:
        if strict:
            raise NewsFetchError("Google News RSS 수집에 실패했습니다.") from exc
        return []


def _fetch_coindesk_news(*, strict: bool = False) -> list[dict]:
    """Fetch CoinDesk's official 25-item RSS once per collector interval."""
    global _coindesk_cache, _coindesk_error_cache

    now = time.time()
    if _coindesk_cache and _coindesk_cache[1] > now:
        return [dict(item) for item in _coindesk_cache[0]]
    if _coindesk_error_cache and _coindesk_error_cache[1] > now:
        if strict:
            raise NewsFetchError(_coindesk_error_cache[0])
        return []

    try:
        def load():
            resp = get_http_client().get(
                _COINDESK_RSS,
                timeout=_HTTP_TIMEOUT,
                headers={
                    "Accept": "application/rss+xml, application/xml;q=0.9",
                    "User-Agent": "gg-parrot-news-collector/1.0",
                },
            )
            resp.raise_for_status()
            parsed = _parse_coindesk_rss(resp.text)
            if not parsed:
                raise ValueError("CoinDesk RSS returned no parseable items")
            return parsed

        if _coindesk_cache:
            items, state = _rss_refreshes.run(
                "coindesk",
                load,
                stale_value=_coindesk_cache[0],
            )
        else:
            items, state = _rss_refreshes.run("coindesk", load)
        if state == "stale":
            return [dict(item) for item in items]
        _coindesk_cache = (items, now + _COIN_CACHE_SECONDS)
        _coindesk_error_cache = None
        return [dict(item) for item in items]
    except Exception as exc:
        message = "CoinDesk RSS 수집에 실패했습니다."
        _coindesk_error_cache = (
            message,
            now + min(60, _COIN_CACHE_SECONDS),
        )
        if strict:
            raise NewsFetchError(message) from exc
        return []


def _fetch_openeden_news(*, strict: bool = False) -> list[dict]:
    """Fetch OpenEden's official hourly RSS for the EDEN asset only."""
    global _openeden_cache, _openeden_error_cache

    now = time.time()
    if _openeden_cache and _openeden_cache[1] > now:
        return [dict(item) for item in _openeden_cache[0]]
    if _openeden_error_cache and _openeden_error_cache[1] > now:
        if strict:
            raise NewsFetchError(_openeden_error_cache[0])
        return []

    try:
        def load():
            resp = get_http_client().get(
                _OPENEDEN_RSS,
                timeout=_HTTP_TIMEOUT,
                headers={
                    "Accept": "application/rss+xml, application/xml;q=0.9",
                    "User-Agent": "gg-parrot-news-collector/1.0",
                },
            )
            resp.raise_for_status()
            return _parse_openeden_rss(resp.text)

        if _openeden_cache:
            items, state = _rss_refreshes.run(
                "openeden",
                load,
                stale_value=_openeden_cache[0],
            )
        else:
            items, state = _rss_refreshes.run("openeden", load)
        if state == "stale":
            return [dict(item) for item in items]
        _openeden_cache = (items, now + _OPENEDEN_CACHE_SECONDS)
        _openeden_error_cache = None
        return [dict(item) for item in items]
    except Exception as exc:
        message = "OpenEden 공식 RSS 수집에 실패했습니다."
        _openeden_error_cache = (message, now + 60)
        if strict:
            raise NewsFetchError(message) from exc
        return []


def _summarize(items: list[dict], *, label: str) -> Optional[str]:
    """헤드라인만 근거로 한 중립 개요(3~4줄). 실패하면 None(개요 생략)."""
    if not items or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        headlines = "\n".join(f"- {it['title']} ({it['source']})" for it in items)
        system = (
            "너는 코인 초보(코린이)에게 '오늘의 코인 동향'을 짚어주는 도우미야. "
            "규칙: (1) 반드시 한국어, 쉬운 말. (2) 아래에 '주어진 헤드라인'에서 드러난 "
            "사실만 요약하고, 목록에 없는 내용이나 새 수치·가격 예측을 절대 지어내지 마. "
            "(3) '사라/팔아라/오른다/추천·수익보장' 같은 투자 조언·전망은 하지 마. "
            "(4) 3~4줄, 오늘 무슨 흐름·이슈가 있었는지 중립적으로. "
            "(5) 어려운 용어가 있으면 한 번만 괄호로 짧게 풀어줘."
        )
        user = (
            f"오늘의 {label} 관련 헤드라인이야. 이걸 근거로 오늘 흐름을 3~4줄로 "
            f"중립 요약해줘(제목 재나열 말고 종합):\n{headlines}"
        )
        key = ai_cache_key(
            "market-news-summary",
            _SUMMARY_PROMPT_VERSION,
            _ANTHROPIC_MODEL,
            {"label": label, "system": system, "headlines": headlines},
        )

        def load():
            response = get_anthropic_client().messages.create(
                model=_ANTHROPIC_MODEL,
                max_tokens=600,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text = (block.text or "").strip()
                    if text:
                        return text
            raise ValueError("empty market-news summary")

        return get_ai_runtime().call(key, load)[0]
    except Exception:
        return None
    return None


def _envelope(items: list[dict], *, overview: Optional[str], label: str, query: str) -> dict:
    return {
        "as_of": _kst_date(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": label,
        "overview": overview,
        "ai": overview is not None,
        "items": items,
        "query": query,
        "disclaimer": _DISCLAIMER,
    }


def get_market_news() -> dict:
    """시장·규제 전반 동향 — 헤드라인 + AI 중립 개요. KST 하루 1회 캐시."""
    global _market_summary_retry_at
    day = _kst_date()
    now = time.time()
    hit = _cache.get("market")
    if hit and hit[1] == day:
        cached = hit[0]
        needs_summary = (
            bool(os.environ.get("ANTHROPIC_API_KEY"))
            and cached.get("overview") is None
            and bool(cached.get("items"))
        )
        if not needs_summary or now < _market_summary_retry_at:
            return cached
        overview = _summarize(cached["items"], label="코인 시장·규제")
        if overview is None:
            _market_summary_retry_at = now + _MARKET_SUMMARY_RETRY_SECONDS
            return cached
        refreshed = _envelope(
            cached["items"],
            overview=overview,
            label="코인 시장·규제 동향",
            query=_MARKET_QUERY,
        )
        _cache["market"] = (refreshed, day)
        _market_summary_retry_at = 0.0
        return refreshed
    items = _fetch_news(_MARKET_QUERY)
    overview = _summarize(items, label="코인 시장·규제") if items else None
    env = _envelope(items, overview=overview, label="코인 시장·규제 동향", query=_MARKET_QUERY)
    if items:  # 빈 결과는 캐시하지 않음(일시적 실패일 수 있음)
        _cache["market"] = (env, day)
        _market_summary_retry_at = (
            now + _MARKET_SUMMARY_RETRY_SECONDS
            if os.environ.get("ANTHROPIC_API_KEY") and overview is None
            else 0.0
        )
    return env


def _coin_news_envelope(
    symbol: str,
    *,
    strict: bool,
    relevant_only: bool = False,
) -> dict:
    # This helper receives a base ticker from the public or worker ingress.
    base = canonical_asset_symbol(symbol)
    if not base:
        return _envelope([], overview=None, label="코인 뉴스", query="")
    name = _COIN_KO.get(base, base)
    queries = _COIN_GOOGLE_QUERIES.get(
        base,
        ((f"{name} 코인 when:7d", "ko"),),
    )
    batches: list[list[dict]] = []
    failures = 0
    candidate_count = 0
    candidate_limit = 50 if base == "EDEN" else 20

    def fetch_query(query: str, locale: str) -> tuple[list[dict], bool]:
        try:
            candidates = _fetch_news(
                query,
                limit=candidate_limit,
                strict=True,
                locale=locale,
            )
            return candidates, False
        except NewsFetchError:
            return [], True

    if len(queries) == 1:
        fetched = {0: fetch_query(*queries[0])}
    else:
        fetched = run_parallel(
            {
                index: (lambda query=query, locale=locale: fetch_query(query, locale))
                for index, (query, locale) in enumerate(queries)
            }
        )
    for index in range(len(queries)):
        candidates, failed = fetched[index]
        if failed:
            failures += 1
            continue
        candidate_count += len(candidates)
        batches.append(
            _relevant_items(
                candidates,
                asset_symbol=base,
                coin_name=name,
                feed_source="google_news_rss",
            )
            if relevant_only
            else candidates
        )
    if strict and failures == len(queries):
        raise NewsFetchError("Google News RSS 수집에 실패했습니다.")
    items = _merge_news_items(*batches)
    query_label = " | ".join(query for query, _locale in queries)
    env = _envelope(
        items,
        overview=None,
        label=f"{name} 뉴스",
        query=query_label,
    )
    env["symbol"] = base
    env["coin_name"] = name
    env["refresh_seconds"] = _COIN_CACHE_SECONDS
    env["candidate_count"] = candidate_count
    return env


def fetch_coin_news_for_collector(symbol: str) -> dict:
    """Fetch and merge ticker-relevant items from every configured RSS source."""
    base = canonical_asset_symbol(symbol)
    if not base:
        return _envelope([], overview=None, label="코인 뉴스", query="")
    name = _COIN_KO.get(base, base)
    query = f"{name} 코인 when:7d"

    def fetch_google():
        try:
            return _coin_news_envelope(base, strict=True, relevant_only=True), True
        except NewsFetchError:
            return None, False

    def fetch_source(loader):
        try:
            return loader(strict=True), True
        except NewsFetchError:
            return [], False

    loaders = {
        "google": fetch_google,
        "coindesk": lambda: fetch_source(_fetch_coindesk_news),
    }
    if base == "EDEN":
        loaders["openeden"] = lambda: fetch_source(_fetch_openeden_news)
    fetched_sources = run_parallel(loaders)

    google_payload, google_available = fetched_sources["google"]
    google_payload = google_payload or {}
    google_raw = list(google_payload.get("items") or [])
    google_fetched_count = int(google_payload.get("candidate_count") or len(google_raw))
    google_query = str(google_payload.get("query") or query)
    coindesk_raw, coindesk_available = fetched_sources["coindesk"]
    openeden_items, openeden_available = fetched_sources.get("openeden", ([], False))

    if not google_available and not coindesk_available and not openeden_available:
        raise NewsFetchError("모든 뉴스 RSS 소스 수집에 실패했습니다.")

    google_items = _relevant_items(
        google_raw,
        asset_symbol=base,
        coin_name=name,
        feed_source="google_news_rss",
    )
    coindesk_items = _relevant_items(
        coindesk_raw,
        asset_symbol=base,
        coin_name=name,
        feed_source="coindesk_rss",
    )
    items = _merge_news_items(openeden_items, coindesk_items, google_items)
    env = _envelope(
        items,
        overview=None,
        label=f"{name} 뉴스",
        query=google_query,
    )
    env["symbol"] = base
    env["coin_name"] = name
    env["refresh_seconds"] = _COIN_CACHE_SECONDS
    sources = []
    if base == "EDEN":
        sources.append({
            "name": "openeden_official_rss",
            "status": "ready" if openeden_available else "error",
            "item_count": len(openeden_items),
            "fetched_count": len(openeden_items),
        })
    sources.extend([
        {
            "name": "coindesk_rss",
            "status": "ready" if coindesk_available else "error",
            "item_count": len(coindesk_items),
            "fetched_count": len(coindesk_raw),
        },
        {
            "name": "google_news_rss",
            "status": "ready" if google_available else "error",
            "item_count": len(google_items),
            "fetched_count": google_fetched_count,
        },
    ])
    env["sources"] = sources
    return env


def _load_latest_coin_snapshot(symbol: str) -> dict | None:
    """Load the worker-owned snapshot lazily to avoid an import cycle."""
    from .agent_features.position_news.repository import get_latest_snapshot

    return get_latest_snapshot(symbol)


def get_coin_news(symbol: str) -> dict:
    """Return a central snapshot first, with request-time RSS as fallback."""
    base = asset_from_market_symbol(symbol)
    if not base:
        return _envelope([], overview=None, label="코인 뉴스", query="")
    if base in position_news_collection_universe():
        try:
            stored = _load_latest_coin_snapshot(base)
        except Exception:
            # The public briefing remains available during a transient DB
            # outage. Authenticated agent reads intentionally stay DB-only.
            stored = None
        if stored is not None and isinstance(stored.get("news_payload"), dict):
            env = dict(stored["news_payload"])
            env["data_source"] = "prefect_db"
            env["snapshot_id"] = str(stored.get("snapshot_id") or "")
            env["collection"] = dict(stored.get("collection") or {})
            return env
    ckey = f"coin:{base}"
    hit = _coin_cache.get(ckey)
    if hit and hit[1] > time.time():
        return hit[0]
    env = _coin_news_envelope(base, strict=False)
    env["data_source"] = "rss_cache"
    if env.get("items"):
        _coin_cache[ckey] = (env, time.time() + _COIN_CACHE_SECONDS)
    return env
