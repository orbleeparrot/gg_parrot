"""Shared Korean public community headlines, independent of global news/AI.

Only fixed public community listings are fetched; never post bodies, authors or
user-supplied URLs. HTTP readers query stored headlines and listing snapshots.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import logging
import os
import re
import threading
import time
from contextlib import nullcontext
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import BigInteger
from sqlmodel import Field, SQLModel, delete, select

from .data import krw
from .cache_runtime import ResponseCache
from .db import get_session
from .exchanges import validate_symbol
from .http_runtime import get_http_client
from .public_news import PublicNewsLease, claim_work, finish_work

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/community", tags=["community"])
SCOPE = "DOMESTIC_COMMUNITY"
SOURCE = "디시인사이드 비트코인 갤러리"
LIST_URL = "https://gall.dcinside.com/board/lists/?id=bitcoins_new1"
ROBOTS_URL = "https://gall.dcinside.com/robots.txt"
SOURCES = (
    {"id": "dcinside_bitcoin", "name": SOURCE, "url": LIST_URL, "robots_url": ROBOTS_URL},
    {"id": "tokenpost_crypto", "name": "토큰포스트 암호화폐", "url": "https://www.tokenpost.kr/forum/crypto",
     "robots_url": "https://www.tokenpost.kr/robots.txt"},
    {"id": "tokenpost_chart", "name": "토큰포스트 차트분석", "url": "https://www.tokenpost.kr/forum/chart",
     "robots_url": "https://www.tokenpost.kr/robots.txt"},
    {"id": "coinpan_free", "name": "코인판 자유게시판", "url": "https://coinpan.com/free",
     "robots_url": "https://coinpan.com/robots.txt"},
)
USER_AGENT = "gg-parrot-community/1.0"
MAX_ITEMS = 2000
MAX_READ_ITEMS = 100
RETENTION_SECONDS = 7 * 86400
LISTING_MAX_AGE_SECONDS = 300
STALE_SECONDS = 180
REFRESH_SECONDS = 60
_KST = timezone(timedelta(hours=9))
_robots_lock = threading.Lock()
_robots_cache: tuple[float, RobotFileParser] | None = None
_source_robots_cache: dict[str, tuple[float, RobotFileParser]] = {}
_runtime_lock = threading.Lock()
_runtime = None
_responses = ResponseCache("domestic-community", max_entries=1, max_bytes=4_000_000,
                           retry_seconds=5, max_retry_seconds=30)


class DomesticCommunityItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    url: str
    source: str = SOURCE
    published: str
    published_ms: int = Field(sa_type=BigInteger, index=True)
    collected_ms: int = Field(sa_type=BigInteger)
    asset_symbols_json: str = "[]"


class DomesticCommunityState(SQLModel, table=True):
    scope: str = Field(default=SCOPE, primary_key=True)
    status: str = "pending"
    last_success_ms: int = Field(default=0, sa_type=BigInteger)
    last_attempt_ms: int = Field(default=0, sa_type=BigInteger)
    failures: int = 0
    sources_json: str = "[]"
    listings_json: str = "{}"


def _iso(millis: int) -> str:
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()


def _clean(text: str, limit=240) -> str:
    return re.sub(r"\s+", " ", "".join(c for c in text if c.isprintable() or c.isspace())).strip()[:limit]


def canonical_post_url(raw: str) -> str | None:
    url = urlsplit(urljoin(LIST_URL, raw))
    query = parse_qs(url.query)
    number = query.get("no", [""])[0]
    if (url.scheme != "https" or url.netloc != "gall.dcinside.com"
            or url.path != "/board/view/" or query.get("id") != ["bitcoins_new1"]
            or not re.fullmatch(r"[0-9]{1,14}", number)):
        return None
    return f"https://gall.dcinside.com/board/view/?id=bitcoins_new1&no={number}"


def _source(source_id: str) -> dict:
    source = next((source for source in SOURCES if source["id"] == source_id), None)
    if source is None:
        raise ValueError("unknown domestic community source")
    return source


def canonical_source_post_url(source_id: str, raw: str) -> str | None:
    source = _source(source_id)
    if source_id == "dcinside_bitcoin":
        return canonical_post_url(raw)
    url = urlsplit(urljoin(source["url"], raw))
    origin = urlsplit(source["url"])
    pattern = re.escape(origin.path) + r"/[0-9]{1,14}"
    if url.scheme != "https" or url.netloc != origin.netloc or not re.fullmatch(pattern, url.path):
        return None
    return f"https://{origin.netloc}{url.path}"


def source_id_for_url(url: str) -> str | None:
    return next((source["id"] for source in SOURCES
                 if canonical_source_post_url(source["id"], url)), None)


class _ListingParser(HTMLParser):
    """Read just the first title link, numeric ID and full timestamp per row."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = ""
        self.anchor = False
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "tr":
            self.row = {"number": "", "title": "", "url": "", "date": ""} if "ub-content" in classes else None
        if self.row is None:
            return
        if tag == "td":
            self.cell = next((c for c in classes if c in {"gall_num", "gall_tit", "gall_date"}), "")
            if self.cell == "gall_date":
                self.row["date"] = attrs.get("title", "")
        if tag == "a" and self.cell == "gall_tit" and not self.row["url"]:
            url = canonical_post_url(attrs.get("href", ""))
            if url:
                self.row["url"] = url
                self.anchor = True
        if tag in {"script", "style"}:
            self.ignore += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignore = max(0, self.ignore - 1)
        if tag == "a":
            self.anchor = False
        if tag == "td":
            self.cell = ""
        if tag == "tr" and self.row is not None:
            if self.row["number"].strip().isdigit() and self.row["url"]:
                self.rows.append(self.row)
            self.row = None
            self.anchor = False

    def handle_data(self, data):
        if self.row is None or self.ignore:
            return
        if self.cell == "gall_num":
            self.row["number"] += data
        elif self.cell == "gall_tit" and self.anchor:
            self.row["title"] += data


