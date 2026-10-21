"""거래소 공식 공지(상장 · 유의 · 입출금 · 에어드랍) — 잡코인은 기사가 거의 없어 공지가 가장 확실한 소식이다.

빗썸: 공식 REST `GET https://api.bithumb.com/v1/notices?count=20`(키 없음, IP별 초당 1회, 최신순 최대 20개).
이 주소는 공지 전용 서버 `feed-api.bithumb.com` 으로 302 를 보내므로 그 한 곳만 따라간다. 웹 공지 화면
(`feed.bithumb.com/notice`)은 Cloudflare 가 막는 사람용 페이지라 읽지 않는다(2026-10-20 조사).
업비트: 공식은 Private WebSocket `announcement`(JWT 필요, 키 권한 무관 · 허용 IP 필수, 연결 중 새 공지만 실시간 —
지난 공지 조회 없음)뿐. 운영 키 `UPBIT_ACCESS_KEY` · `UPBIT_SECRET_KEY` 가 있을 때만 한 프로세스가 임대를 잡고 연결을 유지한다.

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
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import jwt
from sqlalchemy import BigInteger, delete, func
from sqlmodel import Field, SQLModel, select

from . import feed_index
from .db import get_session
from .http_runtime import get_http_client
from .public_news import PublicNewsLease, claim_work, finish_work, renew_work

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
SOURCE_NAMES = {"bithumb": "빗썸 공지", "upbit": "업비트 공지"}

UPBIT_SCOPE = "EXCHANGE_NOTICES_UPBIT"
UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1/private"
_UPBIT_NOTICE_URL = re.compile(r"https://(?:www\.)?upbit\.com/service_center/notice\?id=(\d{1,15})(?:&[A-Za-z0-9_=&-]{0,60})?")
# 업비트 공지 분류 → 빗썸과 같은 글자. 거래 공지는 제목으로 유의 · 종료 · 상장을 가른다(화면이 유의 · 종료만 붉게).
_UPBIT_CATEGORY = {"trade": "거래", "wallet": "입출금", "maintenance": "점검", "digital_asset": "디지털 자산",
                   "notice": "안내", "nft": "NFT", "service": "서비스+", "event": "이벤트"}

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


def upbit_categories(category: str, title: str) -> list[str]:
    if category == "trade":
        if "유의" in title:
            return ["거래유의"]
        if re.search(r"거래\s*지원\s*종료", title):
            return ["거래지원종료"]
        if re.search(r"신규\s*거래\s*지원|마켓\s*추가", title):
            return ["마켓 추가"]
    return [_UPBIT_CATEGORY.get(category, "안내")]


def parse_upbit(message: dict, *, now_ms: int) -> dict | None:
    """공지 WebSocket 한 건 → 저장할 모양(모양이 다르면 None). 오류 메시지는 예외로."""
    if not isinstance(message, dict) or message.get("status") == "UP":
        return None
    if message.get("error"):
        raise ValueError(str((message["error"] or {}).get("name") or "upbit error")[:60])
    if message.get("type") != "announcement":
        return None
    title = re.sub(r"^\[[^\]]{1,10}\]\s*", "", " ".join(str(message.get("title") or "").split()))[:300]
    match = _UPBIT_NOTICE_URL.fullmatch(str(message.get("url") or ""))
    notice_id = str(message.get("uuid") or "")
    try:
        published = datetime.fromisoformat(str(message.get("first_listed_at") or message.get("listed_at")))
        listed = datetime.fromisoformat(str(message.get("listed_at") or message.get("first_listed_at")))
    except ValueError:
        return None
    published = published if published.tzinfo else published.replace(tzinfo=_KST)
    listed = listed if listed.tzinfo else listed.replace(tzinfo=_KST)
    published_ms, modified_ms = int(published.timestamp() * 1000), int(listed.timestamp() * 1000)
    if not title or not match or not re.fullmatch(r"\d{1,15}", notice_id) or published_ms > now_ms + 86_400_000:
        return None
    return {"id": f"upbit:{notice_id}", "exchange": "upbit", "title": title,
            "url": f"https://upbit.com/service_center/notice?id={match.group(1)}",
            "categories": upbit_categories(str(message.get("category") or ""), title),
            "published_ms": published_ms, "modified_ms": modified_ms,
            "published": datetime.fromtimestamp(published_ms / 1000, timezone.utc).isoformat(),
            "asset_symbols": notice_assets(title)}


def upbit_keys() -> tuple[str, str] | None:
    access, secret = os.environ.get("UPBIT_ACCESS_KEY", "").strip(), os.environ.get("UPBIT_SECRET_KEY", "").strip()
    return (access, secret) if access and secret else None


def _upbit_token(access: str, secret: str) -> str:
    return jwt.encode({"access_key": access, "nonce": str(uuid.uuid4())}, secret, algorithm="HS512")


def _save_state(exchange: str, *, status: str, error: str = "", success: bool = False) -> None:
    now_ms = int(time.time() * 1000)
    with get_session() as db:
        state = db.get(ExchangeNoticeState, exchange) or ExchangeNoticeState(exchange=exchange)
        state.status, state.error, state.last_attempt_ms = status, error[:120], now_ms
        state.failures = 0 if success or status == "ready" else min(state.failures + 1, 10)
        if success:
            state.last_success_ms = now_ms
        db.add(state)
        db.commit()


def _connect_upbit(token: str):
    from websockets.sync.client import connect
    # 업비트는 120초 동안 오가는 게 없으면 끊는다 — 라이브러리 PING 프레임(20초)으로 유지한다.
    return connect(UPBIT_WS_URL, additional_headers={"Authorization": f"Bearer {token}"},
                   open_timeout=10, ping_interval=20, ping_timeout=20, max_size=1_000_000)


def run_upbit_once(stop: threading.Event, *, connect=_connect_upbit, write_lock=None) -> str:
    """임대를 잡으면 연결해 새 공지를 받아 저장한다. 끊기거나 멈추면 돌아온다(다음 시도는 부르는 쪽이 정한다)."""
    keys = upbit_keys()
    if not keys:
        return "not_configured"
    token = claim_work(UPBIT_SCOPE)
    if not token:
        return "skipped"
    retry = 5
    try:
        with connect(_upbit_token(*keys)) as ws:
            ws.send(json.dumps([{"ticket": f"ggparrot-notice-{uuid.uuid4().hex[:8]}"},
                                {"type": "announcement", "include_body": False}, {"format": "DEFAULT"}]))
            _save_state("upbit", status="ready", success=True)
            renewed = time.monotonic()
            while not stop.is_set():
                if time.monotonic() - renewed > 60:
                    renew_work(UPBIT_SCOPE, token)
                    renewed = time.monotonic()
                try:
                    raw = ws.recv(timeout=30)
                except TimeoutError:
                    continue
                item = parse_upbit(json.loads(raw), now_ms=int(time.time() * 1000))
                if item:
                    with write_lock or nullcontext(), get_session() as db:
                        _store(db, [item], int(time.time() * 1000))
                        db.commit()
                    logger.info(json.dumps({"event": "upbit_notice", "id": item["id"], "assets": item["asset_symbols"]}))
        return "closed"
    except Exception as exc:
        # 허용 IP 밖이면 401 본문에 NoAuthorizationIp — 이름만 남긴다(키 · 토큰은 남기지 않는다).
        body = getattr(getattr(exc, "response", None), "body", b"") or b""
        name = re.search(rb"V1::Exceptions::(\w+)", body)
        error = name.group(1).decode() if name else type(exc).__name__
        _save_state("upbit", status="error", error=error)
        logger.warning("upbit notices stream failed type=%s", error)
        retry = 900 if error == "NoAuthorizationIp" else 60
        return "error"
    finally:
        finish_work(UPBIT_SCOPE, token, retry_seconds=retry)


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


def get_exchange_notices(exchange: str, symbol_base: str | None = None, *, q: str = "", limit: int = 20, cursor=None) -> dict:
    """한 거래소의 코인 공지 한 쪽(최신순). 코인을 고르면 그 코인 티커가 적힌 공지만.

    티커가 없는 공지(점검 · 고객센터 · 회사 홍보)는 소식 칸에 내지 않는다(2026-10-20 시안 B) — 저장은 해 둔다.
    """
    if exchange not in SOURCE_NAMES or (exchange == "upbit" and not upbit_keys()):
        return {"items": [], "total": 0, "next_cursor": None, "available": False,
                "collection": {"status": "not_configured", "exchange": exchange}}
    now_ms = int(time.time() * 1000)
    notice = ExchangeNotice
    conditions = [notice.exchange == exchange, notice.published_ms >= now_ms - RETENTION_SECONDS * 1000,
                  notice.asset_symbols_json != "[]"]
    if q.strip():
        conditions.append(func.lower(notice.title).like(feed_index.like_pattern(q.strip().lower()), escape="\\"))
    if symbol_base:
        conditions.append(feed_index.mentioning(FEED, notice.id, [symbol_base]))
    with get_session() as db:
        statement = select(notice).where(*conditions)
        if cursor:
            statement = statement.where(feed_index.before(notice.published_ms, notice.id, tuple(cursor)))
        rows = db.exec(statement.order_by(notice.published_ms.desc(), notice.id.desc()).limit(limit + 1)).all()
        total = None if cursor else db.exec(select(func.count()).select_from(notice).where(*conditions)).one()
        state = db.get(ExchangeNoticeState, exchange)
    more, rows = len(rows) > limit, rows[:limit]
    items = [{"id": row.id, "exchange": row.exchange, "title": row.title, "url": row.url,
              "categories": json.loads(row.categories_json), "published": row.published,
              "asset_symbols": json.loads(row.asset_symbols_json), "content_type": "official",
              "source": SOURCE_NAMES[row.exchange], "language": "ko", "region": "kr"} for row in rows]
    # 탭을 둘지 — 빗썸은 늘, 업비트는 실시간으로만 받으니 코인 공지가 하나라도 쌓인 뒤부터(빈 탭을 먼저 보이지 않게).
    available = exchange == "bithumb" or bool(items) or bool(total) or (cursor is not None)
    return {"items": items, "total": total, "available": available, "next_cursor": feed_index.encode_cursor(rows[-1].published_ms, rows[-1].id) if more else None,
            "collection": {"status": state.status if state else "pending", "exchange": exchange,
                           "last_success_ms": state.last_success_ms if state else 0}}


class _Runtime:
    def __init__(self):
        self.stop = threading.Event()
        self.write_lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name="exchange-notices", daemon=True)
        self.upbit = threading.Thread(target=self.run_upbit, name="exchange-notices-upbit", daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                collect_once(stop_event=self.stop, write_lock=self.write_lock)
            except Exception as exc:
                logger.warning("exchange notices collection failed type=%s", type(exc).__name__)
            self.stop.wait(REFRESH_SECONDS)

    def run_upbit(self):
        # 키가 없으면 한 번 확인하고 끝 — 키는 환경변수라 바꾸면 재배포로 다시 뜬다.
        failures = 0
        while not self.stop.is_set():
            try:
                result = run_upbit_once(self.stop, write_lock=self.write_lock)
            except Exception as exc:
                logger.warning("upbit notices loop failed type=%s", type(exc).__name__)
                result = "error"
            if result == "not_configured":
                return
            failures = failures + 1 if result == "error" else 0
            # 임대를 다른 프로세스가 가졌으면 1분 뒤, 끊기면 5초 뒤, 실패가 이어지면 최대 15분까지 늘려 다시.
            self.stop.wait(60 if result == "skipped" else min(900, 5 * 2 ** failures) if failures else 5)


def start_runtime() -> None:
    global _runtime
    if os.environ.get("EXCHANGE_NOTICES_ENABLED", "true").strip().lower() not in {"true", "1", "yes"}:
        return
    with _runtime_lock:
        if _runtime is None:
            _runtime = _Runtime()
            _runtime.thread.start()
            _runtime.upbit.start()


def stop_runtime() -> None:
    global _runtime
    with _runtime_lock:
        runtime, _runtime = _runtime, None
    if runtime:
        runtime.stop.set()
        with runtime.write_lock:
            pass
        runtime.thread.join(timeout=1)
