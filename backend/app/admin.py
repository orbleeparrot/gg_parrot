"""관리자 대시보드 — 사용자(유입)·가입·매크로·뉴스 수집·비용 집계. 모두 읽기 전용이고 메모리 캐시를 거친다.

관리자는 ``User.is_admin`` 이 켜진 계정(또는 부트스트랩용 ADMIN_USERNAMES, auth.require_admin). 응답 모양은
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
PAGE_LABELS = {
    "/": "홈", "/leaderboard": "리더보드", "/gallery": "리더보드", "/builder": "직접 만들기", "/s/:slug": "직접 만들기",
    "/news": "코인동향", "/board": "게시판", "/board/:id": "게시글", "/board/write": "글쓰기", "/board/:id/edit": "글 수정",
    "/agents": "내 에이전트", "/mypage": "내 활동", "/mypage/settings": "프로필 설정", "/login": "로그인",
    "/forgot": "비밀번호 찾기", "/reset": "비밀번호 재설정", "/runner/install": "실행기 설치", "/runner": "실행기 설치",
    "/guide": "FAQ", "/support": "고객센터", "/admin": "관리자",
}
FUNNEL_STEPS = (("visit", "방문 (활성 사용자)"), ("builder", "직접 만들기 화면 진입"), ("backtest", "백테스트 실행"),
                ("signup", "가입"), ("macro_register", "매크로 등록 (리더보드)"), ("macro_unlock", "매크로 구매 (언락)"),
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


def _pct(numerator: float, denominator: float) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _ratio(numerator: float, denominator: float, digits: int = 2) -> float:
    return round(numerator / denominator, digits) if denominator else 0.0


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
    """화면 너비로 기기 분류. 너비를 모르면(0) 데스크톱으로 — 모바일 비중을 부풀리지 않게."""
    try:
        width = int(screen_w or 0)
    except (TypeError, ValueError):
        width = 0
    if width <= 0:
        return "desktop"
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
    __slots__ = ("day", "first_ms", "last_ms", "last_dwell", "views", "visitor", "users", "is_new", "channel", "device",
                 "source", "landing", "exit", "hour")

    def __init__(self, *, day: str, first_ms: int, visitor: str, channel: str, device: str, source: str, landing: str):
        self.day = day
        self.first_ms = first_ms
        self.last_ms = first_ms
        self.last_dwell = 0
        self.views = 0
        self.visitor = visitor
        self.users: set[int] = set()
        self.is_new = False
        self.channel = channel if channel in dict(CHANNELS) else "direct"
        self.device = device if device in dict(DEVICES) else "desktop"
        self.source = source
        self.landing = landing
        self.exit = landing
        self.hour = _hour_kst(first_ms)

    @property
    def seconds(self) -> float:
        return max(0, self.last_ms + self.last_dwell - self.first_ms) / 1000


def _fold_sessions(rows) -> tuple[dict[str, _Session], dict[str, dict]]:
    """view 행(created_ms 순)을 세션과 페이지로 접는다. session_key 없는 옛 행은 방문자·날짜로 묶는다."""
    sessions: dict[str, _Session] = {}
    pages: dict[str, dict] = defaultdict(lambda: {"views": 0, "dwell_sum": 0, "dwell_n": 0, "landings": 0, "exits": 0})
    for session_key, path, created_ms, dwell_ms, is_new, is_landing, channel, device, visitor, user_id, day, host, utm in rows:
        key = session_key or f"~{visitor}:{day}"
        session = sessions.get(key)
        if session is None:
            source = f"utm:{utm}" if utm else (host or "")
            session = _Session(day=day, first_ms=int(created_ms), visitor=visitor or "", channel=channel or classify_channel(host, utm),
                               device=device or "desktop", source=source, landing=path)
            sessions[key] = session
            pages[path]["landings"] += 1
        session.views += 1
        session.last_ms = int(created_ms)
        session.last_dwell = int(dwell_ms or 0)
        session.exit = path
        session.is_new = session.is_new or bool(is_new)
        if user_id:
            session.users.add(int(user_id))
        page = pages[path]
        page["views"] += 1
        if dwell_ms and int(dwell_ms) > 0:
            page["dwell_sum"] += int(dwell_ms)
            page["dwell_n"] += 1
    for session in sessions.values():
        pages[session.exit]["exits"] += 1
    return sessions, pages


def _view_rows(db, since_day: str):
    return db.exec(
        select(Visit.session_key, Visit.path, Visit.created_ms, Visit.dwell_ms, Visit.is_new, Visit.is_landing, Visit.channel,
               Visit.device, Visit.visitor_hash, Visit.user_id, Visit.day_kst, Visit.referrer_host, Visit.utm_source)
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
    """창 안(하루 여유)에 만든 계정 id → 가입 KST 날짜. 채널별 가입 전환(세션 날짜 == 가입일)에 쓴다."""
    since_iso = _iso_from_ms(since_ms - 86_400_000)
    return {int(user_id): _kst_day_from_iso(created) for user_id, created in
            db.exec(select(User.id, User.created_at).where(User.created_at >= since_iso)).all()}


def _same_day_signups(sessions, signup_days: dict[int, str]) -> set[int]:
    return {user_id for session in sessions for user_id in session.users if signup_days.get(user_id) == session.day}


def _window_union(per_day: dict[str, set[str]], day: str, span: int) -> int:
    union: set[str] = set()
    for offset in range(span):
        union.update(per_day.get(_shift_day(day, -offset), ()))
    return len(union)


def _online_5m(db, now_ms: int) -> int:
    """최근 5분 view 의 distinct 방문자 — created_ms 인덱스를 타는 COUNT 하나라 요청마다 세도 싸다."""
    return int(db.exec(select(func.count(func.distinct(Visit.visitor_hash)))
                       .where(Visit.kind == "view", Visit.visitor_hash != "", Visit.created_ms >= now_ms - 300_000)).one())


def users_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    """날짜 단위 집계는 캐시(5분)에서, '지금 접속' 은 캐시가 맞아도 새로 센다 — 그 값만 실시간이어야 한다."""
    days = _clamp_days(days)
    key = f"users:{days}"
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
    generated_at, now_ms = _now()

    pageviews = {day: int(count) for day, count in db.exec(
        select(Visit.day_kst, func.count(Visit.id)).where(Visit.kind == "view", Visit.day_kst >= since_day).group_by(Visit.day_kst)).all()}
    per_day = _visitor_days(db, _shift_day(since_day, -(MAU_SPAN - 1)))
    online_5m = _online_5m(db, now_ms)
    sessions, pages = _fold_sessions(_view_rows(db, since_day))
    signup_days = _signup_days(db, _day_start_ms(since_day))

    by_day: dict[str, list[_Session]] = defaultdict(list)
    for session in sessions.values():
        by_day[session.day].append(session)

    daily = []
    for day in window:
        todays = by_day.get(day, [])
        new_visitors = {s.visitor for s in todays if s.is_new and s.visitor}
        active = len(per_day.get(day, ()))
        count = len(todays)
        daily.append({
            "day": day, "active": active, "new": len(new_visitors), "returning": max(0, active - len(new_visitors)),
            "sessions": count, "pageviews": pageviews.get(day, 0), "pv_per_session": _ratio(pageviews.get(day, 0), count),
            "avg_session_sec": round(sum(s.seconds for s in todays) / count) if count else 0,
            "bounce_pct": _pct(sum(1 for s in todays if s.views == 1), count),
        })
    today_row = daily[-1]

    total_sessions = len(sessions)
    channels = []
    for channel, label in CHANNELS:
        group = [s for s in sessions.values() if s.channel == channel]
        new_visitors = {s.visitor for s in group if s.is_new and s.visitor}
        channels.append({
            "channel": channel, "label": label, "sessions": len(group), "share_pct": _pct(len(group), total_sessions),
            "new_visitors": len(new_visitors), "bounce_pct": _pct(sum(1 for s in group if s.views == 1), len(group)),
            "signup_rate_pct": _pct(len(_same_day_signups(group, signup_days)), len(new_visitors)),
        })

    by_source: dict[str, list[_Session]] = defaultdict(list)
    for session in sessions.values():
        if session.source:
            by_source[session.source].append(session)
    sources = sorted(({"source": source, "channel": group[0].channel, "sessions": len(group),
                       "signups": len(_same_day_signups(group, signup_days))} for source, group in by_source.items()),
                     key=lambda row: (-row["sessions"], row["source"]))[:10]

    page_rows = sorted(({"path": path, "label": PAGE_LABELS.get(path, path), "pageviews": stats["views"],
                         "avg_dwell_sec": round(stats["dwell_sum"] / stats["dwell_n"] / 1000) if stats["dwell_n"] else 0,
                         "landings": stats["landings"], "exit_pct": _pct(stats["exits"], stats["views"])}
                        for path, stats in pages.items()), key=lambda row: (-row["pageviews"], row["path"]))[:12]

    devices = []
    for device, label in DEVICES:
        group = [s for s in sessions.values() if s.device == device]
        devices.append({"device": device, "label": label, "sessions": len(group), "share_pct": _pct(len(group), total_sessions),
                        "avg_session_sec": round(sum(s.seconds for s in group) / len(group)) if group else 0})

    hours = Counter(s.hour for s in sessions.values())
    mau_today = _window_union(per_day, today, MAU_SPAN)
    return {
        "days": days, "generated_at": generated_at,
        "kpis": {"dau": today_row["active"], "wau": _window_union(per_day, today, WAU_SPAN), "mau": mau_today,
                 "stickiness_pct": _pct(today_row["active"], mau_today), "online_5m": online_5m,
                 "bounce_pct_today": today_row["bounce_pct"], "avg_session_sec_today": today_row["avg_session_sec"]},
        "series": {"days": window, "dau": [row["active"] for row in daily],
                   "wau": [_window_union(per_day, day, WAU_SPAN) for day in window], "mau": [_window_union(per_day, day, MAU_SPAN) for day in window]},
        "daily": daily, "channels": channels, "sources": sources, "pages": page_rows, "devices": devices,
        "peak_hours": [{"hour": hour, "sessions": hours.get(hour, 0)} for hour in range(24)],
    }


# --- 가입 · 전환 · 유지 -----------------------------------------------------------------------------
def signups_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    days = _clamp_days(days)
    return _cached(f"signups:{days}", lambda: _signups_report(db, days))


def _retained(view_days: set[str], signup_day: str, offset: int) -> bool:
    threshold = _shift_day(signup_day, offset)
    return any(day >= threshold for day in view_days)


def _signups_report(db, days: int) -> dict:
    window = _days_back(days)
    since_day, today = window[0], window[-1]
    since_ms = _day_start_ms(since_day)
    since_iso = _iso_from_ms(since_ms)
    generated_at, _ = _now()
    window_set = set(window)

    users = [(int(user_id), _kst_day_from_iso(created), bool(deleted), bool(no_password)) for user_id, created, deleted, no_password in
             db.exec(select(User.id, User.created_at, User.is_deleted, User.password_hash == "")).all()]
    total_users = sum(1 for _u, _d, deleted, _m in users if not deleted)
    deletions = sum(1 for _u, _d, deleted, _m in users if deleted)
    in_window = [(user_id, day, "google" if no_password else "email") for user_id, day, _deleted, no_password in users if day in window_set]
    signups_by_day = Counter(day for _u, day, _m in in_window)
    before_window = sum(1 for _u, day, _d, _m in users if day and day < since_day)

    new_by_day = {day: int(count) for day, count in db.exec(
        select(Visit.day_kst, func.count(func.distinct(Visit.visitor_hash)))
        .where(Visit.kind == "view", Visit.is_new.is_(True), Visit.visitor_hash != "", Visit.day_kst >= since_day).group_by(Visit.day_kst)).all()}
    new_visitors = int(db.exec(select(func.count(func.distinct(Visit.visitor_hash)))
                               .where(Visit.kind == "view", Visit.is_new.is_(True), Visit.visitor_hash != "", Visit.day_kst >= since_day)).one())
    per_day = _visitor_days(db, since_day)
    days_per_visitor: Counter = Counter()
    for visitors in per_day.values():
        for visitor in visitors:
            days_per_visitor[visitor] += 1
    active = len(days_per_visitor)
    revisit = sum(1 for count in days_per_visitor.values() if count >= 2)

    user_views: dict[int, set[str]] = defaultdict(set)
    for user_id, day in db.exec(select(Visit.user_id, Visit.day_kst)
                                .where(Visit.kind == "view", Visit.user_id.is_not(None), Visit.day_kst >= since_day).distinct()).all():
        user_views[int(user_id)].add(day)
    backtest_claims = {(int(user_id), day) for user_id, day in db.exec(
        select(DailyQuestClaim.user_id, DailyQuestClaim.date_kst)
        .where(DailyQuestClaim.quest_key == "backtest_run", DailyQuestClaim.date_kst >= since_day)).all()}
    quest_active = {day: int(count) for day, count in db.exec(
        select(DailyQuestClaim.date_kst, func.count(func.distinct(DailyQuestClaim.user_id)))
        .where(DailyQuestClaim.date_kst >= since_day).group_by(DailyQuestClaim.date_kst)).all()}

    def d7_pct(group) -> float:
        eligible = [(user_id, day) for user_id, day, _m in group if _shift_day(day, 7) <= today]
        return _pct(sum(1 for user_id, day in eligible if _retained(user_views.get(user_id, set()), day, 7)), len(eligible))

    daily, cumulative = [], before_window
    for day in window:
        cumulative += signups_by_day.get(day, 0)
        daily.append({
            "day": day, "signups": signups_by_day.get(day, 0), "signup_rate_pct": _pct(signups_by_day.get(day, 0), new_by_day.get(day, 0)),
            "deletions": deletions if day == today else 0, "cumulative": cumulative,
            "first_backtest_same_day": sum(1 for user_id, signup_day, _m in in_window if signup_day == day and (user_id, day) in backtest_claims),
            "quest_active": quest_active.get(day, 0),
        })

    funnel_counts = {
        "visit": active,
        "builder": int(db.exec(select(func.count(func.distinct(Visit.visitor_hash))).where(
            Visit.kind == "view", Visit.visitor_hash != "", Visit.day_kst >= since_day,
            or_(Visit.path == "/builder", Visit.path.like("/s/%")))).one()),
        "backtest": int(db.exec(select(func.count(func.distinct(Visit.visitor_hash))).where(
            Visit.kind == "event", Visit.path == "backtest", Visit.visitor_hash != "", Visit.day_kst >= since_day)).one()),
        "signup": len(in_window),
        "macro_register": int(db.exec(select(func.count(func.distinct(LeaderboardEntry.owner_user_id))).where(
            LeaderboardEntry.is_ai.is_(False), LeaderboardEntry.owner_user_id.is_not(None),
            func.coalesce(LeaderboardEntry.first_created_ms, LeaderboardEntry.created_ms) >= since_ms)).one()),
        "macro_unlock": int(db.exec(select(func.count(func.distinct(MacroUnlock.user_id))).where(MacroUnlock.created_at >= since_iso)).one()),
        "agent_start": int(db.exec(select(func.count(func.distinct(RunSession.user_id))).where(RunSession.started_at >= since_iso)).one()),
    }
    funnel = [{"step": index, "key": key, "label": label, "count": funnel_counts[key]} for index, (key, label) in enumerate(FUNNEL_STEPS, start=1)]

    by_week: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for user_id, day, _method in in_window:
        by_week[_monday(day)].append((user_id, day))
    cohorts = []
    for monday in sorted(by_week):
        members = by_week[monday]
        latest = max(day for _u, day in members)
        row = {"week": f"{monday[5:]} 주", "signups": len(members)}
        for offset in COHORT_DAYS:
            # 아직 N일이 안 지난 코호트는 값이 없다(0 이 아니라 null) — 열지도에서 '미정' 칸으로 그린다.
            row[f"d{offset}"] = None if _shift_day(latest, offset) > today else _pct(
                sum(1 for user_id, day in members if _retained(user_views.get(user_id, set()), day, offset)), len(members))
        cohorts.append(row)

    methods = []
    for method, label in METHODS:
        group = [(user_id, day, m) for user_id, day, m in in_window if m == method]
        methods.append({"method": method, "label": label, "signups": len(group), "share_pct": _pct(len(group), len(in_window)),
                        "d7_retention_pct": d7_pct(group),
                        "first_backtest_same_day": sum(1 for user_id, day, _m in group if (user_id, day) in backtest_claims)})

    return {
        "days": days, "generated_at": generated_at,
        "kpis": {"total_users": total_users, "signups": len(in_window), "signup_rate_pct": _pct(len(in_window), new_visitors),
                 "revisit_pct": _pct(revisit, active), "d7_retention_pct": d7_pct(in_window), "deletions": deletions},
        "series": {"days": window, "signups": [row["signups"] for row in daily], "signup_rate_pct": [row["signup_rate_pct"] for row in daily]},
        "daily": daily, "funnel": funnel, "cohorts": cohorts, "methods": methods,
    }


# --- 매크로 · 마켓 ----------------------------------------------------------------------------------
def macros_report(db, *, days: int = DAYS_DEFAULT) -> dict:
    days = _clamp_days(days)
    return _cached(f"macros:{days}", lambda: _macros_report(db, days))


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
    generated_at, _ = _now()
    window_set = set(window)

    registered_day: dict[int, str] = {}
    for entry_id, created_ms, first_ms in db.exec(
            select(LeaderboardEntry.id, LeaderboardEntry.created_ms, LeaderboardEntry.first_created_ms)
            .where(LeaderboardEntry.is_ai.is_(False), func.coalesce(LeaderboardEntry.first_created_ms, LeaderboardEntry.created_ms) >= since_ms)).all():
        registered_day[int(entry_id)] = day_kst(int(first_ms or created_ms or 0))
    registered_by_day = Counter(day for day in registered_day.values() if day in window_set)

    per_day: dict[str, Counter] = defaultdict(Counter)
    per_entry: dict[int, Counter] = defaultdict(Counter)
    for day, entry_id, impressions, opens, _unlocks in db.exec(
            select(MacroEventDaily.day_kst, MacroEventDaily.entry_id, MacroEventDaily.impressions, MacroEventDaily.opens, MacroEventDaily.unlocks)
            .where(MacroEventDaily.day_kst >= since_day)).all():
        for key, value in (("impressions", impressions), ("opens", opens)):
            per_day[day][key] += int(value or 0)
            per_entry[int(entry_id)][key] += int(value or 0)
    # 언락·매출은 결제 기록(MacroUnlock)이 진실이다 — 카운터는 비콘 배포 뒤부터라 그 전 날짜가 0 으로 나온다.
    for entry_id, price, created_at in db.exec(
            select(MacroUnlock.entry_id, MacroUnlock.price, MacroUnlock.created_at).where(MacroUnlock.created_at >= since_iso)).all():
        day = _kst_day_from_iso(created_at)
        per_day[day]["unlocks"] += 1
        per_day[day]["revenue"] += int(price or 0)
        per_entry[int(entry_id)]["unlocks"] += 1
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
        daily.append({
            "day": day, "registered": registered_by_day.get(day, 0), "impressions": counts["impressions"], "opens": counts["opens"],
            "ctr_pct": _pct(counts["opens"], counts["impressions"]), "unlocks": counts["unlocks"], "cvr_pct": _pct(counts["unlocks"], counts["opens"]),
            "revenue_points": counts["revenue"], "creator_points": counts["creator"],
        })
    totals = Counter()
    for row in daily:
        for key in ("impressions", "opens", "unlocks", "revenue_points", "creator_points"):
            totals[key] += row[key]

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

    run_status = Counter({status: int(count) for status, count in db.exec(select(RunSession.status, func.count(RunSession.id)).group_by(RunSession.status)).all()})
    paper_status = Counter({status: int(count) for status, count in db.exec(select(PaperSession.status, func.count(PaperSession.id)).group_by(PaperSession.status)).all()})
    sessions = [
        {"kind": "agent", "label": "에이전트 (실행기)", "running": run_status["running"], "stopped": run_status["stopped"], "error": run_status["error"],
         "started_period": int(db.exec(select(func.count(RunSession.id)).where(RunSession.started_at >= since_iso)).one()),
         "mainnet": int(db.exec(select(func.count(RunSession.id)).where(RunSession.status == "running", RunSession.testnet.is_(False))).one())},
        {"kind": "paper", "label": "모의 (페이퍼) 세션", "running": paper_status["running"], "stopped": paper_status["stopped"], "error": paper_status["error"],
         "started_period": int(db.exec(select(func.count(PaperSession.id)).where(PaperSession.started_at >= since_iso)).one()), "mainnet": 0},
    ]
    return {
        "days": days, "generated_at": generated_at,
        "kpis": {"registered": sum(registered_by_day.values()), "unlocks": totals["unlocks"], "revenue_points": totals["revenue_points"],
                 "creator_points": totals["creator_points"], "ctr_pct": _pct(totals["opens"], totals["impressions"]),
                 "cvr_pct": _pct(totals["unlocks"], totals["opens"]), "agents_running": run_status["running"]},
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
    articles_today = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.first_seen_ms >= today_start)).one())
    failures_today = int(db.exec(select(func.coalesce(func.sum(CollectorRun.failures), 0))
                                 .where(CollectorRun.engine == "position_news", CollectorRun.day_kst == today)).one())
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
        {"key": "given_up", "label": "포기 (재시도 소진 · 기한 초과)", "value": f"{given_up:,}"},
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
    return _cached(f"costs:{months}", lambda: _costs_report(db, months))


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
