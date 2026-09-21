# 전략방 — 소그룹 유료 채팅 설계

날짜: 2026-09-21 · 상태: 승인됨 (구현 전)

## 1. 목적

리더보드 채팅 사용자들이 "리딩방처럼 작은 방이 있으면 좋겠다"고 요청했다. 이 문서는 그 요청을
**대등한 소그룹 토론방**으로 구현한다. 방장이 지시하고 멤버가 읽는 구조(리딩방)가 아니라,
최대 10명이 같은 권한으로 대화하는 방이며 입장료(포인트)는 "진지한 사람만 들어오게 하는 문턱"이다.

이름은 사용자에게 **"전략방"** 으로 노출한다. "리딩방"이라는 단어는 UI·문구·코드 어디에도 쓰지 않는다.

## 2. 법적 전제 (변호사 의견 아님, 설계 제약)

- 포인트는 현금 가치가 없고 충전·환전이 불가능하다([points.py](../../../backend/app/points.py) "no real money yet").
  이 전제에서 입장료는 게임 내 재화이며 매크로 잠금해제(100P, 창작자 70%)와 같은 성격이다.
- **포인트 현금화(Stage 2: 충전/환전)가 도입되는 시점에 이 기능은 반드시 재심사한다.** 같은 기능이
  "유료 리딩방 중개 + 수수료 30%"가 되어 가상자산이용자보호법상 부정거래 방조·중개 책임이 문제될 수 있다.
- 안전장치를 설계에 고정한다: 방장 동의(투자 권유·수익 보장 금지), 방 상단 고정 고지, 기존 금칙어·신고,
  관리자 폐쇄.

## 3. 사용자 결정 사항 (브레인스토밍 결론)

| 항목 | 결정 |
|---|---|
| 방 성격 | (B) 10명 대등 소그룹 채팅 |
| 수명 | (A) 생성 후 7일 만료, 방장이 50P로 7일 연장 가능 |
| 입장료 | (A) 0~300P, 무료방 허용, 가입 3일 미만 계정은 유료방 입장 불가, 방 생성 하루 1개 |
| 환불 | (A) 환불 없음, 방장 조기 폐쇄 불가, 나가기는 자리만 비움(재입장 시 재결제) |
| 생성 비용 | 100P (플랫폼 싱크) |
| 정산 | 입장료의 70%가 입장 즉시 방장에게, 30%는 플랫폼 싱크 (`UNLOCK_CREATOR_SHARE_PCT` 재사용) |

## 4. 데이터 모델

`backend/app/db.py`

```python
class ChatRoom(SQLModel, table=True):
    id: Optional[int] = PK
    owner_id: int = Field(index=True)
    title: str                      # 2~30자, require_clean_text
    capacity: int                   # 2~10 (방장 포함)
    entry_fee: int                  # 0~300
    created_at: str; created_ms: int (BigInteger, index)
    expires_ms: int (BigInteger, index)   # created_ms + 7일, 연장 시 += 7일
    extended_count: int = 0
    closed_reason: str = ""         # "" | "admin" — 관리자 폐쇄 시만
    closed_at: str = ""

class ChatRoomMember(SQLModel, table=True):
    room_id: int = PK part (index)
    user_id: int = PK part (index)
    paid: int                        # 실제 낸 포인트 (방장은 0)
    joined_at: str; joined_ms: int (BigInteger)

class ChatMessage:  # 기존
    room_id: Optional[int] = Field(default=None, index=True)   # NULL = 공개방

class ChatReadState:  # 기존 — PK 를 (user_id, room_id) 로
    user_id: int = PK part
    room_id: int = Field(default=0, primary_key=True)          # 0 = 공개방
    last_seen_id: int = 0

class User:  # 기존
    room_consent_at: str = ""        # 방 생성 동의 시각 (최초 1회)
```

- ChatReadState PK 변경은 sqlite `_migrate` 에서 "테이블 재생성 + 복사(room_id=0)" 로, Postgres 는 마이그레이션
  SQL 로 처리한다. 기존 행은 전부 공개방(room_id=0)이다.
- `_PG_ADDED_COLUMNS` 에 `chatmessage.room_id`, `user.room_consent_at` 추가. `_PG_PRIVATE_CACHE_TABLES` 에
  `chatroom`, `chatroommember` 추가. Supabase 마이그레이션 `supabase/migrations/20260921120000_chat_rooms.sql`
  (테이블·RLS deny-all·인덱스), `supabase/tests/gg_parrot_rls.sql` 목록에 두 테이블 추가.
- 만료된 방은 삭제하지 않는다. 메시지는 보관되고 멤버는 읽을 수 있다. 목록에서는 빠진다.

