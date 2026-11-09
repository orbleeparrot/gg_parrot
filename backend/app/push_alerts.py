"""앱(PWA) 알림 — 알림 이벤트 · 기기 구독 · 기기별 전송을 나눠 둔다.

- 알림 이벤트(PushOutbox): 누구에게 무슨 일이 일어났는지. 내용 · 일어난 시각 · 만료 시각(쌓을 때 고정) · 중복 막는 키.
- 기기 구독(PushSubscription): 어느 계정의 어떤 구독인지. 세대(version) · 엔드포인트 대기(next_send_ms).
- 기기별 전송(PushDelivery): 알림 하나를 구독 세대 하나에 보내는 일. 시도 · 다음 시각 · 상태를 기기마다 따로.

흐름: 껄무새 중계(agent_commentary)가 말풍선을 남길 때 같은 트랜잭션에서 ``enqueue_says`` 가 이벤트와, 그 순간 켜져 있는
기기마다 전송 한 줄씩을 쌓는다. 커밋 뒤 push_runtime 이 ``dispatch`` 로 전송을 맡아 보낸다. 시험 알림도 같은 길로 간다.

전송 하나의 상태: pending(보낼 차례 · 다시 보내기 대기) → sending(바퀴 하나가 토큰으로 맡음) → accepted(푸시 서비스가 받음) |
failed(영구 거절 · 끝난 구독) | gave_up(시도를 다 씀) | expired(만료 시각이 지남) | cancelled(구독 해지 · 계정 바뀜).

지키는 규칙
1. 기기 하나의 실패 · 대기가 다른 기기의 시도를 쓰지 않는다 — 시도 · 다음 시각은 기기별 전송에만 있다.
2. 늦게 끝난 옛 바퀴 · 옛 요청은 새 상태를 바꾸지 못한다 — 결과는 자기 claim_token 으로 맡은 줄에만, 구독의 성공 ·
   실패 누적 · 삭제는 그 결과를 실제로 쓴 전송 · 아직 쥔 엔드포인트의 응답만, (id, version, endpoint)가 다 맞을 때만,
   앱의 재등록 · 해제는 기대한 version 이 맞을 때만 반영한다.
3. 모든 발송이 엔드포인트 대기와 만료 시각을 지킨다 — 엔드포인트 발송 권한(send_token)을 쥔 바퀴만 그 엔드포인트로
   보내고(일반 · 시험 · 모든 프로세스 공통), 차례로 보내다 막히면 그 뒤는 보내지 않는다. 대기는 더 늦은 값으로만
   바뀌고, 푸시 서비스에 주는 TTL 은 남은 시간만.
4. 대상 기기는 쌓을 때 정한다 — 나중에 켠 기기 · 다른 계정에 지난 알림을 배정하지 않는다. 해지된 구독의 남은 전송은 취소한다.
5. DB 기록과 네트워크를 나눈다 — 쌓기는 말풍선과 한 트랜잭션, 보내기는 DB 연결을 놓은 뒤(운영 DB 왕복 80ms · 웹 풀 8개).
   푸시 서비스가 받은 직후 죽으면 다시 보낼 수 있다 — 같은 tag 라 기기에서는 한 장으로 바뀐다.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import (and_, case, delete as sql_delete, func, literal, literal_column, or_, text as sql_text, true, tuple_,
                        update)
from sqlmodel import select

from . import web_push
from .db import PushDelivery, PushOutbox, PushSubscription, get_session

log = logging.getLogger(__name__)

MAX_DEVICES = 3                     # 한 계정의 기기 수 — 넘으면 가장 오래 안 쓴 기기부터 지운다(11/09 사용자 결정: 3대)
MAX_ENDPOINT = 2048
EVENT_TTL_MS = 60 * 60_000          # 쌓은 뒤 한 시간 — 그 뒤로는 어느 기기에도 보내지 않는다(쌓을 때 고정)
TEST_TTL_MS = 5 * 60_000            # 시험 알림은 5분
KEEP_MS = 3 * 86_400_000            # 기록은 사흘 남겨 같은 글이 다시 울리지 않게 한다
FAIL_LIMIT = 10                     # 연달아 이만큼 거절되면(키 바뀜 등) 그 기기를 지운다
ENDPOINT_BATCH = 100                # 한 바퀴에 맡는 엔드포인트 수
PER_ENDPOINT = 20                   # 엔드포인트 하나에서 한 바퀴에 맡는 전송 수 — 한 기기의 밀린 알림이 바퀴를 다 차지하지 않게
CLEANUP_BATCH = 500                 # 만료 · 보관 정리를 한 번에 이만큼씩(긴 잠금 · 드라이버 파라미터 상한 65,535 를 피한다)
CLEANUP_EVERY_MS = 3_600_000        # 보관 정리 · 집계를 한 시간에 한 번 시작한다
CLEANUP_BUDGET_S = 1.0              # 정리를 한 번 부를 때 쓰는 시간 — 넘으면 멈추고 다음 부름(몇 초 뒤)이 이어 한다
ID_CHUNK = 500                     # IN 목록 한 번의 크기
LEASE_MS = 5 * 60_000               # 맡은 바퀴가 이 안에 끝내지 못하면(죽음 · 멈춤) 다른 바퀴가 다시 맡는다
MAX_ATTEMPTS = 5                    # 기기 하나에 보내 보는 횟수(첫 시도 포함) — 그 기기의 전송에만 센다
RETRY_WAIT_MS = (30_000, 60_000, 120_000, 300_000)  # n 번째 시도가 막혔을 때 다음 시도까지(Retry-After 가 더 길면 그만큼)
SEND_THREADS = 8                    # 서로 다른 엔드포인트끼리만 동시에
SEND_WAVE_MS = 10_000               # 보내기 한 번이 최대 이만큼(web_push.send 시간 제한) 걸린다고 본다
STALE_SAY_MS = 30 * 60_000          # 일어난 지 30분 넘은 일(처음 보는 세션의 옛 체결 등)은 채팅에만 — 늦은 알림은 헷갈린다
CHAT_ONLY_KINDS = {"quiet"}         # '조용~하네요'(45분 동안 별일 없음)는 채팅에만 — 알림으로 울릴 소식이 아니다(11/09 사용자 결정)
PLATFORMS = {"android", "ios", "desktop"}

PENDING, SENDING, ACCEPTED, FAILED, GAVE_UP, EXPIRED, CANCELLED = (
    "pending", "sending", "accepted", "failed", "gave_up", "expired", "cancelled")
LIVE = (PENDING, SENDING)

_cleanup_lock = threading.Lock()
_last_cleanup_ms = 0
_cleanup_cycle: Optional[dict] = None  # 진행 중인 한 시간 정리 — 단계 · 이어 할 자리 · 센 수(다음 부름이 이어 한다)
# 안 끝난 전송 — 부분 인덱스(ix_pushdelivery_live_gen · ix_pushdelivery_live_expiry)의 조건과 같은 꼴로 써야 그 인덱스를 쓴다.
# 값을 SQL 에 그대로 적는다 — 바인드 파라미터면 SQLite 는 부분 인덱스를 못 쓰고, Postgres 도 일반 계획(prepared)에서는 못 쓴다.
_LIVE_ROWS = PushDelivery.state.in_([literal_column(f"'{state}'") for state in LIVE])
_test_lock = threading.Lock()
_last_test_ms: dict[int, int] = {}


# 푸시가 한꺼번에 쓰는 DB 연결 수 — 웹의 일반 연결 풀(DATABASE_POOL_SIZE)을 API 와 함께 쓰므로, 발송 스레드(HTTP 8개)가
# 권한 확인을 몰아 해도 API 몫을 남긴다. HTTP 동시성과 DB 동시성의 예산을 나눈다.
PUSH_DB_CONCURRENCY = max(1, min(8, int(os.environ.get("PUSH_DB_CONCURRENCY", "2") or 2)))
_db_gate = threading.BoundedSemaphore(PUSH_DB_CONCURRENCY)


@contextmanager
def _session():
    """발송 쪽 DB 작업 — 동시에 PUSH_DB_CONCURRENCY 개까지만(연결을 쥐는 동안만 자리를 차지한다)."""
    with _db_gate:
        with get_session() as db:
            yield db


def _chunks(values, size: int = ID_CHUNK):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _monotonic() -> float:
    return time.monotonic()


def _second(ms: int) -> int:
    return -(-int(ms) // 1000) * 1000  # 초 단위로 올림 — 같은 처지의 줄을 묶어 쓰게(운영 DB 왕복 80ms)


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


def _new_version() -> int:
    """구독 세대 — 시각과 무관한 무작위 52비트(자바스크립트 안전 정수). 같은 밀리초에 지우고 다시 켜도 겹치지 않는다."""
    return secrets.randbits(52) or 1


_DEVICE_LOCK = 0x70757368  # 'push' — 계정마다 등록 · 상한 처리를 한 줄로(pg_advisory_xact_lock 의 첫 키)


def _lock_account(db, user_id: int) -> None:
    """같은 계정의 기기 등록을 트랜잭션 끝까지 한 줄로 세운다(Postgres). 두 등록이 동시에 '아직 3대'로 보고 둘 다 남겨
    4대가 되던 것. SQLite 는 쓰기가 원래 한 줄이라 필요 없다."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(sql_text("SELECT pg_advisory_xact_lock(:space, :user_id)"),
                   {"space": _DEVICE_LOCK, "user_id": int(user_id)})


