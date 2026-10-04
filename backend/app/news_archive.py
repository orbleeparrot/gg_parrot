"""근거용 제목 아카이브 — 제목·날짜·출처·URL 만 무기한 보관한다(저장 전용, 네트워크·AI 없음).

asset 인자는 거래소 마켓 심볼이 아니라 이미 코인으로 줄인 값("BTC")이다. 뉴스는 거래소가 아니라
코인에 대한 것이므로 KRW-BTC 와 BTCUSDT 는 같은 행을 쓴다. 줄이는 일은 호출하는 쪽이
news.asset_from_market_symbol 로 한다(BTCFDUSD 같은 견적 통화까지 아는 쪽이며, 쓰는 쪽과
읽는 쪽이 같은 함수를 써야 한 코인이 두 열쇠로 갈라지지 않는다). 여기서는 대문자로 맞추고
영문·숫자 20 자 이내의 평범한 코인 이름인지만 확인한다 — 아니면 자르지 않고 버린다.

근거 조회는 덤으로 붙는 기능이라 저장·조회가 어떻게 실패해도 호출한 쪽(검증 응답)을 깨뜨리면 안 된다.
그래서 DB 오류든 잘못된 입력이든 경고 로그만 남기고 store 는 0, lookup 은 빈 목록을 돌려준다.
못 쓰는 항목(제목·URL·날짜가 없거나 이상한 것)은 항목 단위로 건너뛰어 나머지를 살린다.

URL 은 화면에서 눌러 가는 링크가 되므로 http(s) 만 받고, 2000 자를 넘으면 자르지 않고 버린다
(잘린 링크는 아무 데도 가지 않는다). 중복 판정은 정규화한 URL 로 하고 저장은 원문 그대로 한다.
"""
from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from .db import NewsHeadlineArchive, get_session

logger = logging.getLogger(__name__)

MAX_ROWS = 5
# 제목·출처는 NewsHeadlineArchive 의 열 길이와 같아야 한다. URL 열은 TEXT 라 길이 상한이 코드에만 있다.
_TITLE_MAX = 500
_SOURCE_MAX = 120
_URL_MAX = 2000
# 한 문장에 묶는 행 수 — SQLite 의 바인딩 변수 상한(999)을 넘지 않게 6열 x 100 행으로 끊는다.
_CHUNK = 100
# 게재 시각의 정상 범위(ms). 하한은 2009-01-01 — 비트코인 이전 날짜는 있을 수 없고, 초 단위 값
# (약 1.8e9)이 밀리초 자리에 들어오면 1970 년으로 보이므로 이 하한에서 걸러진다. 상한은 2100-01-01.
_MIN_PUBLISHED_MS = 1_230_768_000_000
_MAX_PUBLISHED_MS = 4_102_444_800_000
_INT64_MAX = 2**63 - 1

_ASSET_SHAPE = re.compile(r"[A-Z0-9]{1,20}")
_URL_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f]")
# 같은 기사를 가리키는데 링크마다 달라지는 추적용 인자.
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _asset(value) -> str:
    asset = str(value or "").strip().upper()
    return asset if _ASSET_SHAPE.fullmatch(asset) else ""


def _normalized_url(url: str) -> str:
    """중복 판정용 URL — 스킴은 https 로, 호스트는 소문자, 조각·추적 인자·끝 슬래시는 뗀다.

    http(s) 가 아니거나 호스트가 없으면 빈 문자열(= 거부).
    """
    if _URL_FORBIDDEN.search(url) or len(url) > _URL_MAX:
        return ""
    parts = urlsplit(url)
    if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    port = parts.port
    if port and port != _DEFAULT_PORTS[parts.scheme.lower()]:
        host = f"{host}:{port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_"))
    path = parts.path.rstrip("/")
    return f"https://{host}{path}" + (f"?{urlencode(query)}" if query else "")


def _published_ms(value):
    """정상 범위의 밀리초 정수, 아니면 None."""
    if isinstance(value, bool):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return millis if _MIN_PUBLISHED_MS <= millis <= _MAX_PUBLISHED_MS else None