class _PublicListingParser(HTMLParser):
    """Read public title/date containers only, never profile or body markup."""
    def __init__(self, source_id):
        super().__init__(convert_charrefs=True)
        self.source_id = source_id
        self.rows, self.stack = [], []
        self.row = None
        self.row_depth = self.title_depth = self.date_depth = self.anchor_depth = 0
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"img", "br", "meta", "link", "input", "hr", "wbr", "source"}:
            return
        self.stack.append(tag)
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        row_tag = "tr" if self.source_id == "coinpan_free" else "section"
        if tag == row_tag and self.row is None:
            self.row = {"title": "", "url": "", "date": "", "notice": bool(classes & {"notice", "board_list_notice"})}
            self.row_depth = len(self.stack)
        if self.row is None:
            return
        if tag in {"script", "style"}:
            self.ignore += 1
        if (tag == "td" and "title" in classes) or (tag == "p" and "list_item_title" in classes):
            self.title_depth = len(self.stack)
        if (tag == "td" and "time" in classes) or (tag == "span" and "community_date" in classes):
            self.date_depth = len(self.stack)
        if tag == "a" and self.title_depth and not self.row["url"]:
            url = canonical_source_post_url(self.source_id, attrs.get("href", ""))
            if url:
                self.row["url"], self.anchor_depth = url, len(self.stack)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.row is not None:
            if tag in {"script", "style"}:
                self.ignore = max(0, self.ignore - 1)
            if depth <= self.anchor_depth:
                self.anchor_depth = 0
            if depth <= self.title_depth:
                self.title_depth = 0
            if depth <= self.date_depth:
                self.date_depth = 0
            if depth == self.row_depth:
                if self.row["url"] and not self.row["notice"]:
                    self.rows.append(self.row)
                self.row = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.row is None or self.ignore:
            return
        if self.anchor_depth:
            self.row["title"] += data
        elif self.date_depth:
            self.row["date"] += data


