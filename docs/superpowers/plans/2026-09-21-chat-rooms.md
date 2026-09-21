# 전략방 (소그룹 유료 채팅) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리더보드 채팅에 사용자가 만드는 소그룹 "전략방"(최대 10명, 포인트 입장료, 7일 만료)을 붙인다.

**Architecture:** 기존 전역 채팅(`ChatMessage`)에 `room_id`(NULL=공개방)를 더하고, 새 모듈 `backend/app/rooms.py`가 방 생성·입장·나가기·연장·목록과 포인트 정산(`points.apply` 재사용)을 맡는다. `chat.py`의 세 함수는 `room_id`를 받아 멤버십·범위·읽음 커서(멤버 행)를 방 단위로 바꾼다. 프론트는 `ChatBox`의 피드 scope에 방을 포함시켜 같은 컴포넌트를 방 모드로 재사용하고, 방 목록/생성은 `RoomsPanel` 탭으로 붙인다.

**Tech Stack:** FastAPI + SQLModel(sqlite dev / Postgres prod), pytest; React + Vite, node:test.

**Spec:** `docs/superpowers/specs/2026-09-21-chat-rooms-design.md`

## Global Constraints

- 사용자 노출 명칭은 **"전략방"**. "리딩방"이라는 단어는 코드·문구·주석 어디에도 쓰지 않는다.
- 상수(정확히 이 값): `ROOM_CREATE_COST=100`, `ROOM_EXTEND_COST=50`, `ROOM_TTL_MS=7*24*3600*1000`, `ROOM_EXTEND_WINDOW_MS=24*3600*1000`, `MIN_CAPACITY=2`, `MAX_CAPACITY=10`, `MAX_ENTRY_FEE=300`, `MIN_ACCOUNT_AGE_FOR_PAID_MS=3*24*3600*1000`, `ROOMS_PER_DAY=1`, `TITLE_MIN=2`, `TITLE_MAX=30`, 방장 몫은 `points.creator_share(fee)`(70%).
- 환불 코드는 어떤 형태로도 존재하지 않는다. 방장 조기 폐쇄 없음(관리자 폐쇄만).
- 오류는 `detail`에 한국어 문장 하나. 상태코드: 402 포인트 부족, 403 권한/자격, 409 상태 충돌, 410 끝난 방, 422 입력 오류, 429 하루 1개.
- 색은 `rgb(var(--c-*))` 토큰만. 다크 규칙을 따로 두지 않는다.
- 커밋은 `git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit`, 한국어 메시지, 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 백엔드 테스트: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings` (레포 루트에서). 프론트: `cd frontend && node --test "tests/*.test.js"` 와 `npx vite build --logLevel error`.
- 긴 파일은 Bash heredoc 대신 Write 도구로 쓴다(인자 길이 제한).

---

## File Structure

| 파일 | 역할 |
|---|---|
| `backend/app/db.py` | `ChatRoom`, `ChatRoomMember` 모델, `ChatMessage.room_id`, `User.room_consent_at`, sqlite/PG 마이그레이션 목록 |
| `backend/app/rooms.py` (신규) | 상수, `RoomError`, `create_room`, `join_room`, `leave_room`, `extend_room`, `close_room_by_admin`, `list_rooms`, `room_view`, `require_member`, `is_open` |
| `backend/app/chat.py` | `add_message`/`list_messages`/`mark_read`에 `room_id` — 멤버십 확인, 범위, 멤버 행 읽음 커서 |
| `backend/app/main.py` | `/api/rooms*` 6개 라우트, `/api/chat*` 3개에 `room_id` |
| `backend/tests/test_rooms.py` (신규) | 백엔드 전부 |
| `supabase/migrations/20260921120000_chat_rooms.sql`, `supabase/tests/gg_parrot_rls.sql` | 프로덕션 스키마·RLS |
| `frontend/src/api.js` | `roomsList/roomCreate/roomJoin/roomLeave/roomExtend`, chat 3개에 `roomId` |
| `frontend/src/lib/roomsCopy.js` (신규) | 문구 |
| `frontend/src/lib/roomFlow.js` (신규) | 순수 함수(남은 시간·입장 가능·검증·탭) |
| `frontend/src/lib/chatBadge.js` | `chatScope(userId, roomId)` |
| `frontend/src/components/RoomsPanel.jsx` + `.css` (신규) | 방 찾기/만들기 탭 |
| `frontend/src/components/ChatBox.jsx` + `.css` | 탭 줄, 방 모드 헤더, `roomId` 스레딩 |
| `frontend/tests/roomFlow.test.js`, `frontend/tests/chatApi.test.js` | 프론트 테스트 |
| `DEPLOY.md`, `frontend/src/pages/Guide.jsx` | 운영 메모·FAQ 한 항목 |

---

### Task 1: 데이터 모델 + 마이그레이션

**Files:**
- Modify: `backend/app/db.py` (User 클래스 끝 ~292행, ChatMessage ~641행, `_migrate` `added["user"]` ~981행·`added["chatmessage"]` ~985행, `_PG_ADDED_COLUMNS["user"]`·`["chatmessage"]` ~1130행, `_PG_PRIVATE_CACHE_TABLES` ~1199행)
- Create: `supabase/migrations/20260921120000_chat_rooms.sql`
- Modify: `supabase/tests/gg_parrot_rls.sql` (owned_tables 배열, 알파벳 순)
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Produces: `ChatRoom(id, owner_id, title, capacity, entry_fee, created_at, created_ms, expires_ms, extended_count, closed_reason, closed_at)`, `ChatRoomMember(room_id, user_id, paid, joined_at, joined_ms, last_seen_id)`, `ChatMessage.room_id: Optional[int]`, `User.room_consent_at: str`.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_rooms.py` 를 새로 만든다:

```python
"""전략방 — 소그룹 유료 채팅(방 생성·입장·정산·만료·방 메시지)."""
from __future__ import annotations

import secrets
import time

from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import ChatMessage, ChatRoom, ChatRoomMember, PointLedger, User, get_session
from app.main import app

client = TestClient(app)


def _signup(*, age_days: float = 10):
    """가입한 계정(토큰, id). 기본으로 가입 10일 전으로 되돌려 유료방 입장 조건(3일)을 만족시킨다."""
    tok = secrets.token_hex(4)
    body = client.post("/api/auth/signup", json={
        "email": f"room{tok}@ex.com", "username": f"room_{tok}", "password": "password123",
    }).json()
    user_id = body["user"]["id"]
    if age_days:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_days * 86400))
        with get_session() as db:
            user = db.get(User, user_id)
            user.created_at = stamp
            db.add(user)
            db.commit()
    return body["token"], user_id


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _balance(user_id):
    with get_session() as db:
        return db.get(User, user_id).points_balance


def _ledger(user_id):
    with get_session() as db:
        return db.exec(select(PointLedger).where(PointLedger.user_id == user_id).order_by(PointLedger.id)).all()


def test_room_tables_and_columns_exist():
    _, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        assert user.room_consent_at == ""
        room = ChatRoom(owner_id=user_id, title="테스트", capacity=3, entry_fee=0,
                        created_at="2026-09-21T00:00:00Z", created_ms=1, expires_ms=2)
        db.add(room)
        db.commit()
        db.refresh(room)
        db.add(ChatRoomMember(room_id=room.id, user_id=user_id, paid=0, joined_at="2026-09-21T00:00:00Z", joined_ms=1))
        db.add(ChatMessage(user_id=user_id, username="x", text="hi", created_at="2026-09-21T00:00:00Z", created_ms=1, room_id=room.id))
        db.commit()
        member = db.get(ChatRoomMember, (room.id, user_id))
        assert member is not None and member.last_seen_id == 0
        assert db.exec(select(ChatMessage).where(ChatMessage.room_id == room.id)).one().text == "hi"
```

- [ ] **Step 2: 실패 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'ChatRoom'`

- [ ] **Step 3: 모델 추가**

`backend/app/db.py` — `User` 클래스의 `ask_consent_at: str = ""` 바로 아래에:

```python
    # 전략방 — 방 만들 때 한 번 받는 동의(투자 권유·수익 보장 발언 금지) 시각. 비어 있으면 아직 동의 전.
    room_consent_at: str = ""
```

`ChatMessage` 클래스의 `created_ms` 필드 아래에:

```python
    # 전략방 메시지면 그 방 id. NULL 이면 기존 공개(리더보드) 채팅.
    room_id: Optional[int] = Field(default=None, index=True)
```

`ChatReadState` 클래스 바로 뒤에 두 모델 추가:

```python
class ChatRoom(SQLModel, table=True):
    """전략방 — 회원이 만드는 소그룹 채팅(최대 10명, 포인트 입장료, 7일 만료). rooms.py 가 규칙을 맡는다."""

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True)
    title: str
    capacity: int  # 방장 포함 정원 2~10
    entry_fee: int  # 0~300 포인트
    created_at: str
    created_ms: int = Field(index=True, sa_type=BigInteger)
    expires_ms: int = Field(index=True, sa_type=BigInteger)  # 생성 + 7일, 연장하면 += 7일
    extended_count: int = 0
    closed_reason: str = ""  # "" | "admin" — 관리자가 닫은 방만 값이 있다
    closed_at: str = ""


class ChatRoomMember(SQLModel, table=True):
    """방 멤버 한 명. 이 행이 곧 방의 읽음 상태(last_seen_id)이기도 하다. 방장도 paid=0 으로 한 행."""

    room_id: int = Field(primary_key=True, index=True)
    user_id: int = Field(primary_key=True, index=True)
    paid: int = 0  # 실제 낸 포인트 — 환불은 없으므로 기록용
    joined_at: str
    joined_ms: int = Field(sa_type=BigInteger)
    last_seen_id: int = 0
```

`_migrate()` 안 `added["user"]` 딕셔너리 끝(`"ask_consent_at"` 항목 뒤)에:

```python
            "room_consent_at": 'ALTER TABLE "user" ADD COLUMN room_consent_at TEXT NOT NULL DEFAULT \'\'',
```

`added["chatmessage"]` 딕셔너리(`"user_id"` 항목 뒤)에:

```python
            "room_id": "ALTER TABLE chatmessage ADD COLUMN room_id INTEGER",
