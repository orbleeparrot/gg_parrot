# 서버 신호 실행기 + 실봉 페이퍼 — 설계

날짜: 2026-09-22 · 상태: 승인됨 (구현 전)

## 1. 문제

전략 로직이 세 곳에서 다르게 돈다.

| | 백테스트 | 리더보드(페이퍼) | 실행기(실계좌) |
|---|---|---|---|
| 전략 로직(RSI 등) | 엔진 | 같은 엔진 | **없음** |
| 캔들 | 실제 `candle_interval` | **3틱 ≈ 9초 합성봉** | — |

1. **실행기는 지표를 보지 않는다.** [runner/macro_runner.py](../../../runner/macro_runner.py)의 `_should_enter`는 규칙 B(지정가)만
   가격을 보고 나머지는 무조건 진입, `_should_exit`는 `take_profit`이 없으면 `DEFAULT_TP_PCT = 3.0`을 붙인다. RSI(7) 25/75
   매크로가 "켜자마자 사고 +3%에 판다"로 돌았다(2026-09-22 ONEUSDT 실사례). 규칙 C~K 전부 같은 상태.
2. **페이퍼는 봉이 가짜다.** [engine/candles.py](../../../backend/app/engine/candles.py) `CandleAggregatorSim`이 3초 틱 3개를
   1봉으로 뭉친다(`PAPER_CANDLE_TICKS`). `candle_interval: "5m"`이 무시되고 지표가 백테스트보다 훨씬 예민하게 움직인다.

## 2. 목표

전략 판단을 **서버 엔진 한 곳**에 두고, 백테스트·리더보드·실계좌가 **같은 봉·같은 로직**으로 돌게 한다. 실행기는 주문만 넣는
실행자가 된다.

## 3. 사용자 결정

1. 방식: 서버가 신호를 내려주고 실행기는 주문만(A안). 페이퍼 루프에서 공용 드라이버를 뽑아 둘이 함께 쓴다.
2. 구버전 실행기: **즉시 차단** — A/B 외 매크로는 v8 미만 실행기로 시작할 수 없다(426). A/B는 기존 로컬 로직이 맞으므로 허용.
3. 실행기 소스도 이 작업에서 함께 고친다. exe 빌드·서명·태그는 기존 절차(성빈).

범위 밖: `/api/realtrade/bundle`의 다운로드형 `bot.py`(별도 레거시 — 후속), 신호 지연 최소화를 위한 WebSocket, 부분 체결 정밀
재조정, 실행기 UI 개편.

## 4. 구성

```
                 ┌──────────── CandleFeed (종목·간격·시장별 1구독) ────────────┐
                 │  마감봉만 · get_recent_klines · 마감 +2s 폴링 · 웜업 500봉    │
                 └──────┬───────────────────────────────────────┬──────────────┘
                        ▼                                       ▼
   시세 3s ──▶  StrategyDriver(페이퍼 세션)          StrategyDriver(실행기 세션)  ◀── 시세 3s
                 │ Fill → papertrade/체크포인트          │ Fill → RunnerCommand(DB) + 이벤트
                 ▼                                       ▼
              리더보드                              heartbeat 응답 commands[] → 실행기 주문 → acks[]
```

### 4.1 `engine/candle_feed.py` — 마감봉 피드

- `CandleFeed.subscribe(symbol, interval, market, callback) -> Subscription`, `unsubscribe(sub)`.
  키 `(symbol, interval, market)`마다 asyncio 태스크 1개. 구독자가 0이 되면 태스크 종료.
- 루프: 다음 봉 마감 시각(`_INTERVAL_MS` 경계) + `CANDLE_GRACE_SECONDS`(기본 2)까지 잠들고, `get_recent_klines(symbol, interval,
  limit=3, market)`을 스레드에서 호출해 `closed=True`이고 `t > last_delivered_t`인 봉을 오름차순으로 콜백에 전달. 아직 새 마감봉이
  없으면 2초 간격 재시도, 최대 `CANDLE_RETRY_SECONDS`(기본 30) 후 다음 경계까지 대기(그 봉은 다음 라운드에 `limit=3`으로 따라잡음).
- `history(symbol, interval, market, n) -> list[Candle]`: 마감봉 최근 n개(웜업용). `n ≤ 1000`.
- 콜백은 async 함수. 예외는 로그만 남기고 피드는 계속 돈다. 콜백 1개 실패가 다른 구독자에게 번지지 않는다.
- `Candle = (t_ms, o, h, l, c)` 네임드튜플.

### 4.2 `engine/candles.py` — 실봉 어댑터·웜업