def _changed() -> HTTPException:
    # 앱이 기대한 세대가 아니다 — 그사이 다른 계정이 이 기기로 켰거나 서버가 지웠다. 되살리거나 덮지 않는다.
    return HTTPException(status_code=409, detail="이 기기의 알림 설정이 바뀌었어요.")


def _cancel(db, generations, *, now_ms: int) -> set[int]:
    """그 구독 세대들의 남은 전송을 취소한다(해지 · 계정 바뀜 · 끝난 구독). 맡고 있던 바퀴의 결과 쓰기는 상태가 바뀌어
    무효가 된다. 영향받은 이벤트 id 를 돌려준다(끝났는지 다시 본다)."""
    generations = [(int(sub_id), int(version)) for sub_id, version in generations]
    if not generations:
        return set()
    rows = db.exec(update(PushDelivery).where(
        tuple_(PushDelivery.subscription_id, PushDelivery.sub_version).in_(generations),
        PushDelivery.state.in_(LIVE)).values(state=CANCELLED, finished_ms=now_ms, lease_ms=0, claim_token="")
        .returning(PushDelivery.event_id)).all()
    return {row[0] for row in rows}


def subscribe(user_id: int, endpoint: str, p256dh: str, auth: str, platform: str = "", *,
              expected_version: Optional[int] = None, now_ms: Optional[int] = None) -> dict:
    """이 기기를 이 계정의 알림 받는 곳으로 등록한다.

    expected_version 없이(앱에서 켜기): 없으면 만들고, 있으면 이 계정 · 키로 바꾼다. 다른 계정의 것이었으면 세대를 올리고
    옛 세대 앞으로 쌓인 전송을 취소한다(앞 사람의 알림이 이 기기로 오지 않게).
    expected_version 과 함께(앱의 12시간 재등록): 이 계정의 그 세대일 때만 키 · 시각을 고친다. 아니면 409 — 서버가 지운
    기기를 되살리거나 다른 계정이 가져간 기기를 덮지 않는다.
    """
    if not web_push.enabled():
        raise HTTPException(status_code=503, detail="지금은 앱 알림을 켤 수 없어요.")
    endpoint = _checked(endpoint, p256dh, auth)
    now_ms = _now_ms() if now_ms is None else now_ms
    platform = platform if platform in PLATFORMS else ""
    with get_session() as db:
        _lock_account(db, user_id)
        if expected_version is not None:
            row = db.exec(update(PushSubscription).where(
                PushSubscription.endpoint == endpoint, PushSubscription.user_id == user_id,
                PushSubscription.version == int(expected_version))
                .values(p256dh=p256dh, auth=auth, platform=platform, updated_ms=now_ms)
                .returning(PushSubscription.id, PushSubscription.version)).first()
            if row is None:
                db.rollback()
                raise _changed()
            db.commit()
            return {"subscribed": True, "version": int(row[1])}
        fresh = _new_version()
        statement = _insert(db)(PushSubscription).values(
            user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth, platform=platform, created_ms=now_ms,
            updated_ms=now_ms, last_ok_ms=0, fail_count=0, next_send_ms=0, version=fresh, send_token="", send_lease_ms=0)
        row = db.exec(statement.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={"user_id": user_id, "p256dh": p256dh, "auth": auth, "platform": platform, "updated_ms": now_ms,
                  "fail_count": 0,
                  "version": case((PushSubscription.user_id != user_id, literal(fresh)), else_=PushSubscription.version)})
            .returning(PushSubscription.id, PushSubscription.version)).first()
        sub_id, version = int(row[0]), int(row[1])
        stale = db.exec(select(PushDelivery.subscription_id, PushDelivery.sub_version).where(
            PushDelivery.subscription_id == sub_id, PushDelivery.sub_version != version,
            PushDelivery.state.in_(LIVE)).distinct()).all()
        touched = _cancel(db, stale, now_ms=now_ms)
        overflow = db.exec(select(PushSubscription.id, PushSubscription.version)
                           .where(PushSubscription.user_id == user_id)
                           .order_by(PushSubscription.updated_ms.desc(), PushSubscription.id.desc())
                           .offset(MAX_DEVICES)).all()
        if overflow:
            db.exec(sql_delete(PushSubscription).where(PushSubscription.id.in_([item[0] for item in overflow])))
            touched |= _cancel(db, overflow, now_ms=now_ms)
        _finish_events(db, touched, now_ms=now_ms)
        db.commit()
    return {"subscribed": True, "version": version}