_ALIASES = {"BTC": ("비트코인", "비트"), "ETH": ("이더리움", "이더"),
            "XRP": ("리플",), "DOGE": ("도지코인", "도지"), "SOL": ("솔라나",)}
_AMBIGUOUS = frozenset({"A", "I", "S", "T", "W", "X", "SC", "GAS", "ME", "ON", "SIGN", "MOVE", "HIGH", "OPEN", "IQ"})
_COIN_CONTEXT = r"코인|토큰|가격|상승|하락|매수|매도|시세|상장|원화|거래|반등|펌핑|숏|롱|차트"
_PROMOTION = re.compile(r"수익\s*보장|원금\s*보장|오픈\s*카톡|오픈\s*채팅|추천인|레퍼럴|증정금|리딩\s*방|텔레그램\s*(?:입장|문의)|가입.*(?:할인|혜택)", re.I)


def asset_matchers(listings: dict) -> dict[str, list[re.Pattern]]:
    result = {}
    for listing in listings.values():
        for row in listing.get("items", []):
            asset = str(row.get("base") or str(row.get("symbol", ""))[4:]).upper()
            if not re.fullmatch(r"[A-Z0-9]{1,20}", asset):
                continue
            patterns = result.setdefault(asset, [])
            aliases = {*_ALIASES.get(asset, ()), str(row.get("korean_name") or "").strip()}
            for alias in aliases:
                if len(alias) >= 2:
                    # Korean particles may follow a name, but embedded unrelated
                    # words (e.g. 비트맵/도지사) must not become asset mentions.
                    patterns.append(re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(alias)
                                               + r"(?=$|[^가-힣A-Za-z0-9]|은|는|이|가|을|를|의|에|도|만|랑|와|과)"))
            english = str(row.get("english_name") or "").strip()
            if len(english) >= 4 and english.upper() not in _AMBIGUOUS:
                patterns.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(english) + r"(?![A-Za-z0-9])", re.I))
            # Bare tickers must be uppercase. Ambiguous short/common tickers
            # need explicit $ or KRW- notation; lowercase "sc"/"icp" is noise.
            prefix = r"(?:\$|KRW-)" if len(asset) < 3 or asset in _AMBIGUOUS else r"(?:\$|KRW-)?"
            ticker_pattern = r"(?<![A-Za-z0-9])" + prefix + re.escape(asset) + r"(?![A-Za-z0-9])"
            if asset == "ICP":
                # ICP is also a laboratory/medical acronym. Bare mentions need
                # market context; explicit market/cashtag notation stands alone.
                patterns.append(re.compile(r"(?<![A-Za-z0-9])(?:\$|KRW-)ICP(?![A-Za-z0-9])"))
                ticker_pattern = r"^(?=.*(?:" + _COIN_CONTEXT + r"))(?=.*" + ticker_pattern + ")"
            patterns.append(re.compile(ticker_pattern))
    return result


def parse_listing(html: str, listings: dict, *, now_ms=None) -> tuple[list[dict], dict]:
    return parse_source_listing("dcinside_bitcoin", html, listings, now_ms=now_ms)


def _published(value: str, source_id: str, now_ms: int) -> tuple[int, str, str]:
    value = _clean(value)
    if source_id == "dcinside_bitcoin":
        published = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
        precision = "second"
    elif re.fullmatch(r"\d{2}:\d{2}", value):
        today = datetime.fromtimestamp(now_ms / 1000, _KST).strftime("%Y-%m-%d")
        published = datetime.strptime(f"{today} {value}", "%Y-%m-%d %H:%M").replace(tzinfo=_KST)
        precision = "minute"
    else:
        published = datetime.strptime(value, "%Y.%m.%d").replace(tzinfo=_KST)
        precision = "day"
    millis = int(published.timestamp() * 1000)
    return millis, published.date().isoformat() if precision == "day" else _iso(millis), precision