- `CandleAggregatorSim` 제거 → `LiveCandleSim`:
  - `step(price, ts)`: 전략을 돌리지 **않는다**. 큐에 있는 Fill 하나를 꺼내 준다(없으면 None). 페이퍼 루프의 3초 틱과 호환.
  - `on_candle(o, h, l, c, ts)`: `inner.on_candle(...)`의 Fill들을 큐에 넣는다. 피드 콜백이 부른다.
  - `equity/state/restore`는 `inner`에 위임(기존 `CandleAggregatorSim`과 같음).
- `CandleSim.warmup(candles)`: 봉을 순서대로 `on_candle`에 넣어 지표·전략 내부 상태를 채운 뒤 `reset_book()`으로 장부를 되돌린다.
  - `reset_book()`(base): `cash=initial_capital`, `lots=[]`, `closed_trades=[]`, `same_bar_sl=0`, `liquidations=0`,
    `liquidated_loss=0.0`, `_day=None`, `_day_start_equity=initial`, `_halted_day=None`, `_cooldown_until=None`,
    `_entry_time=None`, `stopped=False`, 그리고 `self._reset_position_state()` 호출.
  - `_reset_position_state()`: 포지션에 종속된 전략 상태를 초기화(기본 no-op). Trailing(`_peak/_armed/_ref`), Grid(격자·미체결 레벨),
    Martingale(안전주문 단계), SAR(국면), Breakout(당일 진입 플래그) 등 **포지션이 있어야 의미 있는 값**만 되돌린다. 지표
    상태(RSI/BB/MA, 전일 레인지)는 남긴다.
- `make_sim(macro, initial_capital)`(stepper.py): 캔들형이면 `LiveCandleSim`을 돌려준다. `PAPER_CANDLE_TICKS` 환경변수 제거.

### 4.3 `engine/driver.py` — 공용 전략 드라이버

페이퍼 `_Leg/_Runner`의 시뮬레이션 부분을 옮긴다. DB·리더보드·명령 변환은 모른다.

```python
class Leg:  symbol, sim, initial, last_price, equity, ret, liquidations, liquidated_loss
class StrategyDriver:
    def __init__(self, macro: Macro, initial: float, *, symbols: list[str])
    legs: list[Leg]; initial; equity; ret; last_price; trade_count; last_fill; entry_returns
    def tick(self, symbol, price, ts) -> Optional[Fill]        # = 기존 paper._tick + _note_fill
    async def push_candle(self, symbol, candle) -> None          # LiveCandleSim.on_candle 위임(캔들형만)
    def candle_keys(self) -> list[tuple[symbol, interval, market]]   # 캔들형이면 레그별 키, 아니면 []
    def state(self) -> dict                                      # = 기존 paper._state_view
    def restore(self, state: dict, trades: list[dict]) -> None   # = 기존 _rebuild_runner 의 sim.restore 부분
    def warmup(self, candles_by_symbol: dict[str, list[Candle]]) -> None
```

- `paper.py`는 `_Runner`가 `driver: StrategyDriver`를 갖고 `_tick/_aggregate/_note_fill/_state_view/_rebuild_runner`를 드라이버
  호출로 바꾼다. 세션 생성·체크포인트·papertrade 기록·리더보드용 `recent`는 그대로 `paper.py`에 남는다.
- 페이퍼 세션 시작/복구: 캔들형이면 `feed.history(…, 500)`으로 `warmup` 후 `feed.subscribe`. 종료 시 `unsubscribe`.
  `WARMUP_CANDLES = 500`(MA `slow_period ≤ 400` 커버).
- `_run_loop`의 3초 틱은 유지 — 틱형(A/B/C)은 전략 스텝, 캔들형은 시세 갱신 + 큐 드레인.

### 4.4 실행기 명령 (`backend/app/runner_engine.py`)

실행기 세션마다 `StrategyDriver` 하나를 돌리고 Fill을 명령으로 바꾼다.

**DB**

- `RunSession.state_json: str = ""` — 드라이버 상태(페이퍼와 같은 형식). 체크포인트 10초(`PAPER_CHECKPOINT_SECONDS` 공유).
- 새 테이블 `RunnerCommand`: `id`, `session_id(index)`, `seq`(세션 내 1부터), `action`(`buy|sell|short|cover`),
  `notional_frac: float`(진입: 초기자본 대비 주문 금액 비율), `qty_frac: float`(청산: 보유 수량 대비 비율, 1.0=전량),
  `signal_price`, `reason`(사람이 읽는 한 줄, 예: `RSI 23.1 ≤ 25 · 진입`), `created_at`, `expires_at`,
  `status`(`pending|acked|failed|expired`), `acked_at`, `executed_qty`, `fill_price`, `error`.
- Supabase 마이그레이션 `20260922120000_runner_commands.sql` + sqlite `_migrate` + `_PG_ADDED_COLUMNS["runsession"]`.

**Fill → 명령 변환**