def unsubscribe(user_id: int, endpoint: str, *, expected_version: Optional[int] = None,
                now_ms: Optional[int] = None) -> dict:
    """이 기기를 끈다 — 남은 전송도 취소. 기대한 세대를 주면 그 세대일 때만(늦게 온 옛 해제가 새로 켠 것을 지우지 않게)."""
    endpoint = _checked(endpoint, keys_required=False)
    now_ms = _now_ms() if now_ms is None else now_ms
    conditions = [PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint]
    if expected_version is not None:
        conditions.append(PushSubscription.version == int(expected_version))
    with get_session() as db:
        _lock_account(db, user_id)
        gone = db.exec(sql_delete(PushSubscription).where(*conditions)
                       .returning(PushSubscription.id, PushSubscription.version)).all()
        _finish_events(db, _cancel(db, gone, now_ms=now_ms), now_ms=now_ms)
        db.commit()
    return {"subscribed": False}


def status(user_id: int, endpoint: str) -> dict:
    """이 기기(엔드포인트)가 이 계정으로 알림을 받고 있는지와 그 세대 — 앱이 '켜짐'을 서버로 확인한다."""
    endpoint = _checked(endpoint, keys_required=False)
    with get_session() as db:
        version = db.exec(select(PushSubscription.version).where(PushSubscription.user_id == user_id,
                                                                 PushSubscription.endpoint == endpoint)).first()
    return {"subscribed": version is not None, "version": int(version or 0)}


def delete_for_user(db, user_id: int) -> None:
    """회원 탈퇴 — 기기 구독과 알림 · 전송 기록을 지운다(커밋은 호출자)."""
    db.exec(sql_delete(PushSubscription).where(PushSubscription.user_id == user_id))
    db.exec(sql_delete(PushDelivery).where(PushDelivery.user_id == user_id))
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


def _devices(db, user_id: int) -> list:
    return list(db.exec(select(PushSubscription.id, PushSubscription.version, PushSubscription.endpoint)
                        .where(PushSubscription.user_id == user_id)).all())


def _queue(db, user_id: int, devices: list, events: list[dict], *, now_ms: int) -> list[int]:
    """이벤트를 쌓고, 그 순간 켜져 있는 기기마다 전송 한 줄씩(커밋은 호출자). 새로 쌓인 이벤트 id."""
    inserted = db.exec(_insert(db)(PushOutbox).values(events).on_conflict_do_nothing(
        index_elements=[PushOutbox.user_id, PushOutbox.dedupe_key])
        .returning(PushOutbox.id, PushOutbox.expires_ms)).all()
    rows = [{"event_id": int(event_id), "user_id": user_id, "subscription_id": int(sub_id), "sub_version": int(version),
             "endpoint": endpoint, "state": PENDING, "attempts": 0, "next_ms": now_ms, "lease_ms": 0, "claim_token": "",
             "expires_ms": int(expires_ms), "created_ms": now_ms, "finished_ms": 0, "last_outcome": ""}
            for event_id, expires_ms in inserted for sub_id, version, endpoint in devices]
    if rows:
        db.exec(_insert(db)(PushDelivery).values(rows))
    return [int(event_id) for event_id, _ in inserted]


def enqueue_says(db, facts: dict, says: list[dict], *, now_ms: int) -> int:
    """새 중계 말풍선을 알림으로 쌓는다(커밋은 호출자). 쌓은 이벤트 수.

    알림을 켠 기기가 없는 계정은 아무것도 쌓지 않는다. 기사 · 커뮤니티 글은 실행 중인 세션 것만 — 끝나는
    바퀴에 나온 글은 '투자 중인 코인' 소식이 아니다. 체결 · 종료처럼 끝나는 바퀴의 일은 보낸다. '조용~하네요'는 채팅에만.
    쌓다 실패하면 저장 지점만 되돌리고 예외를 올린다 — 호출자(중계)가 말풍선 · 커서까지 되돌려 다음 바퀴에 다시 하도록.
    실패를 삼키면 말풍선만 커밋되고, 다음 바퀴는 이미 말한 일로 여겨 그 알림을 영영 다시 만들지 않는다.
    """
    chosen = [say for say in says if say.get("key") and say.get("hl") and say.get("kind") not in CHAT_ONLY_KINDS
              and now_ms - int(say.get("at_ms") or now_ms) <= STALE_SAY_MS
              and (say.get("kind") != "post" or (facts.get("status") == "running" and isinstance(say.get("post"), dict)))]
    if not chosen or not web_push.enabled():
        return 0
    user_id = int(facts["user_id"])
    events = [{"user_id": user_id, "dedupe_key": _dedupe_key(facts, say)[:160], "created_ms": now_ms,
               "expires_ms": now_ms + EVENT_TTL_MS, "sent_ms": 0, "result": "",
               "payload_json": json.dumps(say_payload(facts, say), ensure_ascii=False, separators=(",", ":"))}
              for say in chosen]
    with db.begin_nested():  # 실패하면 이 저장 지점까지 되돌리고 예외는 호출자에게
        devices = _devices(db, user_id)
        if not devices:
            return 0
        return len(_queue(db, user_id, devices, events, now_ms=now_ms))


# --- 보내기 -------------------------------------------------------------------
def _claimable(now_ms: int):
    # 보낼 차례가 된 전송, 또는 맡은 바퀴가 맡은 시간 안에 끝내지 못한 전송(죽음 · 멈춤). 시도를 다 쓴 것은 맡지 않는다
    # (죽은 바퀴가 마지막 시도를 쥐고 있었으면 _expire 가 gave_up 으로 끝낸다 — 시도는 MAX_ATTEMPTS 를 넘지 않는다).
    return and_(PushDelivery.attempts < MAX_ATTEMPTS,
                or_(and_(PushDelivery.state == PENDING, PushDelivery.next_ms <= now_ms),
                    and_(PushDelivery.state == SENDING, PushDelivery.lease_ms <= now_ms)))


def _finish_events(db, event_ids, *, now_ms: int) -> None:
    """기기별 전송이 모두 끝난 이벤트에 끝난 시각과 까닭을 적는다(커밋은 호출자)."""
    event_ids = sorted({int(event_id) for event_id in event_ids})
    if not event_ids:
        return
    states: dict[int, set] = {}
    for chunk in _chunks(event_ids):  # IN 목록은 잘게 — 대량 만료 뒤에도 드라이버 파라미터 상한을 넘지 않게
        for event_id, state in db.exec(select(PushDelivery.event_id, PushDelivery.state)
                                       .where(PushDelivery.event_id.in_(chunk)).distinct()).all():
            states.setdefault(int(event_id), set()).add(state)
    by_result: dict[str, list[int]] = {}
    for event_id in event_ids:
        seen = states.get(event_id, set())
        if seen & set(LIVE):
            continue
        result = next((name for state, name in ((ACCEPTED, "accepted"), (GAVE_UP, "gave_up"), (EXPIRED, "expired"),
                                                 (FAILED, "failed"), (CANCELLED, "cancelled")) if state in seen),
                      "no_device")
        by_result.setdefault(result, []).append(event_id)
    for result, ids in by_result.items():
        for chunk in _chunks(ids):
            db.exec(update(PushOutbox).where(PushOutbox.id.in_(chunk), PushOutbox.sent_ms == 0)
                    .values(sent_ms=now_ms, result=result))