def _row(asset: str, item) -> dict | None:
    """항목 하나를 저장할 행으로. 못 쓰는 항목이면 None — 한 항목 때문에 묶음을 버리지 않는다."""
    if not isinstance(item, dict):
        return None
    try:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()[:_TITLE_MAX]
        source = str(item.get("source") or "").strip()[:_SOURCE_MAX]
        published_ms = _published_ms(item.get("published_ms"))
        normalized = _normalized_url(url) if url else ""
        if not title or not normalized or published_ms is None:
            return None
        # NUL 은 Postgres 가 거부하고, 짝 없는 서로게이트는 UTF-8 로 인코딩되지 않는다.
        for text in (title, source, url):
            if "\x00" in text:
                return None
            text.encode("utf-8")
        key = hashlib.sha256(f"{asset}\n{normalized}".encode("utf-8")).hexdigest()
    except Exception:
        return None
    return dict(archive_key=key, asset_symbol=asset, published_ms=published_ms,
                title=title, source=source, url=url)


def store(asset: str, items: list[dict]) -> int:
    """쓸 수 있는 항목만 적재하고, 실제로 새로 들어간 행 수를 돌려준다.

    같은 코인·같은 기사(정규화한 URL 기준)는 한 번만 들어가며 이미 있는 행은 세지 않는다.
    어떤 실패도 밖으로 던지지 않고 0(또는 그때까지 들어간 수)을 돌려준다.
    """
    try:
        asset = _asset(asset)
        if not asset:
            return 0
        rows, seen = [], set()
        for item in items:
            row = _row(asset, item)
            if row is not None and row["archive_key"] not in seen:
                seen.add(row["archive_key"])
                rows.append(row)
        if not rows:
            return 0
        inserted = 0
        with get_session() as db:
            insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
            # 묶음마다 따로 커밋한다 — 한 묶음이 거부돼도 이미 들어간 것과 나머지는 살린다.
            for start in range(0, len(rows), _CHUNK):
                try:
                    # PostgreSQL 의 ON CONFLICT 는 rowcount 가 -1 이라 RETURNING 으로 센다(news_ai_budget 과 같다).
                    stmt = insert(NewsHeadlineArchive).values(rows[start:start + _CHUNK]) \
                        .on_conflict_do_nothing(index_elements=["archive_key"]) \
                        .returning(NewsHeadlineArchive.archive_key)
                    added = len(db.exec(stmt).all())
                    db.commit()
                    inserted += added
                except Exception:
                    db.rollback()
                    logger.warning("뉴스 제목 아카이브 저장 실패 (asset=%s, 묶음 시작=%d)", asset, start,
                                   exc_info=True)
        return inserted
    except Exception:
        logger.warning("뉴스 제목 아카이브 저장 실패 (asset=%r)", asset, exc_info=True)
        return 0


def lookup(asset: str, *, start_ms: int, end_ms: int, limit: int = MAX_ROWS) -> list[dict]:
    """구간(양 끝 포함) 안의 제목들을 오래된 순으로. 없거나 실패하면 빈 목록 — 그게 정상 경로다."""
    try:
        asset = _asset(asset)
        # 구간은 int64 안으로 눌러 담는다. 무한대·숫자 아님은 int() 가 던져 아래에서 빈 목록이 된다.
        start_ms = max(-_INT64_MAX, min(_INT64_MAX, int(start_ms)))
        end_ms = max(-_INT64_MAX, min(_INT64_MAX, int(end_ms)))
        limit = max(1, min(int(limit), 1000))
        if not asset or end_ms < start_ms:
            return []
        with get_session() as db:
            rows = db.exec(select(NewsHeadlineArchive).where(
                NewsHeadlineArchive.asset_symbol == asset,
                NewsHeadlineArchive.published_ms >= start_ms,
                NewsHeadlineArchive.published_ms <= end_ms,
            ).order_by(NewsHeadlineArchive.published_ms, NewsHeadlineArchive.archive_key)
                .limit(limit)).all()
            return [{"title": row.title, "source": row.source, "url": row.url,
                     "published_ms": row.published_ms} for row in rows]
    except Exception:
        logger.warning("뉴스 제목 아카이브 조회 실패 (asset=%r)", asset, exc_info=True)
        return []
