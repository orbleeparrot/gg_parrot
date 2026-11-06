"""앱(PWA) 알림 — 기기 구독 저장, 내 에이전트 코인 소식을 보낼 알림으로 쌓기, 꺼내서 보내기.

흐름: 껄무새 중계(agent_commentary)가 실행 중인 세션에 기사 · 커뮤니티 글 말풍선(kind ``post``)을 남길 때
같은 트랜잭션에서 ``enqueue_posts`` 가 PushOutbox 에 한 줄 넣는다(계정 · 글마다 한 번). 커밋 뒤
push_runtime 이 ``dispatch`` 로 꺼내 그 계정의 기기마다 web_push.send 로 보낸다. 네트워크는 DB 연결을
놓은 뒤에 기다린다(운영 DB 왕복 80ms · 웹 풀 8개).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import delete as sql_delete, update
from sqlmodel import select

from . import web_push
from .db import PushOutbox, PushSubscription, get_session

log = logging.getLogger(__name__)

MAX_DEVICES = 10                    # 한 계정의 기기 수 — 넘으면 가장 오래 안 쓴 기기부터 지운다
MAX_ENDPOINT = 2048
PENDING_MAX_AGE_MS = 60 * 60_000    # 한 시간 넘게 못 보낸 알림은 보내지 않는다(서버가 오래 멈췄다 켜진 경우)
KEEP_MS = 3 * 86_400_000            # 보낸 기록은 사흘 남겨 같은 글이 다시 울리지 않게 한다
FAIL_LIMIT = 10                     # 연달아 이만큼 거절되면(키 바뀜 등) 그 기기를 지운다
BATCH = 100
TTL_SECONDS = 3600                  # 기기가 꺼져 있으면 한 시간까지만 기다렸다 전한다
PLATFORMS = {"android", "ios", "desktop"}

_cleanup_lock = threading.Lock()
_last_cleanup_ms = 0
_test_lock = threading.Lock()
_last_test_ms: dict[int, int] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _insert(db):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


# --- 구독 ---------------------------------------------------------------------
def config() -> dict:
    keys = web_push.vapid()
    return {"enabled": keys is not None, "public_key": keys.public_key if keys is not None else ""}


def _checked(endpoint: str, p256dh: str = "", auth: str = "", *, keys_required: bool = True) -> str:
    endpoint = str(endpoint or "").strip()
    if not endpoint or len(endpoint) > MAX_ENDPOINT or not web_push.allowed_endpoint(endpoint):
        raise HTTPException(status_code=400, detail="알림을 받을 수 없는 브라우저예요.")
    if keys_required and not web_push.valid_keys(p256dh, auth):
        raise HTTPException(status_code=400, detail="알림 구독 정보가 올바르지 않아요.")
    return endpoint


def subscribe(user_id: int, endpoint: str, p256dh: str, auth: str, platform: str = "", *,
              now_ms: Optional[int] = None) -> dict:
    """이 기기를 이 계정의 알림 받는 곳으로 등록한다(같은 기기면 계정 · 키만 바꾼다)."""
    if not web_push.enabled():
        raise HTTPException(status_code=503, detail="지금은 앱 알림을 켤 수 없어요.")
    endpoint = _checked(endpoint, p256dh, auth)
    now_ms = _now_ms() if now_ms is None else now_ms
    platform = platform if platform in PLATFORMS else ""
    with get_session() as db:
        statement = _insert(db)(PushSubscription).values(
            user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth, platform=platform,
            created_ms=now_ms, updated_ms=now_ms, last_ok_ms=0, fail_count=0)
        db.exec(statement.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={"user_id": user_id, "p256dh": p256dh, "auth": auth, "platform": platform,
                  "updated_ms": now_ms, "fail_count": 0}))
        overflow = list(db.exec(select(PushSubscription.id).where(PushSubscription.user_id == user_id)
                                .order_by(PushSubscription.updated_ms.desc(), PushSubscription.id.desc())
                                .offset(MAX_DEVICES)).all())
        if overflow:
            db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_(overflow)))
        db.commit()
    return {"subscribed": True}


def unsubscribe(user_id: int, endpoint: str) -> dict:
    endpoint = _checked(endpoint, keys_required=False)
    with get_session() as db:
        db.exec(sql_delete(PushSubscription).where(PushSubscription.user_id == user_id,
                                                   PushSubscription.endpoint == endpoint))
        db.commit()
    return {"subscribed": False}


def status(user_id: int, endpoint: str) -> dict:
    """이 기기(엔드포인트)가 이 계정으로 알림을 받고 있는지 — 설정 스위치를 서버와 맞춘다."""
    endpoint = _checked(endpoint, keys_required=False)
    with get_session() as db:
        found = db.exec(select(PushSubscription.id).where(PushSubscription.user_id == user_id,
                                                          PushSubscription.endpoint == endpoint)).first()
    return {"subscribed": found is not None}


def delete_for_user(db, user_id: int) -> None:
    """회원 탈퇴 — 기기 구독과 보낸 기록을 지운다(커밋은 호출자)."""
    db.exec(sql_delete(PushSubscription).where(PushSubscription.user_id == user_id))
    db.exec(sql_delete(PushOutbox).where(PushOutbox.user_id == user_id))


# --- 쌓기 ---------------------------------------------------------------------
def post_payload(facts: dict, say: dict) -> dict:
    """중계 말풍선(기사 · 커뮤니티 글) 하나를 알림 한 장으로. 문구는 채팅 말풍선과 같다."""
    post = say.get("post") or {}
    title = str(post.get("title") or "").strip()
    summary = str(post.get("summary") or "").strip()
    body = title if not summary or summary == title else f"{title}\n{summary}"
    return {
        "title": f"{facts['base']} · {say.get('hl') or '새 소식이 있어요'}"[:120],
        "body": body[:400],
        "url": f"/agents?session={int(facts['id'])}",
        "tag": str(say.get("key") or "")[:120],
    }


def enqueue_posts(db, facts: dict, says: list[dict], *, now_ms: int) -> int:
    """실행 중인 세션의 새 소식 말풍선을 보낼 알림으로 쌓는다(커밋은 호출자). 쌓은 개수.

    알림을 켠 기기가 없는 계정은 아무것도 쌓지 않는다. 같은 글은 계정마다 한 번 — 같은 코인을 두 세션이
    돌려도 한 번만 울린다.
    """
    posts = [say for say in says if say.get("kind") == "post" and isinstance(say.get("post"), dict)]
    if not posts or facts.get("status") != "running" or not web_push.enabled():
        return 0
    user_id = int(facts["user_id"])
    rows = [{"user_id": user_id, "dedupe_key": str(say["key"])[:160], "created_ms": now_ms, "sent_ms": 0,
             "payload_json": json.dumps(post_payload(facts, say), ensure_ascii=False, separators=(",", ":"))}
            for say in posts]
    try:
        # 알림 쪽이 실패해도 말풍선 저장(같은 트랜잭션)은 그대로 커밋되게 — 저장 지점 안에서만 되돌린다.
        with db.begin_nested():
            if db.exec(select(PushSubscription.id).where(PushSubscription.user_id == user_id).limit(1)
                       ).first() is None:
                return 0
            statement = _insert(db)(PushOutbox).values(rows).on_conflict_do_nothing(
                index_elements=[PushOutbox.user_id, PushOutbox.dedupe_key]).returning(PushOutbox.id)
            return len(db.exec(statement).all())
    except Exception:
        log.warning("push enqueue failed (session=%s)", facts.get("id"), exc_info=True)
        return 0


# --- 보내기 -------------------------------------------------------------------
def _send_all(jobs: list[tuple[dict, bytes]], send: Callable) -> list[str]:
    def one(job):
        subscription, payload = job
        try:
            return send(subscription, payload, ttl=TTL_SECONDS, urgency="high")
        except Exception:
            log.warning("web push send crashed", exc_info=True)
            return web_push.RETRY
    if len(jobs) <= 1:
        return [one(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs)), thread_name_prefix="ggp-push") as pool:
        return list(pool.map(one, jobs))


def _deliver(payloads: list[tuple[int, dict]], subscriptions: list[dict], *, now_ms: int,
             send: Callable) -> dict:
    """(user_id, 알림) 목록을 그 계정 기기마다 보내고, 결과로 기기 상태를 고친다."""
    by_user: dict[int, list[dict]] = {}
    for subscription in subscriptions:
        by_user.setdefault(subscription["user_id"], []).append(subscription)
    jobs = []
    for user_id, payload in payloads:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        jobs += [(subscription, data) for subscription in by_user.get(user_id, [])]
    outcomes = _send_all(jobs, send)
    ok, gone, failed = set(), set(), set()
    for (subscription, _), outcome in zip(jobs, outcomes):
        if outcome == web_push.SENT:
            ok.add(subscription["id"])
        elif outcome == web_push.GONE:
            gone.add(subscription["id"])
        elif outcome == web_push.FAILED:
            failed.add(subscription["id"])
        # RETRY(429 · 5xx · 연결 실패)는 잠깐 막힌 것 — 그 알림만 놓치고 기기는 그대로 둔다.
    failed -= ok | gone
    if ok or gone or failed:
        with get_session() as db:
            if gone:
                db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            if ok:
                db.exec(update(PushSubscription).where(PushSubscription.id.in_(ok))
                        .values(last_ok_ms=now_ms, fail_count=0))
            if failed:
                db.exec(update(PushSubscription).where(PushSubscription.id.in_(failed))
                        .values(fail_count=PushSubscription.fail_count + 1))
                db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_(failed),
                                                           PushSubscription.fail_count >= FAIL_LIMIT))
            db.commit()
    return {"sent": len(ok), "gone": len(gone), "failed": len(failed), "jobs": len(jobs)}


def _subscriptions_for(db, user_ids) -> list[dict]:
    rows = db.exec(select(PushSubscription).where(PushSubscription.user_id.in_(list(user_ids)))).all()
    return [{"id": row.id, "user_id": row.user_id, "endpoint": row.endpoint, "p256dh": row.p256dh,
             "auth": row.auth} for row in rows]


def dispatch(*, now_ms: Optional[int] = None, send: Callable = web_push.send) -> dict:
    """쌓인 알림을 꺼내 보낸다. 꺼낼 때 sent_ms 를 채워 두 프로세스가 같은 알림을 두 번 보내지 않는다."""
    now_ms = _now_ms() if now_ms is None else now_ms
    if not web_push.enabled():
        return {"claimed": 0}
    with get_session() as db:
        pending = select(PushOutbox.id).where(PushOutbox.sent_ms == 0,
                                              PushOutbox.created_ms >= now_ms - PENDING_MAX_AGE_MS
                                              ).order_by(PushOutbox.id).limit(BATCH)
        claimed = list(db.exec(update(PushOutbox).where(PushOutbox.id.in_(pending.scalar_subquery()),
                                                        PushOutbox.sent_ms == 0)
                               .values(sent_ms=now_ms)
                               .returning(PushOutbox.id, PushOutbox.user_id, PushOutbox.payload_json)).all())
        subscriptions = _subscriptions_for(db, {row[1] for row in claimed}) if claimed else []
        db.commit()
    _cleanup(now_ms)
    if not claimed:
        return {"claimed": 0}
    payloads = []
    for _, user_id, payload_json in sorted(claimed):
        try:
            payloads.append((user_id, json.loads(payload_json)))
        except (TypeError, ValueError):
            continue
    result = _deliver(payloads, subscriptions, now_ms=now_ms, send=send)
    return {"claimed": len(claimed), **result}


def _cleanup(now_ms: int) -> None:
    """사흘 지난 보낸 기록을 지운다 — 한 시간에 한 번만."""
    global _last_cleanup_ms
    with _cleanup_lock:
        if now_ms - _last_cleanup_ms < 3_600_000:
            return
        _last_cleanup_ms = now_ms
    try:
        with get_session() as db:
            db.exec(sql_delete(PushOutbox).where(PushOutbox.created_ms < now_ms - KEEP_MS))
            db.commit()
    except Exception:
        log.warning("push outbox cleanup failed", exc_info=True)


def send_test(user_id: int, *, now_ms: Optional[int] = None, send: Callable = web_push.send) -> dict:
    """알림을 켠 직후 '이렇게 와요'를 바로 보여 주는 시험 알림. 계정마다 10초에 한 번."""
    if not web_push.enabled():
        raise HTTPException(status_code=503, detail="지금은 앱 알림을 켤 수 없어요.")
    now_ms = _now_ms() if now_ms is None else now_ms
    with _test_lock:
        if now_ms - _last_test_ms.get(user_id, 0) < 10_000:
            raise HTTPException(status_code=429, detail="잠시 뒤에 다시 보내 주세요.")
        _last_test_ms[user_id] = now_ms
        if len(_last_test_ms) > 5000:
            _last_test_ms.clear()
    with get_session() as db:
        subscriptions = _subscriptions_for(db, {user_id})
    if not subscriptions:
        raise HTTPException(status_code=404, detail="알림을 켠 기기가 없어요.")
    payload = {"title": "껄무새 앱 알림이 켜졌어요",
               "body": "내 에이전트가 거래 중인 코인의 새 기사 · 커뮤니티 글을 이렇게 알려 드려요.",
               "url": "/agents", "tag": "push-test"}
    result = _deliver([(user_id, payload)], subscriptions, now_ms=now_ms, send=send)
    return {"sent": result["sent"], "devices": result["jobs"]}