def _expire(now_ms: int, *, limit: Optional[int] = None) -> int:
    """만료 시각이 지난 전송을 expired 로, 마지막 시도를 쥔 채 죽은 바퀴의 전송을 gave_up 으로 끝낸다 — 한 번에 limit 건까지,
    따로 커밋한다(맡은 바퀴가 아직 쥐고 있는 것은 그 바퀴가 끝낸다). 대량으로 쌓여도 정상 발송을 막지 않는다:
    발송은 만료되지 않은 전송만 맡고, 이 정리가 실패해도 그 바퀴는 그대로 돈다. 끝낸 수를 돌려준다.
    찾는 범위도 좁게 — 만료는 '안 끝난 전송의 만료 시각' 부분 인덱스(ix_pushdelivery_live_expiry)를 그 순서대로 읽어 지난
    것만 본다(끝난 기록 · 아직 살아 있는 전송을 PK 순으로 훑지 않는다). 시도를 다 쓴 채 버려진 전송은 '보내는 중'
    (state, next_ms 인덱스의 앞부분)에서만 찾는다 — 그 수는 바퀴들이 쥔 만큼뿐이다."""
    limit = limit or CLEANUP_BATCH
    lapsed = and_(PushDelivery.state == SENDING, PushDelivery.lease_ms <= now_ms)
    expired = and_(_LIVE_ROWS, PushDelivery.expires_ms <= now_ms,
                   or_(PushDelivery.state == PENDING, PushDelivery.lease_ms <= now_ms))
    finished = 0
    with _session() as db:
        for condition, order, state in ((expired, (PushDelivery.expires_ms, PushDelivery.id), EXPIRED),
                                        (and_(lapsed, PushDelivery.attempts >= MAX_ATTEMPTS), (), GAVE_UP)):
            ids = select(PushDelivery.id).where(condition).order_by(*order).limit(limit)
            rows = db.exec(update(PushDelivery).where(PushDelivery.id.in_(ids), condition)
                           .values(state=state, finished_ms=now_ms, lease_ms=0, claim_token="")
                           .returning(PushDelivery.event_id)).all()
            finished += len(rows)
            _finish_events(db, {row[0] for row in rows}, now_ms=now_ms)
        db.commit()
    return finished


def _may_send(delivery: dict, subscription: dict, token: str, at: int) -> bool:
    """이 HTTP 를 지금 보내도 되는지 — 첫 전송을 포함해 매번 보내기 직전에 DB 로 확인한다. 이 바퀴가 아직 이 엔드포인트의
    발송 권한(토큰 · 맡은 시각)을 쥐고 있고, 구독 세대 · 주소가 그대로이고, 푸시 서비스의 대기가 없고, 이 전송도 아직 이
    바퀴가 맡은 채(다른 계정으로 옮겨 취소되지 않음)여야 한다. 확인할 수 없으면 보내지 않는다."""
    try:
        with _session() as db:
            row = db.exec(select(PushSubscription.version, PushSubscription.endpoint, PushSubscription.send_token,
                                 PushSubscription.send_lease_ms, PushSubscription.next_send_ms,
                                 PushDelivery.state, PushDelivery.claim_token)
                          .join(PushDelivery, PushDelivery.subscription_id == PushSubscription.id)
                          .where(PushDelivery.id == delivery["id"], PushSubscription.id == subscription["id"])).first()
    except Exception:
        log.warning("push authority check failed", exc_info=True)
        return False
    if row is None:
        return False
    version, endpoint, send_token, lease_ms, wait_ms, state, claim_token = row
    return (int(version) == int(subscription["version"]) and endpoint == subscription["endpoint"]
            and send_token == token and int(lease_ms) > at and int(wait_ms) <= at
            and state == SENDING and claim_token == token)