## 5. 포인트 흐름 (`points.py` 의 `apply` 재사용, 한 트랜잭션)

| 사건 | reason | ref | 흐름 |
|---|---|---|---|
| 방 생성 | `room_create` | `room:{id}` | 방장 −100 |
| 유료 입장 | `room_join` / `room_host_earn` | `room:{id}` | 멤버 −fee, 방장 +`creator_share(fee)` |
| 연장 | `room_extend` | `room:{id}` | 방장 −50, `expires_ms += 7일`, `extended_count += 1` |

상수(`backend/app/rooms.py`): `ROOM_CREATE_COST=100`, `ROOM_EXTEND_COST=50`, `ROOM_TTL_MS=7일`,
`ROOM_EXTEND_WINDOW_MS=24시간`, `MAX_CAPACITY=10`, `MIN_CAPACITY=2`, `MAX_ENTRY_FEE=300`,
`MIN_ACCOUNT_AGE_FOR_PAID_MS=3일`, `ROOMS_PER_DAY=1`, `TITLE_MIN=2`, `TITLE_MAX=30`.

- fee=0 이면 원장 기록 없이 멤버만 추가한다.
- 방장은 생성 시 `paid=0` 으로 자동 멤버가 된다. 방장은 나갈 수 없다.
- 환불은 어떤 경로로도 없다. 방장 잔액을 되돌리는 코드는 존재하지 않는다.
- 방장 행은 `_lock_member` 패턴(`SELECT … FOR UPDATE`)으로 잠근 뒤 정산한다. 입장 동시성:
  방 행도 잠근 상태에서 정원을 세고 삽입한다(정원 초과 경합 방지). sqlite 는 단일 writer 라 자연히 직렬화된다.

## 6. 규칙

생성 (`POST /api/rooms`)
- 로그인 + `assert_can_write`(차단 계정 불가).
- `consent=true` 필수. 최초 생성 시 `User.room_consent_at` 기록; 이후에는 이미 기록돼 있으면 `consent` 생략 가능.
- 오늘(KST) 이미 방을 만들었으면 429 "전략방은 하루에 하나만 만들 수 있어요. 내일 다시 만들어 주세요."
- 잔액 < 100 → 402 (기존 `InsufficientPoints` 매핑).
- 제목 `require_clean_text(title, "방 제목")`, 길이 2~30, 정원 2~10, 입장료 0~300 아니면 422.

입장 (`POST /api/rooms/{id}/join`)
- 만료·폐쇄된 방 → 410 "이 전략방은 끝났어요."
- 이미 멤버 → 409 "이미 들어와 있는 방이에요."
- 정원 초과 → 409 "정원이 다 찼어요."
- 입장료 > 0 이고 가입 3일 미만 → 403 "가입 3일 후부터 유료 전략방에 들어갈 수 있어요."
- 잔액 부족 → 402.
- 성공 시 `{room, points_balance}`.

나가기 (`DELETE /api/rooms/{id}/leave`)
- 방장 → 403 "방장은 나갈 수 없어요. 방은 만료일에 자동으로 끝나요."
- 멤버 행 삭제. 환불 없음. 응답 `{ok:true}`.

연장 (`POST /api/rooms/{id}/extend`)
- 방장만. 만료 24시간 전부터 만료 시각까지만 가능(그 외 409 "만료 하루 전부터 연장할 수 있어요.").
- 폐쇄된 방은 불가. 잔액 부족 402.

관리자 폐쇄 (`POST /api/admin/rooms/{id}/close`, 기존 admin 인증)
- `closed_reason="admin"`, `closed_at` 기록. 이후 쓰기·입장·연장 불가, 읽기는 멤버에게 허용.

메시지 (`/api/chat` 3개 라우트에 `room_id: Optional[int]` 쿼리/바디 추가)
- `room_id` 없음 → 기존 공개방 동작 그대로(오늘 KST 만).
- `room_id` 있음 → 멤버가 아니면 403 "이 전략방의 멤버만 볼 수 있어요.". 조회 범위는
  `created_ms >= room.created_ms` (방 생성 이후 전부, 7일치). 쓰기는 만료·폐쇄 전에만(410).
- 기존 레이트리밋(`_RATE_MAX`/`_RATE_WINDOW`)은 방 구분 없이 사용자 단위로 그대로.
- 응답의 `disclaimer` 는 유지하고, 방 모드에서는 `room` 요약(제목·정원·인원·만료·방장 여부)을 함께 내려준다.

목록 (`GET /api/rooms`)
- 로그인 필요. 열린 방(만료 전·폐쇄 아님)을 `created_ms` 내림차순, 최대 50개.
- 항목: `id, title, owner_username, owner_avatar_url, capacity, member_count, entry_fee, expires_ms, is_member, is_owner, can_extend`.
- 내가 멤버인 방은 만료된 것까지 별도 배열 `mine` 로 함께(읽기용 탭). 만료 후 7일 지난 방은 `mine` 에서도 빠짐.