- 진입(`buy`/`short`): `notional_frac = fill.qty * fill.price / driver.initial`.
- 청산(`sell`/`cover`): `qty_frac = fill.qty / (sim이 Fill 직전 보유한 수량)`; 전량이면 1.0. 드라이버가 Fill 직전 수량을 함께 넘긴다.
- `reason`은 sim이 Fill에 실어 준다(`Fill.reason: str = ""` 필드 추가; 각 sim의 `_open_long/_close_all` 호출부가 채운다. 채우지
  않으면 `"진입"`/`"청산"`/`"손절"`/`"강제 청산"` 기본값).
- `expires_at = created_at + COMMAND_TTL_SECONDS`(기본 90). 만료된 pending 명령은 heartbeat에서 `expired`로 바꾸고 보내지 않는다 —
  10분 전 진입 신호를 뒤늦게 실행하면 안 된다.

**heartbeat 프로토콜 (v8)**

요청(`RunnerHeartbeatRequest`)에 추가:
```json
"acks": [{"command_id": 12, "ok": true, "executed_qty": 0.5, "fill_price": 0.0123, "error": ""}],
"protocol": 2
```
응답에 추가:
```json
"commands": [{"id": 13, "seq": 4, "action": "sell", "qty_frac": 1.0, "notional_frac": 0.0,
              "signal_price": 0.0127, "reason": "RSI 76.2 ≥ 75 · 청산", "expires_at": "…"}]
```
- 서버: `acks` 처리(status·executed_qty·fill_price·error 저장, 세션 이벤트 `order` 기록) → 만료 처리 → `pending`을 `seq` 순으로 응답.
- 실행기: `commands`를 순서대로 실행, 결과를 **다음 heartbeat**의 `acks`로 보고. 같은 `id`를 두 번 받으면(ack가 유실된 경우) 실행하지 않고
  다시 ack만 한다(실행기 메모리에 최근 ack 200개 유지).
- 청산 실패(`ok=false`)는 세션 `note`를 `"청산 실패 — 확인 필요"`로 두고 같은 명령을 `pending`으로 되돌려 다음 heartbeat에 재전송,
  최대 3회. 3회 실패면 `failed`로 두고 알림(`notifications.notify` agent).
- 진입 실패는 `failed`로 끝낸다. sim은 가상 포지션을 갖고 실행기는 비어 있으므로, 다음 청산 명령은 실행기에서 `held_qty == 0`이라
  주문 없이 `ok=true, executed_qty=0`으로 ack된다. 세션 이벤트에 `⚠ 진입 실패 — 이번 사이클은 건너뜁니다`를 남긴다.
- 불일치 감지: heartbeat의 `in_position`과 드라이버 `state()["in_position"]`이 다르면(명령 pending이 없을 때) 이벤트 `warn` 1회
  기록(연속 중복 방지).

**세션 수명**

- `start_session`: 실행기 `protocol ≥ 2`(=v8)이면 `macro` 필수(없으면 422), 드라이버 생성·웜업·피드 구독·`_drivers[session_id]`.
  `protocol < 2`이고 `rule_type ∉ {A, B}`이면 **426** `"지표형 매크로는 실행기 v8 이상이 필요해요. 실행기를 업데이트해 주세요."`
  (`_RUNNER_SIGNAL_MIN_VERSION = "8"`, 환경변수 `RUNNER_SIGNAL_MIN_VERSION`). `launch-tickets/claim`도 같은 규칙으로 거절해 웹
  화면이 업데이트를 안내할 수 있게 한다.
- `heartbeat`: 위 프로토콜. 세션의 마지막 heartbeat가 `COMMAND_TTL_SECONDS`보다 오래됐어도 드라이버는 계속 돈다(리더보드처럼 상태는
  이어짐). 명령은 만료로 정리된다.
- `mark_stopped`/`request_stop(close_and_stop)`: 드라이버 정지·피드 해제. `close_and_stop`은 기존처럼 실행기가 로컬에서 전량 청산.
- 기동 시 `resume_running_runner_sessions()`: `RunSession.status == "running"`이고 `protocol ≥ 2`인 세션을 `state_json`으로 복구 +
  웜업 + 구독(페이퍼 `resume_running_sessions`와 같은 패턴, `main.py` lifespan에 추가). 구버전 세션(`state_json == ""`)은 건드리지
  않는다(실행기 로컬 로직이 계속 돈다).

### 4.5 실행기 (`runner/macro_runner.py`, v8)

