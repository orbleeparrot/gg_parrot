# 리더보드 실시간 상태 — 설계

날짜: 2026-09-21 · 상태: 승인됨 (구현 전)

## 1. 문제

리더보드 수익률이 "멈춰 있다"고 느껴진다. 원인은 두 겹이다.

1. **포지션이 없으면 수익률은 상수다.** `PositionSim.equity()`([stepper.py:265](../../../backend/app/engine/stepper.py))는
   `in_pos`가 아니면 현금을 그대로 돌려준다. 매크로는 대부분의 시간을 진입 대기·청산 후 재진입 대기로 보내므로
   숫자가 움직일 이유가 없다. 페이퍼 세션은 스스로 "완료"되지 않는다 — 익절/손절 → 쿨다운 → 재진입 대기의 루프이고,
   일일 손실 한도는 "오늘만 중단", 세션 종료는 리플레이 소진·수동 정지·오류뿐이다.
2. **움직여도 갱신 사슬이 길다.** 가격 3s → DB 체크포인트 **20s**(`PAPER_CHECKPOINT_SECONDS`) → 스냅샷 5s → 프론트 5s(비활성 60s).
3. **프론트가 상태를 전혀 안 그린다.** [Leaderboard.jsx](../../../frontend/src/pages/Leaderboard.jsx)는 `return_pct` 하나만 보여 준다.
   "12:41 익절 후 대기"도 "정지됨"도 똑같이 멈춘 숫자다.

## 2. 목표

가짜로 숫자를 흔들지 않고, **왜 멈춰 있는지**를 보여 주고, **움직일 수 있는 것(보유 중 미실현)** 은 현재가로 실시간 움직이게 한다.

## 3. 사용자 결정 (범위)

1. 행 상태 5종 + 거래 횟수 + 마지막 체결 표시
2. 보유 중인 행은 현재가로 미실현 수익률을 클라이언트에서 실시간 계산
3. 체크포인트 20s → 10s

범위 밖: 서버 푸시(SSE/WS), 수익률 히스토리·스파크라인, 정렬을 클라이언트 실시간 값으로 바꾸는 것(정렬은 서버 스냅샷 값 유지 — 행이 튀지 않게).

## 4. 행 상태 (5종)

| state | 조건 (우선순위 순) | 표시 |
|---|---|---|
| `stopped` | `PaperSession.status != "running"` | `종료됨 · 최종 {return}%` |
| `halted` | `halted_today` | `일일 손실 한도 · 내일 재개` |
| `holding` | `in_position` | `보유 중 · 진입가 {entry_price}` + 실시간 미실현 |
| `exited` | `trade_count > 0` (마지막 체결이 청산) | `{HH:MM} {익절|손절|청산} {return_at_trade}%` + `재진입 대기` 또는 `쿨다운 {n}분` |
| `waiting` | 그 외 | `진입 대기 · {SYMBOL} {last_price}` |

모든 상태에 `거래 {trade_count}회`를 붙인다(0회면 생략). 익절/손절 구분: 마지막 체결의 `return_at_trade`가 직전 진입 체결의
`return_at_trade`보다 크면 익절, 아니면 손절 — 서버가 계산해 `last_fill_kind`(`"tp"|"sl"|"exit"`)로 내려준다. 진입 체결만 있고 청산이
없으면 `holding`이므로 이 계산은 청산 체결에서만 한다. 판단 정보가 부족하면 `"exit"`.

## 5. 백엔드

### 5.1 시뮬레이터 상태 노출 (`backend/app/engine/stepper.py`)

`PositionSim.state()`와 `DcaSim.state()` 추가 — 순수 조회, 부작용 없음:

```python
{"in_position": bool, "dir": +1 | -1, "qty": float, "entry_price": float,
 "cooldown_until_ms": int | None, "halted_today": bool}
```
- PositionSim: `in_position=in_pos`, `dir=+1 if side is LONG else -1`, `entry_price=entry_fill`, `cooldown_until_ms`는
  `_cooldown_until`(있으면 epoch ms), `halted_today = _halted_day is not None and _halted_day == _day`.
- DcaSim: `in_position = qty > 0`, `dir=+1`, `entry_price = cost_basis/qty` (qty>0일 때, 아니면 0), `cooldown_until_ms=None`,
  `halted_today` 같은 규칙(`stopped`는 손절 후 상태 — `in_position=False`).

### 5.2 러너·체크포인트 (`backend/app/paper.py`)

- `_Runner`에 `trade_count: int = 0`, `last_fill: dict | None`(= `_fill_payload` + `ms`) 추가. `_tick_and_checkpoint`에서 체결마다 갱신.
  세션은 새로 시작할 때만 만들어지므로(재개 없음) 0에서 시작해도 맞다.
- `_snapshot()`에 `"state"` 추가:
  ```python
  {"in_position": any(leg.sim.state()["in_position"] for leg in legs),
   "halted_today": any(...), "cooldown_until_ms": max(있는 값들) or None,
   "trade_count": runner.trade_count, "last_fill_ms": ..., "last_fill_side": ..., "last_fill_return": ...,
   "last_fill_kind": "tp"|"sl"|"exit"|"" , "last_price": runner.last_price, "checkpoint_ms": now_ms,
   "legs": [{"symbol", "qty", "dir", "entry_price", "last_price", "in_position"} for each leg]}
  ```
  `last_fill_kind`: 청산 체결(side ∈ {sell, cover})이면 직전 진입 체결(`runner.recent`에서 찾음)의 `return_at_trade`와 비교, 없으면 `"exit"`;
  진입 체결이면 `""`.