def parse_source_listing(source_id: str, html: str, listings: dict, *, now_ms=None) -> tuple[list[dict], dict]:
    source = _source(source_id)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    parser = _ListingParser() if source_id == "dcinside_bitcoin" else _PublicListingParser(source_id)
    parser.feed(html)
    if not parser.rows:
        raise ValueError("public listing has no recognized normal rows")
    matchers = asset_matchers(listings)
    stats = {"normal_rows": len(parser.rows), "irrelevant": 0, "invalid_date": 0, "duplicates": 0}
    items, seen = [], set()
    for row in parser.rows[:100]:
        title = _clean(row["title"])
        assets = sorted(asset for asset, patterns in matchers.items() if any(p.search(title) for p in patterns))
        if not title or not re.search(r"[가-힣]", title) or not assets or _PROMOTION.search(title):
            stats["irrelevant"] += 1
            continue
        try:
            published_ms, published, precision = _published(row["date"], source_id, now_ms)
        except (ValueError, TypeError):
            stats["invalid_date"] += 1
            continue
        if not now_ms - RETENTION_SECONDS * 1000 <= published_ms <= now_ms + 300_000:
            stats["invalid_date"] += 1
            continue
        identity = sha256(row["url"].encode()).hexdigest()
        if identity in seen:
            stats["duplicates"] += 1
            continue
        seen.add(identity)
        items.append({"id": identity, "title": title, "url": row["url"], "source": source["name"],
                      "source_id": source_id, "published_precision": precision,
                      "published": published, "published_ms": published_ms,
                      "asset_symbols": assets})
    return items, {**stats, "matched": len(items)}


