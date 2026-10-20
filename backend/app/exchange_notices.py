"""거래소 공식 공지(상장 · 유의 · 입출금 · 에어드랍) — 잡코인은 기사가 거의 없어 공지가 가장 확실한 소식이다.

빗썸: 공식 REST `GET https://api.bithumb.com/v1/notices?count=20`(키 없음, IP별 초당 1회, 최신순 최대 20개).
이 주소는 공지 전용 서버 `feed-api.bithumb.com` 으로 302 를 보내므로 그 한 곳만 따라간다. 웹 공지 화면
(`feed.bithumb.com/notice`)은 Cloudflare 가 막는 사람용 페이지라 읽지 않는다(2026-10-20 조사).
업비트: 공식은 Private WebSocket(API 키 필요, 새 공지만 실시간)뿐이라 키를 받기 전에는 '연결 전'.

공지 제목의 괄호 속 티커(`콘크리트(CT) 원화 마켓 추가`, `셀레스티아(TIA), 만타(MANTA) …`)로 코인을 잇는다 —
제목에 거래소가 직접 적은 표기라 기사처럼 이름이 겹쳐 잘못 잇는 일이 적다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import BigInteger, delete
from sqlmodel import Field, SQLModel, select

from . import feed_index
from .db import get_session
from .http_runtime import get_http_client
from .public_news import PublicNewsLease, claim_work, finish_work

logger = logging.getLogger(__name__)

SCOPE = "EXCHANGE_NOTICES"
FEED = feed_index.feed_key("kr", "notice")
BITHUMB_URL = "https://api.bithumb.com/v1/notices?count=20"
_BITHUMB_REDIRECT_HOST = "feed-api.bithumb.com"
_BITHUMB_NOTICE_URL = re.compile(r"https://feed\.bithumb\.com/notice/(\d{1,12})")
REFRESH_SECONDS = 60
RETENTION_SECONDS = 30 * 86400
MAX_ITEMS = 1000
MAX_BYTES = 256_000
_KST = timezone(timedelta(hours=9))
# 괄호 하나에 티커 하나 — "(KRW, BTC, USDT 마켓)" 같은 마켓 목록이나 "(10/20 오전 9시~)" 는 잡히지 않는다.
_TICKER = re.compile(r"\(([A-Z0-9]{2,15})\)")
_NOT_COINS = frozenset({"KRW"})

_runtime_lock = threading.Lock()
_runtime = None


class ExchangeNotice(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "bithumb:1655276"
    exchange: str
    title: str
    url: str
    categories_json: str = "[]"
    published: str
    published_ms: int = Field(sa_type=BigInteger, index=True)
    modified_ms: int = Field(default=0, sa_type=BigInteger)
    collected_ms: int = Field(sa_type=BigInteger)
    asset_symbols_json: str = "[]"


class ExchangeNoticeState(SQLModel, table=True):
    exchange: str = Field(primary_key=True)
    status: str = "pending"
    last_attempt_ms: int = Field(default=0, sa_type=BigInteger)
    last_success_ms: int = Field(default=0, sa_type=BigInteger)
    failures: int = 0
    error: str = ""


def notice_assets(title: str) -> list[str]:
    return feed_index.clean_assets(t for t in _TICKER.findall(title) if t not in _NOT_COINS)


def _kst_ms(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST).timestamp() * 1000)


def parse_bithumb(payload, *, now_ms: int) -> list[dict]:
    """빗썸 응답을 검사해 저장할 모양으로 — 모양이 다른 줄은 버리고, 목록이 아니면 오류."""
    if not isinstance(payload, list):
        raise ValueError("bithumb notices must be a list")
    items, seen = [], set()
    for row in payload[:20]:
        if not isinstance(row, dict):
            continue
        title = " ".join(str(row.get("title") or "").split())[:300]
        match = _BITHUMB_NOTICE_URL.fullmatch(str(row.get("pc_url") or ""))
        categories = [str(c)[:20] for c in row.get("categories") or [] if isinstance(c, str)][:5]
        try:
            published_ms = _kst_ms(str(row.get("published_at") or ""))
            modified_ms = _kst_ms(str(row["modified_at"])) if row.get("modified_at") else published_ms
        except ValueError:
            continue
        # 예약 게시(조금 뒤 시각)는 받아 두되, 하루 넘게 앞선 시각은 잘못된 값으로 본다.
        if not title or not match or published_ms > now_ms + 86_400_000:
            continue
        identity = f"bithumb:{match.group(1)}"
        if identity in seen:
            continue
        seen.add(identity)
        items.append({"id": identity, "exchange": "bithumb", "title": title, "url": match.group(0),
                      "categories": categories, "published_ms": published_ms, "modified_ms": modified_ms,
                      "published": datetime.fromtimestamp(published_ms / 1000, timezone.utc).isoformat(),
                      "asset_symbols": notice_assets(title)})
    return items


def fetch_bithumb() -> list | dict:
    """문서의 주소 → 공지 서버 302 한 번만 따라간다(다른 곳으로 보내면 오류)."""
    client = get_http_client()
    url = BITHUMB_URL
    for _ in range(2):
        with client.stream("GET", url, headers={"Accept": "application/json"}, timeout=8.0,
                           follow_redirects=False) as response:
            if response.status_code in (301, 302, 307, 308) and url == BITHUMB_URL:
                target = response.headers.get("location", "")
                parts = urlsplit(target)
                if parts.scheme != "https" or parts.hostname != _BITHUMB_REDIRECT_HOST or not parts.path.startswith("/v1/notices"):
                    raise ValueError("bithumb notices redirected to an unexpected host")
                url = f"https://{_BITHUMB_REDIRECT_HOST}/v1/notices?count=20"
                continue
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("bithumb notices must be JSON")
            body, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("bithumb notices exceed size limit")
                body.append(chunk)
            return json.loads(b"".join(body))
    raise ValueError("bithumb notices redirected more than once")


def _store(db, items: list[dict], now_ms: int) -> None:
    """한 번에 읽고 한 번에 쓴다(운영 DB 왕복 약 80ms)."""
    ids = [item["id"] for item in items]
    existing = {row.id: row for row in db.exec(select(ExchangeNotice).where(ExchangeNotice.id.in_(ids))).all()} if ids else {}
    links = []
    for item in items:
        row = existing.get(item["id"]) or ExchangeNotice(id=item["id"], exchange=item["exchange"], collected_ms=now_ms,
                                                         title="", url="", published="", published_ms=0)
        row.title, row.url, row.published = item["title"], item["url"], item["published"]
        row.published_ms, row.modified_ms = item["published_ms"], item["modified_ms"]
        row.categories_json = json.dumps(item["categories"], ensure_ascii=False)
        row.asset_symbols_json = json.dumps(item["asset_symbols"])
        db.add(row)
        links.append((row.id, item["asset_symbols"], row.published_ms))
    db.flush()
    feed_index.replace_assets_many(db, FEED, links)
    cutoff = now_ms - RETENTION_SECONDS * 1000
    db.exec(delete(ExchangeNotice).where(ExchangeNotice.published_ms < cutoff))
    feed_index.prune(db, FEED, cutoff)
    overflow = db.exec(select(ExchangeNotice.id).order_by(ExchangeNotice.published_ms.desc(), ExchangeNotice.id)
                       .offset(MAX_ITEMS)).all()
    if overflow:
        db.exec(delete(ExchangeNotice).where(ExchangeNotice.id.in_(overflow)))
        feed_index.drop_items(db, FEED, overflow)


def collect_once(*, now_ms=None, stop_event=None, write_lock=None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms

    def cancelled():
        return stop_event is not None and stop_event.is_set()

    with write_lock or nullcontext():
        if cancelled():
            return {"status": "cancelled"}
        token = claim_work(SCOPE)  # 임대는 실제 시각 — now_ms 는 저장 · 보관 기간 계산에만
    if not token:
        return {"status": "skipped"}
    retry_seconds = REFRESH_SECONDS
    items, error = [], ""
    try:
        try:
            items = parse_bithumb(fetch_bithumb(), now_ms=now_ms)
        except Exception as exc:  # 네트워크 · 모양 오류는 상태에 남기고 다음 주기에 다시
            error = type(exc).__name__
        with write_lock or nullcontext(), get_session() as db:
            if cancelled():
                return {"status": "cancelled"}
            lease = db.exec(select(PublicNewsLease).where(PublicNewsLease.scope == SCOPE).with_for_update()).one_or_none()
            if lease is None or lease.token != token or lease.lease_until_ms <= int(time.time() * 1000):
                raise RuntimeError("exchange notices lease lost")
            state = db.get(ExchangeNoticeState, "bithumb") or ExchangeNoticeState(exchange="bithumb")
            state.last_attempt_ms = now_ms
            if error:
                state.status, state.error, state.failures = "error", error, min(state.failures + 1, 10)
                retry_seconds = min(900, REFRESH_SECONDS * 2 ** state.failures)
            else:
                _store(db, items, now_ms)
                state.status, state.error, state.failures, state.last_success_ms = "ready", "", 0, now_ms
            db.add(state)
            db.commit()
        return {"status": "error" if error else "ready", "item_count": len(items), "error": error}
    finally:
        with write_lock or nullcontext():
            if not cancelled():
                finish_work(SCOPE, token, retry_seconds=retry_seconds)


def get_exchange_notices(exchange: str, symbol_base: str | None = None, *, limit: int = 20, cursor=None) -> dict:
    """한 거래소의 공지 한 쪽(최신순). 코인을 고르면 그 코인 티커가 적힌 공지만."""
    if exchange != "bithumb":
        return {"items": [], "total": 0, "next_cursor": None,
                "collection": {"status": "not_configured", "exchange": exchange}}
    now_ms = int(time.time() * 1000)
    notice = ExchangeNotice
    conditions = [notice.exchange == exchange, notice.published_ms >= now_ms - RETENTION_SECONDS * 1000]
    if symbol_base:
        conditions.append(feed_index.mentioning(FEED, notice.id, [symbol_base]))
    with get_session() as db:
        statement = select(notice).where(*conditions)
        if cursor:
            statement = statement.where(feed_index.before(notice.published_ms, notice.id, tuple(cursor)))
        rows = db.exec(statement.order_by(notice.published_ms.desc(), notice.id.desc()).limit(limit + 1)).all()
        state = db.get(ExchangeNoticeState, exchange)
    more, rows = len(rows) > limit, rows[:limit]
    items = [{"id": row.id, "exchange": row.exchange, "title": row.title, "url": row.url,
              "categories": json.loads(row.categories_json), "published": row.published,
              "asset_symbols": json.loads(row.asset_symbols_json), "content_type": "official",
              "source": "빗썸 공지", "language": "ko", "region": "kr"} for row in rows]
    return {"items": items, "next_cursor": feed_index.encode_cursor(rows[-1].published_ms, rows[-1].id) if more else None,
            "collection": {"status": state.status if state else "pending", "exchange": exchange,
                           "last_success_ms": state.last_success_ms if state else 0}}


class _Runtime:
    def __init__(self):
        self.stop = threading.Event()
        self.write_lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name="exchange-notices", daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                collect_once(stop_event=self.stop, write_lock=self.write_lock)
            except Exception as exc:
                logger.warning("exchange notices collection failed type=%s", type(exc).__name__)
            self.stop.wait(REFRESH_SECONDS)


def start_runtime() -> None:
    global _runtime
    if os.environ.get("EXCHANGE_NOTICES_ENABLED", "true").strip().lower() not in {"true", "1", "yes"}:
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
        with runtime.write_lock:
            pass
        runtime.thread.join(timeout=1)