- `RUNNER_VERSION = "8"`, heartbeat에 `protocol: 2`, `acks` 동봉. start payload에 `macro`는 이미 보낸다.
- 삭제: `_should_enter`, `_should_exit`, `_was_stop_exit`, `DEFAULT_TP_PCT`, `_strategy_targets`의 `tp/buy_price/sell_price`.
- `BotThread.run` 루프:
  1. 종료 명령 확인(기존)
  2. 시세(기존)
  3. **로컬 안전망만** 평가: `RiskGuard.force_close`(일일 손실·최대 보유시간) + `stop_loss_pct`. 서버와 끊겨도 손절은 된다.
     서버 손절 명령이 뒤에 와도 `held_qty == 0`이면 주문 없이 ack.
  4. heartbeat → 응답 `commands` 순서대로 실행:
     - `buy`/`short`: `budget = capital(initial_capital) or MAX_ORDER_USDT`, `notional = min(budget * notional_frac, MAX_ORDER_USDT)`
       → `_order_qty` → `_place`. 이미 포지션이 있으면 **추가 진입**(held_qty·entry_price 가중평균 갱신 — 그리드·마틴게일).
     - `sell`/`cover`: `qty = _round_step(held_qty * qty_frac, step)`; `qty_frac ≥ 0.999`면 `_close_position()`.
     - 결과를 `pending_acks`에 쌓아 다음 heartbeat에 실어 보낸다.
  5. 스냅샷(기존)
- 로그: `[신호] RSI 23.1 ≤ 25 · 진입 → BUY 120 ONEUSDT`처럼 `reason`을 그대로 보여 준다.
- 서버와 통신 실패 시: 명령이 없으니 진입은 멈추고(안전), 청산은 로컬 안전망만. 로그에 `서버 연결 재시도 중 — 신호 대기` 1회.
- 테스트(`runner/test_macro_runner_*.py`): 명령 실행·중복 id 무시·ack 축적·부분 청산 수량·추가 진입 평균가.

### 4.6 프론트

- 실행기 다운로드/안내 문구의 버전을 v8로. 마이페이지 세션 이벤트는 기존 렌더러가 `signal`/`order`/`warn` kind를 그대로 보여 준다
  (아이콘 매핑 3개 추가).
- 426 메시지는 기존 티켓 상태 폴링이 이미 표시한다.

## 5. 봉 타이밍과 지연

봉 마감 → 피드 폴링(+2s) → sim → 명령 DB → 다음 heartbeat(≤5s) → 주문. 전체 **약 3~8초**. 백테스트는 마감가에 체결하므로 실계좌는
그만큼 슬리피지가 더 있다 — 매크로의 `slippage_pct`가 그걸 흡수하는 값이다. 리더보드도 같은 봉으로 돌아가므로 페이퍼 수익률이
백테스트 곡선과 같은 리듬으로 움직인다.

## 6. 테스트

백엔드 `backend/tests/`
- `test_candle_feed.py`: 경계 계산, 마감봉만 전달, 재시도 후 따라잡기, 구독 0이면 태스크 종료, 콜백 예외 격리, `history`.
- `test_candles_warmup.py`: `warmup` 후 지표 상태는 남고 장부는 초기(각 캔들형 sim 1케이스씩), `LiveCandleSim.step`은 전략을 안 돌림,
  `on_candle` → 큐 → `step` 드레인.
- `test_driver.py`: 틱형/캔들형 tick·push_candle, `state/restore` 왕복, `trade_count/last_fill/entry_returns`가 기존 paper 테스트와 같음.
- `test_leaderboard_state.py` 기존 케이스 전부 통과(`CandleAggregatorSim` 참조는 `LiveCandleSim`으로 교체).
- `test_runner_engine.py`: Fill→명령 변환(진입 `notional_frac`, 부분/전량 청산 `qty_frac`), 만료, ack 처리(ok/실패/재전송 3회/중복),
  구버전+지표형 426, 구버전+A/B 허용, v8+macro 없음 422, `resume_running_runner_sessions` 복구, 불일치 warn 1회.
- `test_startup_initialization.py`: lifespan이 두 resume을 모두 부른다.

실행기 `runner/`: §4.5.

## 7. 배포 메모

- 순서: (1) 서버 배포 — 구버전 실행기 A/B는 그대로, 지표형은 426. (2) v8 exe 빌드·서명·태그. (3) 다운로드 URL·`RUNNER_EXE_VERSION=8`.
- 마이그레이션: `runsession.state_json`, `runnercommand` 테이블. 되돌리기: 컬럼·테이블은 그대로 두어도 무해.
- 환경변수(모두 기본값으로 충분): `CANDLE_GRACE_SECONDS=2`, `CANDLE_RETRY_SECONDS=30`, `COMMAND_TTL_SECONDS=90`,
  `RUNNER_SIGNAL_MIN_VERSION=8`. `PAPER_CANDLE_TICKS` 제거.
- 바이낸스 호출: 피드는 (종목·간격·시장)당 봉마다 1회 + 시작 시 웜업 1회. 현재 규모에서 무시할 수준.
- 기존 실행 중인 페이퍼 세션(캔들형)은 재배포 복구 때 웜업을 거쳐 실봉으로 넘어간다. 그동안 쌓인 가상 포지션은 `state_json`으로
  이어진다.
