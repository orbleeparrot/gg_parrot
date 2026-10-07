"""화면 오류 모으기 — 브라우저의 오류 경계·전역 오류가 한 줄씩 보낸다(2026-10-07 결정 8).

같은 날 같은 오류(종류·화면·문장)는 한 행에 횟수만 올리고, 30일이 지난 날은 지운다. 계정·IP·UA·쿼리는 받지도
저장하지도 않는다. 사용자가 알려 주기 전에는 몰랐던 '내 에이전트 빈 화면'(GG-012) 같은 사고를 배포 직후 보려는 것.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from .db import ClientError, get_session

KST = timezone(timedelta(hours=9))
RETENTION_DAYS = 30
MAX_MESSAGE = 300
KINDS = ("render", "chunk", "unhandled", "rejection")
# 앱의 화면 경로 틀(App.jsx Routes). 그 밖의 경로는 '/other' 로 묶는다 — 공유 주소·글 번호 같은 값은 남기지 않는다.
_STATIC_ROUTES = frozenset({
    "/", "/admin", "/agents", "/board", "/board/write", "/builder", "/builder/pro", "/forgot", "/gallery", "/guide",
    "/leaderboard", "/login", "/mypage", "/mypage/settings", "/news", "/reset", "/runner", "/runner/install", "/support",
})
_URL = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PRUNE_EVERY_S = 3600
_prune_lock = threading.Lock()
_last_prune = 0.0


def normalize_route(value: str) -> str:
    path = (value or "/").split("?", 1)[0].split("#", 1)[0] or "/"
    path = "/" + path.strip("/") if path != "/" else "/"
    if path in _STATIC_ROUTES:
        return path
    if re.fullmatch(r"/s/[^/]+", path):
        return "/s/:id"
    if re.fullmatch(r"/board/[^/]+/edit", path):
        return "/board/:id/edit"
    if re.fullmatch(r"/board/[^/]+", path):
        return "/board/:id"
    return "/other"


def sanitize_message(value: str) -> str:
    """한 줄로 접고, URL 의 쿼리·조각과 이메일을 지우고, 300자로 자른다."""
    text = " ".join(str(value or "").split())
    text = _URL.sub(r"\1", text)
    text = _EMAIL.sub("[email]", text)
    return text[:MAX_MESSAGE]


def fingerprint(kind: str, route: str, message: str) -> str:
    return hashlib.sha1(f"{kind}|{route}|{message}".encode("utf-8")).hexdigest()[:16]


def _day_kst(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, KST).strftime("%Y-%m-%d")


def _prune(db: Session, now_ms: int) -> None:
    cutoff = _day_kst(now_ms - RETENTION_DAYS * 86_400_000)
    db.exec(delete(ClientError).where(ClientError.day_kst < cutoff))


def record(kind: str, route: str, message: str, build: str = "", *, now_ms: int | None = None) -> None:
    """같은 날 같은 오류면 횟수만 올리고, 처음이면 새 행. 한 시간에 한 번 30일 지난 행을 지운다."""
    global _last_prune
    if kind not in KINDS:
        raise ValueError("unknown kind")
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    route = normalize_route(route)
    message = sanitize_message(message)
    if not message:
        return
    day = _day_kst(now_ms)
    key = fingerprint(kind, route, message)
    for attempt in range(2):
        with get_session() as db:
            row = db.exec(select(ClientError).where(ClientError.day_kst == day, ClientError.fingerprint == key)).first()
            if row is None:
                row = ClientError(day_kst=day, fingerprint=key, kind=kind, route=route, message=message,
                                  build=build[:40], count=0, first_ms=now_ms, last_ms=now_ms)
            row.count += 1
            row.last_ms = max(row.last_ms, now_ms)
            if build:
                row.build = build[:40]
            db.add(row)
            with _prune_lock:
                due = time.monotonic() - _last_prune >= _PRUNE_EVERY_S
                if due:
                    _last_prune = time.monotonic()
            if due:
                _prune(db, now_ms)
            try:
                db.commit()
                return
            except IntegrityError:
                # 같은 오류가 동시에 처음 들어왔다 — 다른 쪽이 만든 행에 횟수를 더하러 한 번 더.
                db.rollback()
                if attempt:
                    raise


def report(db: Session, days: int = 7, *, now_ms: int | None = None, limit: int = 100) -> dict:
    """관리자 '화면 오류' — 기간 안의 오류를 지문별로 합쳐 최근 순으로."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    days = max(1, min(int(days or 7), RETENTION_DAYS))
    since = _day_kst(now_ms - (days - 1) * 86_400_000)
    rows = db.exec(select(ClientError).where(ClientError.day_kst >= since)).all()
    groups: dict[str, dict] = {}
    for row in rows:
        item = groups.setdefault(row.fingerprint, {
            "kind": row.kind, "route": row.route, "message": row.message, "build": row.build,
            "count": 0, "days": 0, "first_ms": row.first_ms, "last_ms": row.last_ms,
        })
        item["count"] += row.count
        item["days"] += 1
        item["first_ms"] = min(item["first_ms"], row.first_ms)
        if row.last_ms >= item["last_ms"]:
            item["last_ms"], item["build"] = row.last_ms, row.build or item["build"]
    items = sorted(groups.values(), key=lambda item: item["last_ms"], reverse=True)[:limit]
    return {
        "days": days,
        "since": since,
        "total": sum(item["count"] for item in groups.values()),
        "kinds": {kind: sum(item["count"] for item in groups.values() if item["kind"] == kind) for kind in KINDS},
        "items": items,
    }
