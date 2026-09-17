"""관리자 대시보드 — 유입·가입·매크로·뉴스 수집 지표. 모두 읽기 전용 집계다.

관리자는 ``User.is_admin`` 이 켜진 계정(또는 부트스트랩용 환경 변수 ADMIN_USERNAMES). 프로필의
'관리자 대시보드' 버튼과 ``/api/admin/*`` 가 이 계정에만 열린다.

유입(visit)은 프론트가 화면에 들어갈 때 ``POST /api/visit`` 로 남기는 한 건: 경로·유입 출처 호스트·
UTM source·브라우저 익명 id 의 해시·(로그인이면) 회원 id. IP·UA 는 저장하지 않는다. 90일 뒤 정리.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import delete, func
from sqlmodel import select

from .db import (BoardComment, BoardPost, DailyQuestClaim, LeaderboardEntry, MacroUnlock, PaperSession, PointLedger,
                 RunSession, User, UserMacro, Visit)

_KST = timezone(timedelta(hours=9))
VISIT_RETENTION_DAYS = 90
DAYS_DEFAULT = 30
_UTM_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _now() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def day_kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _days_back(days: int) -> list[str]:
    today = datetime.now(timezone.utc).astimezone(_KST).date()
    return [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days - 1, -1, -1)]


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
    if not host or host.endswith("gg-parrot.vercel.app") or host in {"localhost", "127.0.0.1"}:
        return ""
    return host[:120]


def record_visit(db, *, path: str, referrer: str, utm_source: str, visitor: str, user_id: Optional[int], secret: str) -> Visit:
    _, ms = _now()
    row = Visit(
        day_kst=day_kst(ms),
        path=str(path or "/").split("?")[0][:120],
        referrer_host=referrer_host(referrer),
        utm_source=_UTM_RE.sub("", str(utm_source or ""))[:60],
        visitor_hash=visitor_hash(visitor, secret),
        user_id=user_id,
        created_ms=ms,
    )
    db.add(row)
    return row


def prune_visits(db, *, retention_days: int = VISIT_RETENTION_DAYS) -> int:
    _, ms = _now()
    cutoff = ms - retention_days * 86_400_000
    result = db.exec(delete(Visit).where(Visit.created_ms < cutoff))
    return int(result.rowcount or 0) if result.rowcount is not None and result.rowcount >= 0 else 0


_last_prune_ms = 0
PRUNE_EVERY_MS = 24 * 3_600_000


def maybe_prune_visits(db) -> int:
    """비콘이 들어올 때 하루에 한 번만 오래된 행을 지운다(별도 스케줄러 없이)."""
    global _last_prune_ms
    _, ms = _now()
    if ms - _last_prune_ms < PRUNE_EVERY_MS:
        return 0
    _last_prune_ms = ms
    return prune_visits(db)


def _series(rows, days: list[str]) -> list[dict]:
    counts = {day: 0 for day in days}
    for day, count in rows:
        if day in counts:
            counts[day] = int(count)
    return [{"day": day, "count": counts[day]} for day in days]


def _rule_type(macro_json: str) -> str:
    try:
        return str(json.loads(macro_json or "{}").get("rule_type") or "?")
    except ValueError:
        return "?"


def overview(db, *, days: int = DAYS_DEFAULT) -> dict:
    """대시보드 한 화면 분량. 구간은 최근 ``days`` 일(KST 기준)."""
    days = max(7, min(90, int(days)))
    window = _days_back(days)
    since_day = window[0]
    since_ms = int(datetime.strptime(since_day, "%Y-%m-%d").replace(tzinfo=_KST).timestamp() * 1000)
    since_iso = datetime.fromtimestamp(since_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- 유입 ---
    visits_by_day = db.exec(select(Visit.day_kst, func.count(Visit.id)).where(Visit.day_kst >= since_day).group_by(Visit.day_kst)).all()
    visitors_by_day = db.exec(select(Visit.day_kst, func.count(func.distinct(Visit.visitor_hash)))
                              .where(Visit.day_kst >= since_day, Visit.visitor_hash != "").group_by(Visit.day_kst)).all()
    referrers = db.exec(select(Visit.referrer_host, func.count(Visit.id)).where(Visit.day_kst >= since_day, Visit.referrer_host != "")
                        .group_by(Visit.referrer_host).order_by(func.count(Visit.id).desc()).limit(8)).all()
    sources = db.exec(select(Visit.utm_source, func.count(Visit.id)).where(Visit.day_kst >= since_day, Visit.utm_source != "")
                      .group_by(Visit.utm_source).order_by(func.count(Visit.id).desc()).limit(8)).all()
    paths = db.exec(select(Visit.path, func.count(Visit.id)).where(Visit.day_kst >= since_day)
                    .group_by(Visit.path).order_by(func.count(Visit.id).desc()).limit(8)).all()
    today = window[-1]
    visits_today = int(db.exec(select(func.count(Visit.id)).where(Visit.day_kst == today)).one())
    visitors_today = int(db.exec(select(func.count(func.distinct(Visit.visitor_hash))).where(Visit.day_kst == today, Visit.visitor_hash != "")).one())
    signed_in_today = int(db.exec(select(func.count(func.distinct(Visit.user_id))).where(Visit.day_kst == today, Visit.user_id.is_not(None))).one())

    # --- 가입 ---
    users = db.exec(select(User.created_at).where(User.is_deleted.is_(False))).all()
    signups_by_day = Counter(day_kst(int(datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000))
                             for created in users if created)
    total_users = len(users)
    deleted_users = int(db.exec(select(func.count(User.id)).where(User.is_deleted.is_(True))).one())
    active_quest_users = db.exec(select(DailyQuestClaim.date_kst, func.count(func.distinct(DailyQuestClaim.user_id)))
                                 .where(DailyQuestClaim.date_kst >= since_day).group_by(DailyQuestClaim.date_kst)).all()

    # --- 매크로 ---
    entries = db.exec(select(LeaderboardEntry.created_ms, LeaderboardEntry.macro_json, LeaderboardEntry.symbol, LeaderboardEntry.owner_user_id, LeaderboardEntry.is_ai)).all()
    registered_by_day = Counter(day_kst(int(ms)) for ms, *_rest in entries if ms and int(ms) >= since_ms)
    rule_types = Counter(_rule_type(macro_json) for _ms, macro_json, *_rest in entries)
    symbols = Counter(symbol for _ms, _mj, symbol, *_rest in entries)
    unlocks = db.exec(select(MacroUnlock.created_at, MacroUnlock.price).where(MacroUnlock.created_at >= since_iso)).all()
    unlocks_by_day = Counter(created[:10] for created, _price in unlocks)
    unlock_points = sum(int(price) for _created, price in unlocks)
    creator_earned = int(db.exec(select(func.coalesce(func.sum(PointLedger.delta), 0)).where(PointLedger.reason == "unlock_earn", PointLedger.created_at >= since_iso)).one())
    quest_points = int(db.exec(select(func.coalesce(func.sum(PointLedger.delta), 0)).where(PointLedger.reason == "quest", PointLedger.created_at >= since_iso)).one())
    quests_by_key = db.exec(select(DailyQuestClaim.quest_key, func.count(DailyQuestClaim.id)).where(DailyQuestClaim.date_kst >= since_day).group_by(DailyQuestClaim.quest_key)).all()
    saved_macros = int(db.exec(select(func.count(UserMacro.id))).one())
    runs = db.exec(select(RunSession.status, func.count(RunSession.id)).group_by(RunSession.status)).all()
    runs_started = int(db.exec(select(func.count(RunSession.id)).where(RunSession.started_at >= since_iso)).one())
    mainnet_running = int(db.exec(select(func.count(RunSession.id)).where(RunSession.status == "running", RunSession.testnet.is_(False))).one())
    papers = db.exec(select(PaperSession.status, func.count(PaperSession.id)).group_by(PaperSession.status)).all()
    posts = int(db.exec(select(func.count(BoardPost.id)).where(BoardPost.created_at >= since_iso)).one())
    comments = int(db.exec(select(func.count(BoardComment.id)).where(BoardComment.created_at >= since_iso)).one())

    return {
        "days": days,
        "generated_at": _now()[0],
        "traffic": {
            "today": {"visits": visits_today, "visitors": visitors_today, "signed_in": signed_in_today},
            "visits": _series(visits_by_day, window),
            "visitors": _series(visitors_by_day, window),
            "referrers": [{"host": host, "count": int(count)} for host, count in referrers],
            "sources": [{"source": source, "count": int(count)} for source, count in sources],
            "paths": [{"path": path, "count": int(count)} for path, count in paths],
        },
        "signups": {
            "total": total_users,
            "deleted": deleted_users,
            "period": sum(count for day, count in signups_by_day.items() if day >= since_day),
            "by_day": _series(signups_by_day.items(), window),
            "active_by_day": _series(active_quest_users, window),
        },
        "macros": {
            "leaderboard_total": len(entries),
            "leaderboard_ai": sum(1 for *_rest, is_ai in entries if is_ai),
            "registered_period": sum(registered_by_day.values()),
            "registered_by_day": _series(registered_by_day.items(), window),
            "rule_types": [{"rule_type": key, "count": count} for key, count in rule_types.most_common(8)],
            "symbols": [{"symbol": key, "count": count} for key, count in symbols.most_common(8)],
            "unlocks_period": len(unlocks),
            "unlocks_by_day": _series(unlocks_by_day.items(), window),
            "unlock_points": unlock_points,
            "creator_earned": creator_earned,
            "quest_points": quest_points,
            "quests": [{"quest": key, "count": int(count)} for key, count in quests_by_key],
            "saved_macros": saved_macros,
            "runs": {status: int(count) for status, count in runs},
            "runs_started_period": runs_started,
            "mainnet_running": mainnet_running,
            "papers": {status: int(count) for status, count in papers},
            "posts_period": posts,
            "comments_period": comments,
        },
    }


def news_status(db) -> dict:
    """뉴스 수집(크롤링) 현황 — 종목 뉴스 상태·기사 피드·보강 대기·공개 뉴스 리스·고래·AI 예산."""
    from .agent_features.position_news.articles import ENRICHMENT_STALL_LEASE, NewsArticle, NewsArticleFeed, NewsMaintenanceLease
    from .db import TickerNewsAiBudget, TickerNewsState, WhaleTradeState
    from .public_news import PublicNewsLease

    _, ms = _now()
    states = db.exec(select(TickerNewsState.asset_symbol, TickerNewsState.collection_status, TickerNewsState.consecutive_failures,
                            TickerNewsState.last_error, TickerNewsState.last_attempt_ms)).all()
    status_counts = Counter(status for _s, status, *_r in states)
    failing = [{"asset": asset, "failures": int(failures), "error": (error or "")[:120], "last_attempt": day_kst(int(last or 0)) if last else ""}
               for asset, _status, failures, error, last in states if int(failures or 0) > 0]
    failing.sort(key=lambda row: -row["failures"])
    feeds = db.exec(select(NewsArticleFeed.asset_symbol, NewsArticleFeed.item_count, NewsArticleFeed.ready_count, NewsArticleFeed.updated_ms)).all()
    feed_rows = [{"asset": asset, "items": int(items), "ready": int(ready), "updated_min_ago": max(0, (ms - int(updated or 0)) // 60_000) if updated else None}
                 for asset, items, ready, updated in feeds]
    feed_rows.sort(key=lambda row: (row["updated_min_ago"] is None, -(row["updated_min_ago"] or 0)))
    pending = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(True))).one())
    due = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(True), NewsArticle.enrichment_next_ms <= ms)).one())
    given_up = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.enrichment_pending.is_(False), NewsArticle.enrichment_attempts > 0)).one())
    ready_total = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.ready.is_(True))).one())
    total_articles = int(db.exec(select(func.count(NewsArticle.article_id))).one())
    fresh_1h = int(db.exec(select(func.count(NewsArticle.article_id)).where(NewsArticle.first_seen_ms >= ms - 3_600_000)).one())
    stall = db.get(NewsMaintenanceLease, ENRICHMENT_STALL_LEASE)
    stalled_until = int(stall.next_run_ms) if stall and int(stall.next_run_ms) > ms else 0
    public_leases = db.exec(select(PublicNewsLease.scope, PublicNewsLease.next_run_ms, PublicNewsLease.lease_until_ms)).all()
    whales = db.exec(select(WhaleTradeState.symbol, WhaleTradeState.market, WhaleTradeState.collection_status, WhaleTradeState.last_success_ms, WhaleTradeState.consecutive_failures)).all()
    budget = db.exec(select(TickerNewsAiBudget.budget_date_kst, TickerNewsAiBudget.used)).all()
    return {
        "generated_at": _now()[0],
        "tickers": {"total": len(states), "by_status": dict(status_counts), "failing": failing[:10]},
        "articles": {"total": total_articles, "ready": ready_total, "pending": pending, "due": due, "given_up": given_up, "fresh_1h": fresh_1h,
                     "stalled_until_ms": stalled_until},
        "feeds": feed_rows[:60],
        "public_news": [{"scope": scope, "next_run_min": max(0, (int(next_run or 0) - ms) // 60_000), "working": int(lease or 0) > ms}
                        for scope, next_run, lease in public_leases],
        "whales": [{"symbol": symbol, "market": market, "status": status, "last_success_min_ago": max(0, (ms - int(last or 0)) // 60_000) if last else None,
                    "failures": int(failures or 0)} for symbol, market, status, last, failures in whales],
        "ai_budget": [{"key": key, "used": int(used)} for key, used in budget],
    }