```

같은 함수의 `CREATE INDEX IF NOT EXISTS ix_chatmessage_user_created_ms …` 실행문 바로 뒤에:

```python
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_chatmessage_room_id ON chatmessage (room_id)")
```

`_PG_ADDED_COLUMNS["user"]` 에 `"room_consent_at": "VARCHAR NOT NULL DEFAULT ''"` 추가, `_PG_ADDED_COLUMNS["chatmessage"]` 를 `{"user_id": "INTEGER", "room_id": "INTEGER"}` 로. `_PG_PRIVATE_CACHE_TABLES` 튜플의 `"askmacrosession",` 뒤에 `"chatroom", "chatroommember",` 추가.

- [ ] **Step 4: Supabase 마이그레이션 + RLS 목록**

`supabase/migrations/20260921120000_chat_rooms.sql`:

```sql
-- 전략방 (2026-09-21)
-- ① user.room_consent_at — 방 생성 동의 시각.  ② chatmessage.room_id — 방 메시지(NULL = 공개 채팅).
-- ③ chatroom / chatroommember — 방·멤버(멤버 행이 방 읽음 커서). 서버 전용: RLS 켜고 anon/authenticated 권한 회수.

alter table public."user" add column if not exists room_consent_at varchar not null default '';
alter table public.chatmessage add column if not exists room_id integer;
create index if not exists ix_chatmessage_room_id on public.chatmessage (room_id);

create table if not exists public.chatroom (
  id bigserial primary key,
  owner_id integer not null,
  title varchar not null,
  capacity integer not null,
  entry_fee integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0,
  expires_ms bigint not null default 0,
  extended_count integer not null default 0,
  closed_reason varchar not null default '',
  closed_at varchar not null default ''
);
create index if not exists ix_chatroom_owner_id on public.chatroom (owner_id);
create index if not exists ix_chatroom_created_ms on public.chatroom (created_ms);
create index if not exists ix_chatroom_expires_ms on public.chatroom (expires_ms);

create table if not exists public.chatroommember (
  room_id integer not null,
  user_id integer not null,
  paid integer not null default 0,
  joined_at varchar not null,
  joined_ms bigint not null default 0,
  last_seen_id integer not null default 0,
  primary key (room_id, user_id)
);
create index if not exists ix_chatroommember_user_id on public.chatroommember (user_id);

alter table public.chatroom enable row level security;
alter table public.chatroommember enable row level security;
revoke all privileges on table public.chatroom from public, anon, authenticated;
revoke all privileges on table public.chatroommember from public, anon, authenticated;
revoke all privileges on sequence public.chatroom_id_seq from public, anon, authenticated;
```

`supabase/tests/gg_parrot_rls.sql` 의 `owned_tables` 배열에서 `'chatmessage',` 뒤에 `'chatroom',` `'chatroommember',` 두 줄을 추가한다(알파벳 순 유지).

- [ ] **Step 5: 테스트 통과 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: `1 passed`

기존 채팅 테스트도 그대로: `backend/.venv/Scripts/python -m pytest backend/tests/test_chat.py -q -p no:warnings` (파일이 있으면) Expected: 전부 pass.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/db.py backend/tests/test_rooms.py supabase/migrations/20260921120000_chat_rooms.sql supabase/tests/gg_parrot_rls.sql
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: ChatRoom·ChatRoomMember 모델, chatmessage.room_id, user.room_consent_at + 마이그레이션

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: rooms.py — 생성 규칙

**Files:**
- Create: `backend/app/rooms.py`
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Consumes: Task 1 모델; `points.apply(db, user, delta, reason, ref)`, `points.InsufficientPoints`; `chat._lock_member(db, user_id)`; `auth.assert_can_write`; `moderation.require_clean_text`; `leaderboard.today_start_ms()`; `avatars.avatar_url(user_id, db=db)`.
- Produces:
  - `class RoomError(Exception)` with `.status: int`, `.message: str`
  - `create_room(db, account: User, *, title: str, capacity: int, entry_fee: int, consent: bool) -> dict` → `{"room": view, "points_balance": int}` (커밋함)
  - `room_view(db, room: ChatRoom, viewer_id: int | None, *, now_ms: int | None = None) -> dict`
  - `is_open(room, now_ms) -> bool`
  - 상수 전부(Global Constraints).

- [ ] **Step 1: 실패하는 테스트 작성** (`test_rooms.py` 끝에 추가)

```python
def _create(token, **overrides):
    body = {"title": "비트 단타 토론", "capacity": 3, "entry_fee": 100, "consent": True}
    body.update(overrides)
    return client.post("/api/rooms", json=body, headers=_auth(token))


def test_create_room_charges_100_and_seats_owner():
    token, owner_id = _signup()
    before = _balance(owner_id)
    r = _create(token)
    assert r.status_code == 200, r.text
    room = r.json()["room"]
    assert room["title"] == "비트 단타 토론" and room["capacity"] == 3 and room["entry_fee"] == 100
    assert room["member_count"] == 1 and room["is_owner"] and room["is_member"]
    assert room["expires_ms"] - room["created_ms"] == 7 * 24 * 3600 * 1000
    assert r.json()["points_balance"] == before - 100
    rows = _ledger(owner_id)
    assert rows[-1].delta == -100 and rows[-1].reason == "room_create" and rows[-1].ref == f"room:{room['id']}"
    with get_session() as db:
        assert db.get(ChatRoomMember, (room["id"], owner_id)).paid == 0
        assert db.get(User, owner_id).room_consent_at != ""


def test_create_room_requires_consent_once():
    token, _ = _signup()
    assert _create(token, consent=False).status_code == 422
    assert _create(token).status_code == 200
    # 두 번째 날 이후에는 consent 를 빼도 된다 — 여기서는 하루 1개 규칙에 걸리므로 429 로 "동의 통과"를 확인한다.
    assert _create(token, consent=False).status_code == 429


def test_create_room_one_per_day_and_validation():
    token, _ = _signup()
    assert _create(token).status_code == 200
    r = _create(token)
    assert r.status_code == 429 and "하루" in r.json()["detail"]
    other, _ = _signup()
    assert _create(other, title="a").status_code == 422
    assert _create(other, title="x" * 31).status_code == 422
    assert _create(other, capacity=1).status_code == 422
    assert _create(other, capacity=11).status_code == 422
    assert _create(other, entry_fee=-1).status_code == 422
    assert _create(other, entry_fee=301).status_code == 422


def test_create_room_needs_points_and_open_account():
    token, user_id = _signup()
    with get_session() as db:
        user = db.get(User, user_id)
        user.points_balance = 99
        db.add(user)
        db.commit()
    r = _create(token)
    assert r.status_code == 402
    assert _ledger(user_id)[-1].reason != "room_create"
    with get_session() as db:
        user = db.get(User, user_id)
        user.points_balance = 1000
        user.is_blocked = True
        db.add(user)
        db.commit()
    assert _create(token).status_code == 403
