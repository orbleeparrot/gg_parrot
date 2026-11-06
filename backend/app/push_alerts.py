"""앱(PWA) 알림 — 기기 구독 저장, 내 에이전트 중계를 보낼 알림으로 쌓기, 꺼내서 보내기.

흐름: 껄무새 중계(agent_commentary)가 말풍선을 남길 때 — 매수 · 매도, 손익 구간, 급등락 · 출렁임, 진입 대기,
문제, 뉴스 · 커뮤니티 글, 시작 · 종료 등 채팅에 뜨는 것 전부 — 같은 트랜잭션에서 ``enqueue_says`` 가
PushOutbox 에 한 줄씩 넣는다. 커밋 뒤
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
LEASE_MS = 5 * 60_000               # 꺼낸 프로세스가 이 안에 끝내지 못하면(죽음 · 멈춤) 다른 바퀴가 다시 꺼낸다
MAX_ATTEMPTS = 5                    # 잠깐 막힌 기기(429 · 5xx · 연결 실패)에 보내 보는 횟수(첫 시도 포함)
RETRY_WAIT_MS = (30_000, 60_000, 120_000, 300_000)  # n 번째 시도가 막혔을 때 다음 시도까지(Retry-After 가 더 길면 그만큼)
SEND_THREADS = 8
SEND_WAVE_MS = 10_000               # 한 번에 SEND_THREADS 개씩 — 한 차례가 최대 이만큼(web_push.send 시간 제한) 걸린다고 본다
TTL_SECONDS = 3600                 # 기기가 꺼져 있으면 한 시간까지만 기다렸다 전한다
STALE_SAY_MS = 30 * 60_000          # 일어난 지 30분 넘은 일(처음 보는 세션의 옛 체결 등)은 채팅에만 — 늦은 알림은 헷갈린다
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
def _sub_text(sub) -> str:
    if not isinstance(sub, dict):
        return ""
    return f"{sub.get('b') or ''}{sub.get('t') or ''}".strip()


def say_payload(facts: dict, say: dict) -> dict:
    """중계 말풍선 하나를 알림 한 장으로. 제목은 '코인 · 말풍선 첫 줄', 본문은 말풍선의 숫자 줄과 덧붙인 줄 —
    문구는 채팅과 같다(매크로가 주어인 말투 그대로). 기사 · 커뮤니티 글은 제목과 내용.
    ``at`` 은 일어난 시각 — 다시 보내기로 늦게 도착해도(매수가 매도보다 늦게 오는 등) 휴대폰 알림에 실제 시각이 찍힌다."""
    post = say.get("post") if isinstance(say.get("post"), dict) else None
    if post:
        title = str(post.get("title") or "").strip()
        summary = str(post.get("summary") or "").strip()
        lines = [title] + ([summary] if summary and summary != title else [])
    else:
        lines = [str(say.get("nums") or "").strip(), *(_sub_text(sub) for sub in say.get("subs") or [])]
    return {
        "title": f"{facts['base']} · {say.get('hl') or '새 소식이 있어요'}"[:120],
        "body": "\n".join(line for line in lines if line)[:400],
        "url": f"/agents?session={int(facts['id'])}",
        "tag": _dedupe_key(facts, say)[:120],
        "at": int(say.get("at_ms") or 0),
    }


def _dedupe_key(facts: dict, say: dict) -> str:
    # 기사 · 글은 계정마다 한 번(같은 코인을 두 세션이 돌려도), 그 밖의 일은 세션마다 그 말풍선 하나.
    key = str(say.get("key") or "")
    return key if say.get("kind") == "post" else f"s{int(facts['id'])}:{key}"


def enqueue_says(db, facts: dict, says: list[dict], *, now_ms: int) -> int:
    """새 중계 말풍선을 보낼 알림으로 쌓는다(커밋은 호출자). 쌓은 개수.

    알림을 켠 기기가 없는 계정은 아무것도 쌓지 않는다. 기사 · 커뮤니티 글은 실행 중인 세션 것만 — 끝나는
    바퀴에 나온 글은 '투자 중인 코인' 소식이 아니다. 체결 · 종료처럼 끝나는 바퀴의 일은 보낸다.
    """
    chosen = [say for say in says if say.get("key") and say.get("hl")
              and now_ms - int(say.get("at_ms") or now_ms) <= STALE_SAY_MS
              and (say.get("kind") != "post" or (facts.get("status") == "running" and isinstance(say.get("post"), dict)))]
    if not chosen or not web_push.enabled():
        return 0
    user_id = int(facts["user_id"])
    rows = [{"user_id": user_id, "dedupe_key": _dedupe_key(facts, say)[:160], "created_ms": now_ms, "sent_ms": 0,
             "lease_ms": 0, "attempts": 0, "done_json": "",
             "payload_json": json.dumps(say_payload(facts, say), ensure_ascii=False, separators=(",", ":"))}
            for say in chosen]
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
    with ThreadPoolExecutor(max_workers=min(SEND_THREADS, len(jobs)), thread_name_prefix="ggp-push") as pool:
        return list(pool.map(one, jobs))


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _device_results(db, jobs: list[tuple[dict, bytes]], outcomes: list[str], *, now_ms: int) -> dict:
    """보낸 결과로 기기 상태를 고친다(커밋은 호출자). 기기 수로 센다.

    받음은 실패 횟수를 지우고, 끝난 구독(404 · 410)은 지우고, 거절(그 밖의 4xx)은 세다가 FAIL_LIMIT 번이면 지운다.
    잠깐 막힘(429 · 5xx · 연결 실패)은 기기를 그대로 둔다 — 그 알림은 dispatch 가 다시 보낸다.
    """
    ok, gone, failed, retry = set(), set(), set(), set()
    for (subscription, _), outcome in zip(jobs, outcomes):
        {web_push.SENT: ok, web_push.GONE: gone, web_push.FAILED: failed}.get(outcome, retry).add(subscription["id"])
    failed -= ok | gone
    retry -= ok | gone | failed
    if gone:
        db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_(gone)))
    if ok:
        db.exec(update(PushSubscription).where(PushSubscription.id.in_(ok)).values(last_ok_ms=now_ms, fail_count=0))
    if failed:
        db.exec(update(PushSubscription).where(PushSubscription.id.in_(failed))
                .values(fail_count=PushSubscription.fail_count + 1))
        db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_(failed),
                                                   PushSubscription.fail_count >= FAIL_LIMIT))
    return {"sent": len(ok), "gone": len(gone), "failed": len(failed), "retry": len(retry), "jobs": len(jobs)}


def _deliver(payloads: list[tuple[int, dict]], subscriptions: list[dict], *, now_ms: int,
             send: Callable) -> dict:
    """(user_id, 알림) 목록을 그 계정 기기마다 한 번 보내고(다시 보내지 않는다), 결과로 기기 상태를 고친다."""
    jobs = [(subscription, _encode(payload)) for user_id, payload in payloads
            for subscription in subscriptions if subscription["user_id"] == user_id]
    outcomes = _send_all(jobs, send)
    with get_session() as db:
        result = _device_results(db, jobs, outcomes, now_ms=now_ms)
        db.commit()
    return result


def _subscriptions_for(db, user_ids) -> list[dict]:
    rows = db.exec(select(PushSubscription).where(PushSubscription.user_id.in_(list(user_ids)))).all()
    return [{"id": row.id, "user_id": row.user_id, "endpoint": row.endpoint, "p256dh": row.p256dh,
             "auth": row.auth} for row in rows]


def _ids(text: str) -> set[int]:
    try:
        return {int(value) for value in json.loads(text or "[]")}
    except (TypeError, ValueError):
        return set()


def dispatch(*, now_ms: Optional[int] = None, send: Callable = web_push.send) -> dict:
    """쌓인 알림을 꺼내 보낸다.

    꺼낼 때 ``lease_ms`` 로 맡아 두 프로세스가 같은 알림을 함께 보내지 않게 하고, 보낸 뒤에야 ``sent_ms`` 를 채운다.
    보내는 도중 프로세스가 죽으면 맡은 시간(LEASE_MS)이 지나 다시 꺼낸다 — 드물게 한 기기에 두 번 갈 수 있지만 같은
    tag 라 기기에서는 한 장으로 바뀐다. 잠깐 막힌 기기는 그 기기에만 RETRY_WAIT_MS(또는 Retry-After) 뒤에 다시
    보낸다(MAX_ATTEMPTS 번, 쌓인 지 PENDING_MAX_AGE_MS 안). 결과는 아직 맡고 있는 줄에만 쓴다(``_owned``).
    """
    now_ms = _now_ms() if now_ms is None else now_ms
    if not web_push.enabled():
        return {"claimed": 0}
    with get_session() as db:
        pending = select(PushOutbox.id).where(PushOutbox.sent_ms == 0, PushOutbox.lease_ms <= now_ms,
                                              PushOutbox.created_ms >= now_ms - PENDING_MAX_AGE_MS
                                              ).order_by(PushOutbox.id).limit(BATCH)
        claimed = list(db.exec(update(PushOutbox).where(PushOutbox.id.in_(pending.scalar_subquery()),
                                                        PushOutbox.sent_ms == 0, PushOutbox.lease_ms <= now_ms)
                               .values(lease_ms=now_ms + LEASE_MS, attempts=PushOutbox.attempts + 1)
                               .returning(PushOutbox.id, PushOutbox.user_id, PushOutbox.payload_json,
                                          PushOutbox.done_json, PushOutbox.attempts)).all())
        subscriptions = _subscriptions_for(db, {row[1] for row in claimed}) if claimed else []
        db.commit()
    _cleanup(now_ms)
    if not claimed:
        return {"claimed": 0}
    by_user: dict[int, list[dict]] = {}
    for subscription in subscriptions:
        by_user.setdefault(subscription["user_id"], []).append(subscription)
    jobs, owners, done, attempts = [], [], {}, {}
    for row_id, user_id, payload_json, done_json, tries in sorted(claimed):
        done[row_id], attempts[row_id] = _ids(done_json), int(tries or 0)
        try:
            data = _encode(json.loads(payload_json))
        except (TypeError, ValueError):
            continue  # 망가진 줄은 보내지 않고 끝낸다
        for subscription in by_user.get(user_id, []):
            if subscription["id"] not in done[row_id]:
                jobs.append((subscription, data))
                owners.append(row_id)
    # 큰 묶음(알림 × 기기)은 보내는 데 LEASE_MS 보다 오래 걸릴 수 있다 — 그동안 다른 작업이 다시 꺼내지 않게 늘려 둔다.
    needed = -(-len(jobs) // SEND_THREADS) * SEND_WAVE_MS + 60_000
    if needed > LEASE_MS:
        with get_session() as db:
            for where in _owned(attempts, attempts):
                db.exec(update(PushOutbox).where(*where).values(lease_ms=now_ms + needed))
            db.commit()
    outcomes = _send_all(jobs, send)
    blocked, delay = set(), {}
    for row_id, (subscription, _), outcome in zip(owners, jobs, outcomes):
        if outcome == web_push.RETRY:
            blocked.add(row_id)
            delay[row_id] = max(delay.get(row_id, 0), int(getattr(outcome, "retry_after_s", 0) or 0) * 1000)
        else:
            done[row_id].add(subscription["id"])
    finished = [row_id for row_id in done if row_id not in blocked or attempts[row_id] >= MAX_ATTEMPTS]
    later: dict[tuple[int, str, int], list[int]] = {}
    for row_id in sorted(blocked.difference(finished)):
        wait = max(RETRY_WAIT_MS[min(attempts[row_id], len(RETRY_WAIT_MS)) - 1], delay.get(row_id, 0))
        later.setdefault((attempts[row_id], json.dumps(sorted(done[row_id])), now_ms + wait), []).append(row_id)
    written = 0
    with get_session() as db:
        result = _device_results(db, jobs, outcomes, now_ms=now_ms)
        for where in _owned(finished, attempts):
            written += db.exec(update(PushOutbox).where(*where).values(sent_ms=now_ms, lease_ms=0, done_json="")).rowcount
        for (tries, done_json, lease_ms), ids in later.items():  # 같은 처지끼리 묶어 쓴다(운영 DB 왕복 80ms)
            written += db.exec(update(PushOutbox).where(PushOutbox.id.in_(ids), PushOutbox.attempts == tries,
                                                        PushOutbox.sent_ms == 0)
                               .values(lease_ms=lease_ms, done_json=done_json)).rowcount
        db.commit()
    lost = len(claimed) - written
    if lost:
        log.warning("push dispatch outlived its lease; %s alerts were taken over by another pass", lost)
    gave_up = len(blocked.intersection(finished))
    if gave_up:
        log.warning("push gave up after %s attempts (%s alerts)", MAX_ATTEMPTS, gave_up)
    return {"claimed": len(claimed), **result, "later": sum(len(ids) for ids in later.values()), "lost": lost}


def _owned(row_ids, attempts: dict[int, int]) -> list[tuple]:
    """이 작업이 아직 맡고 있는 줄을 고르는 조건들. 꺼낼 때마다 attempts 가 오르므로, 맡은 시간이 지나 다른 작업이 다시
    꺼낸 줄은 attempts 가 이미 달라 걸리지 않는다 — 늦게 끝난 옛 작업이 새 작업의 결과(받은 기기 · 다음 시각)를 덮지 않게."""
    groups: dict[int, list[int]] = {}
    for row_id in row_ids:
        groups.setdefault(attempts[row_id], []).append(row_id)
    return [(PushOutbox.id.in_(ids), PushOutbox.attempts == tries, PushOutbox.sent_ms == 0)
            for tries, ids in sorted(groups.items())]


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
               "body": "내 에이전트의 매매 · 손익 · 시세 · 뉴스 소식을 이렇게 알려 드려요.",
               "url": "/agents", "tag": "push-test"}
    result = _deliver([(user_id, payload)], subscriptions, now_ms=now_ms, send=send)
    # 화면이 받음 · 일부 · 못 보냄을 가른다 — retry 는 '잠시 뒤 다시', 나머지 실패는 '알림 다시 켜기'(구독이 끝났거나 거절됨).
    return {"sent": result["sent"], "devices": result["jobs"], "retry": result["retry"]}