## 7. API 요약

```
GET    /api/rooms                        → {items:[...], mine:[...], server_ms}
POST   /api/rooms                        {title, capacity, entry_fee, consent?} → {room, points_balance}
POST   /api/rooms/{id}/join              → {room, points_balance}
DELETE /api/rooms/{id}/leave             → {ok:true}
POST   /api/rooms/{id}/extend            → {room, points_balance}
POST   /api/admin/rooms/{id}/close       → {room}
GET    /api/chat?room_id=…               (기존 파라미터 그대로 + room_id)
POST   /api/chat        {text, room_id?}
PUT    /api/chat/read   {last_seen_id, room_id?}
```

오류는 기존 관례대로 `detail` 에 한국어 문장 하나. 상태코드: 402 포인트 부족, 403 권한/자격, 409 상태 충돌,
410 종료된 방, 422 입력 오류, 429 하루 1개.

## 8. 프론트

- `frontend/src/lib/roomsCopy.js` — 문구(고지, 동의문, 오류 안내, 남은 시간 표기).
- `frontend/src/lib/roomFlow.js` — 순수 함수: `remainingLabel(expires_ms, now)`, `canJoin(room, me)`,
  `joinConfirmText(room)`, `validateCreate({title,capacity,entry_fee})`, 탭 배열 계산
  `roomTabs(mine, activeRoomId)`. node:test 로 검증.
- `frontend/src/lib/chatStore.js` — scope 문자열에 방을 포함: `chatScope(userId, roomId=0)` → `"chat:{userId}:{roomId}"`.
  기존 호출은 roomId 생략으로 동작 불변.
- `ChatBox.jsx` — 상단 탭 줄: `전체` · 내가 든 방(제목, 미확인 수) · `방 찾기`. 활성 탭이 방이면
  `MemberChatBox` 에 `roomId` 를 넘겨 모든 `api.chat*` 호출에 `room_id` 를 붙이고, 헤더에
  제목·👥n/cap·⏳남은 시간·나가기(방장은 연장) 를 표시한다. 방 상단에 고정 고지 한 줄.
- `RoomsPanel.jsx`(신규, ChatBox 내부 탭) — 열린 방 카드 목록 + 입장 버튼(ConfirmDialog: "N포인트가 차감돼요.
  환불은 없어요.") + `방 만들기` 폼(제목·정원·입장료·동의 체크박스). 성공 시 해당 방 탭으로 전환.
- `api.js` — `roomsList`, `roomCreate`, `roomJoin`, `roomLeave`, `roomExtend`; `chatList/chatPost/chatRead` 에 `roomId` 옵션.
- 포인트 잔액은 `updateAuthUser({...getAuthUser(), points_balance})` 패턴으로 즉시 반영.
- 로그아웃 상태: 탭 줄에 `전체` 만. `방 찾기` 는 로그인 유도.

## 9. 테스트

백엔드 `backend/tests/test_rooms.py`
- 생성: 정상(−100 원장, 방장 멤버 paid=0, room_consent_at 기록), 동의 없음 422, 하루 1개 429, 잔액 부족 402,
  제목/정원/입장료 범위 422, 차단 계정 403.
- 입장: 유료 정상(멤버 −fee, 방장 +70%, 원장 2행, ref 일치), 무료 정상(원장 없음), 중복 409, 정원 409,
  가입 3일 미만 403(무료방은 허용), 잔액 부족 402(원장·멤버 변화 없음), 만료 410.
- 나가기: 멤버 정상(환불 없음 확인), 방장 403, 재입장 시 재결제.
- 연장: 창 밖 409, 창 안 정상(+7일, −50), 폐쇄 방 불가.
- 메시지: 비멤버 조회/쓰기 403, 멤버 조회 범위(방 생성 이전 메시지 제외, 공개방 메시지 섞이지 않음),
  만료 후 쓰기 410 읽기 200, room_id 없으면 기존 공개방 테스트 전부 그대로 통과.
- 관리자 폐쇄 후 입장·쓰기·연장 불가.

프론트 `frontend/tests/roomFlow.test.js` — 남은 시간 표기, canJoin 분기, validateCreate, roomTabs 정렬(활성 방 우선, 미확인 수).

## 10. 범위 밖 (YAGNI)

- 실시간 푸시(폴링 유지), 방 검색·태그, 초대 링크, 방장 강퇴, 비공개 방, 방 제목 수정, 환불 어떤 형태든.
- 포인트 현금화 관련 모든 것 — Stage 2 재심사 항목.