- `PaperSession.state_json: str = ""` 컬럼 추가(sqlite `_migrate`, `_PG_ADDED_COLUMNS["papersession"]`, Supabase 마이그레이션
  `20260921150000_paper_session_state.sql` — `alter table … add column if not exists state_json varchar not null default ''`).
  `_persist_checkpoint`가 `row.state_json = json.dumps(snapshot["state"])`를 쓴다. `_persist_finalize`도 마지막 상태를 쓴다.
- `CHECKPOINT_SECONDS` 기본값 `"20"` → `"10"`. `render.yaml`에 `PAPER_CHECKPOINT_SECONDS`가 없으면 추가하지 않는다(기본값으로 충분).

### 5.3 리더보드 뷰 (`backend/app/leaderboard.py`)

- `_durable_statuses`가 `PaperSession.state_json`도 읽어 `"state": dict`로 돌려준다(파싱 실패·빈 값이면 `{}`).
- 순수 함수 `derive_row_state(session_status: str, state: dict) -> str` (§4 우선순위).
- `_entry_view` 출력에 추가:
  ```
  "state": "waiting"|"holding"|"exited"|"halted"|"stopped"|"none",   # 세션 없으면 "none"
  "trade_count": int, "last_fill_kst": "HH:MM"|None, "last_fill_kind": str, "last_fill_return": float|None,
  "cooldown_until_ms": int|None, "last_price": float|None, "checkpoint_ms": int|None, "virtual_balance": float|None,
  "legs": [{"symbol","qty","dir","entry_price","last_price","in_position"}],   # 잠긴 항목도 내려준다 — 전략이 아니라 상태
  ```
  잠금(`locked`) 항목도 상태·미실현은 보여 준다. 전략 내용(`macro`, `human_summary`)만 계속 숨긴다. `legs`의 `qty`는
  가상 자본 기준이라 전략을 유추할 수 없다.

### 5.4 일괄 시세 (`GET /api/prices?symbols=BTCUSDT,ETHUSDT`)

- 공개 읽기(`PUBLIC_READS`에 추가). `symbols` 1~30개, 각 `^[A-Z0-9]{2,20}USDT$` 아니면 422 "종목 형식이 잘못됐어요."
- 응답 `{"prices": {"BTCUSDT": 96412.1, ...}, "ms": now_ms}` — `get_ticker_price_cached(symbol)`(TTL 2s) 사용, 못 받은 종목은 생략.
- `backend/app/marketdata.py`에 `batch_prices(symbols) -> dict` 로 두고 라우트는 얇게.

## 6. 프론트

### 6.1 순수 모듈 `frontend/src/lib/leaderboardState.js` (node:test)

- `liveReturn(entry, prices, initial)`: `holding`이고 모든 보유 leg의 현재가가 있으면
  `equity_now = entry.equity + Σ qty * (price_now − leg.last_price) * dir`, `return = (equity_now − initial)/initial*100`;
  아니면 `entry.return_pct`. `initial`은 서버가 내려주는 `virtual_balance`(§5.3에 `"virtual_balance"` 추가). 값이 없으면 서버값.
- `stateLine(entry, now)`: §4 표시 문자열 `{text, tone}` (`tone`: `"muted"|"live"|"good"|"bad"|"warn"`).
- `cooldownLabel(cooldown_until_ms, now)`: `"쿨다운 14분"` / 지나면 `"재진입 대기"`.
- `symbolsOf(items)`: 보유 중 행의 leg 심볼 합집합(중복 제거, 최대 30).

### 6.2 `Leaderboard.jsx`

- 수익률 셀 아래 `lb-state` 캡션 한 줄(`stateLine`). `holding`이면 값 옆에 작은 펄스 점(`.lb-live-dot`, `prefers-reduced-motion`이면 정지).
- 시세 폴링: `useAdaptivePolling`으로 `api.prices(symbolsOf(items))` **3s**(최대 30s 백오프). 보유 중 행이 없으면 폴링 안 함.
  값은 `useState` `prices`에 두고 `ret(e)` 대신 `liveReturn(e, prices, e.virtual_balance)`로 표시.
- 정렬·순위는 서버값 그대로.
- `api.js`: `prices(symbols, options)`.
- 색은 `rgb(var(--c-*))`, 문구는 `lib/leaderboardCopy.js`에 모은다(기존 파일이 있으면 거기에).

## 7. 테스트

백엔드 `backend/tests/test_leaderboard_state.py`
- `PositionSim.state()`: 진입 전/후/청산 후/쿨다운 중/일일 한도 후 값.
- `DcaSim.state()`: 매수 후 `in_position`, `entry_price = cost_basis/qty`.
- `_persist_checkpoint`가 `state_json`을 쓰고 `_durable_statuses`가 읽는다.
- `derive_row_state` 우선순위 6케이스(stopped > halted > holding > exited > waiting, 세션 없음 none).
- `_entry_view`에 새 필드가 있고 잠긴 항목도 `state`·`legs`는 있다.
- `/api/prices`: 정상, 31개 422, 형식 오류 422, 시세 실패 종목 생략.

프론트 `frontend/tests/leaderboardState.test.js`
- `liveReturn` 롱/숏/포트폴리오/가격 없음 케이스, `stateLine` 5상태 + 거래 횟수, `cooldownLabel`, `symbolsOf`.
- `chatApi.test.js` 방식으로 `api.prices` 쿼리 검증.

## 8. 성능 메모

- 체크포인트 10s: 실행 세션 N개 × 6회/분 DB 쓰기. 현재 규모(수십 세션)에서 문제없음. 커지면 `PAPER_CHECKPOINT_SECONDS`로 되돌릴 수 있다.
- `/api/prices`는 2s 캐시를 공유하므로 사용자 수와 무관하게 바이낸스 호출은 종목당 2s에 1회.
