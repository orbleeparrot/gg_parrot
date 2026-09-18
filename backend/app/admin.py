"""관리자 대시보드 — 사용자(유입)·가입·매크로·뉴스 수집·비용 집계. 모두 읽기 전용이고 메모리 캐시를 거친다.

관리자는 ``User.is_admin`` 이 켜진 계정뿐이다(auth.require_admin). 응답 모양은
scratchpad 의 admin-contract 를 그대로 따른다 — 프론트(AdminDashboard.jsx)가 키 이름을 그대로 읽는다.

비콘(``POST /api/visit``·``/api/visit/leave``)은 화면 진입(view) 과 행동(event) 한 건씩을 Visit 표에 남긴다.
IP·UA 는 저장하지 않고, 브라우저 익명 id 는 서버 비밀과 섞은 해시만 남긴다. 세션·신규·랜딩·화면 너비는 브라우저가
판단해 보내고, 채널(유입 URL·utm)과 기기(화면 너비)는 서버가 분류한다. 기록은 best-effort — 실패해도 화면 흐름을
막지 않는다. 보존은 가장 긴 창(90일)에 MAU 창(30일)을 더한 119일 — 90일 차트의 첫 점도 온전한 30일 창으로 센다.

집계는 창(days)의 Visit 행에서 필요한 컬럼만 읽어 파이썬으로 접는다 — 세션 길이·이탈·종료 페이지는 SQL 로 표현하기
번거롭고, 관리자 전용이라 호출이 드물다. 일별 페이지뷰·활성 사용자처럼 단순한 수는 SQL group by.

캐시: 날짜 단위 집계(사용자·가입·매크로·비용)는 5분 — 화면이 1분마다 다시 묻는데 TTL 이 그와 같으면 매번 빗나가
raw 행 전체를 1분마다 다시 읽게 된다. '지금 접속(5분)' 만 요청마다 COUNT 하나로 새로 센다. 뉴스 수집 현황은 운영
상태판이라 60초(모두 COUNT·작은 표).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Callable, Optional
from urllib.parse import urlsplit

from sqlalchemy import delete, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from .db import (CollectorRun, DailyQuestClaim, LeaderboardEntry, MacroEventDaily, MacroUnlock, PaperSession, PointLedger,
                 RunSession, User, Visit)

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
DAYS_DEFAULT = 30
DAYS_MAX = 90
WAU_SPAN = 7
MAU_SPAN = 30
# 90일 창의 첫 날짜(오늘-89)의 MAU 는 오늘-118 까지 필요하다 — 보존이 90일이면 첫 29점이 잘린 창(첫 점의 MAU = DAU)으로 계산돼
# 가짜 상승 곡선이 그려진다. 그래서 보존 = 가장 긴 창 + MAU 창 - 1.
VISIT_RETENTION_DAYS = DAYS_MAX + MAU_SPAN - 1
MONTHS_DEFAULT = 6
CACHE_TTL_SECONDS = 300  # 날짜 단위 집계 — 화면 폴링(60초)보다 길어야 캐시가 맞는다
NEWS_CACHE_TTL_SECONDS = 60  # 운영 상태판 — 값이 작고(COUNT·작은 표) 가까운 실시간이 필요
DWELL_CAP_MS = 6 * 3_600_000
SESSION_GAP_MS = 30 * 60_000  # 브라우저의 세션 경계(30분 무활동)와 같다 — 뷰 사이 간격의 체류 상한이자 '아직 보고 있는' 세션 판정
HEARTBEAT_LIVE_MS = 5 * 60_000  # 실행기 하트비트가 이 안이면 실행 중(notifications.ACTIVE_SESSION_WINDOW 과 같은 뜻)
EVENT_NAMES = frozenset({"backtest", "signup", "macro_register", "macro_unlock", "agent_start"})
PATH_MAX = 120
_UTM_RE = re.compile(r"[^A-Za-z0-9_.\-]")
_ID_SEGMENTS = (re.compile(r"^\d+$"), re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I), re.compile(r"^[0-9a-f]{16,}$", re.I))
_SEARCH_HOSTS = ("google.", "naver.", "bing.", "duckduckgo.", "daum.", "yahoo.")
_SOCIAL_SUFFIXES = ("t.co", "twitter.com", "x.com")  # 정확히 이 호스트거나 그 하위 — 'netflix.com' 이 x.com 에 걸리지 않게
_SOCIAL_TOKENS = ("facebook.", "instagram.", "youtube.", "kakao", "telegram", "discord", "reddit", "threads")
_OWN_HOSTS = ("gg-parrot.vercel.app", "localhost", "127.0.0.1")

CHANNELS = (("direct", "직접 접속"), ("search", "검색"), ("referral", "추천 링크"), ("social", "소셜"), ("campaign", "캠페인 (utm)"))
DEVICES = (("mobile", "모바일"), ("desktop", "데스크톱"), ("tablet", "태블릿"))
DEVICE_UNKNOWN = ("unknown", "알 수 없음")  # 화면 너비를 모르는 뷰 — 표에는 보이되 비율 분모에서 뺀다
PAGE_LABELS = {
    "/": "홈", "/leaderboard": "리더보드", "/gallery": "리더보드", "/builder": "직접 만들기", "/s/:slug": "직접 만들기",
    "/news": "코인동향", "/board": "게시판", "/board/:id": "게시글", "/board/write": "글쓰기", "/board/:id/edit": "글 수정",
    "/agents": "내 에이전트", "/mypage": "내 활동", "/mypage/settings": "프로필 설정", "/login": "로그인",
    "/forgot": "비밀번호 찾기", "/reset": "비밀번호 재설정", "/runner/install": "실행기 설치", "/runner": "실행기 설치",
    "/guide": "FAQ", "/support": "고객센터", "/admin": "관리자",
}
# 퍼널은 둘 — 같은 집단·같은 기간 안에서만 '이전 단계 대비' 가 뜻을 가진다. 익명 방문자(비콘 기록 뒤부터)와 회원(전체 이력)을
# 한 줄에 놓으면 방문 대비 8,640% 같은 값이 나온다(2026-09-18 점검).
FUNNEL_ACQUISITION_STEPS = (("visit", "방문 (활성 방문자)"), ("builder", "직접 만들기 화면 진입"), ("backtest", "백테스트 실행 (이벤트)"),
                            ("signup", "가입 (창 안 가입일)"))
FUNNEL_MEMBER_STEPS = (("signup", "가입"), ("macro_register", "매크로 등록 (리더보드)"), ("macro_unlock", "매크로 구매 (언락)"),
                       ("agent_start", "에이전트 실행 (실행기)"))
METHODS = (("google", "구글 간편 가입"), ("email", "이메일 가입"))
COHORT_DAYS = (1, 3, 7, 14, 30)


# --- 시각 ---------------------------------------------------------------------------------------
def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def day_kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _today_kst() -> str:
    return datetime.now(timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _day_start_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_KST).timestamp() * 1000)


def _iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shift_day(day: str, offset: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")


def _days_back(days: int) -> list[str]:
    today = datetime.now(timezone.utc).astimezone(_KST).date()
    return [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days - 1, -1, -1)]


def _kst_day_from_iso(created_at: str) -> str:
    """User.created_at 같은 UTC ISO('YYYY-MM-DDTHH:MM:SSZ') → KST 날짜. 못 읽으면 빈 값."""
    raw = str(created_at or "")[:19]
    try:
        moment = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return moment.astimezone(_KST).strftime("%Y-%m-%d")


def _hour_kst(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).hour


def _monday(day: str) -> str:
    parsed = datetime.strptime(day, "%Y-%m-%d")
    return (parsed - timedelta(days=parsed.weekday())).strftime("%Y-%m-%d")


def _pct(numerator: float, denominator: float) -> Optional[float]:
    """분모가 0 이면 None — 0.0% 는 '아무도 안 했다' 는 가짜 숫자다. 화면은 None 을 '—' 로 그린다."""
    return round(numerator / denominator * 100, 1) if denominator else None


def _ratio(numerator: float, denominator: float, digits: int = 2) -> Optional[float]:
    return round(numerator / denominator, digits) if denominator else None


def signup_method_of(signup_method: str, has_password: bool, is_deleted: bool) -> str:
    """가입 방법. 컬럼이 있으면 그 값, 없는 옛 행은 살아 있을 때만 비밀번호 유무로 추정한다 — 탈퇴 행은 해시를 비우므로
    추정하면 전부 '구글' 이 된다(2026-09-18 점검). 그런 행은 'unknown'."""
    method = str(signup_method or "").strip().lower()
    if method in dict(METHODS):
        return method
    if is_deleted:
        return "unknown"
    return "email" if has_password else "google"


def _ago_label(ms: int, now_ms: int) -> str:
    if not ms:
        return "없음"
    minutes = max(0, (now_ms - int(ms)) // 60_000)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 24 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60)}일 전"


def _clamp_days(days) -> int:
    try:
        return max(7, min(DAYS_MAX, int(days)))
    except (TypeError, ValueError):
        return DAYS_DEFAULT


# --- 캐시 ---------------------------------------------------------------------------------------
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, ttl: float = CACHE_TTL_SECONDS) -> Optional[dict]:
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_put(key: str, value: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def _cached(key: str, build: Callable[[], dict], *, ttl: float = CACHE_TTL_SECONDS) -> dict:
    value = _cache_get(key, ttl)
    if value is None:
        value = build()
        _cache_put(key, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- 비콘 기록 ------------------------------------------------------------------------------------
def visitor_hash(visitor: str, secret: str) -> str:
    """브라우저 익명 id 를 서버 비밀과 섞어 해시 — 원래 id 는 저장하지 않는다."""
    value = str(visitor or "").strip()[:80]
    if not value:
        return ""
    return hashlib.sha256(f"{secret}:{value}".encode("utf-8")).hexdigest()[:24]


def referrer_host(referrer: str) -> str:
    """유입 출처는 호스트만(경로·쿼리는 버린다). 같은 사이트 안 이동은 빈 값."""
    try:
        host = (urlsplit(str(referrer or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    if not host or any(host == own or host.endswith("." + own) for own in _OWN_HOSTS):
        return ""
    return host[:120]


def classify_channel(host: str, utm_source: str) -> str:
    """utm 이 있으면 캠페인, 출처가 없으면(직접·자기 사이트) 직접, 검색 엔진·소셜은 각각, 나머지는 추천 링크."""
    if utm_source:
        return "campaign"
    host = (host or "").lower()
    if not host:
        return "direct"
    if any(token in host for token in _SEARCH_HOSTS):
        return "search"
    if any(host == suffix or host.endswith("." + suffix) for suffix in _SOCIAL_SUFFIXES):
        return "social"
    if any(token in host for token in _SOCIAL_TOKENS):
        return "social"
    return "referral"


def classify_device(screen_w: int) -> str:
    """화면 너비로 기기 분류. 너비를 모르면(0) '알 수 없음' — 데스크톱으로 몰면 기기 비중이 왜곡된다."""
    try:
        width = int(screen_w or 0)
    except (TypeError, ValueError):
        width = 0
    if width <= 0:
        return DEVICE_UNKNOWN[0]
    if width < 768:
        return "mobile"
    if width < 1024:
        return "tablet"
    return "desktop"


def sanitize_path(raw: str) -> str:
    """프론트 rum.sanitizeRoute 와 같은 규칙: 쿼리·해시 제거, 숫자·hex·uuid 조각은 ':id'. 공유 슬러그(/s/xxx)는 ':slug'."""
    path = re.split(r"[?#]", str(raw or "/"), 1)[0] or "/"
    path = "/" + path.lstrip("/")
    segments = [":id" if any(pattern.match(segment) for pattern in _ID_SEGMENTS) else segment for segment in path.split("/")]
    if len(segments) >= 3 and segments[1] == "s" and segments[2]:
        segments[2] = ":slug"
    joined = "/".join(segments)
    if len(joined) > 1:
        joined = joined.rstrip("/")
    return joined[:PATH_MAX] or "/"


def _view_key_exists(db, view_key: str) -> bool:
    return db.exec(select(Visit.id).where(Visit.view_key == view_key).limit(1)).first() is not None


def record_visit(db, *, kind: str = "view", path: str = "/", view_key: str = "", session_key: str = "", referrer: str = "",
                 utm_source: str = "", visitor: str = "", is_new: bool = False, is_landing: bool = False, screen_w: int = 0,
                 user_id: Optional[int], secret: str) -> Optional[Visit]:
    """비콘 한 건을 Visit 행으로. 같은 view_key 재전송·모르는 행동 이름은 조용히 버린다. 실패해도 예외를 내지 않는다."""
    kind = "event" if kind == "event" else "view"
    view_key = str(view_key or "").strip()[:64]
    if kind == "event":
        name = str(path or "").strip()
        if name not in EVENT_NAMES:
            return None
        stored_path = name
    else:
        stored_path = sanitize_path(path)
    try:
        if view_key and _view_key_exists(db, view_key):
            return None
        _, ms = _now()
        host = referrer_host(referrer)
        utm = _UTM_RE.sub("", str(utm_source or ""))[:60]
        row = Visit(
            day_kst=day_kst(ms), path=stored_path, referrer_host=host, utm_source=utm, visitor_hash=visitor_hash(visitor, secret),
            user_id=user_id, created_ms=ms, kind=kind, session_key=str(session_key or "").strip()[:64], view_key=view_key,
            dwell_ms=0, is_new=bool(is_new), is_landing=bool(is_landing), channel=classify_channel(host, utm),
            device=classify_device(screen_w), screen_w=max(0, min(int(screen_w or 0), 100_000)),
        )
        db.add(row)
        db.commit()
        return row
    except IntegrityError:  # view_key 유니크 — 같은 비콘이 동시에 두 번 온 것(재시도·StrictMode). 중복은 조용히 무시한다.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception as exc:  # noqa: BLE001 — 비콘은 화면 흐름을 절대 막지 않는다
        logger.warning("visit 기록 실패: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def record_leave(db, *, view_key: str, dwell_ms: int) -> None:
    """페이지를 떠날 때의 체류시간 — 같은 view_key 행에 max(기존, 값), 상한 6시간(탭을 켜 둔 채 잔 경우)."""
    view_key = str(view_key or "").strip()[:64]
    try:
        value = max(0, min(int(dwell_ms or 0), DWELL_CAP_MS))
    except (TypeError, ValueError):
        value = 0
    if not view_key or value <= 0:
        return
    try:
        db.exec(update(Visit).where(Visit.view_key == view_key, Visit.dwell_ms < value).values(dwell_ms=value))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit 체류시간 기록 실패: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def prune_visits(db, *, retention_days: int = VISIT_RETENTION_DAYS) -> int:
    _, ms = _now()
    cutoff = ms - retention_days * 86_400_000
    result = db.exec(delete(Visit).where(Visit.created_ms < cutoff))
    return int(result.rowcount or 0) if result.rowcount is not None and result.rowcount >= 0 else 0


_last_prune_ms = 0
PRUNE_EVERY_MS = 24 * 3_600_000


def maybe_prune_visits(db) -> int:
    """비콘이 들어올 때 하루에 한 번만 오래된 행을 지운다(별도 스케줄러 없이). 실패해도 비콘은 성공."""
    global _last_prune_ms
    _, ms = _now()
    if ms - _last_prune_ms < PRUNE_EVERY_MS:
        return 0
    _last_prune_ms = ms
    try:
        removed = prune_visits(db)
        db.commit()
        return removed
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit 정리 실패: %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


# --- 사용자 지표 ----------------------------------------------------------------------------------
class _Session:
    """세션 하나. 세션 시간은 '체류 합' — 뷰마다 leave 비콘의 dwell_ms 가 있으면 그 값, 없고 다음 뷰가 있으면 그 간격(30분 상한),
    마지막 뷰인데 비콘이 없으면 모름(제외). 전부 모르면 seconds 는 None 이고 평균의 분모에 들어가지 않는다 —
    첫 뷰와 마지막 뷰의 시각 차로 재면 탭을 숨긴 채 둔 시간이 그대로 들어가 80배까지 부풀었다(2026-09-18 점검).
    """
    __slots__ = ("day", "first_ms", "last_ms", "views", "visitor", "users", "is_new", "channel", "device", "source", "landing", "exit",
                 "hour", "dwell_ms", "dwell_known", "open_ms", "open_dwell", "open_path")

    def __init__(self, *, day: str, first_ms: int, visitor: str, channel: str, device: str, source: str, landing: str):
        self.day = day
        self.first_ms = first_ms
        self.last_ms = first_ms
        self.views = 0
        self.visitor = visitor
        self.users: set[int] = set()
        self.is_new = False
        self.channel = channel if channel in dict(CHANNELS) else "direct"
        self.device = device if device in dict(DEVICES) or device == DEVICE_UNKNOWN[0] else DEVICE_UNKNOWN[0]
        self.source = source
        self.landing = landing
        self.exit = landing
        self.hour = _hour_kst(first_ms)
        self.dwell_ms = 0  # 체류를 아는 뷰들의 합
        self.dwell_known = False  # 체류를 아는 뷰가 하나라도 있는가
        self.open_ms = 0  # 아직 체류가 확정되지 않은(마지막) 뷰
        self.open_dwell = 0
        self.open_path = ""

    @property
    def seconds(self) -> Optional[float]:
        return self.dwell_ms / 1000 if self.dwell_known else None


def _settle_view(session: _Session, pages: dict, next_ms: Optional[int]) -> None:
    """직전 뷰의 체류를 확정한다. 비콘 값 > 다음 뷰까지의 간격(30분 상한) > 모름(제외) 순."""
    if not session.open_path:
        return
    dwell = session.open_dwell
    known = dwell > 0
    if not known and next_ms is not None:
        dwell = min(max(0, next_ms - session.open_ms), SESSION_GAP_MS)
        known = True
    if known:
        session.dwell_ms += dwell
        session.dwell_known = True
        page = pages[session.open_path]
        page["dwell_sum"] += dwell
        page["dwell_n"] += 1
    session.open_path = ""


def _fold_sessions(rows, *, window_set: set[str], now_ms: int) -> tuple[dict[str, _Session], dict[str, dict]]:
    """view 행(created_ms 순)을 세션과 페이지로 접는다. session_key 없는 옛 행은 방문자·날짜로 묶는다.

    세션 날짜(첫 뷰의 날짜)가 창 밖이면 그 세션의 뷰는 전부 버린다 — 행은 창 하루 전부터 읽으므로 자정을 넘긴 세션은
    시작한 날에만 한 번 센다. 페이지뷰도 세션 날짜로 접어 Σ뷰/Σ세션 이 표의 pv_per_session 과 같다.
    화면 너비를 모르는(0) 뷰의 기기는 '알 수 없음' — 옛 행은 desktop 으로 저장돼 있어 여기서 다시 판정한다.
    종료 페이지: 30분 안에 마지막 뷰가 있는 세션은 아직 보고 있을 수 있으므로 종료로 세지 않는다.
    """
    sessions: dict[str, _Session] = {}
    skipped: set[str] = set()
    pages: dict[str, dict] = defaultdict(lambda: {"views": 0, "dwell_sum": 0, "dwell_n": 0, "landings": 0, "exits": 0})
    for session_key, path, created_ms, dwell_ms, is_new, is_landing, channel, device, visitor, user_id, day, host, utm, screen_w in rows:
        key = session_key or f"~{visitor}:{day}"
        if key in skipped:
            continue
        session = sessions.get(key)
        if session is None:
            if day not in window_set:
                skipped.add(key)
                continue
            source = f"utm:{utm}" if utm else (host or "")
            device = DEVICE_UNKNOWN[0] if int(screen_w or 0) <= 0 else (device or classify_device(screen_w))
            session = _Session(day=day, first_ms=int(created_ms), visitor=visitor or "", channel=channel or classify_channel(host, utm),
                               device=device, source=source, landing=path)
            sessions[key] = session
            pages[path]["landings"] += 1
        else:
            _settle_view(session, pages, int(created_ms))
        session.views += 1
        session.last_ms = int(created_ms)
        session.exit = path
        session.is_new = session.is_new or bool(is_new)
        session.open_ms, session.open_dwell, session.open_path = int(created_ms), int(dwell_ms or 0), path
        if user_id:
            session.users.add(int(user_id))
        pages[path]["views"] += 1
    for session in sessions.values():
        _settle_view(session, pages, None)
        if session.last_ms <= now_ms - SESSION_GAP_MS:
            pages[session.exit]["exits"] += 1
    return sessions, pages


def _avg_seconds(group) -> Optional[int]:
    """체류를 아는 세션만의 평균(초). 하나도 없으면 None — 0 으로 두면 '아무도 머물지 않았다' 는 가짜 숫자가 된다."""
    measured = [s.seconds for s in group if s.seconds is not None]
    return round(sum(measured) / len(measured)) if measured else None


def _view_rows(db, since_day: str):
    return db.exec(
        select(Visit.session_key, Visit.path, Visit.created_ms, Visit.dwell_ms, Visit.is_new, Visit.is_landing, Visit.channel,
               Visit.device, Visit.visitor_hash, Visit.user_id, Visit.day_kst, Visit.referrer_host, Visit.utm_source, Visit.screen_w)
        .where(Visit.kind == "view", Visit.day_kst >= since_day).order_by(Visit.created_ms, Visit.id)
    ).all()


def _visitor_days(db, since_day: str) -> dict[str, set[str]]:
    """날짜별 활성 방문자 집합 — WAU/MAU 창 합집합과 재방문율 재료."""
    per_day: dict[str, set[str]] = defaultdict(set)
    for day, visitor in db.exec(select(Visit.day_kst, Visit.visitor_hash)
                                .where(Visit.kind == "view", Visit.day_kst >= since_day, Visit.visitor_hash != "").distinct()).all():
        per_day[day].add(visitor)
    return per_day


def _signup_days(db, since_ms: int) -> dict[int, str]:
    """창 안(하루 여유)에 만든 살아 있는 계정 id → 가입 KST 날짜. 채널별 가입 전환(세션 날짜 == 가입일)에 쓴다."""
    since_iso = _iso_from_ms(since_ms - 86_400_000)
    return {int(user_id): _kst_day_from_iso(created) for user_id, created in
            db.exec(select(User.id, User.created_at).where(User.created_at >= since_iso, User.is_deleted.is_(False))).all()}


def _same_day_signups(sessions, signup_days: dict[int, str]) -> set[int]:
    return {user_id for session in sessions for user_id in session.users if signup_days.get(user_id) == session.day}


def _window_union(per_day: dict[str, set[str]], day: str, span: int) -> int:
    union: set[str] = set()
    for offset in range(span):
        union.update(per_day.get(_shift_day(day, -offset), ()))
    return len(union)


def _online_5m(db, now_ms: int) -> int:
    """최근 5분 안에 보고 있던 distinct 방문자 — 하트비트가 dwell_ms 를 갱신하므로 '뷰 시작 + 체류' 가 5분 안이면 접속 중.
    created_ms 조건은 인덱스를 타기 위한 하한(체류 상한 6시간)."""
    cutoff = now_ms - 300_000
    return int(db.exec(select(func.count(func.distinct(Visit.visitor_hash)))
                       .where(Visit.kind == "view", Visit.visitor_hash != "", Visit.created_ms >= cutoff - DWELL_CAP_MS,
                              Visit.created_ms + Visit.dwell_ms >= cutoff)).one())


def _coverage(db) -> dict:
    """각 기록의 시작일 — 그 전 날짜는 '측정 불가'(None) 이지 0 이 아니다. 하드코딩하지 않고 표에서 min 을 읽는다."""
    def first(column, *criteria) -> Optional[str]:
        statement = select(func.min(column))
        if criteria:
            statement = statement.where(*criteria)
        value = db.exec(statement).one()
        return str(value) if value else None
    return {"visits_since": first(Visit.day_kst, Visit.kind == "view"), "events_since": first(Visit.day_kst, Visit.kind == "event"),
            "macro_events_since": first(MacroEventDaily.day_kst), "quests_since": first(DailyQuestClaim.date_kst)}


def users_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    """날짜 단위 집계는 캐시(5분)에서, '지금 접속' 은 캐시가 맞아도 새로 센다 — 그 값만 실시간이어야 한다."""
    days = _clamp_days(days)
    key = f"users:{days}:{_today_kst()}"  # 자정을 넘기면 창이 바뀌므로 날짜가 키에 들어간다
    report = _cache_get(key)
    if report is None:
        report = _users_report(db, days)
        _cache_put(key, report)
        return report
    _, now_ms = _now()
    return {**report, "kpis": {**report["kpis"], "online_5m": _online_5m(db, now_ms)}}


def _users_report(db, days: int) -> dict:
    window = _days_back(days)
    since_day, today = window[0], window[-1]
    window_set = set(window)
    generated_at, now_ms = _now()

    per_day = _visitor_days(db, _shift_day(since_day, -(MAU_SPAN - 1)))
    online_5m = _online_5m(db, now_ms)
    # 창 하루 전부터 읽어 자정을 넘긴 세션이 잘리지 않게 하고, 세션 날짜가 창 밖이면 fold 가 버린다.
    sessions, pages = _fold_sessions(_view_rows(db, _shift_day(since_day, -1)), window_set=window_set, now_ms=now_ms)
    signup_days = _signup_days(db, _day_start_ms(since_day))
    coverage = _coverage(db)

    by_day: dict[str, list[_Session]] = defaultdict(list)
    for session in sessions.values():
        by_day[session.day].append(session)

    daily = []
    for day in window:
        todays = by_day.get(day, [])
        new_visitors = {s.visitor for s in todays if s.is_new and s.visitor}
        active = len(per_day.get(day, ()))
        count = len(todays)
        views = sum(s.views for s in todays)  # 세션 날짜로 접은 페이지뷰 — Σ뷰/Σ세션 항등
        daily.append({
            "day": day, "active": active, "new": len(new_visitors), "returning": max(0, active - len(new_visitors)),
            "sessions": count, "pageviews": views, "pv_per_session": _ratio(views, count),
            "avg_session_sec": _avg_seconds(todays), "bounce_pct": _pct(sum(1 for s in todays if s.views == 1), count),
        })
    today_row = daily[-1]

    # 가입 전환은 분자·분모를 같은 집합에서: 창 안 신규 방문자(first-touch — 가장 이른 세션의 채널 하나에만 귀속) 중
    # **신규로 온 그날**(is_new 세션의 날짜)에 가입한 사람. 회원 전체를 신규 방문자로 나누면 8,640% 가 나온다(2026-09-18 점검).
    # 분자 규칙은 signups_report 와 같아야 한다(신규 방문일 == 가입일 == 그날 방문 행의 회원). '어느 세션 날짜든 가입일과
    # 같으면' 으로 세면 1일에 검색으로 처음 왔다가 3일에 직접 접속으로 가입한 사람이 검색 채널 전환으로 잡혀 두 표가 어긋난다.
    first_touch: dict[str, _Session] = {}
    new_visitor_set: set[str] = set()
    new_day: dict[str, str] = {}  # 방문자 → 신규(is_new) 세션의 날짜. 여럿이면 가장 이른 날.
    for session in sessions.values():
        if not session.visitor:
            continue
        earliest = first_touch.get(session.visitor)
        if earliest is None or session.first_ms < earliest.first_ms:
            first_touch[session.visitor] = session
        if session.is_new:
            new_visitor_set.add(session.visitor)
            if session.day < new_day.get(session.visitor, "~"):
                new_day[session.visitor] = session.day
    converted: set[str] = set()
    for session in sessions.values():
        if not session.visitor or session.day != new_day.get(session.visitor):
            continue
        if any(signup_days.get(user_id) == session.day for user_id in session.users):
            converted.add(session.visitor)

    total_sessions = len(sessions)
    channels = []
    for channel, label in CHANNELS:
        group = [s for s in sessions.values() if s.channel == channel]
        new_visitors = {v for v in new_visitor_set if first_touch[v].channel == channel}
        channels.append({
            "channel": channel, "label": label, "sessions": len(group), "share_pct": _pct(len(group), total_sessions),
            "new_visitors": len(new_visitors), "bounce_pct": _pct(sum(1 for s in group if s.views == 1), len(group)),
            "signup_rate_pct": _pct(len(new_visitors & converted), len(new_visitors)),
        })

    by_source: dict[str, list[_Session]] = defaultdict(list)
    for session in sessions.values():
        if session.source:
            by_source[session.source].append(session)
    sources = sorted(({"source": source, "channel": group[0].channel, "sessions": len(group),
                       "signups": len(_same_day_signups(group, signup_days))} for source, group in by_source.items()),
                     key=lambda row: (-row["sessions"], row["source"]))[:10]

    page_rows = sorted(({"path": path, "label": PAGE_LABELS.get(path, path), "pageviews": stats["views"],
                         "avg_dwell_sec": round(stats["dwell_sum"] / stats["dwell_n"] / 1000) if stats["dwell_n"] else None,
                         "landings": stats["landings"], "exit_pct": _pct(stats["exits"], stats["views"])}
                        for path, stats in pages.items()), key=lambda row: (-row["pageviews"], row["path"]))[:12]

    # 기기 비율의 분모는 기기를 아는 세션 — '알 수 없음' 행은 보여 주되 비율은 없다(desktop 으로 몰면 비중이 왜곡된다).
    known_devices = sum(1 for s in sessions.values() if s.device != DEVICE_UNKNOWN[0])
    devices = []
    for device, label in DEVICES + (DEVICE_UNKNOWN,):
        group = [s for s in sessions.values() if s.device == device]
        devices.append({"device": device, "label": label, "sessions": len(group),
                        "share_pct": None if device == DEVICE_UNKNOWN[0] else _pct(len(group), known_devices),
                        "avg_session_sec": _avg_seconds(group)})

    hours = Counter(s.hour for s in sessions.values())
    mau_today = _window_union(per_day, today, MAU_SPAN)
    return {
        "days": days, "generated_at": generated_at, "coverage": coverage,
        "kpis": {"dau": today_row["active"], "wau": _window_union(per_day, today, WAU_SPAN), "mau": mau_today,
                 "stickiness_pct": _pct(today_row["active"], mau_today), "online_5m": online_5m,
                 "bounce_pct_today": today_row["bounce_pct"], "avg_session_sec_today": today_row["avg_session_sec"],
                 "new_visitors": len(new_visitor_set)},
        "series": {"days": window, "dau": [row["active"] for row in daily],
                   "wau": [_window_union(per_day, day, WAU_SPAN) for day in window], "mau": [_window_union(per_day, day, MAU_SPAN) for day in window]},
        "daily": daily, "channels": channels, "sources": sources, "pages": page_rows, "devices": devices,
        "peak_hours": [{"hour": hour, "sessions": hours.get(hour, 0)} for hour in range(24)],
    }


# --- 가입 · 전환 · 유지 -----------------------------------------------------------------------------
def signups_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    days = _clamp_days(days)
    return _cached(f"signups:{days}:{_today_kst()}", lambda: _signups_report(db, days))


def _retained(view_days: set[str], signup_day: str, offset: int) -> bool:
    threshold = _shift_day(signup_day, offset)
    return any(day >= threshold for day in view_days)


def _funnel(steps, counts: dict[str, int]) -> list[dict]:
    """단계별 count 와 첫 단계·직전 단계 대비 비율. 분모 0 이면 None(첫 단계의 '직전 대비' 도 None)."""
    rows, first, prev = [], None, None
    for index, (key, label) in enumerate(steps, start=1):
        count = int(counts.get(key, 0))
        rows.append({"step": index, "key": key, "label": label, "count": count,
                     "pct_of_first": _pct(count, count if first is None else first),
                     "pct_of_prev": None if prev is None else _pct(count, prev)})
        first = count if first is None else first
        prev = count
    return rows


def _signups_report(db, days: int) -> dict:
    window = _days_back(days)
    since_day, today = window[0], window[-1]
    since_ms = _day_start_ms(since_day)
    since_iso = _iso_from_ms(since_ms)
    generated_at, _ = _now()
    window_set = set(window)
    coverage = _coverage(db)
    visits_since, events_since, quests_since = coverage["visits_since"], coverage["events_since"], coverage["quests_since"]
    # 가입 당일 백테스트는 이벤트 비콘 ∪ 퀘스트 수령 — 둘 중 이른 기록 시작일부터 잴 수 있다.
    backtest_since = min(day for day in (events_since, quests_since) if day) if (events_since or quests_since) else None
    cohort_since = _monday(since_day)  # 코호트 주는 월요일 경계 — 창 첫 주가 잘려도 그 주 전체를 읽는다(partial 표시)

    users = [(int(user_id), _kst_day_from_iso(created), bool(deleted), signup_method_of(method, bool(has_password), bool(deleted)),
              _kst_day_from_iso(deleted_at))
             for user_id, created, deleted, method, has_password, deleted_at in
             db.exec(select(User.id, User.created_at, User.is_deleted, User.signup_method, User.password_hash != "", User.deleted_at)).all()]
    # 탈퇴 회원은 가입 집계(가입 수·퍼널·코호트·방법) 어디에도 넣지 않는다 — 탈퇴는 deleted_at 으로 따로 센다.
    alive = [(user_id, day, method) for user_id, day, deleted, method, _del in users if not deleted and day]
    total_users = sum(1 for _u, _d, deleted, _m, _del in users if not deleted)
    signup_day_of = {user_id: day for user_id, day, _m in alive}
    in_window = [(user_id, day, method) for user_id, day, method in alive if day in window_set]
    signups_by_day = Counter(day for _u, day, _m in in_window)
    before_window = sum(1 for _u, day, _m in alive if day < since_day)
    deletions_by_day = Counter(del_day for _u, _d, deleted, _m, del_day in users if deleted and del_day in window_set)

    # 방문 행 한 번 읽기(날짜·방문자·회원·신규 여부 distinct): 재방문율·신규 방문자·당일 가입·리텐션 재료를 전부 여기서 접는다.
    per_day: dict[str, set[str]] = defaultdict(set)
    new_by_day: dict[str, set[str]] = defaultdict(set)
    converted_by_day: dict[str, set[str]] = defaultdict(set)
    user_views: dict[int, set[str]] = defaultdict(set)
    signup_visitors: set[str] = set()
    for day, visitor, user_id, is_new in db.exec(
            select(Visit.day_kst, Visit.visitor_hash, Visit.user_id, Visit.is_new)
            .where(Visit.kind == "view", Visit.day_kst >= cohort_since).distinct()).all():
        if user_id:
            user_views[int(user_id)].add(day)
        if not visitor or day not in window_set:
            continue
        per_day[day].add(visitor)
        if is_new:
            new_by_day[day].add(visitor)
        if user_id and signup_day_of.get(int(user_id)) == day:
            converted_by_day[day].add(visitor)
        if user_id and signup_day_of.get(int(user_id)) in window_set:
            signup_visitors.add(visitor)
    days_per_visitor: Counter = Counter()
    for visitors in per_day.values():
        for visitor in visitors:
            days_per_visitor[visitor] += 1
    active = len(days_per_visitor)
    revisit = sum(1 for count in days_per_visitor.values() if count >= 2)
    new_visitors = set().union(*new_by_day.values()) if new_by_day else set()
    converted_new = set().union(*(new_by_day[day] & converted_by_day[day] for day in new_by_day)) if new_by_day else set()

    def signup_rate(day: str) -> Optional[float]:
        if not visits_since or day < visits_since:
            return None  # 방문 기록이 없던 날 — 측정 불가이지 0% 가 아니다
        return _pct(len(new_by_day.get(day, set()) & converted_by_day.get(day, set())), len(new_by_day.get(day, set())))

    backtest_events = {(int(user_id), day) for user_id, day in db.exec(
        select(Visit.user_id, Visit.day_kst)
        .where(Visit.kind == "event", Visit.path == "backtest", Visit.user_id.is_not(None), Visit.day_kst >= since_day).distinct()).all()}
    backtest_claims = {(int(user_id), day) for user_id, day in db.exec(
        select(DailyQuestClaim.user_id, DailyQuestClaim.date_kst)
        .where(DailyQuestClaim.quest_key == "backtest_run", DailyQuestClaim.date_kst >= since_day)).all()}
    backtests = backtest_events | backtest_claims
    quest_active = {day: int(count) for day, count in db.exec(
        select(DailyQuestClaim.date_kst, func.count(func.distinct(DailyQuestClaim.user_id)))
        .where(DailyQuestClaim.date_kst >= since_day).group_by(DailyQuestClaim.date_kst)).all()}

    def measurable(day: str, offset: int) -> bool:
        # 가입+N 이 아직 안 왔거나(미정) 방문 기록이 시작되기 전이면(이탈인지 알 수 없음) 분모에서 뺀다.
        threshold = _shift_day(day, offset)
        return bool(visits_since) and visits_since <= threshold <= today

    def retention_pct(group, offset: int) -> Optional[float]:
        eligible = [(user_id, day) for user_id, day, *_rest in group if measurable(day, offset)]
        if not eligible:
            return None
        return _pct(sum(1 for user_id, day in eligible if _retained(user_views.get(user_id, set()), day, offset)), len(eligible))

    def same_day_backtests(group) -> Optional[int]:
        if not backtest_since or all(day < backtest_since for day in window):
            return None
        return sum(1 for user_id, day, *_rest in group if (user_id, day) in backtests)

    daily, cumulative = [], before_window
    for day in window:
        cumulative += signups_by_day.get(day, 0)  # 살아 있는 회원 기준 누적
        daily.append({
            "day": day, "signups": signups_by_day.get(day, 0), "signup_rate_pct": signup_rate(day),
            "deletions": deletions_by_day.get(day, 0), "cumulative": cumulative,
            "first_backtest_same_day": None if not backtest_since or day < backtest_since else
            sum(1 for user_id, signup_day, _m in in_window if signup_day == day and (user_id, day) in backtests),
            "quest_active": None if not quests_since or day < quests_since else quest_active.get(day, 0),
        })

    # 퍼널 ① 유입(방문자 키, 창 안): 방문 → 직접 만들기 진입 → 백테스트 이벤트 → 가입(그 방문자의 회원 가입일이 창 안).
    acquisition = _funnel(FUNNEL_ACQUISITION_STEPS, {
        "visit": active,
        "builder": int(db.exec(select(func.count(func.distinct(Visit.visitor_hash))).where(
            Visit.kind == "view", Visit.visitor_hash != "", Visit.day_kst >= since_day,
            or_(Visit.path == "/builder", Visit.path.like("/s/%")))).one()),
        "backtest": int(db.exec(select(func.count(func.distinct(Visit.visitor_hash))).where(
            Visit.kind == "event", Visit.path == "backtest", Visit.visitor_hash != "", Visit.day_kst >= since_day)).one()),
        "signup": len(signup_visitors),
    })
    # 퍼널 ② 회원(회원 키, 창 안 가입자): 뒤 단계는 앞 단계의 부분집합 — 옛 회원의 구매를 이번 창 가입자 수로 나누지 않는다.
    signup_ids = {user_id for user_id, _d, _m in in_window}
    register_ids = signup_ids & {int(user_id) for user_id in db.exec(select(LeaderboardEntry.owner_user_id).where(
        LeaderboardEntry.is_ai.is_(False), LeaderboardEntry.owner_user_id.is_not(None),
        func.coalesce(LeaderboardEntry.first_created_ms, LeaderboardEntry.created_ms) >= since_ms).distinct()).all()}
    unlock_ids = register_ids & {int(user_id) for user_id in db.exec(
        select(MacroUnlock.user_id).where(MacroUnlock.created_at >= since_iso).distinct()).all()}
    agent_ids = unlock_ids & {int(user_id) for user_id in db.exec(
        select(RunSession.user_id).where(RunSession.started_at >= since_iso).distinct()).all()}
    members = _funnel(FUNNEL_MEMBER_STEPS, {"signup": len(signup_ids), "macro_register": len(register_ids),
                                            "macro_unlock": len(unlock_ids), "agent_start": len(agent_ids)})

    by_week: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for user_id, day, _method in alive:
        if day >= cohort_since:
            by_week[_monday(day)].append((user_id, day))
    cohorts = []
    for monday in sorted(by_week):
        group = by_week[monday]
        row = {"week": f"{monday[5:]} 주", "monday": monday, "signups": len(group),
               # 방문 기록으로 잴 수 있는 회원 수(가입+1 이 기록 시작 뒤) — 셀은 각각 가입+N 으로 다시 판정한다.
               "measurable": sum(1 for _u, day in group if visits_since and _shift_day(day, 1) >= visits_since),
               "partial": monday < since_day or _shift_day(monday, 6) > today}
        for offset in COHORT_DAYS:
            row[f"d{offset}"] = retention_pct(group, offset)
        cohorts.append(row)

    methods = []
    for method, label in METHODS:
        group = [(user_id, day, m) for user_id, day, m in in_window if m == method]
        methods.append({"method": method, "label": label, "signups": len(group), "share_pct": _pct(len(group), len(in_window)),
                        "d7_retention_pct": retention_pct(group, 7), "first_backtest_same_day": same_day_backtests(group)})

    revisit_days = None
    if visits_since:
        covered = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(visits_since, "%Y-%m-%d")).days + 1
        revisit_days = max(1, min(days, covered))

    return {
        "days": days, "generated_at": generated_at, "coverage": coverage, "revisit_days": revisit_days,
        "kpis": {"total_users": total_users, "signups": len(in_window),
                 "signup_rate_pct": None if not visits_since else _pct(len(converted_new), len(new_visitors)),
                 "revisit_pct": _pct(revisit, active), "d7_retention_pct": retention_pct(in_window, 7),
                 "deletions": sum(deletions_by_day.values()), "new_visitors": len(new_visitors)},
        "series": {"days": window, "signups": [row["signups"] for row in daily], "signup_rate_pct": [row["signup_rate_pct"] for row in daily]},
        "daily": daily, "funnel_acquisition": acquisition, "funnel_members": members, "cohorts": cohorts, "methods": methods,
    }


# --- 매크로 · 마켓 ----------------------------------------------------------------------------------
def macros_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    days = _clamp_days(days)
    return _cached(f"macros:{days}:{_today_kst()}", lambda: _macros_report(db, days))


def _rule_type(macro_json: str) -> str:
    try:
        return str(json.loads(macro_json or "{}").get("rule_type") or "?")
    except ValueError:
        return "?"


def _entry_ref(ref: str) -> Optional[int]:
    if not str(ref or "").startswith("entry:"):
        return None
    try:
        return int(str(ref)[6:])
    except ValueError:
        return None


def _macros_report(db, days: int) -> dict:
    window = _days_back(days)
    since_day = window[0]
    since_ms = _day_start_ms(since_day)
    since_iso = _iso_from_ms(since_ms)
    generated_at, now_ms = _now()
    window_set = set(window)
    coverage = _coverage(db)
    events_since = coverage["macro_events_since"]

    registered_day: dict[int, str] = {}
    for entry_id, created_ms, first_ms in db.exec(
            select(LeaderboardEntry.id, LeaderboardEntry.created_ms, LeaderboardEntry.first_created_ms)
            .where(LeaderboardEntry.is_ai.is_(False), func.coalesce(LeaderboardEntry.first_created_ms, LeaderboardEntry.created_ms) >= since_ms)).all():
        registered_day[int(entry_id)] = day_kst(int(first_ms or created_ms or 0))
    registered_by_day = Counter(day for day in registered_day.values() if day in window_set)

    # 노출·열람·구매 수는 모두 MacroEventDaily 한 원천에서 — 구매만 결제 기록(전체 이력)에서 읽으면 열람(비콘 뒤부터)과
    # 기간이 어긋나 판매 있는 매크로의 전환율이 0.0% 로 나왔다(2026-09-18 점검). 매출·제작자 수익은 결제 기록·원장이 진실.
    per_day: dict[str, Counter] = defaultdict(Counter)
    per_entry: dict[int, Counter] = defaultdict(Counter)
    for day, entry_id, impressions, opens, unlocks in db.exec(
            select(MacroEventDaily.day_kst, MacroEventDaily.entry_id, MacroEventDaily.impressions, MacroEventDaily.opens, MacroEventDaily.unlocks)
            .where(MacroEventDaily.day_kst >= since_day)).all():
        for key, value in (("impressions", impressions), ("opens", opens), ("unlocks", unlocks)):
            per_day[day][key] += int(value or 0)
            per_entry[int(entry_id)][key] += int(value or 0)
    for entry_id, price, created_at in db.exec(
            select(MacroUnlock.entry_id, MacroUnlock.price, MacroUnlock.created_at).where(MacroUnlock.created_at >= since_iso)).all():
        per_day[_kst_day_from_iso(created_at)]["revenue"] += int(price or 0)
        per_entry[int(entry_id)]["revenue"] += int(price or 0)
    for ref, delta, created_ms in db.exec(
            select(PointLedger.ref, PointLedger.delta, PointLedger.created_ms)
            .where(PointLedger.reason == "unlock_earn", PointLedger.created_ms >= since_ms)).all():
        per_day[day_kst(int(created_ms or 0))]["creator"] += int(delta or 0)
        entry_id = _entry_ref(ref)
        if entry_id is not None:
            per_entry[entry_id]["creator"] += int(delta or 0)

    daily = []
    for day in window:
        counts = per_day.get(day, Counter())
        covered = bool(events_since) and day >= events_since  # 카운터가 생기기 전 날짜는 측정 불가(0 이 아니다)
        daily.append({
            "day": day, "registered": registered_by_day.get(day, 0),
            "impressions": counts["impressions"] if covered else None, "opens": counts["opens"] if covered else None,
            "ctr_pct": _pct(counts["opens"], counts["impressions"]) if covered else None,
            "unlocks": counts["unlocks"] if covered else None,
            "cvr_pct": _pct(counts["unlocks"], counts["opens"]) if covered else None,
            "revenue_points": counts["revenue"], "creator_points": counts["creator"],
        })
    totals = Counter()
    for row in daily:
        for key in ("impressions", "opens", "unlocks", "revenue_points", "creator_points"):
            totals[key] += row[key] or 0

    ranked = sorted(per_entry.items(), key=lambda item: (-item[1]["revenue"], -item[1]["opens"], -item[1]["impressions"], item[0]))[:20]
    top = []
    if ranked:
        ids = [entry_id for entry_id, _c in ranked]
        entries = {int(row[0]): row for row in db.exec(
            select(LeaderboardEntry.id, LeaderboardEntry.symbol, LeaderboardEntry.human_summary, LeaderboardEntry.macro_json,
                   LeaderboardEntry.username, LeaderboardEntry.nickname, LeaderboardEntry.owner_user_id, LeaderboardEntry.created_ms,
                   LeaderboardEntry.first_created_ms, LeaderboardEntry.paper_session_id).where(LeaderboardEntry.id.in_(ids))).all()}
        owner_ids = [row[6] for row in entries.values() if row[6] is not None]
        owners = {int(user_id): username for user_id, username in
                  db.exec(select(User.id, User.username).where(User.id.in_(owner_ids))).all()} if owner_ids else {}
        paper_ids = [row[9] for row in entries.values() if row[9]]
        returns = {int(session_id): float(value or 0) for session_id, value in
                   db.exec(select(PaperSession.id, PaperSession.current_return).where(PaperSession.id.in_(paper_ids))).all()} if paper_ids else {}
        for entry_id, counts in ranked:
            row = entries.get(entry_id)
            if row is None:
                continue  # 삭제된 엔트리 — 기록은 남지만 표에는 안 올린다
            _id, symbol, summary, macro_json, username, nickname, owner_id, created_ms, first_ms, paper_id = row
            name = str(summary or "").strip()[:40] or f"{_rule_type(macro_json)} {symbol}"
            top.append({
                "entry_id": entry_id, "name": name, "creator": owners.get(owner_id) or username or nickname or "", "symbol": symbol,
                "registered_day": day_kst(int(first_ms or created_ms or 0)), "impressions": counts["impressions"], "opens": counts["opens"],
                "ctr_pct": _pct(counts["opens"], counts["impressions"]), "unlocks": counts["unlocks"], "cvr_pct": _pct(counts["unlocks"], counts["opens"]),
                "revenue_points": counts["revenue"], "creator_points": counts["creator"],
                "paper_return_pct": round(returns.get(int(paper_id), 0.0), 2) if paper_id else None,
            })

    # '실행 중' 은 status 만으로는 못 믿는다 — 실행기가 죽으면 종료 보고 없이 running 이 남는다(50개 중 최근 하트비트 9/4).
    # 하트비트가 5분 안인 세션만 실행 중, 나머지는 stale 로 따로 센다. 모의 세션은 하트비트가 없어 종료 보고 기준(paper_liveness).
    live_cutoff = _iso_from_ms(now_ms - HEARTBEAT_LIVE_MS)
    run_status = Counter({status: int(count) for status, count in db.exec(select(RunSession.status, func.count(RunSession.id)).group_by(RunSession.status)).all()})
    live_running = int(db.exec(select(func.count(RunSession.id)).where(RunSession.status == "running", RunSession.last_heartbeat_at >= live_cutoff)).one())
    live_mainnet = int(db.exec(select(func.count(RunSession.id)).where(
        RunSession.status == "running", RunSession.testnet.is_(False), RunSession.last_heartbeat_at >= live_cutoff)).one())
    paper_status = Counter({status: int(count) for status, count in db.exec(select(PaperSession.status, func.count(PaperSession.id)).group_by(PaperSession.status)).all()})
    sessions = [
        {"kind": "agent", "label": "에이전트 (실행기)", "running": live_running, "stale": max(0, run_status["running"] - live_running),
         "stopped": run_status["stopped"], "error": run_status["error"],
         "started_period": int(db.exec(select(func.count(RunSession.id)).where(RunSession.started_at >= since_iso)).one()), "mainnet": live_mainnet},
        {"kind": "paper", "label": "모의 (페이퍼) 세션", "running": paper_status["running"], "stale": 0, "stopped": paper_status["stopped"],
         "error": paper_status["error"],
         "started_period": int(db.exec(select(func.count(PaperSession.id)).where(PaperSession.started_at >= since_iso)).one()), "mainnet": 0},
    ]
    return {
        "days": days, "generated_at": generated_at, "coverage": coverage, "paper_liveness": "reported",
        "kpis": {"registered": sum(registered_by_day.values()), "unlocks": totals["unlocks"], "revenue_points": totals["revenue_points"],
                 "creator_points": totals["creator_points"], "ctr_pct": _pct(totals["opens"], totals["impressions"]),
                 "cvr_pct": _pct(totals["unlocks"], totals["opens"]), "agents_running": live_running},
        "series": {"days": window, "registered": [row["registered"] for row in daily], "unlocks": [row["unlocks"] for row in daily],
                   "ctr_pct": [row["ctr_pct"] for row in daily], "cvr_pct": [row["cvr_pct"] for row in daily]},
        "daily": daily, "top": top, "sessions": sessions,
    }


# --- 뉴스 수집 현황 --------------------------------------------------------------------------------
_BOARD_STATUS = {"ready": "ok", "empty": "empty", "error": "bad"}
_BOARD_ORDER = {"bad": 0, "wait": 1, "empty": 2, "ok": 3}


def news_report(db) -> dict:
    return _cached("news", lambda: _news_report(db), ttl=NEWS_CACHE_TTL_SECONDS)


def _engines_report(db) -> dict:
    """수집 엔진·시간대·소스 표는 collector_runs 가 만든다. 모듈이 없거나 터지면 빈 표 — 나머지 화면은 살린다."""
    try:
        module = import_module(f"{__package__}.collector_runs")
        report = module.engines_report(db)
        return {key: list(report.get(key) or []) for key in ("engines", "hourly", "sources")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("collector_runs 보고 실패: %s", exc)
        return {"engines": [], "hourly": [], "sources": []}


def _news_report(db) -> dict:
    from .agent_features.position_news.articles import ENRICHMENT_STALL_LEASE, NewsArticle, NewsMaintenanceLease
    from .db import TickerNewsAiBudget, TickerNewsState
    from .public_news import PublicNewsLease

    generated_at, now_ms = _now()
    today = day_kst(now_ms)
    today_start = _day_start_ms(today)

    states = db.exec(select(TickerNewsState.asset_symbol, TickerNewsState.collection_status, TickerNewsState.consecutive_failures,
                            TickerNewsState.last_error, TickerNewsState.last_attempt_ms, TickerNewsState.last_success_ms,
                            TickerNewsState.next_collection_ms)).all()
    board, failing = [], []
    for asset, status, failures, error, last_attempt, last_success, next_ms in states:
        failures = int(failures or 0)
        kind = _BOARD_STATUS.get(str(status or ""), "wait")
        label = {"ok": _ago_label(int(last_success or 0), now_ms), "bad": f"실패 {failures}", "empty": "비어 있음", "wait": "대기"}[kind]
        board.append({"asset": asset, "status": kind, "label": label, "last_success_ms": int(last_success or 0), "failures": failures})
        if failures > 0:
            failing.append({"asset": asset, "failures": failures, "last_attempt_ms": int(last_attempt or 0), "last_success_ms": int(last_success or 0),
                            "next_attempt_ms": int(next_ms or 0), "error": str(error or "")[:160]})
    board.sort(key=lambda row: (_BOARD_ORDER[row["status"]], row["last_success_ms"], row["asset"]))
    failing.sort(key=lambda row: (-row["failures"], row["asset"]))

    pending = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(True))).one())
    due = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(True), NewsArticle.enrichment_next_ms <= now_ms)).one())
    given_up = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(False), NewsArticle.enrichment_attempts > 0)).one())
    # '오늘 포기' 는 오늘 처음 본 기사 중 포기한 것 — 포기 시각 컬럼이 없어 first_seen_ms 로 잰다. 누적과 따로 보인다(A11).
    given_up_today = int(db.exec(select(func.count(NewsArticle.article_id)).where(
        NewsArticle.enrichment_pending.is_(False), NewsArticle.enrichment_attempts > 0, NewsArticle.first_seen_ms >= today_start)).one())
    articles_today = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.first_seen_ms >= today_start)).one())
    # '오늘 실패' 는 표에 나오는 여섯 엔진의 실행 실패 합 — 종목 뉴스만 읽으면 온체인·보강 실패가 빠져 상시 0 이었다(A5).
    try:
        engine_keys = [engine for engine, *_rest in import_module(f"{__package__}.collector_runs").ENGINES]
    except Exception:  # noqa: BLE001 — 이웃 모듈이 없어도 화면은 산다
        engine_keys = ["position_news", "whale_activity", "onchain_holders", "public_news", "article_enrichment", "coindesk_probe"]
    failures_today = int(db.exec(select(func.coalesce(func.sum(CollectorRun.failures), 0))
                                 .where(CollectorRun.engine.in_(engine_keys), CollectorRun.day_kst == today)).one())
    stall = db.get(NewsMaintenanceLease, ENRICHMENT_STALL_LEASE)
    stalled_until = int(stall.next_run_ms) if stall and int(stall.next_run_ms) > now_ms else 0

    budgets = {key: int(used or 0) for key, used in db.exec(select(TickerNewsAiBudget.budget_date_kst, TickerNewsAiBudget.used)).all()}
    ai_limit = max(0, int(os.environ.get("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "10") or 0))
    coindesk_daily = max(0, int(os.environ.get("COINDESK_NEWS_MAX_CALLS_PER_DAY", "20") or 0))
    coindesk_total = max(0, int(os.environ.get("COINDESK_NEWS_MAX_TOTAL_CALLS", "100") or 0))

    leases = db.exec(select(PublicNewsLease.scope, PublicNewsLease.next_run_ms)).all()
    market_next = [max(0, (int(next_ms or 0) - now_ms) // 60_000) for scope, next_ms in leases if str(scope).upper() == "MARKET"]
    ticker_next = [max(0, (int(next_ms or 0) - now_ms) // 60_000) for scope, next_ms in leases if str(scope).upper() != "MARKET"]
    public_parts = []
    if market_next:
        public_parts.append(f"market {market_next[0]}분")
    if ticker_next:
        public_parts.append(f"종목 {len(ticker_next)}개 · 가장 빠른 {min(ticker_next)}분")
    enrichment = [
        {"key": "pending", "label": "보강 대기 기사", "value": f"{pending:,}"},
        {"key": "due", "label": "지금 처리 가능", "value": f"{due:,}"},
        {"key": "given_up_today", "label": "오늘 포기 (오늘 수집 기사 중 · 재시도 소진 · 기한 초과)", "value": f"{given_up_today:,}"},
        {"key": "given_up", "label": "누적 포기 (전체 기간)", "value": f"{given_up:,}"},
        {"key": "stall", "label": "정체 쉼", "value": f"{max(0, (stalled_until - now_ms) // 1000)}초 뒤 탐침" if stalled_until else "없음"},
        {"key": "public_next", "label": "공개 뉴스 다음 수집", "value": " · ".join(public_parts) or "없음"},
        {"key": "ai_budget", "label": "종목 뉴스 AI 예산", "value": f"{budgets.get(today, 0):,} / {ai_limit:,}"},
        {"key": "coindesk", "label": "CoinDesk API 예산",
         "value": f"{budgets.get(f'coindesk_news:{today}', 0):,} / {coindesk_daily:,} · 누적 {budgets.get('coindesk_news:lifetime', 0):,} / {coindesk_total:,}"},
    ]
    engines = _engines_report(db)
    return {
        "generated_at": generated_at,
        "kpis": {"tickers_ok": sum(1 for row in board if row["status"] == "ok"), "tickers_total": len(board),
                 "tickers_failing": len(failing), "articles_today": articles_today, "failures_today": failures_today, "pending": pending,
                 "ai_budget_used": budgets.get(today, 0), "ai_budget_limit": ai_limit},
        "engines": engines["engines"], "hourly": engines["hourly"], "sources": engines["sources"],
        "board": board, "failing": failing, "enrichment": enrichment,
    }


# --- 비용 ---------------------------------------------------------------------------------------
def costs_report(db, *, months: int = MONTHS_DEFAULT) -> dict:
    try:
        months = max(1, min(24, int(months)))
    except (TypeError, ValueError):
        months = MONTHS_DEFAULT
    return _cached(f"costs:{months}:{_today_kst()}", lambda: _costs_report(db, months))


def _costs_report(db, months: int) -> dict:
    """비용 표는 api_usage 가 만든다(Gemini 토큰 × 단가 + 고정액). 모듈이 없거나 터지면 빈 표 — 화면은 살린다."""
    try:
        module = import_module(f"{__package__}.api_usage")
        return module.costs_report(db, months=months)
    except Exception as exc:  # noqa: BLE001
        logger.warning("api_usage 보고 실패: %s", exc)
        generated_at, _ = _now()
        today = _today_kst()
        return {
            "generated_at": generated_at, "month": today[:7], "month_days_elapsed": int(today[8:10]),
            "kpis": {"month_total_usd": 0.0, "last_month_total_usd": 0.0, "gemini_month_usd": 0.0, "gemini_calls_month": 0, "gemini_today_usd": 0.0},
            "monthly": [], "providers": [], "purposes": [], "daily": [],
        }