```

- [ ] **Step 2: 실패 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: 새 테스트 4개 FAIL (404 — 라우트 없음).

- [ ] **Step 3: rooms.py 작성 (생성 부분) + 라우트**

`backend/app/rooms.py`:

```python
"""전략방 — 회원이 만드는 소그룹 채팅(최대 10명, 포인트 입장료, 7일 만료).

돈이 아니라 포인트다(points.py "no real money yet"). 입장료의 70% 는 방장에게, 30% 는 플랫폼 싱크 —
매크로 잠금해제와 같은 경제. 환불 코드는 어디에도 없다: 나가기는 자리만 비우고, 방은 만료로만 끝난다
(관리자 폐쇄 제외). 포인트 현금화가 들어오면 이 모듈 전체를 다시 심사한다(스펙 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from . import avatars, points
from .auth import AuthError, assert_can_write
from .db import ChatRoom, ChatRoomMember, User
from .leaderboard import today_start_ms
from .moderation import require_clean_text

ROOM_CREATE_COST = 100
ROOM_EXTEND_COST = 50
ROOM_TTL_MS = 7 * 24 * 3600 * 1000
ROOM_EXTEND_WINDOW_MS = 24 * 3600 * 1000
MIN_CAPACITY = 2
MAX_CAPACITY = 10
MAX_ENTRY_FEE = 300
MIN_ACCOUNT_AGE_FOR_PAID_MS = 3 * 24 * 3600 * 1000
ROOMS_PER_DAY = 1
TITLE_MIN = 2
TITLE_MAX = 30
LIST_LIMIT = 50
MINE_GRACE_MS = ROOM_TTL_MS  # 만료 후 이만큼은 '내 방' 탭에 읽기용으로 남긴다

DISCLAIMER = "전략방 대화는 투자 조언이 아니며, 매매 판단과 책임은 본인에게 있습니다. 투자 권유·수익 보장 발언은 금지돼요."
CONSENT_TEXT = "전략방에서 특정 코인 매수·매도를 권유하거나 수익을 보장하는 발언을 하지 않겠습니다. 위반하면 방이 닫힐 수 있어요."


class RoomError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(now.timestamp() * 1000)


def _lock_member(db, user_id: int) -> User:
    from .chat import _lock_member as lock  # 같은 잠금 규칙(sqlite BEGIN IMMEDIATE / pg FOR UPDATE)

    return lock(db, user_id)


def is_open(room: ChatRoom, now_ms: int) -> bool:
    return not room.closed_reason and room.expires_ms > now_ms


def _member_count(db, room_id: int) -> int:
    return int(db.exec(select(func.count(ChatRoomMember.user_id)).where(ChatRoomMember.room_id == room_id)).one() or 0)


def room_view(db, room: ChatRoom, viewer_id: int | None, *, now_ms: int | None = None) -> dict:
    now_ms = now_ms if now_ms is not None else _now()[1]
    owner = db.get(User, room.owner_id)
    member = db.get(ChatRoomMember, (room.id, viewer_id)) if viewer_id is not None else None
    is_owner = viewer_id == room.owner_id
    return {
        "id": room.id,
        "title": room.title,
        "owner_username": owner.username if owner else "",
        "owner_avatar_url": avatars.avatar_url(room.owner_id, db=db),
        "capacity": room.capacity,
        "member_count": _member_count(db, room.id),
        "entry_fee": room.entry_fee,
        "created_ms": room.created_ms,
        "expires_ms": room.expires_ms,
        "extended_count": room.extended_count,
        "is_open": is_open(room, now_ms),
        "closed_reason": room.closed_reason,
        "is_member": member is not None,
        "is_owner": is_owner,
        "can_extend": is_owner and is_open(room, now_ms) and room.expires_ms - now_ms <= ROOM_EXTEND_WINDOW_MS,
        "last_seen_id": member.last_seen_id if member else 0,
    }


def _validate_create(title: str, capacity: int, entry_fee: int) -> str:
    title = (title or "").strip()
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise RoomError(422, f"방 제목은 {TITLE_MIN}~{TITLE_MAX}자로 적어 주세요.")
    require_clean_text(title, "방 제목")
    if not MIN_CAPACITY <= int(capacity) <= MAX_CAPACITY:
        raise RoomError(422, f"정원은 {MIN_CAPACITY}~{MAX_CAPACITY}명이에요.")
    if not 0 <= int(entry_fee) <= MAX_ENTRY_FEE:
        raise RoomError(422, f"입장료는 0~{MAX_ENTRY_FEE}포인트예요.")
    return title


def create_room(db: Session, account: User, *, title: str, capacity: int, entry_fee: int, consent: bool) -> dict:
    assert_can_write(account)
    title = _validate_create(title, capacity, entry_fee)
    owner = _lock_member(db, int(account.id))
    if not owner.room_consent_at and not consent:
        raise RoomError(422, "전략방을 만들려면 안내에 동의해 주세요.")
    made_today = db.exec(select(func.count(ChatRoom.id)).where(
        ChatRoom.owner_id == owner.id, ChatRoom.created_ms >= today_start_ms(),
    )).one()
    if int(made_today or 0) >= ROOMS_PER_DAY:
        raise RoomError(429, "전략방은 하루에 하나만 만들 수 있어요. 내일 다시 만들어 주세요.")
    created_at, now_ms = _now()
    room = ChatRoom(owner_id=owner.id, title=title, capacity=int(capacity), entry_fee=int(entry_fee),
                    created_at=created_at, created_ms=now_ms, expires_ms=now_ms + ROOM_TTL_MS)
    db.add(room)
    db.flush()
    points.apply(db, owner, -ROOM_CREATE_COST, "room_create", f"room:{room.id}")
    if not owner.room_consent_at:
        owner.room_consent_at = created_at
        db.add(owner)
    db.add(ChatRoomMember(room_id=room.id, user_id=owner.id, paid=0, joined_at=created_at, joined_ms=now_ms))
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, owner.id, now_ms=now_ms), "points_balance": owner.points_balance}
```

`backend/app/main.py`:
- import 줄(`from . import chat as chat_mod` 근처)에 `from . import rooms as rooms_mod` 추가.
- `class ChatReadRequest` 아래에:

```python
class RoomCreateRequest(BaseModel):
    title: str
    capacity: int
    entry_fee: int = 0
    consent: bool = False
```

- `@app.put("/api/chat/read")` 라우트 뒤에:

```python
# --- 전략방 ---------------------------------------------------------------
def _room_http(exc: Exception) -> HTTPException:
    if isinstance(exc, rooms_mod.RoomError):
        return HTTPException(exc.status, exc.message)
    if isinstance(exc, points_mod.InsufficientPoints):
        return HTTPException(402, str(exc))
    raise exc


@app.post("/api/rooms")
def rooms_create(req: RoomCreateRequest, account: User = Depends(auth_mod.current_user_in_session),
                 db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.create_room(db, account, title=req.title, capacity=req.capacity,
                                     entry_fee=req.entry_fee, consent=req.consent)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)
```

`points_mod` 가 main.py 에 이미 import 돼 있는지 확인(`points_mod.InsufficientPoints` 를 unlock 라우트가 쓴다 → 있음). `AuthError`(차단 계정 403)는 기존 예외 핸들러가 처리한다(chat_post 와 같은 경로).

- [ ] **Step 4: 통과 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: `5 passed`

- [ ] **Step 5: 커밋**

```bash
git add backend/app/rooms.py backend/app/main.py backend/tests/test_rooms.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: 방 생성 — 100P 차감, 동의 1회, 하루 1개, 제목·정원·입장료 검증

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: rooms.py — 입장·나가기·연장·관리자 폐쇄 (포인트 정산)

**Files:**
- Modify: `backend/app/rooms.py`, `backend/app/main.py`
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Produces: `join_room(db, account, room_id) -> {"room", "points_balance"}`, `leave_room(db, account, room_id) -> {"ok": True}`, `extend_room(db, account, room_id) -> {"room", "points_balance"}`, `close_room_by_admin(db, room_id) -> {"room"}`, `get_room(db, room_id) -> ChatRoom` (없으면 `RoomError(404, "전략방을 찾을 수 없어요.")`).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def _backdate_room(room_id, *, created_ago_ms=0, expires_in_ms=None):
    with get_session() as db:
        room = db.get(ChatRoom, room_id)
        now_ms = int(time.time() * 1000)
        room.created_ms = now_ms - created_ago_ms
        if expires_in_ms is not None:
            room.expires_ms = now_ms + expires_in_ms
        db.add(room)
        db.commit()


def test_join_paid_room_splits_70_30_and_free_room_writes_no_ledger():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=100).json()["room"]["id"]
    owner_before = _balance(owner_id)
    guest_tok, guest_id = _signup()
    guest_before = _balance(guest_id)
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert r.status_code == 200, r.text
    assert r.json()["points_balance"] == guest_before - 100
    assert r.json()["room"]["member_count"] == 2 and r.json()["room"]["is_member"]
    assert _balance(owner_id) == owner_before + 70
    g = _ledger(guest_id)[-1]
    o = _ledger(owner_id)[-1]
    assert (g.delta, g.reason, g.ref) == (-100, "room_join", f"room:{room_id}")
    assert (o.delta, o.reason, o.ref) == (70, "room_host_earn", f"room:{room_id}")
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)).paid == 100

    free_owner, _ = _signup()
    free_id = _create(free_owner, entry_fee=0).json()["room"]["id"]
    newbie_tok, newbie_id = _signup(age_days=0)  # 방금 가입 — 무료방은 들어갈 수 있다
    ledger_before = len(_ledger(newbie_id))
    assert client.post(f"/api/rooms/{free_id}/join", headers=_auth(newbie_tok)).status_code == 200
    assert len(_ledger(newbie_id)) == ledger_before


def test_join_rejects_duplicate_full_young_and_broke():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, capacity=2, entry_fee=50).json()["room"]["id"]
    guest_tok, guest_id = _signup()
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 200
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 409
    third_tok, _ = _signup()
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(third_tok))
    assert r.status_code == 409 and "정원" in r.json()["detail"]

    big_owner, _ = _signup()
    big_id = _create(big_owner, capacity=5, entry_fee=50).json()["room"]["id"]
    young_tok, _ = _signup(age_days=1)
    assert client.post(f"/api/rooms/{big_id}/join", headers=_auth(young_tok)).status_code == 403
    broke_tok, broke_id = _signup()
    with get_session() as db:
        user = db.get(User, broke_id)
        user.points_balance = 49
        db.add(user)
        db.commit()
    r = client.post(f"/api/rooms/{big_id}/join", headers=_auth(broke_tok))
    assert r.status_code == 402
    with get_session() as db:
        assert db.get(ChatRoomMember, (big_id, broke_id)) is None
    assert _balance(broke_id) == 49


def test_join_expired_or_closed_room_is_410():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    _backdate_room(room_id, expires_in_ms=-1)
    guest_tok, _ = _signup()
    r = client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert r.status_code == 410


def test_leave_frees_seat_without_refund_and_owner_cannot_leave():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=100).json()["room"]["id"]
    guest_tok, guest_id = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    after_join = _balance(guest_id)
    owner_after_join = _balance(owner_id)
    assert client.delete(f"/api/rooms/{room_id}/leave", headers=_auth(guest_tok)).json() == {"ok": True}
    assert _balance(guest_id) == after_join and _balance(owner_id) == owner_after_join
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)) is None
    # 다시 들어오면 다시 낸다
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 200
    assert _balance(guest_id) == after_join - 100
    r = client.delete(f"/api/rooms/{room_id}/leave", headers=_auth(owner_tok))
    assert r.status_code == 403


def test_extend_only_in_last_day_and_only_owner():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok)).status_code == 409
    _backdate_room(room_id, expires_in_ms=3600 * 1000)
    guest_tok, _ = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(guest_tok)).status_code == 403
    before = _balance(owner_id)
    r = client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok))
    assert r.status_code == 200, r.text
    assert r.json()["points_balance"] == before - 50
    room = r.json()["room"]
    assert room["extended_count"] == 1
    assert room["expires_ms"] - int(time.time() * 1000) > 7 * 24 * 3600 * 1000
    assert _ledger(owner_id)[-1].reason == "room_extend"


def test_admin_close_blocks_join_and_extend():
    owner_tok, _ = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    admin_tok, admin_id = _signup()
    with get_session() as db:
        user = db.get(User, admin_id)
        user.is_admin = True
        db.add(user)
        db.commit()
    r = client.post(f"/api/admin/rooms/{room_id}/close", headers=_auth(admin_tok))
    assert r.status_code == 200 and r.json()["room"]["closed_reason"] == "admin"
    guest_tok, _ = _signup()
    assert client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok)).status_code == 410
    _backdate_room(room_id, expires_in_ms=3600 * 1000)
    assert client.post(f"/api/rooms/{room_id}/extend", headers=_auth(owner_tok)).status_code == 410
    plain_tok, _ = _signup()
    assert client.post(f"/api/admin/rooms/{room_id}/close", headers=_auth(plain_tok)).status_code == 403
```

- [ ] **Step 2: 실패 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: 새 테스트 6개 FAIL (404).

- [ ] **Step 3: 구현**

`backend/app/rooms.py` 끝에 추가:

```python
def get_room(db, room_id: int) -> ChatRoom:
    room = db.get(ChatRoom, int(room_id))
    if room is None:
        raise RoomError(404, "전략방을 찾을 수 없어요.")
    return room


def _account_age_ms(user: User, now_ms: int) -> int:
    try:
        created = datetime.strptime(user.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0
    return now_ms - int(created.timestamp() * 1000)


def join_room(db: Session, account: User, room_id: int) -> dict:
    assert_can_write(account)
    guest = _lock_member(db, int(account.id))  # 잔액 경합 방지 — 방 행보다 먼저 잠근다
    room = db.exec(select(ChatRoom).where(ChatRoom.id == int(room_id)).with_for_update()).first()
    if room is None:
        raise RoomError(404, "전략방을 찾을 수 없어요.")
    joined_at, now_ms = _now()
    if not is_open(room, now_ms):
        raise RoomError(410, "이 전략방은 끝났어요.")
    if db.get(ChatRoomMember, (room.id, guest.id)) is not None:
        raise RoomError(409, "이미 들어와 있는 방이에요.")
    if _member_count(db, room.id) >= room.capacity:
        raise RoomError(409, "정원이 다 찼어요.")
    if room.entry_fee > 0 and _account_age_ms(guest, now_ms) < MIN_ACCOUNT_AGE_FOR_PAID_MS:
        raise RoomError(403, "가입 3일 후부터 유료 전략방에 들어갈 수 있어요.")
    if room.entry_fee > 0:
        points.apply(db, guest, -room.entry_fee, "room_join", f"room:{room.id}")  # 부족하면 InsufficientPoints
        share = points.creator_share(room.entry_fee)
        if share > 0:
            owner = db.exec(select(User).where(User.id == room.owner_id).with_for_update()).first()
            if owner is not None and not owner.is_deleted:
                points.apply(db, owner, share, "room_host_earn", f"room:{room.id}")
    db.add(ChatRoomMember(room_id=room.id, user_id=guest.id, paid=room.entry_fee, joined_at=joined_at, joined_ms=now_ms))
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, guest.id, now_ms=now_ms), "points_balance": guest.points_balance}


def leave_room(db: Session, account: User, room_id: int) -> dict:
    room = get_room(db, room_id)
    if room.owner_id == account.id:
        raise RoomError(403, "방장은 나갈 수 없어요. 방은 만료일에 자동으로 끝나요.")
    member = db.get(ChatRoomMember, (room.id, int(account.id)))
    if member is None:
        raise RoomError(404, "들어와 있는 방이 아니에요.")
    db.delete(member)  # 환불 없음 — 포인트는 건드리지 않는다
    db.commit()
    return {"ok": True}


def extend_room(db: Session, account: User, room_id: int) -> dict:
    owner = _lock_member(db, int(account.id))
    room = db.exec(select(ChatRoom).where(ChatRoom.id == int(room_id)).with_for_update()).first()
    if room is None:
        raise RoomError(404, "전략방을 찾을 수 없어요.")
    if room.owner_id != owner.id:
        raise RoomError(403, "방장만 연장할 수 있어요.")
    _, now_ms = _now()
    if not is_open(room, now_ms):
        raise RoomError(410, "이 전략방은 끝났어요.")
    if room.expires_ms - now_ms > ROOM_EXTEND_WINDOW_MS:
        raise RoomError(409, "만료 하루 전부터 연장할 수 있어요.")
    points.apply(db, owner, -ROOM_EXTEND_COST, "room_extend", f"room:{room.id}")
    room.expires_ms += ROOM_TTL_MS
    room.extended_count += 1
    db.add(room)
    db.commit()
    db.refresh(room)
    return {"room": room_view(db, room, owner.id, now_ms=now_ms), "points_balance": owner.points_balance}


def close_room_by_admin(db: Session, room_id: int) -> dict:
    room = get_room(db, room_id)
    closed_at, now_ms = _now()
    if not room.closed_reason:
        room.closed_reason = "admin"
        room.closed_at = closed_at
        db.add(room)
        db.commit()
        db.refresh(room)
    return {"room": room_view(db, room, None, now_ms=now_ms)}
```

`backend/app/main.py` — `rooms_create` 뒤에:

```python
@app.post("/api/rooms/{room_id}/join")
def rooms_join(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
               db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.join_room(db, account, room_id)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)


@app.delete("/api/rooms/{room_id}/leave")
def rooms_leave(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
                db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.leave_room(db, account, room_id)
    except rooms_mod.RoomError as exc:
        raise _room_http(exc)


@app.post("/api/rooms/{room_id}/extend")
def rooms_extend(room_id: int, account: User = Depends(auth_mod.current_user_in_session),
                 db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.extend_room(db, account, room_id)
    except (rooms_mod.RoomError, points_mod.InsufficientPoints) as exc:
        db.rollback()
        raise _room_http(exc)


@app.post("/api/admin/rooms/{room_id}/close")
def admin_room_close(room_id: int, admin: User = Depends(auth_mod.require_admin),
                     db: Session = Depends(request_session)) -> dict:
    try:
        return rooms_mod.close_room_by_admin(db, room_id)
    except rooms_mod.RoomError as exc:
        raise _room_http(exc)
```

- [ ] **Step 4: 통과 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: `11 passed`

- [ ] **Step 5: 커밋**

```bash
git add backend/app/rooms.py backend/app/main.py backend/tests/test_rooms.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: 입장(70/30 정산)·나가기(환불 없음)·연장·관리자 폐쇄

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: rooms.py — 목록

**Files:**
- Modify: `backend/app/rooms.py`, `backend/app/main.py`
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Produces: `list_rooms(db, account: User) -> {"items": [view...], "mine": [view...], "server_ms": int, "disclaimer": str, "consent_text": str, "consented": bool, "create_cost": 100, "extend_cost": 50, "max_entry_fee": 300, "max_capacity": 10}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_list_rooms_shows_open_rooms_and_my_rooms_including_expired():
    a_tok, a_id = _signup()
    open_id = _create(a_tok, title="열린 방", entry_fee=0).json()["room"]["id"]
    b_tok, _ = _signup()
    gone_id = _create(b_tok, title="끝난 방", entry_fee=0).json()["room"]["id"]
    me_tok, me_id = _signup()
    client.post(f"/api/rooms/{gone_id}/join", headers=_auth(me_tok))
    _backdate_room(gone_id, expires_in_ms=-1)
    r = client.get("/api/rooms", headers=_auth(me_tok))
    assert r.status_code == 200
    body = r.json()
    ids = [room["id"] for room in body["items"]]
    assert open_id in ids and gone_id not in ids
    mine = {room["id"]: room for room in body["mine"]}
    assert gone_id in mine and mine[gone_id]["is_open"] is False and mine[gone_id]["is_member"]
    assert open_id not in mine
    assert body["consented"] is False and body["create_cost"] == 100 and "disclaimer" in body
    assert client.get("/api/rooms").status_code == 401
```

- [ ] **Step 2: 실패 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings -k list_rooms`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`rooms.py` 끝에:

```python
def list_rooms(db: Session, account: User) -> dict:
    _, now_ms = _now()
    viewer_id = int(account.id)
    open_rows = db.exec(select(ChatRoom).where(
        ChatRoom.closed_reason == "", ChatRoom.expires_ms > now_ms,
    ).order_by(ChatRoom.created_ms.desc()).limit(LIST_LIMIT)).all()
    mine_rows = db.exec(select(ChatRoom).join(ChatRoomMember, ChatRoomMember.room_id == ChatRoom.id).where(
        ChatRoomMember.user_id == viewer_id, ChatRoom.expires_ms > now_ms - MINE_GRACE_MS,
    ).order_by(ChatRoom.created_ms.desc())).all()
    return {
        "items": [room_view(db, room, viewer_id, now_ms=now_ms) for room in open_rows],
        "mine": [room_view(db, room, viewer_id, now_ms=now_ms) for room in mine_rows],
        "server_ms": now_ms,
        "disclaimer": DISCLAIMER,
        "consent_text": CONSENT_TEXT,
        "consented": bool(account.room_consent_at),
        "create_cost": ROOM_CREATE_COST,
        "extend_cost": ROOM_EXTEND_COST,
        "max_entry_fee": MAX_ENTRY_FEE,
        "max_capacity": MAX_CAPACITY,
    }
```

`main.py` — `rooms_create` 앞에:

```python
@app.get("/api/rooms")
def rooms_list(account: User = Depends(auth_mod.current_user_in_session),
               db: Session = Depends(request_session)) -> dict:
    return rooms_mod.list_rooms(db, account)
```

- [ ] **Step 4: 통과 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: `12 passed`

- [ ] **Step 5: 커밋**

```bash
git add backend/app/rooms.py backend/app/main.py backend/tests/test_rooms.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: 목록 API — 열린 방 + 내 방(만료 후 7일까지 읽기용)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: chat.py — 방 단위 메시지·읽음

**Files:**
- Modify: `backend/app/chat.py` (`add_message` 65행, `_member_seen_id` 102행, `mark_read` 119행, `list_messages` 140행), `backend/app/main.py` (`ChatPostRequest`/`ChatReadRequest` 373행, chat 라우트 1496~1540행)
- Test: `backend/tests/test_rooms.py`

**Interfaces:**
- Consumes: `rooms.get_room`, `rooms.is_open`, `rooms.room_view`, `rooms.RoomError`.
- Produces: `add_message(account, text, *, room_id: int | None = None, db=None)`, `mark_read(account, last_seen_id, *, room_id=None, db=None)`, `list_messages(account, *, room_id=None, ...)`. 방 모드 응답에 `"room": room_view` 추가, `day_start_ms` 는 `room.created_ms`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_room_messages_are_member_only_and_scoped():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    # 공개 채팅 메시지는 방에 섞이지 않는다
    assert client.post("/api/chat", json={"text": "공개 안녕"}, headers=_auth(owner_tok)).status_code == 200
    r = client.post("/api/chat", json={"text": "방 안녕", "room_id": room_id}, headers=_auth(owner_tok))
    assert r.status_code == 200, r.text
    outsider_tok, _ = _signup()
    assert client.get(f"/api/chat?room_id={room_id}", headers=_auth(outsider_tok)).status_code == 403
    assert client.post("/api/chat", json={"text": "몰래", "room_id": room_id}, headers=_auth(outsider_tok)).status_code == 403
    assert client.get(f"/api/chat?room_id={room_id}").status_code == 401
    guest_tok, guest_id = _signup()
    client.post(f"/api/rooms/{room_id}/join", headers=_auth(guest_tok))
    body = client.get(f"/api/chat?room_id={room_id}", headers=_auth(guest_tok)).json()
    texts = [m["text"] for m in body["items"]]
    assert texts == ["방 안녕"]
    assert body["room"]["id"] == room_id and body["day_start_ms"] == body["room"]["created_ms"]
    public = client.get("/api/chat", headers=_auth(guest_tok)).json()
    assert "방 안녕" not in [m["text"] for m in public["items"]]
    assert "room" not in public
    # 방 읽음 커서는 멤버 행에 남는다
    last_id = body["items"][-1]["id"]
    assert client.put("/api/chat/read", json={"last_seen_id": last_id, "room_id": room_id}, headers=_auth(guest_tok)).json() == {"seen_id": last_id}
    with get_session() as db:
        assert db.get(ChatRoomMember, (room_id, guest_id)).last_seen_id == last_id
    again = client.get(f"/api/chat?room_id={room_id}", headers=_auth(guest_tok)).json()
    assert again["seen_id"] == last_id and again["unseen_count"] == 0


def test_room_messages_before_creation_are_hidden_and_expired_room_is_read_only():
    owner_tok, owner_id = _signup()
    room_id = _create(owner_tok, entry_fee=0).json()["room"]["id"]
    with get_session() as db:
        db.add(ChatMessage(user_id=owner_id, username="x", text="옛날", created_at="2020-01-01T00:00:00Z", created_ms=1, room_id=room_id))
        db.commit()
    client.post("/api/chat", json={"text": "지금", "room_id": room_id}, headers=_auth(owner_tok))
    body = client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok)).json()
    assert [m["text"] for m in body["items"]] == ["지금"]
    _backdate_room(room_id, expires_in_ms=-1)
    assert client.get(f"/api/chat?room_id={room_id}", headers=_auth(owner_tok)).status_code == 200
    r = client.post("/api/chat", json={"text": "늦음", "room_id": room_id}, headers=_auth(owner_tok))
    assert r.status_code == 410
    assert client.get("/api/chat?room_id=999999", headers=_auth(owner_tok)).status_code == 404
```

- [ ] **Step 2: 실패 확인**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings -k room_messages`
Expected: FAIL (`room_id` 무시돼 403 이 아니라 200 등).

- [ ] **Step 3: chat.py 수정**

상단 import 에 `from . import rooms as rooms_mod` 와 `from .db import ChatRoomMember` 추가.

헬퍼 두 개를 `_latest_id` 아래에 추가:

```python
def _latest_id(db, *, start_ms: int | None = None, room_id: int | None = None) -> int:
    query = select(func.max(ChatMessage.id))
    if room_id is None:
        query = query.where(ChatMessage.room_id.is_(None))
    else:
        query = query.where(ChatMessage.room_id == room_id)
    if start_ms is not None:
        query = query.where(ChatMessage.created_ms >= start_ms)
    return int(db.exec(query).one() or 0)


def _require_room_member(db, user_id: int | None, room_id: int):
    """방 메시지는 멤버만. (room, member) 를 돌려준다. 만료·폐쇄된 방도 읽기는 된다."""
    if user_id is None:
        raise ValueError("로그인이 필요해요.")
    room = rooms_mod.get_room(db, room_id)
    member = db.get(ChatRoomMember, (room.id, user_id))
    if member is None:
        raise rooms_mod.RoomError(403, "이 전략방의 멤버만 볼 수 있어요.")
    return room, member
```

(기존 `_latest_id` 는 위 시그니처로 교체 — 공개방 호출은 `room_id=None` 이라 `room_id IS NULL` 조건이 추가되어 방 메시지가 공개 latest 에 섞이지 않는다.)

`add_message(account, text, *, room_id: int | None = None, db=None)`:
- `row = ChatMessage(...)` 만들기 전, `author = _lock_member(...)` 뒤에:

```python
        if room_id is not None:
            room, _ = _require_room_member(db, author.id, room_id)
            if not rooms_mod.is_open(room, now_ms):
                raise rooms_mod.RoomError(410, "이 전략방은 끝났어요.")
```
- `ChatMessage(...)` 에 `room_id=room_id` 추가.

`_member_seen_id(user_id, *, room_id=None, db=None)`: `room_id` 가 있으면 멤버 행의 `last_seen_id` 를 돌려준다(없으면 `_require_room_member` 가 403). 공개방 경로는 그대로.

`mark_read(account, last_seen_id, *, room_id=None, db=None)`: `room_id` 가 있으면

```python
        if room_id is not None:
            _lock_member(db, user_id)
            room, member = _require_room_member(db, user_id, room_id)
            target = min(max(0, last_seen_id), _latest_id(db, room_id=room.id))
            if target > member.last_seen_id:
                member.last_seen_id = target
                db.add(member)
            db.commit()
            return {"seen_id": int(member.last_seen_id)}
```

`list_messages(account, *, room_id=None, ...)`:
- 함수 앞부분에서 `room = None`; `room_id` 가 있으면 `room, member = _require_room_member(db, user_id, room_id)`, `start_ms = room.created_ms`, `server_seen = member.last_seen_id`. 아니면 기존(`today_start_ms()`, `_member_seen_id`).
- `_latest_id(db, start_ms=start_ms, room_id=room_id)` 로 호출(두 곳 모두).
- 메시지 query 에 `ChatMessage.room_id == room_id` (room_id 있음) / `ChatMessage.room_id.is_(None)` (없음) 조건 추가. unseen query 도 같은 조건.
- 반환 dict: `room_id` 가 있으면 `"room": rooms_mod.room_view(db, room, user_id, now_ms=snapshot_ms)` 추가. `disclaimer` 는 방 모드에서 `rooms_mod.DISCLAIMER`.

`main.py`:
- `ChatPostRequest` 에 `room_id: Optional[int] = None`, `ChatReadRequest` 에 `room_id: Optional[int] = None`.
- `chat_list` 에 `room_id: Optional[int] = Query(default=None, ge=1)` 파라미터, `chat_mod.list_messages(..., room_id=room_id, ...)`. `except rooms_mod.RoomError as exc: raise HTTPException(exc.status, exc.message)` 추가(ValueError 401 처리 앞에).
- `chat_post`: `chat_mod.add_message(account, req.text, room_id=req.room_id, db=db)`, `RoomError` 매핑 추가.
- `chat_read`: `chat_mod.mark_read(account, req.last_seen_id, room_id=req.room_id, db=db)`, `RoomError` 매핑 추가.

- [ ] **Step 4: 통과 확인 (방 + 기존 채팅 회귀)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_rooms.py -q -p no:warnings`
Expected: `14 passed`

Run: `backend/.venv/Scripts/python -m pytest backend/tests -q -p no:warnings -k "chat" --continue-on-collection-errors`
Expected: 기존 채팅 테스트 전부 pass (실패가 있으면 `room_id IS NULL` 조건 누락을 의심).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/chat.py backend/app/main.py backend/tests/test_rooms.py
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: /api/chat 에 room_id — 멤버만, 방 생성 이후 범위, 멤버 행 읽음 커서, 끝난 방은 읽기만

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 프론트 — api.js · roomsCopy · roomFlow (순수 계층)

**Files:**
- Modify: `frontend/src/api.js` (chat 3개 함수 ~418행), `frontend/src/lib/chatBadge.js` (`chatScope`)
- Create: `frontend/src/lib/roomsCopy.js`, `frontend/src/lib/roomFlow.js`
- Test: `frontend/tests/roomFlow.test.js`, `frontend/tests/chatApi.test.js`

**Interfaces:**
- Produces:
  - `api.roomsList(options)`, `api.roomCreate({title, capacity, entry_fee, consent})`, `api.roomJoin(id)`, `api.roomLeave(id)`, `api.roomExtend(id)`; `api.chatList({roomId,...})`, `api.chatPost(text, {roomId})`, `api.chatRead(id, {roomId})`.
  - `chatScope(userId, roomId = 0)` → 기존 `"anon"`/`"member:{id}"`, 방이면 `"member:{id}:room:{roomId}"`.
  - `roomFlow.js`: `remainingLabel(expiresMs, nowMs)`, `canJoin(room, me, nowMs)` → `{ok, reason}`, `joinConfirmText(room)`, `validateCreate({title, capacity, entryFee})` → `{ok, errors:{title?,capacity?,entryFee?}}`, `roomTabs(mine, activeRoomId)`.
  - `roomsCopy.js`: `TAB_ALL="전체"`, `TAB_FIND="방 찾기"`, `ROOM_NOTICE`, `CREATE_TITLE`, `CONSENT_LABEL`, `JOIN_FREE`, `NO_ROOMS`, `LOGIN_TO_FIND`, `OWNER_CANNOT_LEAVE`, `LEAVE_CONFIRM`, `EXTEND_LABEL`, `ENDED`.

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/tests/roomFlow.test.js`:

```js
import assert from "node:assert/strict";
import test from "node:test";

import { canJoin, joinConfirmText, remainingLabel, roomTabs, validateCreate } from "../src/lib/roomFlow.js";
import { chatScope } from "../src/lib/chatBadge.js";

const DAY = 24 * 3600 * 1000;

test("remainingLabel shows days, then hours, then '곧 종료', then '끝남'", () => {
  const now = 1_000_000_000_000;
  assert.equal(remainingLabel(now + 5 * DAY + 3600 * 1000, now), "5일 남음");
  assert.equal(remainingLabel(now + 5 * 3600 * 1000, now), "5시간 남음");
  assert.equal(remainingLabel(now + 30 * 60 * 1000, now), "곧 종료");
  assert.equal(remainingLabel(now - 1, now), "끝남");
});

test("canJoin explains each block", () => {
  const now = 1_000_000_000_000;
  const me = { id: 7, points_balance: 500, created_at: new Date(now - 10 * DAY).toISOString() };
  const room = { id: 1, is_open: true, is_member: false, member_count: 2, capacity: 3, entry_fee: 100, expires_ms: now + DAY };
  assert.deepEqual(canJoin(room, me, now), { ok: true, reason: "" });
  assert.equal(canJoin({ ...room, is_member: true }, me, now).reason, "이미 들어와 있어요");
  assert.equal(canJoin({ ...room, member_count: 3 }, me, now).reason, "정원이 다 찼어요");
  assert.equal(canJoin({ ...room, is_open: false }, me, now).reason, "끝난 방이에요");
  assert.equal(canJoin(room, { ...me, points_balance: 99 }, now).reason, "포인트가 부족해요");
  const young = { ...me, created_at: new Date(now - DAY).toISOString() };
  assert.equal(canJoin(room, young, now).reason, "가입 3일 후부터 유료방에 들어갈 수 있어요");
  assert.equal(canJoin({ ...room, entry_fee: 0 }, young, now).ok, true);
  assert.equal(canJoin(room, null, now).reason, "로그인이 필요해요");
});

test("joinConfirmText names the fee and says no refund", () => {
  assert.match(joinConfirmText({ entry_fee: 100, title: "비트 토론" }), /100포인트/);
  assert.match(joinConfirmText({ entry_fee: 100, title: "비트 토론" }), /환불/);
  assert.equal(joinConfirmText({ entry_fee: 0, title: "무료" }).includes("포인트"), false);
});

test("validateCreate enforces title 2~30, capacity 2~10, fee 0~300", () => {
  assert.equal(validateCreate({ title: "비트 토론", capacity: 5, entryFee: 100 }).ok, true);
  assert.equal(validateCreate({ title: "a", capacity: 5, entryFee: 0 }).errors.title, "2~30자로 적어 주세요");
  assert.equal(validateCreate({ title: "x".repeat(31), capacity: 5, entryFee: 0 }).errors.title, "2~30자로 적어 주세요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 1, entryFee: 0 }).errors.capacity, "2~10명이에요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 11, entryFee: 0 }).errors.capacity, "2~10명이에요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 5, entryFee: 301 }).errors.entryFee, "0~300포인트예요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 5, entryFee: -1 }).errors.entryFee, "0~300포인트예요");
});

test("roomTabs puts 전체 first, my rooms (open first, active pinned), 방 찾기 last", () => {
  const mine = [
    { id: 1, title: "A", is_open: true, last_seen_id: 0 },
    { id: 2, title: "B", is_open: false, last_seen_id: 0 },
    { id: 3, title: "C", is_open: true, last_seen_id: 0 },
  ];
  const tabs = roomTabs(mine, 3);
  assert.deepEqual(tabs.map((t) => t.key), ["all", "room:3", "room:1", "room:2", "find"]);
  assert.equal(tabs[1].label, "C");
  assert.equal(tabs[3].ended, true);
  assert.deepEqual(roomTabs([], null).map((t) => t.key), ["all", "find"]);
});

test("chatScope keeps legacy scopes and adds a room suffix", () => {
  assert.equal(chatScope(null), "anon");
  assert.equal(chatScope(5), "member:5");
  assert.equal(chatScope(5, 0), "member:5");
  assert.equal(chatScope(5, 12), "member:5:room:12");
});
```

`frontend/tests/chatApi.test.js` 끝에 테스트 추가:

```js
test("chat and room requests carry room_id", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, ...options });
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  });
  await api.chatList({ roomId: 12, afterId: 3 });
  await api.chatPost("hi", { roomId: 12 });
  await api.chatRead(9, { roomId: 12 });
  await api.chatPost("public");
  await api.roomsList();
  await api.roomCreate({ title: "t", capacity: 3, entry_fee: 50, consent: true });
  await api.roomJoin(12);
  await api.roomLeave(12);
  await api.roomExtend(12);
  const q = new URL(calls[0].url, "https://fixture.invalid").searchParams;
  assert.equal(q.get("room_id"), "12");
  assert.equal(q.get("after_id"), "3");
  assert.deepEqual(JSON.parse(calls[1].body), { text: "hi", room_id: 12 });
  assert.deepEqual(JSON.parse(calls[2].body), { last_seen_id: 9, room_id: 12 });
  assert.deepEqual(JSON.parse(calls[3].body), { text: "public" });
  assert.equal(calls[4].url, "/api/rooms");
  assert.equal(calls[5].method, "POST");
  assert.deepEqual(JSON.parse(calls[5].body), { title: "t", capacity: 3, entry_fee: 50, consent: true });
  assert.equal(calls[6].url, "/api/rooms/12/join");
  assert.equal(calls[7].method, "DELETE");
  assert.equal(calls[7].url, "/api/rooms/12/leave");
  assert.equal(calls[8].url, "/api/rooms/12/extend");
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && node --test tests/roomFlow.test.js tests/chatApi.test.js`
Expected: FAIL (모듈 없음 / `room_id` 없음)

- [ ] **Step 3: 구현**

`frontend/src/lib/chatBadge.js`:

```js
export function chatScope(userId, roomId = 0) {
  if (userId == null) return "anon";
  return roomId ? `member:${userId}:room:${roomId}` : `member:${userId}`;
}
```

`frontend/src/api.js` — chat 3개를 교체하고 rooms 5개 추가:

```js
  // leaderboard chat (daily KST) — roomId 가 있으면 전략방(방 생성 이후 전부)
  chatList: ({ beforeId, seenId, afterId, metadataOnly, messageIds, roomId, ...options } = {}) => {
    const query = new URLSearchParams();
    if (roomId) query.set("room_id", String(roomId));
    if (beforeId != null) query.set("before_id", String(beforeId));
    if (seenId != null) query.set("seen_id", String(seenId));
    if (afterId != null) query.set("after_id", String(afterId));
    if (metadataOnly) query.set("metadata_only", "true");
    if (messageIds?.length) query.set("message_ids", messageIds.join(","));
    return req(`/api/chat${query.size ? `?${query}` : ""}`, { timeoutMs: 15_000, ...options });
  },
  chatPost: (text, { roomId, ...options } = {}) =>
    req("/api/chat", { timeoutMs: 15_000, ...options, method: "POST", body: JSON.stringify(roomId ? { text, room_id: roomId } : { text }) }),
  chatRead: (lastSeenId, { roomId, ...options } = {}) =>
    req("/api/chat/read", { timeoutMs: 15_000, ...options, method: "PUT", body: JSON.stringify(roomId ? { last_seen_id: lastSeenId, room_id: roomId } : { last_seen_id: lastSeenId }) }),

  // 전략방
  roomsList: (options = {}) => req("/api/rooms", { timeoutMs: 15_000, ...options }),
  roomCreate: (body, options = {}) => req("/api/rooms", { timeoutMs: 15_000, ...options, method: "POST", body: JSON.stringify(body) }),
  roomJoin: (roomId, options = {}) => req(`/api/rooms/${roomId}/join`, { timeoutMs: 15_000, ...options, method: "POST" }),
  roomLeave: (roomId, options = {}) => req(`/api/rooms/${roomId}/leave`, { timeoutMs: 15_000, ...options, method: "DELETE" }),
  roomExtend: (roomId, options = {}) => req(`/api/rooms/${roomId}/extend`, { timeoutMs: 15_000, ...options, method: "POST" }),
```

`frontend/src/lib/roomsCopy.js`:

```js
// 전략방 — 문구 한곳. "리딩방"이라는 말은 쓰지 않는다.
export const TAB_ALL = "전체";
export const TAB_FIND = "방 찾기";
export const ROOM_NOTICE = "전략방 대화는 투자 조언이 아니에요. 매수·매도 권유와 수익 보장 발언은 금지돼요.";
export const CREATE_TITLE = "전략방 만들기";
export const CREATE_HINT = (cost) => `만들면 ${cost}포인트가 차감돼요. 7일 뒤에 자동으로 끝나고, 만료 하루 전에 연장할 수 있어요.`;
export const CONSENT_LABEL = "특정 코인 매수·매도를 권유하거나 수익을 보장하는 발언을 하지 않을게요. 위반하면 방이 닫힐 수 있어요.";
export const FEE_HINT = "입장료의 70%는 방장에게 가요. 0이면 무료방이에요.";
export const JOIN_FREE = "무료로 들어가기";
export const NO_ROOMS = "아직 열린 전략방이 없어요. 첫 방을 만들어 볼까요?";
export const LOGIN_TO_FIND = "로그인하면 전략방을 찾고 만들 수 있어요.";
export const OWNER_CANNOT_LEAVE = "방장은 나갈 수 없어요. 방은 만료일에 자동으로 끝나요.";
export const LEAVE_CONFIRM = "나가면 다시 들어올 때 입장료를 다시 내야 해요. 환불은 없어요.";
export const EXTEND_LABEL = (cost) => `${cost}포인트로 7일 연장`;
export const ENDED = "끝난 방이에요. 읽을 수만 있어요.";
export const ROOM_EMPTY = "아직 조용해요. 첫 이야기를 남겨 봐요.";
```

`frontend/src/lib/roomFlow.js`:

```js
// 전략방 — 순수 함수만. UI 는 이 결과를 그린다.
const DAY = 24 * 3600 * 1000;
const HOUR = 3600 * 1000;
export const CREATE_COST = 100;
export const MIN_ACCOUNT_AGE_MS = 3 * DAY;

export function remainingLabel(expiresMs, nowMs = Date.now()) {
  const left = Number(expiresMs) - nowMs;
  if (left <= 0) return "끝남";
  if (left >= DAY) return `${Math.floor(left / DAY)}일 남음`;
  if (left >= HOUR) return `${Math.floor(left / HOUR)}시간 남음`;
  return "곧 종료";
}

function accountAgeMs(me, nowMs) {
  const created = Date.parse(me?.created_at || "");
  return Number.isFinite(created) ? nowMs - created : Infinity;
}

export function canJoin(room, me, nowMs = Date.now()) {
  if (!me) return { ok: false, reason: "로그인이 필요해요" };
  if (room.is_member) return { ok: false, reason: "이미 들어와 있어요" };
  if (!room.is_open || Number(room.expires_ms) <= nowMs) return { ok: false, reason: "끝난 방이에요" };
  if (room.member_count >= room.capacity) return { ok: false, reason: "정원이 다 찼어요" };
  if (room.entry_fee > 0) {
    if (accountAgeMs(me, nowMs) < MIN_ACCOUNT_AGE_MS) return { ok: false, reason: "가입 3일 후부터 유료방에 들어갈 수 있어요" };
    if ((me.points_balance ?? 0) < room.entry_fee) return { ok: false, reason: "포인트가 부족해요" };
  }
  return { ok: true, reason: "" };
}

export function joinConfirmText(room) {
  if (!room.entry_fee) return `'${room.title}' 방에 들어갈까요?`;
  return `'${room.title}' 방에 들어가면 ${room.entry_fee}포인트가 차감돼요. 나가도 환불은 없어요.`;
}

export function validateCreate({ title, capacity, entryFee }) {
  const errors = {};
  const t = String(title || "").trim();
  if (t.length < 2 || t.length > 30) errors.title = "2~30자로 적어 주세요";
  const cap = Number(capacity);
  if (!Number.isInteger(cap) || cap < 2 || cap > 10) errors.capacity = "2~10명이에요";
  const fee = Number(entryFee);
  if (!Number.isInteger(fee) || fee < 0 || fee > 300) errors.entryFee = "0~300포인트예요";
  return { ok: Object.keys(errors).length === 0, errors };
}

// 탭: 전체 · 활성 방 · 열린 내 방 · 끝난 내 방 · 방 찾기
export function roomTabs(mine, activeRoomId) {
  const rooms = [...(mine || [])].sort((a, b) => {
    if (a.id === activeRoomId) return -1;
    if (b.id === activeRoomId) return 1;
    if (a.is_open !== b.is_open) return a.is_open ? -1 : 1;
    return 0;
  });
  return [
    { key: "all", label: "전체" },
    ...rooms.map((room) => ({ key: `room:${room.id}`, roomId: room.id, label: room.title, ended: !room.is_open })),
    { key: "find", label: "방 찾기" },
  ];
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && node --test "tests/*.test.js"`
Expected: 전부 pass (기존 387 + 새 7).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/api.js frontend/src/lib/chatBadge.js frontend/src/lib/roomsCopy.js frontend/src/lib/roomFlow.js frontend/tests/roomFlow.test.js frontend/tests/chatApi.test.js
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방 프론트: API 클라이언트·문구·순수 규칙(남은 시간·입장 가능·검증·탭)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 프론트 — ChatBox 탭 + 방 모드 + RoomsPanel

**Files:**
- Modify: `frontend/src/components/ChatBox.jsx` (`ChatBox` 래퍼 160~166행, `MemberChatBox` 시그니처 168행, `api.chat*` 호출 289·324·502·532·558·590행, 헤더 700~725행, 로그인 프롬프트 ~853행), `frontend/src/components/ChatBox.css`
- Create: `frontend/src/components/RoomsPanel.jsx`, `frontend/src/components/RoomsPanel.css`

**Interfaces:**
- Consumes: Task 6 전부, `ConfirmDialog({open, title, body?, confirmLabel?, busy?, onConfirm, onCancel})` (기존 컴포넌트 — 파일 열어 실제 prop 이름 확인 후 사용), `useAuth()`, `getAuthUser()`, `updateAuthUser()`.
- Produces: `ChatBox` 가 내부 상태로 `activeTab`(`"all" | "find" | "room:{id}"`)을 갖고, `MemberChatBox` 에 `roomId`, `room`(목록에서 온 요약), `tabs`, `activeTab`, `onSelectTab`, `roomsPanel`(element) 을 넘긴다.

- [ ] **Step 1: ChatBox 래퍼 — 탭 상태와 방 목록**

`ChatBox` 를 다음으로 교체:

```jsx
const ChatBox = memo(function ChatBox(props) {
  const { token, user } = useAuth();
  const member = token && user?.id != null ? user : null;
  const [activeTab, setActiveTab] = useState("all");
  const [rooms, setRooms] = useState(null);   // {items, mine, ...} — 로그인 상태에서만
  const [roomsVersion, setRoomsVersion] = useState(0);
  const refreshRooms = useCallback(() => setRoomsVersion((v) => v + 1), []);
  useEffect(() => {
    if (!member) { setRooms(null); setActiveTab("all"); return undefined; }
    const controller = new AbortController();
    api.roomsList({ signal: controller.signal }).then(setRooms).catch(() => {});
    return () => controller.abort();
  }, [member?.id, roomsVersion]);
  const roomId = activeTab.startsWith("room:") ? Number(activeTab.slice(5)) : 0;
  const room = roomId ? (rooms?.mine || []).find((r) => r.id === roomId) || null : null;
  useEffect(() => {  // 목록에서 사라진 방(만료 7일 경과·나감)이 활성 탭이면 전체로
    if (roomId && rooms && !room) setActiveTab("all");
  }, [roomId, rooms, room]);
  const tabs = member ? roomTabs(rooms?.mine || [], roomId) : [{ key: "all", label: TAB_ALL }];
  const scope = chatScope(member?.id, roomId);
  const roomsPanel = activeTab === "find" ? (
    <RoomsPanel member={member} data={rooms} onChanged={refreshRooms} onEnter={(id) => { refreshRooms(); setActiveTab(`room:${id}`); }} />
  ) : null;
  return <MemberChatBox key={scope} {...props} member={member} scope={scope} roomId={roomId} room={room}
                        tabs={tabs} activeTab={activeTab} onSelectTab={setActiveTab} roomsPanel={roomsPanel} onRoomsChanged={refreshRooms} />;
});
```

import 추가: `RoomsPanel`, `roomTabs` from `../lib/roomFlow.js`, `TAB_ALL, ROOM_NOTICE, ENDED, ROOM_EMPTY, OWNER_CANNOT_LEAVE, LEAVE_CONFIRM, EXTEND_LABEL` from `../lib/roomsCopy.js`, `remainingLabel` from `../lib/roomFlow.js`, `ConfirmDialog`.

- [ ] **Step 2: MemberChatBox — roomId 스레딩**

- 시그니처: `function MemberChatBox({ member, scope, roomId = 0, room = null, tabs, activeTab, onSelectTab, roomsPanel, onRoomsChanged, defaultOpen = false, defaultStickerTray = false })`.
- `isCurrent` 콜백: `chatScope(getToken() ? getAuthUser()?.id : null, roomId) === scope`.
- 6곳의 `api.chatList({...})` 호출에 `roomId` 추가, `api.chatRead(seenId, { roomId, signal })`, `api.chatPost(body.trim(), { roomId, signal })`.
- 방 모드 응답의 `data.room` 은 `feed` 에 안 들어가도 된다(래퍼가 목록으로 그린다). 단 `member_count`·`is_open` 최신값은 `data.room` 이 더 정확하므로 `useState(null)` 로 `liveRoom` 을 두고 `chatList` 성공 시 `if (data.room) setLiveRoom(data.room)`. 헤더는 `liveRoom || room` 을 쓴다.
- 방이 끝났으면(`!(liveRoom || room)?.is_open`) 컴포저 대신 `<div className="chat-login-prompt"><span>{ENDED}</span></div>`.
- 빈 상태 문구: 방 모드면 `ROOM_EMPTY`.

- [ ] **Step 3: 헤더 — 탭 줄과 방 정보**

`<header className="chat-head">` 안 `.chat-head-title` 을 방 모드에 따라 분기:

```jsx
<div className="chat-head-title">
  {roomId && (liveRoom || room) ? (
    <>
      <h3>{(liveRoom || room).title}</h3>
      <p>👥 <span className="num">{(liveRoom || room).member_count}/{(liveRoom || room).capacity}</span> · ⏳ {remainingLabel((liveRoom || room).expires_ms)}{(liveRoom || room).entry_fee ? <> · <span className="num">{(liveRoom || room).entry_fee}</span>P</> : " · 무료"}</p>
    </>
  ) : (
    <><h3>리더보드 채팅</h3><p>KST <span className="num">{kstClock()}</span> · 오늘의 대화</p></>
  )}
</div>
```

`</header>` 바로 뒤에 탭 줄(로그인 상태에서만, 탭이 2개 초과이거나 방 찾기 포함):

```jsx
{member ? (
  <nav className="chat-tabs" aria-label="채팅방">
    {tabs.map((tab) => (
      <button key={tab.key} type="button" className={`chat-tab${activeTab === tab.key ? " is-active" : ""}${tab.ended ? " is-ended" : ""}`}
              aria-pressed={activeTab === tab.key} onClick={() => onSelectTab(tab.key)} title={tab.ended ? ENDED : undefined}>
        {tab.label}
      </button>
    ))}
  </nav>
) : null}
```

방 모드 고정 고지 + 나가기/연장(탭 줄 아래):

```jsx
{roomId ? (
  <div className="chat-room-bar">
    <span className="chat-room-notice">{ROOM_NOTICE}</span>
    {(liveRoom || room)?.is_owner
      ? ((liveRoom || room)?.can_extend ? <button type="button" className="chat-room-action" onClick={extendRoom} disabled={roomBusy}>{EXTEND_LABEL(50)}</button> : <span className="chat-room-owner" title={OWNER_CANNOT_LEAVE}>방장</span>)
      : <button type="button" className="chat-room-action" onClick={() => setLeaving(true)} disabled={roomBusy}>나가기</button>}
  </div>
) : null}
```

`activeTab === "find"` 이면 `.chat-log`·컴포저 자리에 `roomsPanel` 을 그린다(`{roomsPanel ? roomsPanel : (<>기존 로그…컴포저</>)}`).

핸들러(컴포넌트 안):

```jsx
const [leaving, setLeaving] = useState(false);
const [roomBusy, setRoomBusy] = useState(false);
const [liveRoom, setLiveRoom] = useState(null);
async function leaveRoom() {
  setRoomBusy(true);
  try { await api.roomLeave(roomId); setLeaving(false); onRoomsChanged?.(); onSelectTab?.("all"); }
  catch (reason) { setError(reason?.message || "나가지 못했어요."); }
  finally { setRoomBusy(false); }
}
async function extendRoom() {
  setRoomBusy(true);
  try {
    const data = await api.roomExtend(roomId);
    setLiveRoom(data.room);
    if (data.points_balance != null) updateAuthUser({ ...getAuthUser(), points_balance: data.points_balance });
    onRoomsChanged?.();
  } catch (reason) { setError(reason?.message || "연장하지 못했어요."); }
  finally { setRoomBusy(false); }
}
```

`ConfirmDialog` 를 `ReportDialog` 옆에: `<ConfirmDialog open={leaving} title="전략방 나가기" body={LEAVE_CONFIRM} confirmLabel="나가기" busy={roomBusy} onConfirm={leaveRoom} onCancel={() => setLeaving(false)} />` — `ConfirmDialog.jsx` 를 열어 실제 prop 이름(`body`/`message`/`children`, `confirmLabel`/`confirmText`)에 맞춘다.

- [ ] **Step 4: RoomsPanel**

`frontend/src/components/RoomsPanel.jsx`:

```jsx
import { useState } from "react";
import { api } from "../api.js";
import { getAuthUser, updateAuthUser } from "../lib/auth.js";
import { canJoin, joinConfirmText, remainingLabel, validateCreate } from "../lib/roomFlow.js";
import { CONSENT_LABEL, CREATE_HINT, CREATE_TITLE, FEE_HINT, JOIN_FREE, LOGIN_TO_FIND, NO_ROOMS } from "../lib/roomsCopy.js";
import ConfirmDialog from "./ConfirmDialog.jsx";
import UserAvatar from "./UserAvatar.jsx";
import "./RoomsPanel.css";

// 전략방 찾기·만들기 — ChatBox 의 '방 찾기' 탭. 목록은 부모(ChatBox)가 들고 있고 바뀌면 onChanged 로 다시 받는다.
export default function RoomsPanel({ member, data, onChanged, onEnter }) {
  const [joining, setJoining] = useState(null);   // 확인 중인 방
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ title: "", capacity: 5, entryFee: 0, consent: false });
  if (!member) return <div className="rooms-panel"><p className="rooms-empty">{LOGIN_TO_FIND}</p></div>;
  if (!data) return <div className="rooms-panel"><div className="chat-skeleton" aria-hidden="true"><i /><i /><i /></div></div>;
  const me = getAuthUser();
  const consented = data.consented;

  async function join(room) {
    setBusy(true); setError("");
    try {
      const result = await api.roomJoin(room.id);
      if (result.points_balance != null) updateAuthUser({ ...getAuthUser(), points_balance: result.points_balance });
      setJoining(null);
      onEnter?.(room.id);
    } catch (reason) { setError(reason?.message || "들어가지 못했어요."); }
    finally { setBusy(false); }
  }

  async function create(event) {
    event.preventDefault();
    const check = validateCreate(form);
    if (!check.ok) { setError(Object.values(check.errors)[0]); return; }
    if (!consented && !form.consent) { setError("안내에 동의해 주세요."); return; }
    setBusy(true); setError("");
    try {
      const result = await api.roomCreate({ title: form.title.trim(), capacity: Number(form.capacity), entry_fee: Number(form.entryFee), consent: form.consent || consented });
      if (result.points_balance != null) updateAuthUser({ ...getAuthUser(), points_balance: result.points_balance });
      setCreating(false);
      onEnter?.(result.room.id);
    } catch (reason) { setError(reason?.message || "만들지 못했어요."); }
    finally { setBusy(false); }
  }

  return (
    <div className="rooms-panel">
      {creating ? (
        <form className="rooms-create" onSubmit={create}>
          <h4>{CREATE_TITLE}</h4>
          <p className="rooms-hint">{CREATE_HINT(data.create_cost)}</p>
          <label>방 제목<input value={form.title} maxLength={30} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="예: 비트 4시간봉 토론" /></label>
          <label>정원 <span className="num">{form.capacity}</span>명<input type="range" min="2" max={data.max_capacity} value={form.capacity} onChange={(e) => setForm({ ...form, capacity: Number(e.target.value) })} /></label>
          <label>입장료 <span className="num">{form.entryFee}</span>P<input type="range" min="0" max={data.max_entry_fee} step="10" value={form.entryFee} onChange={(e) => setForm({ ...form, entryFee: Number(e.target.value) })} /></label>
          <p className="rooms-hint">{FEE_HINT}</p>
          {!consented ? <label className="rooms-consent"><input type="checkbox" checked={form.consent} onChange={(e) => setForm({ ...form, consent: e.target.checked })} /> {CONSENT_LABEL}</label> : null}
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : null}
          <div className="rooms-actions">
            <button type="button" className="btn-secondary" onClick={() => { setCreating(false); setError(""); }} disabled={busy}>취소</button>
            <button type="submit" className="btn-primary" disabled={busy}>{busy ? "만드는 중…" : `${data.create_cost}P로 만들기`}</button>
          </div>
        </form>
      ) : (
        <>
          <div className="rooms-head">
            <span className="rooms-count">열린 전략방 <b className="num">{data.items.length}</b></span>
            <button type="button" className="btn-primary rooms-new" onClick={() => { setCreating(true); setError(""); }}>+ 방 만들기</button>
          </div>
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : null}
          {data.items.length === 0 ? <p className="rooms-empty">{NO_ROOMS}</p> : (
            <ul className="rooms-list">
              {data.items.map((room) => {
                const gate = canJoin(room, me);
                return (
                  <li key={room.id} className="rooms-card">
                    <UserAvatar src={room.owner_avatar_url} name={room.owner_username} size={28} />
                    <div className="rooms-card-body">
                      <strong>{room.title}</strong>
                      <small>{room.owner_username} · 👥 <span className="num">{room.member_count}/{room.capacity}</span> · ⏳ {remainingLabel(room.expires_ms)} · {room.entry_fee ? <><span className="num">{room.entry_fee}</span>P</> : "무료"}</small>
                    </div>
                    {room.is_member
                      ? <button type="button" className="btn-secondary" onClick={() => onEnter?.(room.id)}>들어가기</button>
                      : <button type="button" className="btn-primary" disabled={!gate.ok || busy} title={gate.reason || undefined} onClick={() => setJoining(room)}>{room.entry_fee ? `${room.entry_fee}P 입장` : JOIN_FREE}</button>}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <ConfirmDialog open={Boolean(joining)} title="전략방 입장" body={joining ? joinConfirmText(joining) : ""} confirmLabel="들어가기" busy={busy} onConfirm={() => join(joining)} onCancel={() => setJoining(null)} />
    </div>
  );
}
```

`UserAvatar` 는 ChatBox 가 이미 쓰는 컴포넌트 — import 경로를 ChatBox.jsx 상단에서 그대로 가져온다. `btn-primary`/`btn-secondary` 가 프로젝트 공용 클래스인지 `index.css` 에서 확인하고, 없으면 ChatBox 의 `chat-history` 류 버튼 클래스를 쓴다.

- [ ] **Step 5: CSS**

`frontend/src/components/ChatBox.css` 끝에:

```css
/* 전략방 탭 줄 — 머리 아래 한 줄. 색은 토큰만. */
.chat-tabs { display: flex; gap: 4px; padding: 6px 10px 0; overflow-x: auto; scrollbar-width: none; border-bottom: 1px solid rgb(var(--c-slate-200)); }
.chat-tabs::-webkit-scrollbar { display: none; }
.chat-tab { flex: 0 0 auto; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 6px 10px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: rgb(var(--c-slate-600)); font-size: 13px; font-weight: 600; cursor: pointer; }
.chat-tab.is-active { color: rgb(var(--c-slate-900)); border-bottom-color: rgb(var(--c-brand)); }
.chat-tab.is-ended { opacity: .55; }
.chat-room-bar { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: rgb(var(--c-slate-100)); color: rgb(var(--c-slate-700)); font-size: 12px; }
.chat-room-notice { flex: 1; min-width: 0; }
.chat-room-action { flex: 0 0 auto; border: 1px solid rgb(var(--c-slate-300)); border-radius: 999px; padding: 3px 10px; background: rgb(var(--c-surface)); color: rgb(var(--c-slate-900)); font-size: 12px; cursor: pointer; }
.chat-room-owner { flex: 0 0 auto; font-weight: 700; color: rgb(var(--c-slate-900)); }
```

`frontend/src/components/RoomsPanel.css`:

```css
.rooms-panel { flex: 1; min-height: 0; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; color: rgb(var(--c-slate-900)); }
.rooms-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rooms-count { font-size: 13px; color: rgb(var(--c-slate-600)); }
.rooms-empty { margin: 24px 0; text-align: center; color: rgb(var(--c-slate-600)); font-size: 13px; }
.rooms-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.rooms-card { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 10px; border: 1px solid rgb(var(--c-slate-200)); border-radius: 12px; background: rgb(var(--c-surface)); }
.rooms-card-body { min-width: 0; }
.rooms-card-body strong { display: block; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rooms-card-body small { display: block; margin-top: 2px; font-size: 12px; color: rgb(var(--c-slate-600)); }
.rooms-create { display: grid; gap: 10px; }
.rooms-create h4 { margin: 0; font-size: 15px; }
.rooms-create label { display: grid; gap: 4px; font-size: 13px; font-weight: 600; }
.rooms-create input[type="text"], .rooms-create input:not([type]) { border: 1px solid rgb(var(--c-slate-300)); border-radius: 8px; padding: 8px 10px; background: rgb(var(--c-surface)); color: rgb(var(--c-slate-900)); }
.rooms-hint { margin: 0; font-size: 12px; color: rgb(var(--c-slate-600)); }
.rooms-consent { display: flex !important; align-items: flex-start; gap: 8px; font-weight: 500 !important; line-height: 1.4; }
.rooms-actions { display: flex; justify-content: flex-end; gap: 8px; }
```

- [ ] **Step 6: 빌드 + 브라우저 확인**

Run: `cd frontend && npx vite build --logLevel error && node --test "tests/*.test.js"`
Expected: 빌드 OK, 테스트 전부 pass.

preview_start `frontend-dev`(5173) + `backend-dev`(8000). 두 계정(`/api/auth/signup` 으로 만들고 localStorage `ggp_token`/`ggp_user` 설정; 유료 입장 테스트는 DB 에서 `created_at` 을 3일 전으로 되돌린다)으로:
1. 리더보드 채팅 열기 → 탭 `전체 · 방 찾기` 보임.
2. 방 찾기 → 방 만들기(제목·정원·입장료 50·동의) → 잔액 −100, 방 탭으로 전환, 헤더에 👥1/N·⏳6일.
3. 다른 계정으로 방 찾기 → `50P 입장` → ConfirmDialog → 입장 → 잔액 −50, 방장 +35.
4. 방에서 메시지 주고받기, `전체` 탭 메시지와 섞이지 않음.
5. 멤버 `나가기` → 확인 → 전체 탭으로.
6. 다크 모드에서 탭·카드 글자색 확인(`resize_window colorScheme dark`).
스크린샷 1장(방 모드 헤더+탭+메시지).

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/components/ChatBox.jsx frontend/src/components/ChatBox.css frontend/src/components/RoomsPanel.jsx frontend/src/components/RoomsPanel.css
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방 프론트: 채팅 탭(전체·내 방·방 찾기), 방 모드 헤더·고지·나가기·연장, 방 찾기/만들기 패널

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: 문서 + 전체 회귀

**Files:**
- Modify: `DEPLOY.md` (「껄무새에게 물어볼까?」 섹션 뒤), `frontend/src/pages/Guide.jsx` (FAQ 목록, 물어볼까 항목 뒤), `render.yaml` (변경 없음 — 환경변수 없음, 확인만)

- [ ] **Step 1: DEPLOY.md**

「껄무새에게 물어볼까?」 섹션 뒤에:

```markdown
## 전략방 (소그룹 유료 채팅)

- 마이그레이션: `supabase/migrations/20260921120000_chat_rooms.sql` (chatroom·chatroommember, chatmessage.room_id, user.room_consent_at). 서버 기동 시 `_PG_ADDED_COLUMNS` 가 컬럼을 보강하지만 테이블·RLS 는 마이그레이션이 만든다 — 배포 전에 적용.
- 포인트 상수는 `backend/app/rooms.py` 상단(생성 100·연장 50·입장료 0~300·정원 2~10·7일). 환경변수 없음.
- 환불 없음. 관리자 폐쇄: `POST /api/admin/rooms/{id}/close`.
- **포인트 현금화(충전/환전)를 도입할 때 이 기능을 다시 심사한다** — 스펙 `docs/superpowers/specs/2026-09-21-chat-rooms-design.md` §2.
```

- [ ] **Step 2: Guide FAQ 한 항목**

기존 "껄무새에게 물어볼까? 는 투자 추천인가요?" 항목 바로 뒤에 같은 형식으로:

- q: `전략방은 뭔가요? 입장료는 환불되나요?`
- a: `리더보드 채팅에서 회원이 만드는 최대 10명 소그룹 대화방이에요. 만들 때 100포인트, 들어갈 때 방장이 정한 입장료(0~300포인트)를 내고, 입장료의 70%는 방장에게 가요. 7일 뒤 자동으로 끝나고 환불은 없어요. 전략방 대화는 투자 조언이 아니며, 매수·매도 권유나 수익 보장 발언은 금지돼요.`

- [ ] **Step 3: 전체 회귀**

Run (레포 루트): `backend/.venv/Scripts/python -m pytest backend/tests -q -p no:warnings --continue-on-collection-errors`
Expected: `test_rooms.py` 14 pass, 기존 실패는 main 과 동일한 것만(test_agent_position_news ×2, test_startup_initialization ×1, prefect/encoding 수집 오류 6).

Run: `cd frontend && node --test "tests/*.test.js" && npx vite build --logLevel error`
Expected: 전부 pass, 빌드 OK.

- [ ] **Step 4: 커밋**

```bash
git add DEPLOY.md frontend/src/pages/Guide.jsx
git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit -m "전략방: 배포 메모·가이드 FAQ

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