def _fetch_text(url: str, *, max_bytes: int) -> str:
    if url not in {value for source in SOURCES for value in (source["url"], source["robots_url"])}:
        raise ValueError("source URL is not allowlisted")
    with get_http_client().stream("GET", url, headers={"User-Agent": USER_AGENT},
                                  timeout=8.0, follow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("source must return HTTP 200 without redirect")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        expected = {"text/plain", "text/html"} if url.endswith("robots.txt") else {"text/html", "application/xhtml+xml"}
        if content_type not in expected:
            raise ValueError("unexpected public source content type")
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("public source response exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")


def fetch_listing() -> str:
    global _robots_cache
    with _robots_lock:
        if _robots_cache is None or time.monotonic() - _robots_cache[0] > 3600:
            robots = RobotFileParser(ROBOTS_URL)
            robots.parse(_fetch_text(ROBOTS_URL, max_bytes=64_000).splitlines())
            _robots_cache = (time.monotonic(), robots)
        if not _robots_cache[1].can_fetch(USER_AGENT, LIST_URL):
            raise ValueError("public source robots policy disallows collection")
    return _fetch_text(LIST_URL, max_bytes=512_000)


def fetch_source_listing(source_id: str) -> str:
    if source_id == "dcinside_bitcoin":
        return fetch_listing()
    source = _source(source_id)
    with _robots_lock:
        cached = _source_robots_cache.get(source["robots_url"])
        if cached is None or time.monotonic() - cached[0] > 3600:
            robots = RobotFileParser(source["robots_url"])
            robots.parse(_fetch_text(source["robots_url"], max_bytes=64_000).splitlines())
            cached = (time.monotonic(), robots)
            _source_robots_cache[source["robots_url"]] = cached
        if not cached[1].can_fetch(USER_AGENT, source["url"]):
            raise ValueError("public source robots policy disallows collection")
    return _fetch_text(source["url"], max_bytes=512_000)


def _fresh_listings(listings: dict, now_ms: int) -> dict:
    return {exchange: entry for exchange, entry in listings.items()
            if -5000 <= now_ms - entry.get("fetched_ms", 0) <= LISTING_MAX_AGE_SECONDS * 1000}


def collect_once(*, now_ms=None, stop_event=None, write_lock=None) -> dict:
    """One process holds a durable lease; source/network work is outside SQL."""
    fixed_clock = now_ms is not None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    def cancelled():
        return stop_event is not None and stop_event.is_set()

    with write_lock or nullcontext():
        if cancelled():
            return {"status": "cancelled", "retry_seconds": REFRESH_SECONDS}
        token = claim_work(SCOPE, now_ms=now_ms)
    if not token:
        return {"status": "skipped", "retry_seconds": REFRESH_SECONDS}
    retry_seconds = REFRESH_SECONDS
    try:
        with get_session() as db:
            previous = db.get(DomesticCommunityState, SCOPE)
            listings = json.loads(previous.listings_json) if previous else {}
            previous_sources = {source["id"]: source for source in json.loads(previous.sources_json)} if previous else {}
        sources = []
        # Exchange catalogs must refresh on their own 60s cadence, even when
        # every community source is in a longer durable failure cooldown.
        for exchange in ("upbit", "bithumb"):
            if cancelled():
                return {"status": "cancelled", "retry_seconds": REFRESH_SECONDS}
            try:
                payload = krw.list_symbols(exchange)
                fetched_ms = int(payload["fetched_at"] * 1000)
                if not -5000 <= now_ms - fetched_ms <= LISTING_MAX_AGE_SECONDS * 1000:
                    raise ValueError("exchange listing expired")
                listings[exchange] = {"items": payload["items"], "fetched_ms": fetched_ms}
                sources.append({"id": exchange, "status": "ready", "fetched_at": _iso(fetched_ms)})
            except Exception as exc:
                sources.append({"id": exchange, "status": "error", "error": type(exc).__name__})
        items = []
        attempted = 0
        successes = 0
        observed_ms = now_ms
        for source in SOURCES:
            if cancelled():
                return {"status": "cancelled", "retry_seconds": REFRESH_SECONDS}
            old = previous_sources.get(source["id"], {})
            current = {"id": source["id"], "name": source["name"], **old}
            attempt_ms = now_ms if fixed_clock else int(time.time() * 1000)
            if old.get("next_attempt_ms", 0) > attempt_ms:
                sources.append(current)
                continue
            try:
                usable = _fresh_listings(listings, observed_ms)
                if not usable:
                    # No request was sent. Do not pretend a source attempt or
                    # increase the source's network failure/backoff counter.
                    current.update(status="pending", waiting_for="exchange_listings")
                    sources.append(current)
                    continue
                attempted += 1
                current["last_attempt_ms"] = attempt_ms
                html = fetch_source_listing(source["id"])
                observed_ms = now_ms if fixed_clock else int(time.time() * 1000)
                parsed, stats = parse_source_listing(source["id"], html, usable, now_ms=observed_ms)
                items.extend(parsed)
                successes += 1
                current = {"id": source["id"], "name": source["name"], "status": "ready", **stats,
                           "failures": 0, "last_attempt_ms": attempt_ms, "last_success_ms": observed_ms,
                           "next_attempt_ms": observed_ms + REFRESH_SECONDS * 1000}
            except Exception as exc:
                observed_ms = now_ms if fixed_clock else int(time.time() * 1000)
                failures = min(old.get("failures", 0) + 1, 10)
                current.update(status="error", error=type(exc).__name__, failures=failures,
                               next_attempt_ms=observed_ms + min(900, REFRESH_SECONDS * 2 ** failures) * 1000)
            sources.append(current)
        community_sources = [source for source in sources if source["id"] in {s["id"] for s in SOURCES}]
        ready_count = sum(source.get("status") == "ready" for source in community_sources)
        status = ("ready" if ready_count == len(SOURCES) else "partial" if ready_count
                  else "error" if any(s.get("status") == "error" for s in community_sources) else "pending")
        with write_lock or nullcontext(), get_session() as db:
            if cancelled():
                return {"status": "cancelled", "retry_seconds": REFRESH_SECONDS}
            lease = db.exec(select(PublicNewsLease).where(PublicNewsLease.scope == SCOPE).with_for_update()).one_or_none()
            if lease is None or lease.token != token or lease.lease_until_ms <= int(time.time() * 1000):
                raise RuntimeError("domestic community publication lease lost")
            state = db.get(DomesticCommunityState, SCOPE) or DomesticCommunityState()
            state.status = status
            if attempted:
                state.last_attempt_ms = now_ms
            state.sources_json = json.dumps(sources, ensure_ascii=False)
            state.listings_json = json.dumps(listings, ensure_ascii=False)
            if attempted:
                state.failures = 0 if successes else min(state.failures + 1, 10)
            if successes:
                state.last_success_ms = observed_ms
            for item in items:
                row = db.get(DomesticCommunityItem, item["id"])
                if row is None:
                    db.add(DomesticCommunityItem(**{k: item[k] for k in
                           ("id", "title", "url", "source", "published", "published_ms")},
                           collected_ms=observed_ms, asset_symbols_json=json.dumps(item["asset_symbols"])))
                else:
                    row.title = item["title"]
                    row.asset_symbols_json = json.dumps(item["asset_symbols"])
                    db.add(row)
            db.add(state)
            db.exec(delete(DomesticCommunityItem).where(DomesticCommunityItem.published_ms < observed_ms - RETENTION_SECONDS * 1000))
            db.flush()
            overflow = db.exec(select(DomesticCommunityItem.id).order_by(
                DomesticCommunityItem.published_ms.desc(), DomesticCommunityItem.id).offset(MAX_ITEMS)).all()
            if overflow:
                db.exec(delete(DomesticCommunityItem).where(DomesticCommunityItem.id.in_(overflow)))
            db.commit()
        _responses.clear()
        return {"status": status, "item_count": len(items), "attempted_sources": attempted,
                "sources": sources, "retry_seconds": REFRESH_SECONDS}
    except Exception:
        retry_seconds = REFRESH_SECONDS
        raise
    finally:
        with write_lock or nullcontext():
            # Cancelled collectors leave the short lease to expire. A stopped
            # runtime must not write after the application closes its DB pool.
            if not cancelled():
                finish_work(SCOPE, token, retry_seconds=retry_seconds)


def _read_snapshot() -> dict:
    with get_session() as db:
        state = db.get(DomesticCommunityState, SCOPE)
        if state is None:
            return {"state": None, "items": []}
        rows = db.exec(select(DomesticCommunityItem).order_by(
            DomesticCommunityItem.published_ms.desc(), DomesticCommunityItem.id).limit(MAX_ITEMS)).all()
        return {"state": {"status": state.status, "last_success_ms": state.last_success_ms,
                           "sources": json.loads(state.sources_json), "listings": json.loads(state.listings_json)},
                "items": [{"id": row.id, "title": row.title, "url": row.url, "source": row.source,
                           "published": row.published, "published_ms": row.published_ms,
                           "asset_symbols": json.loads(row.asset_symbols_json)} for row in rows]}


def get_domestic_community(exchange: str, symbol: str | None = None, *, q: str = "", source: str | None = None,
                           limit: int = MAX_READ_ITEMS, now_ms=None) -> dict:
    if exchange not in {"upbit", "bithumb"}:
        raise ValueError("국내 커뮤니티는 업비트 또는 빗썸을 선택하세요.")
    symbol = validate_symbol(symbol, exchange) if symbol else None
    if not isinstance(q, str) or len(q) > 100:
        raise ValueError("검색어는 100자 이하여야 합니다.")
    if source is not None:
        _source(source)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_READ_ITEMS:
        raise ValueError("결과 수는 1~100개여야 합니다.")
    query = _clean(q, 100).casefold()
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    result = {"exchange": exchange, "items": [], "collection": {"status": "pending", "sources": []},
              "refresh_seconds": REFRESH_SECONDS, "total": 0, "limit": limit, "query": _clean(q, 100),
              "source_filter": source, "search_scope": "prepared_headlines_last_7_days",
              "available_sources": [{"id": s["id"], "name": s["name"]} for s in SOURCES],
              "notice": "국내 공개 커뮤니티에서 선택한 거래소의 원화 상장 종목을 언급한 글입니다. 작성자의 이용 거래소와는 무관하며 투자 조언이 아닙니다."}
    if symbol:
        result["symbol"] = symbol
    snapshot, cache_state = _responses.get_or_load("shared", _read_snapshot, ttl=10, stale_ttl=10)
    state = snapshot["state"]
    if state is None:
        return result
    sources = state["sources"]
    listing = _fresh_listings(state["listings"], now_ms).get(exchange)
    status = state["status"]
    stale = bool(state["last_success_ms"] and now_ms - state["last_success_ms"] > STALE_SECONDS * 1000)
    exchange_failed = any(s.get("id") == exchange and s.get("status") == "error" for s in sources)
    if status == "ready" and (stale or not listing or exchange_failed or cache_state == "stale"):
        status = "stale"
    result["collection"] = {"status": status, "sources": sources}
    if state["last_success_ms"]:
        result["collection"]["last_success_at"] = _iso(state["last_success_ms"])
    if status in {"stale", "partial", "error"}:
        result["stale"] = True
    if not listing:
        result["notice"] += " 현재 거래소 상장 목록을 확인하지 못해 글을 표시하지 않습니다."
        return result
    allowed = {row["base"] for row in listing["items"]}
    if symbol and symbol[4:] not in allowed:
        raise ValueError("선택한 거래소의 원화 상장 종목이 아닙니다.")
    query_assets = {item["base"] for item in listing["items"] if query and any(query in str(item.get(key, "")).casefold()
                    for key in ("base", "symbol", "korean_name", "english_name"))}
    for row in snapshot["items"]:
        if row["published_ms"] < now_ms - RETENTION_SECONDS * 1000:
            continue
        assets = sorted(set(row["asset_symbols"]) & allowed)
        if not assets or symbol and symbol[4:] not in assets:
            continue
        source_id = source_id_for_url(row["url"])
        if source_id is None or source and source_id != source:
            continue
        if query and query not in row["title"].casefold() and not set(assets) & query_assets:
            continue
        result["total"] += 1
        if len(result["items"]) >= limit:
            continue
        result["items"].append({key: row[key] for key in ("id", "title", "url", "source", "published")})
        result["items"][-1].update(excerpt="", content_type="community", language="ko", region="kr", asset_symbols=assets,
            source_id=source_id, published_precision="day" if "T" not in row["published"] else
            "minute" if source_id == "coinpan_free" else "second")
    return result


@router.get("/domestic")
def domestic_community_feed(exchange: str = Query("upbit"), symbol: str | None = Query(None, max_length=24),
                            q: str = Query("", max_length=100), source: str | None = Query(None, max_length=40),
                            limit: int = Query(MAX_READ_ITEMS, ge=1, le=MAX_READ_ITEMS)):
    try:
        return get_domestic_community(exchange, symbol, q=q, source=source, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class _Runtime:
    def __init__(self):
        self.stop = threading.Event()
        self.write_lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name="domestic-community", daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                collect_once(stop_event=self.stop, write_lock=self.write_lock)
                delay = REFRESH_SECONDS
            except Exception as exc:
                logger.warning("domestic community collection failed type=%s", type(exc).__name__)
                delay = REFRESH_SECONDS
            self.stop.wait(delay)


def start_runtime() -> None:
    global _runtime
    if os.environ.get("DOMESTIC_COMMUNITY_ENABLED", "true").strip().lower() not in {"true", "1", "yes"}:
        return
    with _runtime_lock:
        if _runtime is None:
            _runtime = _Runtime()
            _runtime.thread.start()


def stop_runtime() -> None:
    global _runtime
    with _runtime_lock:
        runtime, _runtime = _runtime, None
    if runtime:
        runtime.stop.set()
        # Wait only for a current SQL mutation, not an outbound source timeout.
        # Every later mutation checks stop while holding this same lock.
        with runtime.write_lock:
            pass
        runtime.thread.join(timeout=1)