def _send_endpoint(queue: list, send: Callable, *, now_ms: int, started: float, lease_until: int,
                   token: str) -> list[tuple]:
    """한 엔드포인트의 전송을 차례로 보낸다 — 응답을 반영한 뒤 다음 것을 고른다.

    잠깐 막히거나(429 · 5xx · 연결 실패) 구독이 끝나면(404 · 410) 그 뒤 전송은 이번 바퀴에 보내지 않는다. 시각은 맡기
    전부터 잰다(started) — 맡는 트랜잭션 · 스레드 대기 · 바퀴가 멈춘 시간까지 빼고 남은 수명만 TTL 로 주고, 맡은 시간이
    지났으면 보내지 않는다. 모든 HTTP 직전에 _may_send 로 권한 · 세대 · 대기 · 전송 상태를 확인하고, 아니면 이 전송부터
    내려놓는다. (전송, 결과, 응답 시각) 목록을 돌려준다.
    """
    out = []
    for index, (delivery, subscription, data) in enumerate(queue):
        at = now_ms + max(0, int((_monotonic() - started) * 1000))
        left_ms = int(delivery["expires_ms"]) - at
        if at < lease_until and left_ms < 1000:
            out.append((delivery, "expired", at))
            continue
        if at >= lease_until or not _may_send(delivery, subscription, token, at):
            out.extend((rest, "lapsed", at) for rest, _, _ in queue[index:])
            break
        # 확인한 뒤 보낼 때까지 걸린 시간도 뺀다(확인이 DB 왕복이다).
        at = now_ms + max(0, int((_monotonic() - started) * 1000))
        left_ms = int(delivery["expires_ms"]) - at
        if at >= lease_until or left_ms < 1000:
            out.extend((rest, "lapsed", at) for rest, _, _ in queue[index:])
            break
        try:
            outcome = send(subscription, data, ttl=left_ms // 1000, urgency="high")
        except Exception:
            log.warning("web push send crashed", exc_info=True)
            outcome = web_push.RETRY
        responded = now_ms + max(0, int((_monotonic() - started) * 1000))
        after = int(getattr(outcome, "retry_after_s", 0) or 0) * 1000
        if outcome == web_push.RETRY and after:
            # 받는 즉시 적는다 — 바퀴 끝까지 기다리면 그사이 다른 바퀴가 이 기기에 새 알림을 보낸다.
            _hold(subscription["endpoint"], _second(responded + after))
        out.append((delivery, outcome, responded))
        if outcome in (web_push.RETRY, web_push.GONE):
            follow = "held" if outcome == web_push.RETRY else "ended"
            out.extend((rest, follow, responded) for rest, _, _ in queue[index + 1:])
            break
    return out


def _release(db, token: str) -> None:
    db.exec(update(PushSubscription).where(PushSubscription.send_token == token).values(send_token="", send_lease_ms=0))


def _later_of(column, until: int):
    return case((column < until, until), else_=column)


def _hold(endpoint: str, until: int) -> None:
    """푸시 서비스가 기다리라고 한 엔드포인트에 그 시각을 적는다 — 더 늦은 값으로만. 제한은 주소에 걸리므로 세대를 묻지 않는다."""
    try:
        with _session() as db:
            db.exec(update(PushSubscription).where(PushSubscription.endpoint == endpoint)
                    .values(next_send_ms=_later_of(PushSubscription.next_send_ms, until)))
            db.commit()
    except Exception:
        log.warning("push hold write failed", exc_info=True)  # 바퀴 끝의 _device_results 가 다시 적는다


def _backoff(attempts: int) -> int:
    return RETRY_WAIT_MS[min(max(attempts, 1), len(RETRY_WAIT_MS)) - 1]


def _due_per_endpoint(db, token: str, generation, live, scope: list, now_ms: int):
    """쥔 엔드포인트마다 차례 PER_ENDPOINT 건의 전송 id. Postgres 는 엔드포인트마다 LATERAL … ORDER BY id LIMIT 으로
    부분 인덱스에서 필요한 몇 건만 찾는다(밀린 줄 전체를 정렬하지 않는다). SQLite(개발 · 시험)는 ROW_NUMBER."""
    conditions = (live, _claimable(now_ms), PushDelivery.expires_ms > now_ms, *scope)
    if db.get_bind().dialect.name == "postgresql":
        turn = (select(PushDelivery.id)
                .where(PushDelivery.subscription_id == PushSubscription.id, PushDelivery.sub_version == PushSubscription.version,
                       PushDelivery.endpoint == PushSubscription.endpoint, *conditions)
                .order_by(PushDelivery.id).limit(PER_ENDPOINT).lateral())
        return select(turn.c.id).select_from(PushSubscription).join(turn, true()).where(PushSubscription.send_token == token)
    ranked = (select(PushDelivery.id.label("id"),
                     func.row_number().over(partition_by=PushDelivery.subscription_id, order_by=PushDelivery.id).label("rank"))
              .join(PushSubscription, generation)
              .where(PushSubscription.send_token == token, *conditions).subquery())
    return select(ranked.c.id).where(ranked.c.rank <= PER_ENDPOINT)


def dispatch(*, now_ms: Optional[int] = None, send: Callable = web_push.send,
             event_ids: Optional[list[int]] = None) -> dict:
    """보낼 차례가 된 기기별 전송을 맡아 보낸다(event_ids 를 주면 그 이벤트 것만 — 시험 알림).

    엔드포인트 발송 권한(pushsubscription.send_token)을 먼저 쥐고 그 엔드포인트의 전송만 맡는다 — 일반 · 시험 알림,
    다른 프로세스 모두 이 한 줄로 조율해 한 엔드포인트를 두 바퀴가 동시에 쓰지 않는다. 기다리라고 한 엔드포인트
    (next_send_ms)는 쥐지 않는다. 같은 엔드포인트는 차례로, 서로 다른 엔드포인트끼리만 동시에 보낸다. 결과는 토큰이
    그대로인 전송 · 아직 쥔 엔드포인트의 것만 쓰고, 같은 트랜잭션에서 권한을 푼다.
    """
    now_ms = _now_ms() if now_ms is None else now_ms
    if not web_push.enabled() or (event_ids is not None and not event_ids):
        return {"claimed": 0}
    token = uuid.uuid4().hex
    started = _monotonic()  # 시각은 맡기 전부터 — DB 처리 · 스레드 대기 · 멈춤도 맡은 시간과 남은 수명에서 뺀다
    try:  # 만료 정리는 따로, 한 묶음만 — 실패해도 이 바퀴의 정상 발송은 그대로(맡기는 만료되지 않은 전송만 고른다)
        _expire(now_ms)
    except Exception:
        log.warning("push expiry sweep failed; dispatching anyway", exc_info=True)
    generation = and_(PushSubscription.id == PushDelivery.subscription_id,
                      PushSubscription.version == PushDelivery.sub_version,
                      PushSubscription.endpoint == PushDelivery.endpoint)
    scope = [PushDelivery.event_id.in_(list(event_ids))] if event_ids else []
    free = and_(PushSubscription.next_send_ms <= now_ms, PushSubscription.send_lease_ms <= now_ms)
    with _session() as db:
        # ① 보낼 차례인 전송이 있는 엔드포인트의 발송 권한부터 — 다른 바퀴(일반 · 시험 · 다른 프로세스)가 쥐고 있거나
        #    푸시 서비스가 기다리라고 한 엔드포인트는 건너뛴다. 조건부 UPDATE 라 두 바퀴가 같은 엔드포인트를 함께 쥐지 못한다.
        #    엔드포인트마다 보낼 것이 '있는지'만 묻는다(EXISTS). 읽는 방법은 옵티마이저가 정한다 — 엔드포인트마다 인덱스에서
        #    첫 한 건을 찾거나(엔드포인트가 적을 때), 안 끝난 전송으로 hash semi join(많을 때). 어느 쪽이든 끝난 기록은 읽지 않는다.
        #    고르는 차례는 마지막으로 배정된 시각(served_ms)이 오랜 순 — 맡을 때 이 바퀴 시각을 적으므로 성공 여부와 무관하게
        #    돌아간다. 받은 시각으로 고르면 실패만 하는 엔드포인트가 밀린 줄이 남는 동안 늘 앞자리를 차지했다(11/09 리뷰).
        live = _LIVE_ROWS  # 부분 인덱스(ix_pushdelivery_live_gen) 조건과 맞춘다
        due_here = (select(PushDelivery.id)
                    .where(generation, live, _claimable(now_ms), PushDelivery.expires_ms > now_ms, *scope).exists())
        wanted = (select(PushSubscription.id).where(free, due_here)
                  .order_by(PushSubscription.served_ms, PushSubscription.id).limit(ENDPOINT_BATCH))
        owned = db.exec(update(PushSubscription).where(PushSubscription.id.in_(wanted.scalar_subquery()), free)
                        .values(send_token=token, send_lease_ms=now_ms + LEASE_MS, served_ms=now_ms)
                        .returning(PushSubscription.id, PushSubscription.version, PushSubscription.endpoint,
                                   PushSubscription.p256dh, PushSubscription.auth)).all()
        subs = {int(row[0]): {"id": int(row[0]), "version": int(row[1]), "endpoint": row[2], "p256dh": row[3],
                              "auth": row[4]} for row in owned}
        claimed, payloads, more = [], {}, len(subs) >= ENDPOINT_BATCH
        if subs:
            # ② 쥔 엔드포인트의 전송을 엔드포인트마다 PER_ENDPOINT 건까지 공정하게 맡는다(이 바퀴의 토큰).
            due = _due_per_endpoint(db, token, generation, live, scope, now_ms)
            claimed = [dict(row._mapping) for row in db.exec(
                update(PushDelivery).where(PushDelivery.id.in_(due), _claimable(now_ms))
                .values(state=SENDING, claim_token=token, lease_ms=now_ms + LEASE_MS, attempts=PushDelivery.attempts + 1)
                .returning(PushDelivery.id, PushDelivery.event_id, PushDelivery.subscription_id, PushDelivery.sub_version,
                           PushDelivery.endpoint, PushDelivery.attempts, PushDelivery.expires_ms)).all()]
            assigned = {item["subscription_id"] for item in claimed}
            # ③ 전송을 하나도 맡지 못한 엔드포인트의 권한은 바로 돌려준다 — 다른 바퀴가 쓸 수 있게.
            db.exec(update(PushSubscription).where(PushSubscription.send_token == token,
                                                   PushSubscription.id.notin_(assigned or {0}))
                    .values(send_token="", send_lease_ms=0))
            per_endpoint: dict[int, int] = {}
            for item in claimed:
                per_endpoint[item["subscription_id"]] = per_endpoint.get(item["subscription_id"], 0) + 1
            more = more or any(count >= PER_ENDPOINT for count in per_endpoint.values())
            if claimed:
                payloads = dict(db.exec(select(PushOutbox.id, PushOutbox.payload_json).where(
                    PushOutbox.id.in_({item["event_id"] for item in claimed}))).all())
        db.commit()
    if not claimed:
        return {"claimed": 0}

    plan: dict[int, dict] = {}       # 전송 id → 쓸 값
    queues: dict[str, list] = {}     # 엔드포인트 → 차례로 보낼 전송
    for delivery in sorted(claimed, key=lambda item: item["id"]):
        subscription = subs.get(delivery["subscription_id"])
        if (subscription is None or subscription["version"] != int(delivery["sub_version"])
                or subscription["endpoint"] != delivery["endpoint"]):
            plan[delivery["id"]] = {"state": CANCELLED, "outcome": "changed"}  # 맡는 사이 구독이 바뀌었다
            continue
        try:
            payload = json.loads(payloads.get(delivery["event_id"]) or "")
            payload["event"] = int(delivery["event_id"])
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            plan[delivery["id"]] = {"state": FAILED, "outcome": "broken"}
            continue
        queues.setdefault(delivery["endpoint"], []).append((delivery, subscription, data))

    # 큰 묶음은 LEASE_MS 보다 오래 걸릴 수 있다 — 그동안 다른 바퀴가 다시 맡지 않게 늘려 둔다.
    jobs = sum(len(queue) for queue in queues.values())
    longest = max((len(queue) for queue in queues.values()), default=0)
    needed = max(-(-jobs // SEND_THREADS), longest) * SEND_WAVE_MS + 60_000
    lease_until = now_ms + LEASE_MS
    if needed > LEASE_MS:
        lease_until = now_ms + needed
        with _session() as db:
            db.exec(update(PushSubscription).where(PushSubscription.send_token == token)
                    .values(send_lease_ms=lease_until))
            db.exec(update(PushDelivery).where(PushDelivery.claim_token == token, PushDelivery.state == SENDING)
                    .values(lease_ms=lease_until))
            db.commit()
    work = list(queues.values())

    def run(queue):
        return _send_endpoint(queue, send, now_ms=now_ms, started=started, lease_until=lease_until, token=token)
    if len(work) <= 1:
        results = [run(queue) for queue in work]
    else:
        with ThreadPoolExecutor(max_workers=min(SEND_THREADS, len(work)), thread_name_prefix="ggp-push") as pool:
            results = list(pool.map(run, work))

    counts = {"sent": 0, "retry": 0, "gone": 0, "failed": 0, "held": 0, "expired": 0, "gave_up": 0}
    holds = {}
    answers = []  # (전송 id, 구독 열쇠, 응답) — 결과를 실제로 쓴 전송의 것만 구독에 반영한다
    for outcomes in results:
        blocked_until = 0
        for delivery, outcome, responded in outcomes:
            key = (delivery["subscription_id"], int(delivery["sub_version"]), delivery["endpoint"])
            attempts = int(delivery["attempts"])
            if outcome in (web_push.SENT, web_push.GONE, web_push.FAILED):
                answers.append((delivery["id"], key, outcome))
            if outcome == web_push.SENT:
                plan[delivery["id"]] = {"state": ACCEPTED, "outcome": "sent", "at": responded}
                counts["sent"] += 1
            elif outcome == web_push.GONE:
                plan[delivery["id"]] = {"state": FAILED, "outcome": "gone", "at": responded}
                counts["gone"] += 1
            elif outcome == web_push.FAILED:
                plan[delivery["id"]] = {"state": FAILED, "outcome": "failed", "at": responded}
                counts["failed"] += 1
            elif outcome == web_push.RETRY:
                after = int(getattr(outcome, "retry_after_s", 0) or 0) * 1000
                if after:
                    holds[key] = max(holds.get(key, 0), _second(responded + after))
                due = _second(responded + max(_backoff(attempts), after))
                blocked_until = due
                counts["retry"] += 1
                if attempts >= MAX_ATTEMPTS:
                    plan[delivery["id"]] = {"state": GAVE_UP, "outcome": "retry", "at": responded}
                    counts["gave_up"] += 1
                elif due >= int(delivery["expires_ms"]):
                    plan[delivery["id"]] = {"state": EXPIRED, "outcome": "retry", "at": responded}
                    counts["expired"] += 1
                else:
                    plan[delivery["id"]] = {"state": PENDING, "outcome": "retry", "next": due}
            elif outcome == "held":
                # 앞 전송이 막혀 이번엔 보내지 않았다 — 시도로 세지 않고, 막힌 시각 뒤에 그 차례로.
                due = blocked_until or _second(responded + RETRY_WAIT_MS[0])
                counts["held"] += 1
                if due >= int(delivery["expires_ms"]):
                    plan[delivery["id"]] = {"state": EXPIRED, "outcome": "held", "at": responded, "undo": True}
                    counts["expired"] += 1
                else:
                    plan[delivery["id"]] = {"state": PENDING, "outcome": "held", "next": due, "undo": True}
            elif outcome == "ended":
                plan[delivery["id"]] = {"state": CANCELLED, "outcome": "gone", "at": responded, "undo": True}
            elif outcome == "lapsed":
                # 맡은 시간을 넘겨 보내지 않았다 — 아직 이 바퀴 것이면 바로 다음 바퀴가 맡게 내려놓는다(다른 바퀴가 맡았으면 무효).
                plan[delivery["id"]] = {"state": PENDING, "outcome": "lapsed", "next": _second(responded), "undo": True}
            else:  # expired
                plan[delivery["id"]] = {"state": EXPIRED, "outcome": "expired", "at": responded, "undo": True}
                counts["expired"] += 1

    groups: dict[tuple, list[int]] = {}
    for delivery_id, item in plan.items():
        groups.setdefault((item["state"], item.get("next", 0), _second(item.get("at", now_ms)), item["outcome"],
                           bool(item.get("undo"))), []).append(delivery_id)
    written: set[int] = set()
    with _session() as db:
        # ① 아직 이 바퀴가 쥔 엔드포인트(맡은 시간이 지나 다른 바퀴가 가져갔으면 빠진다) — 끝날 때까지 잠가 둔다.
        still = set(db.exec(select(PushSubscription.id).where(PushSubscription.send_token == token)
                            .with_for_update()).all())
        # ② 전송 결과 — 토큰이 그대로인 줄만. RETURNING 으로 실제로 쓴 줄을 안다.
        for (state, next_ms, at, outcome, undo), ids in groups.items():
            values = {"state": state, "last_outcome": outcome, "lease_ms": 0, "claim_token": ""}
            if state == PENDING:
                values["next_ms"] = next_ms
            else:
                values["finished_ms"] = at
            if undo:
                values["attempts"] = PushDelivery.attempts - 1
            written |= {row[0] for row in db.exec(update(PushDelivery).where(
                PushDelivery.id.in_(ids), PushDelivery.claim_token == token, PushDelivery.state == SENDING)
                .values(**values).returning(PushDelivery.id)).all()}
        # ③ 구독(성공 · 실패 누적 · 삭제)에는 결과를 실제로 쓴 전송 · 아직 쥔 엔드포인트의 응답만 — 권한을 잃은 옛 바퀴의
        #    늦은 실패가 정상 구독을 지우고 새 알림까지 취소하지 않게. 대기(Retry-After)는 받는 즉시 적었고 더 늦은 값으로만.
        accepted, failed, gone = {}, {}, {}
        for delivery_id, key, outcome in answers:
            if delivery_id not in written or key[0] not in still:
                continue
            if outcome == web_push.SENT:
                accepted[key] = True
            elif outcome == web_push.GONE:
                gone[key] = True
            else:
                failed[key] = failed.get(key, 0) + 1
        touched = {item["event_id"] for item in claimed}
        touched |= _device_results(db, accepted=accepted, holds=holds, failed=failed, gone=gone, now_ms=now_ms)
        _release(db, token)  # ④ 발송 권한을 푼다
        _finish_events(db, touched, now_ms=now_ms)
        db.commit()
    written = len(written)
    lost = len(claimed) - written
    if lost:
        log.warning("push dispatch could not record %s deliveries (taken over by another pass or cancelled)", lost)
    if counts["gave_up"] or counts["expired"]:
        log.warning("push deliveries ended undelivered: gave_up=%s expired=%s", counts["gave_up"], counts["expired"])
    # more — 이 바퀴가 다 맡지 못한 차례가 남았을 수 있다(엔드포인트 · 엔드포인트당 상한) — push_runtime 이 바로 다음 바퀴를 돈다.
    return {"claimed": len(claimed), "jobs": jobs, "lost": lost, "more": more, **counts}


def _device_results(db, *, accepted: dict, holds: dict, failed: dict, gone: dict, now_ms: int) -> set[int]:
    """푸시 서비스 응답으로 기기 상태를 고친다(커밋은 호출자). 보낸 그 세대 — (id, version, endpoint)가 다 맞을 때만.

    받음은 실패 횟수를 지우지만 대기(next_send_ms)는 지우지 않는다. 대기는 주소에 걸려 세대를 묻지 않고, 더 늦은 값으로만
    바뀐다(받는 즉시 _hold 가 적고 여기서 한 번 더). 끝난 구독(404 · 410)과
    FAIL_LIMIT 번 거절된 구독은 지우고 남은 전송을 취소한다. 영향받은 이벤트 id 를 돌려준다.
    """
    def mine(keys):
        return tuple_(PushSubscription.id, PushSubscription.version, PushSubscription.endpoint).in_(list(keys))
    touched: set[int] = set()
    if accepted:
        db.exec(update(PushSubscription).where(mine(accepted)).values(
            fail_count=0, last_ok_ms=case((PushSubscription.last_ok_ms < now_ms, now_ms),
                                          else_=PushSubscription.last_ok_ms)))
    for until in sorted(set(holds.values())):  # 대기는 주소에 — 세대가 바뀌어도 푸시 서비스의 제한은 그대로다
        endpoints = [key[2] for key, value in holds.items() if value == until]
        db.exec(update(PushSubscription).where(PushSubscription.endpoint.in_(endpoints))
                .values(next_send_ms=_later_of(PushSubscription.next_send_ms, until)))
    for count in sorted(set(failed.values())):
        keys = [key for key, value in failed.items() if value == count and key not in gone]
        if keys:
            db.exec(update(PushSubscription).where(mine(keys)).values(fail_count=PushSubscription.fail_count + count))
    if failed:
        removed = db.exec(sql_delete(PushSubscription).where(mine(failed), PushSubscription.fail_count >= FAIL_LIMIT)
                          .returning(PushSubscription.id, PushSubscription.version)).all()
        touched |= _cancel(db, removed, now_ms=now_ms)
    if gone:
        removed = db.exec(sql_delete(PushSubscription).where(mine(gone))
                          .returning(PushSubscription.id, PushSubscription.version)).all()
        touched |= _cancel(db, removed, now_ms=now_ms)
    return touched


def maintain(*, now_ms: Optional[int] = None, budget_s: Optional[float] = None) -> dict:
    """한 시간 정리 — 발송 바퀴 밖(push_runtime 의 정리 작업)에서 부른다. 발송은 정리를 기다리지 않는다.

    한 시간에 한 번 한 바퀴를 시작해 단계별로 CLEANUP_BATCH 건씩 커밋한다: 만료 → 끝나지 않은 채 남은 이벤트 마무리 →
    사흘 지난 전송 · 이벤트 삭제 → 집계 로그('알림이 안 왔다' 문의 · 정리가 유입을 따라가는지 볼 때). 한 번 부를 때
    budget_s(기본 CLEANUP_BUDGET_S) 초까지만 하고, 남은 일은 멈춘 자리부터 다음 부름이 이어 한다 — 오래 멈췄다 켜져
    많이 쌓였어도 한 번이 길어지지 않고, 처리량은 시간당 상한 없이 쌓인 만큼 따라간다(부르는 간격이 짧아진다).
    실패하면 그 바퀴를 접고 다음 시간에 다시. {"active": 이어 할 일이 남았는지} 를 돌려준다."""
    global _cleanup_cycle, _last_cleanup_ms
    now_ms = _now_ms() if now_ms is None else now_ms
    if not _cleanup_lock.acquire(blocking=False):  # 다른 스레드가 하는 중 — 그쪽이 이어 한다
        return {"active": False}
    try:
        cycle = _cleanup_cycle
        if cycle is None:
            if now_ms - _last_cleanup_ms < CLEANUP_EVERY_MS:
                return {"active": False}
            _last_cleanup_ms = now_ms
            cycle = {"step": 0, "after": 0, "expired": 0, "checked": 0, "removed": {"pushdelivery": 0, "pushoutbox": 0},
                     "started_ms": now_ms}
        deadline = _monotonic() + (CLEANUP_BUDGET_S if budget_s is None else budget_s)
        try:
            while cycle["step"] < len(_CLEANUP_STEPS):
                if _CLEANUP_STEPS[cycle["step"]](cycle, now_ms):
                    cycle["step"] += 1
                    cycle["after"] = 0
                if _monotonic() >= deadline:
                    break
        except Exception:
            log.warning("push cleanup failed; retrying next hour", exc_info=True)
            cycle = None
        _cleanup_cycle = cycle if cycle is not None and cycle["step"] < len(_CLEANUP_STEPS) else None
        return {"active": _cleanup_cycle is not None}
    finally:
        _cleanup_lock.release()


def _sweep_expired(cycle: dict, now_ms: int) -> bool:
    count = _expire(now_ms)
    cycle["expired"] += count
    return count < CLEANUP_BATCH


def _close_lingering(cycle: dict, now_ms: int) -> bool:
    """만료 뒤에도 끝난 시각이 비어 있는 이벤트를 마무리한다. 아직 안 끝난 전송이 남아 못 닫는 이벤트가 있어도 id 로
    이어 읽어(after) 같은 묶음을 되풀이해 읽지 않는다 — 안 보낸 이벤트만 담는 (sent_ms, id) 인덱스의 앞부분만 읽는다."""
    with _session() as db:
        ids = db.exec(select(PushOutbox.id).where(PushOutbox.sent_ms == 0, PushOutbox.id > cycle["after"],
                                                  PushOutbox.expires_ms < now_ms - LEASE_MS)
                      .order_by(PushOutbox.id).limit(CLEANUP_BATCH)).all()
        _finish_events(db, ids, now_ms=now_ms)
        db.commit()
    if ids:
        cycle["after"] = max(ids)
    cycle["checked"] += len(ids)
    return len(ids) < CLEANUP_BATCH


def _remove_old(model):
    """사흘 지난 기록을 지운다 — id 순 맨 앞 CLEANUP_BATCH 행만 읽고 그중 지난 것만(id 는 쌓은 순서라 오래된 것이 앞에 있다).
    지울 것이 없어도 표 전체를 훑지 않는다. 맨 앞에 아직 안 지난 행이 끼면 이번 바퀴는 거기서 멈추고 다음 시간에 이어 한다."""
    def step(cycle: dict, now_ms: int) -> bool:
        with _session() as db:
            head = select(model.id).order_by(model.id).limit(CLEANUP_BATCH).scalar_subquery()
            removed = db.exec(sql_delete(model).where(model.id.in_(head), model.created_ms < now_ms - KEEP_MS)).rowcount
            db.commit()
        cycle["removed"][model.__tablename__] += removed
        return removed < CLEANUP_BATCH
    return step


def _report(cycle: dict, now_ms: int) -> bool:
    with _session() as db:
        counts = dict(db.exec(select(PushDelivery.state, func.count()).where(
            PushDelivery.finished_ms >= now_ms - CLEANUP_EVERY_MS).group_by(PushDelivery.state)).all())
        oldest_live = db.exec(select(func.min(PushDelivery.created_ms)).where(_LIVE_ROWS)).first()
        oldest_kept = db.exec(select(PushDelivery.created_ms).order_by(PushDelivery.id).limit(1)).first()
    log.info("push deliveries last hour: %s · oldest live %ss · cleanup expired=%s events_checked=%s removed=%s "
             "took=%ss · oldest kept %sh", counts, (now_ms - oldest_live) // 1000 if oldest_live else 0,
             cycle["expired"], cycle["checked"], cycle["removed"], (now_ms - cycle["started_ms"]) // 1000,
             (now_ms - oldest_kept) // 3_600_000 if oldest_kept else 0)
    return True


_CLEANUP_STEPS = (_sweep_expired, _close_lingering, _remove_old(PushDelivery), _remove_old(PushOutbox), _report)


def send_test(user_id: int, *, now_ms: Optional[int] = None, send: Callable = web_push.send) -> dict:
    """알림을 켠 직후 '이렇게 와요'를 바로 보여 주는 시험 알림. 계정마다 10초에 한 번.

    보통 알림과 같은 길(이벤트 → 기기별 전송 → dispatch)로 보낸다 — 엔드포인트 대기 · 만료 · 세대 규칙을 똑같이 받는다.
    기다리라는 기기에는 지금 보내지 않고(retry 로 셈) 대기가 끝나면 5분 안에서 보낸다.
    """
    if not web_push.enabled():
        raise HTTPException(status_code=503, detail="지금은 앱 알림을 켤 수 없어요.")
    now_ms = _now_ms() if now_ms is None else now_ms
    with _test_lock:
        if now_ms - _last_test_ms.get(user_id, 0) < 10_000:
            raise HTTPException(status_code=429, detail="잠시 뒤에 다시 보내 주세요.")
        _last_test_ms[user_id] = now_ms
        if len(_last_test_ms) > 5000:
            _last_test_ms.clear()
    payload = {"title": "껄무새 앱 알림이 켜졌어요",
               "body": "내 에이전트의 매매 · 손익 · 시세 · 뉴스 소식을 이렇게 알려 드려요.",
               "url": "/agents", "tag": "push-test", "at": now_ms}
    with get_session() as db:
        devices = _devices(db, user_id)
        if not devices:
            raise HTTPException(status_code=404, detail="알림을 켠 기기가 없어요.")
        event_ids = _queue(db, user_id, devices, [{
            "user_id": user_id, "dedupe_key": f"test:{now_ms}:{uuid.uuid4().hex[:8]}", "created_ms": now_ms,
            "expires_ms": now_ms + TEST_TTL_MS, "sent_ms": 0, "result": "",
            "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}], now_ms=now_ms)
        db.commit()
    for attempt in range(3):
        dispatch(now_ms=now_ms, send=send, event_ids=event_ids)
        with get_session() as db:
            # 다른 바퀴가 그 엔드포인트로 보내는 중이면(발송 권한) 잠깐 기다렸다 다시 — 기다리라는 기기는 기다리지 않는다.
            busy = db.exec(select(func.count()).select_from(PushDelivery).join(
                PushSubscription, PushSubscription.id == PushDelivery.subscription_id).where(
                PushDelivery.event_id.in_(event_ids), PushDelivery.state == PENDING,
                PushSubscription.next_send_ms <= now_ms, PushSubscription.send_lease_ms > now_ms)).one()
        if not busy or attempt == 2:
            break
        time.sleep(0.3)
    with get_session() as db:
        states = list(db.exec(select(PushDelivery.state).where(PushDelivery.event_id.in_(event_ids))).all())
    # 화면이 받음 · 일부 · 못 보냄을 가른다 — retry 는 '잠시 뒤 다시', 나머지 실패는 '알림 다시 켜기'(구독이 끝났거나 거절됨).
    return {"sent": states.count(ACCEPTED), "devices": len(states),
            "retry": sum(1 for state in states if state in LIVE)}
