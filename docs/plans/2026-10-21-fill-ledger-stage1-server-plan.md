# 체결 기준 장부 1단계 — 서버 구현 계획(마틴게일 H)

작성 2026-10-21 · 설계 [2026-10-21-fill-ledger-limit-orders-design.md](2026-10-21-fill-ledger-limit-orders-design.md) §5 · §9 · 짝 [실행기 계획](2026-10-21-fill-ledger-stage1-runner-plan.md)
작업 폴더 `worktrees/gg_parrot/fill-ledger` · 브랜치 `feat/fill-ledger-limit-orders`

## 범위

- 대상: **마틴게일(H) · 롱 · 현물 · 레버리지 1** 이고 **실행기 연동 레벨 21 이상(0.21.0+)으로 새로 시작한** 세션. 이때만 `exec_policy = "fill_ledger_v1"` 을 저장한다.
- 그 밖(다른 규칙 · 레벨 20 이하 · 진행 중인 세션 321 등)은 지금 경로 그대로다. D · E · I 확장은 H 검증 뒤 별도 계획.
- 배포: 서버는 잠든 채로 먼저 나갈 수 있다(레벨 21 실행기가 없으면 정책이 붙는 세션이 생기지 않는다). 실제로 켜지는 때는 실행기 0.21.0(1 · 2단계 실행기 기능을 함께 실은 판) 공개 뒤. 병합 · 배포는 '배포해' 때만.
- 끄는 스위치: `RUNNER_FILL_LEDGER_MIN_VERSION=999` → 새 세션에 정책이 붙지 않는다(이미 붙은 세션은 그대로 — 결정 4).

## 구현 방식(이 계획에서 정함)

1. **진입**은 지금처럼 신호 때 가상 롯을 넣어 돈을 잡아 둔다(전체 예산에 미체결 예약이 들어간다 — 결정 1). 체결 보고가 오면 그 주문의 가상 롯을 실제 체결(수량 · 평균가 · 수수료)로 바꾼다. 0 체결이면 롯을 뺀다.
2. **청산**은 신호 때 장부를 바꾸지 않는다 — '청산 대기'. 보고된 체결량만큼 줄이고, 다 팔리면(먼지만 남으면) 그때 포지션 끝 처리(차수 초기화 · 손절 대기)를 한다. 0 체결로 끝나면 대기만 푼다.
3. **미결 주문이 있는 동안 전략은 판단하지 않는다.** 그동안 닫힌 봉은 가장 최근 하나만 들고 있다가 주문이 정리되면 그 봉으로 판단한다(재기동 되살리기와 같은 '가장 최근 하나' 규칙). 그래서 결정 2(매수 정리 → 청산 수량 확정)가 1단계에서는 구조로 지켜진다.
4. **체결 기록표 `runnerfill` 이 원장이다.** 하트비트가 ack · 로컬 체결을 이 표에 넣고(세션 · 주문 키 유일 → 한 번만), 서버 전략 루프가 틱마다 아직 안 접은 행을 장부에 접는다. 접은 키는 state_json 에 함께 저장된다 → 서버 재기동 · 저장 실패 · 중복 보고 · 응답 유실 어디서도 한 번만 반영된다.
   - 하트비트는 루프 스레드를 기다리지 않는다(지금 되돌리기의 `_run_on_loop` 같은 대기 없음). 반영은 다음 틱(≤ 3초).
5. **백테스트 · 모의 · 옛 세션은 플래그가 꺼진 채라 한 줄도 다르게 돌지 않는다.** 과제 1의 고정 시험이 지킨다.

## 실행기 ↔ 서버 약속(레벨 21)

ack 에 `orders` — 이 명령으로 낸 주문마다 하나(보통 0~1개):

```json
{"command_id": 41, "ok": true, "executed_qty": 11.7, "fill_price": 427.0, "outcome": "execution_reported",
 "orders": [{"client_order_id": "ggp-…", "exchange_order_id": "…", "status": "FILLED", "side": "buy",
             "executed_qty": 11.7, "acquired_qty": 11.7, "fill_price": 427.0,
             "fee_quote": 2.49, "fee_known": true, "ts_ms": 1761000000000}]}
```

하트비트 본문에 `local_fills` — 서버 명령 없이 실행기가 낸 주문(손절 · 안전 규칙 정리):

```json
"local_fills": [{"client_order_id": "ggp-…", "exchange_order_id": "…", "status": "FILLED", "side": "sell",
                 "executed_qty": 11.7, "acquired_qty": 11.7, "fill_price": 405.0,
                 "fee_quote": 2.37, "fee_known": true, "reason": "stop_loss", "ts_ms": 1761000300000}]
```

- 키는 `client_order_id`(실행기가 주문마다 새로 만드는 `ggp-…`). 체결 0 인 주문은 장부에 영향이 없어 넣지 않는다.
- 한 본문 안에서 ack 는 모두 local_fills 보다 먼저 일어난 일이다 — 실행기는 하트비트가 성공해야 명령을 실행하고, 실패하면 둘 다 그대로 다시 보낸다(`server.rs` `unsent_acks`). 서버는 ack 를 먼저, local_fills 를 나중에 넣는다.
- `fee_quote` 는 견적 통화로 옮긴 수수료 전부(기초자산 수수료 포함). `acquired_qty < executed_qty` 면 그 차이가 기초자산 수수료다 → 현금에서 뺄 견적 통화 수수료 = `fee_quote − (executed − acquired) × fill_price`.

## 장부 상태(state_json, 정책 세션만)

`legs[0].strategy.ledger`:

```json
{"v": 1, "seq": 17, "epoch": 3,
 "lots": [{"ref": "o15", "qty": 11.7, "fill": 427.0, "margin": 4995.9}],
 "orders": {"o17": {"side": "sell", "kind": "tp", "epoch": 3, "qty": 11.7, "price": 435.54,
                     "is_stop": false, "signal_ms": 1761000900000}},
 "exit": "o17", "dust": null, "deferred": null,
 "keys": ["ggp-…"], "recent": {"o16": {"kind": "so", "tier": 1, "epoch": 3}}}
```

- `lots` 는 롯을 하나씩 그대로 저장한다 — `CandleSim.restore()` 가 평단 하나로 합친 뒤 `restore_strategy_lots` 에서 다시 깐다(그리드 D 와 같은 자리). 현금은 합친 롯과 같게 나온다(Σ수량×체결가 · Σ증거금이 같다).
- `epoch` 는 포지션이 끝날 때마다 1 오른다. 이전 포지션 주문의 늦은 정리는 장부를 고치지 않는다(뒷정리만).
- `keys` 는 접은 체결 키 최근 200개, `recent` 는 정리된 주문 최근 20개(늦은 체결의 차수 찾기용).
- H 는 strategy 에 `reopen: {"2": {"line": 409.92, "remaining": 1800.0}}`(잔예산이 남은 차수)를 더한다.

## 파일 배치

| 파일 | 새/고침 | 맡는 일 |
|---|---|---|
| `backend/app/engine/fill_ledger.py` | 새 | 장부 자료(주문 번호 · 미결 주문 · 접은 키 · 먼지 · 보류 봉 · epoch · 체크포인트 칸)와 체결 보고 한 건의 계산(보유 증가 · 현금 유출 · 수수료). DB 를 모른다 |
| `backend/app/engine/candles.py` | 고침 | 장부를 바꾸는 일은 기존 장부 메서드(`_open_long` · `_close_all`) 옆에: CandleSim 정책 켜기 · `fold` · 진입 체결로 고치기 · 청산 대기 · 체결만큼 줄이기 · 판단 보류 · 포지션 끝 훅 `_after_close` · 먼지 포함 자산. MartingaleSim: 차수 잔예산 · 실제 최초 매수가. LiveCandleSim: 접기 위임 |
| `backend/app/engine/stepper.py` | 고침 | `Fill.order_ref`(기본 "") |
| `backend/app/engine/driver.py` | 고침 | `enable_fill_ledger()` · `fold(fills, resolutions)` |
| `backend/app/runner_fills.py` | 새 | 체결 기록표 읽기 · 쓰기: ack · 로컬 체결 넣기(한 번만) · 명령 누적 체결 · 접을 행 읽기 · 접은 결과 저장 |
| `backend/app/runner_engine.py` | 고침 | 정책 읽어 드라이버에 켜기 · `order_ref` 저장 · 정책 세션의 ack · 취소 · 틱 접기 · 불일치 경고 보류 |
| `backend/app/runner.py` | 고침 | `FILL_LEDGER_MIN_VERSION` · 정책 정하기 · 하트비트 `local_fills` 넘기기 · 세션 정리 때 체결 행 삭제 |
| `backend/app/db.py` | 고침 | `RunSession.exec_policy` · `RunnerCommand.order_ref` · `RunnerFill` · 기동 보정 |
| `supabase/migrations/20261021120000_fill_ledger_v1.sql` | 새 | 위 세 가지(병합 전에 운영 적용) |

runner_engine.py 가 이미 1,267줄이라 기록표 읽기 · 쓰기는 `runner_fills.py` 로 뗀다. 장부 계산은 엔진 패키지 안(`fill_ledger.py`)에 둬 DB 없이 시험한다.

## 시험 실행

```
cd backend
env -u DATABASE_URL NEWS_IMAGES_DISABLED=1 /data/aires/playground/clcleh123/miniconda3/envs/gg_parrot/bin/python -m pytest tests/<파일> -q
```

과제마다 끝에 백엔드 전체 시험을 한 번 돌린다(옛 경로 회귀 확인). 참고 시험: `tests/test_runner_entry_rollback.py`(마틴게일 · 그리드 매크로 · `_driver`), `tests/test_runner_engine.py`(`_session` · `_RsiFeed` · `_no_loop` · `_tick_once` 직접 호출).

---

## 과제 1 — 백테스트 결과 고정(안전망 먼저)

파일: `backend/tests/test_fill_ledger_golden.py`, `backend/tests/fixtures/fill_ledger_golden.json`

1. 시험 안에 결정적 봉 생성기를 쓴다 — 1시간 봉 720개: 완만한 상승 → 급락 −18% → 반등 → 횡보, 봉마다 위아래 꼬리(난수 없이 sin · 고정 패턴).
2. 매크로 7개: H 셋(① dev 1% · 최대 3차 · 익절 2% ② vol_scale 1.5 · step_scale 1.2 · 손절 5% · 쿨다운 30분 ③ 최대 보유 24시간 · 일일 손실 3%), D 그리드 · E 트레일링 · I 돌파 · F RSI 하나씩(CandleSim 공용 코드를 고치므로).
3. `make_candle_sim(macro)` 에 봉을 넣고 매 봉 `on_candle` 이 돌려준 Fill 전부(side · price · qty · reason, 유효숫자 12자리) · `closed_trades` · `same_bar_sl` · 마지막 자산을 모은다. `GOLDEN_WRITE=1` 이면 fixture 에 쓰고, 아니면 대조한다.
4. 지금 코드에서 `GOLDEN_WRITE=1` 로 fixture 를 만든 뒤, 플래그 없이 통과를 확인한다.
5. 커밋.

완료: 지금 코드에서 통과. 과제 4~7 은 이 시험을 깨면 안 된다 — fixture 를 다시 쓰지 말고 원인을 찾는다.

## 과제 2 — 스키마: exec_policy · order_ref · runnerfill

파일: `backend/app/db.py`, `supabase/migrations/20261021120000_fill_ledger_v1.sql`, `backend/tests/test_fill_ledger_schema.py`(새), `backend/tests/test_db_startup_migrations.py`(늘림), `backend/app/runner.py`(`_delete_session_rows` · `prune_ended_sessions_batch`)

1. 실패하는 시험: 새 sqlite DB 에 `runsession.exec_policy` · `runnercommand.order_ref` · `runnerfill` 이 있다 / 옛 스키마에 기동 보정이 두 칸을 더하고 표를 만든다 / `(session_id, fill_key)` 유일이라 같은 키 두 번째 넣기가 막힌다 / 세션 정리 뒤 그 세션의 runnerfill 행이 0개.
2. 돌려 실패를 확인한다.
3. 모델: `RunSession.exec_policy: str = ""`, `RunnerCommand.order_ref: str = ""`, `RunnerFill`(id · session_id · command_id(null 가능) · fill_key · exchange_order_id · source(command|local) · side · status · executed_qty · acquired_qty · price · fee_quote · fee_known · reason · runner_ts_ms · created_ms · applied_ms(0 = 아직 안 접음)), 색인 `ux_runnerfill_session_key(session_id, fill_key)` · `ix_runnerfill_session_applied(session_id, applied_ms, id)`.
4. 기동 보정(`_PG_ADDED_COLUMNS` 와 runnercommand 추가 줄 근처)에 두 칸을 `TEXT NOT NULL DEFAULT ''` 로 더한다.
5. 마이그레이션: alter 두 개 + create table + 색인 둘 + RLS 켜기 · 표 · 시퀀스 권한 회수(`20261017120000_agent_commentary.sql` 꼴).
6. `_delete_session_rows` 가 runnerfill 도 지운다.
7. 통과 확인 → 커밋.

완료: 시험 통과, 마이그레이션이 모델과 같은 정의. 운영 적용은 병합 직전(`~/.local/share/supabase/supabase db push --db-url … --dry-run` → `--yes`, 작업 폴더에서).

## 과제 3 — 실행 방식 정하기(exec_policy)

파일: `backend/app/runner.py`(start_session · 새 함수), `backend/app/runner_engine.py`(`_load_session` · `_start_driver`), `backend/app/engine/driver.py`(칸만), `backend/tests/test_fill_ledger_policy.py`

1. 실패하는 시험:
   - 레벨 21 + H 롱 현물 레버리지 1 → 새 세션 `exec_policy == "fill_ledger_v1"`.
   - 레벨 20 + H → "" · 레벨 21 + F(RSI) → "" · 레벨 21 + H 숏 또는 선물 → "" · 여러 종목 → "".
   - `RUNNER_FILL_LEDGER_MIN_VERSION=999` → 레벨 21 이어도 "".
   - 정책 세션은 `start_driver` 뒤 `driver.fill_ledger is True`, 아닌 세션은 False — `resume_running_runner_sessions` 로 되살려도 같다.
   - heartbeat 는 정책 · runner_version 을 바꾸지 않는다.
2. 실패 확인.
3. `FILL_LEDGER_MIN_VERSION`(환경변수, 기본 "21") · `fill_ledger_policy(macro, runner_version) -> str` 를 `supports_signals` 옆에 둔다. start_session 이 `RunSession(exec_policy=…)` 로 저장한다.
4. `_load_session` 이 `exec_policy` 를 돌려주고, `_start_driver` 가 정책이면 `driver.enable_fill_ledger()` 를 부른다. 이 과제에서 `enable_fill_ledger` 는 `self.fill_ledger = True` 만 세운다(장부는 과제 4).
5. 통과 → 커밋.

완료: 시험 통과 + 백엔드 전체 시험 통과(옛 세션 동작 그대로).

## 과제 4 — 주문 번호와 진입 체결 반영

파일: `backend/app/engine/fill_ledger.py`(새), `backend/app/engine/stepper.py`, `backend/app/engine/candles.py`, `backend/app/engine/driver.py`, `backend/app/runner_engine.py`(`_persist_fill` · `upsert_command`), `backend/tests/test_fill_ledger_entries.py`

동작:
- 정책 sim 은 진입 · 청산 신호마다 주문 번호 `o<n>` 을 매기고 `Fill.order_ref` 로 싣는다. 진입 롯에 같은 번호를 붙이고, 미결 주문에 `{side, kind(base|so), tier, epoch, qty, price, margin, fee}`(가상 값)를 남긴다.
- `driver.fold(fills=[…])` 의 매수 체결 → 그 번호의 가상 롯을 실제로 바꾼다: 수량 = acquired, 체결가 = 가중평균가, 증거금 = 수량 × 체결가, 현금 += 가상(증거금 + 수수료) − 실제(체결 금액 + 견적 통화 수수료). 기본 매수면 `_base_price` = 실제 체결가.
- 명령 행에 `order_ref` 를 저장한다. `upsert_command` 의 '같은 신호 중복' 판정은 둘 다 order_ref 가 있으면 order_ref 까지 같을 때만 — 잔예산 재매수가 같은 가격선 · 60초 안에 나와도 중복으로 삼키지 않게(삼키면 주문이 영영 미결로 남아 전략이 멈춘다).

1. 실패하는 시험:
   - 정책 H: 종가 426 기본 매수 → `Fill.order_ref == "o1"`, 명령 행 `order_ref == "o1"`.
   - 매수 체결 427 · 11.7개 · 수수료 2.49원을 접으면 → 평단 427, `_base_price` 427, 현금 = 시작 − 427×11.7 − 2.49, 익절선 427×1.02(설계 5절 '가상 426 → 실제 427').
   - 같은 키를 두 번 접어도 한 번.
   - 기초자산 수수료(acquired < executed): 보유 = acquired, 자산 = 현금 + acquired × 가격.
   - `state()` → 새 드라이버 `restore()` → 롯 · 미결 주문 · 키가 그대로, 같은 키를 다시 접어도 그대로.
   - 같은 가격선 · 60초 안 · order_ref 가 다른 두 매수 명령은 둘 다 생긴다. order_ref 없는 옛 명령의 중복 판정은 그대로.
   - 정책 꺼진 H: `order_ref == ""`, state_json 모양이 지금과 같다.
2. 실패 확인.
3. `fill_ledger.py`: `Ledger`(자료 · `to_state` · `from_state`) · `correct_entry(sim, order, fills)`.
4. `stepper.Fill` 에 `order_ref: str = ""`. CandleSim 에 `enable_fill_ledger()` · `_open_long` 의 번호 매기기 · `strategy_state`/`restore_strategy_lots` 의 ledger 저장 · 복원(MartingaleSim). `LiveCandleSim` 위임.
5. `StrategyDriver.enable_fill_ledger()` 가 H 가 아니면 ValueError(다른 규칙은 아직 손대지 않는다는 표시). `fold()` 는 sim 에 넘기고 요약(접은 키 · 정리한 번호 · 사람이 읽을 이벤트)을 돌려준다.
6. `_persist_fill` · `upsert_command` 고침.
7. 통과 + 과제 1 고정 시험 통과 → 커밋.

완료: 시험 · 고정 시험 · 백엔드 전체 통과.

## 과제 5 — 진입 0 체결 · 부분 체결(결정 1 차수 잔예산)

파일: `backend/app/engine/fill_ledger.py`, `backend/app/engine/candles.py`(MartingaleSim), `backend/tests/test_fill_ledger_entries.py`

`fold(resolutions=[{ref, status, hold_until_ms}])` — status 는 명령의 끝 상태(acked · failed · expired · cancelled). 그 명령의 체결은 resolutions 보다 먼저 접는다.

- 기본 매수 0 체결 → 롯을 빼 무포지션, `_base_price` 0, 진입 대기 `hold_until_ms`(failed · expired 때만).
- 물타기 0 체결 → 롯을 빼고 그 차수를 다시 연다(남은 예산 = 배정 예산, 가격선 그대로). failed · expired 면 진입 대기를 건다. cancelled(로컬 청산 · 정리로 취소)면 다시 열지 않는다.
- 물타기 부분 체결 → 잔예산 = 배정 예산 − 체결 금액 − 견적 통화 수수료. `min_order_floor(거래소, 현물)` 이상이면 그 가격선에 다시 연다, 아니면 그 차수 완료.
- 다시 연 차수는 봉 저가가 **그 가격선 이하일 때만** 잔예산만큼 산다(같은 차수 번호로 새 주문). 위면 아무것도 안 한다. 뒤 차수는 `_so_done` 대로 따로 간다(앞 차수가 덜 찼다고 미루지 않는다).
- 이전 epoch 주문의 정리는 롯 · 차수를 건드리지 않는다. 정리된 주문의 늦은 매수 체결은 실제 롯으로 더하고, `recent` 의 차수가 다시 열려 있으면 그 잔예산에서 뺀다.

1. 실패하는 시험: 위 다섯 줄마다 하나 + '미결 주문 금액이 현금에서 빠져 있어 같은 봉의 다음 차수가 그만큼 덜 산다' + '포지션이 끝나면 다시 연 차수가 사라진다'.
2. 실패 확인.
3. 구현(`MartingaleSim._strategy` 의 물타기 고리 앞에 다시 연 차수 확인 · `_entry_resolved` 훅).
4. 통과 + 고정 시험 → 커밋.

완료: 설계 5절 완료 기준 '물타기 부분 체결 · 무체결 …' 이 시험으로 확인된다.

## 과제 6 — 청산 대기 · 체결만큼 줄이기 · 먼지(결정 2 · 3)

파일: `backend/app/engine/fill_ledger.py`, `backend/app/engine/candles.py`, `backend/tests/test_fill_ledger_exits.py`

- 정책 모드 `_close_all` → 장부는 그대로 두고 청산 주문을 미결로 남긴다(`exit` = 번호). Fill 은 `qty = 보유 전량`, `qty_before = qty`(명령 비율 1.0 — 지금 `_fill_meta` 는 장부가 바뀐 뒤를 가정해 0.5 가 나온다). 청산 대기 중 두 번째 `_close_all` 은 None.
- `_liquidate_all` · `_reduce_long` 은 정책 모드에서 부르면 안 된다(현물 H 는 쓰지 않는다) — 부르면 RuntimeError.
- 매도 체결(ack 든 로컬이든) → 모든 롯을 같은 비율로 줄인다(남은 평단 그대로 — 실행기 장부와 같은 규칙). 현금 += 증거금 몫 + 수량×(체결가 − 롯 체결가) − 수수료, `closed_trades` 에 손익.
- 남은 평가액이 `min_order_floor` 미만(또는 수량 ≤ 1e-12)이면 포지션 끝: 남은 롯은 `dust` 로 옮기고 `_after_close(is_stop)` — H 는 `_so_done = 0` · 다시 연 차수 비움, `_entry_time = None`, 손절이면 `신호 봉 시각 + 쿨다운`, epoch + 1, `exit` 비움.
- 청산 주문이 끝났는데(acked · failed) 거래할 수 있는 보유가 남았으면 `exit` 만 비운다 — 다음 봉에서 다시 판단한다.
- 먼지: `in_position()` 은 False(진입 가능), `equity()` 에는 들어간다. 다음 기본 매수 때 먼지 롯을 롯 목록에 합친다(원가 가중 평균), `_base_price` 는 새 롯의 체결가(평단 아님). 일일 손실 기준 · 쿨다운은 건드리지 않는다.
- 백테스트 모드도 `_close_all` 이 곧바로 `_after_close` 를 부르게 바꾸고, MartingaleSim 의 `self._so_done = 0` 두 줄을 지운다(같은 동작 — 고정 시험이 지킨다).

1. 실패하는 시험:
   - 익절 신호 → 보유 10 그대로 · 청산 대기 · 명령 비율 1.0.
   - 10 중 4 체결 접기 → 보유 6 · 청산 대기 유지(명령이 다시 보내질 때). 명령이 failed 로 끝남 → 대기만 풀리고 보유 6.
   - 10 전량 체결 → 무포지션 · `_so_done` 0 · 실현손익 = 실제 체결가 기준.
   - 먼지(남은 평가 120원) → 무포지션 · 먼지 보유 · 자산에 포함. 다음 기본 매수 → 평단에 합침 · `_base_price` = 새 체결가.
   - 손절 청산 체결 → 쿨다운이 신호 봉 시각 기준. 0 체결로 끝난 손절은 쿨다운 없음.
   - 일일 손실 정지는 먼지 처리로 풀리지 않는다.
   - 정책 모드에서 `_liquidate_all` 호출 → RuntimeError.
2. 실패 확인.
3. 구현.
4. 통과 + 고정 시험 → 커밋.

완료: 설계 5절 '청산 부분 체결 · 무체결 확인 → 복원', '먼지 …' 기준이 시험으로 확인된다.

## 과제 7 — 미결 주문 동안 판단 보류

파일: `backend/app/engine/candles.py`(CandleSim.on_candle · LiveCandleSim), `backend/app/engine/fill_ledger.py`, `backend/app/runner_engine.py`(`_start_driver` 되살리기), `backend/tests/test_fill_ledger_defer.py`

- 정책 모드 `on_candle`: 미결 주문이 있으면 봉을 `deferred` 에 두고 체결 없이 끝낸다. 이미 하나 있으면 그 봉은 판단 없이 진입 필터 갱신만 하고 새 봉으로 바꾼다.
- `fold` 뒤 미결 주문이 없고 `deferred` 가 있으면 그 봉으로 `on_candle` 을 돌린다 — 나온 체결은 LiveCandleSim 큐로(다음 틱이 명령으로 만든다).
- `deferred` 는 ledger 상태에 저장된다. `_start_driver` 되살리기는 같은 시각 봉을 두 번 판단하지 않는다(`_unjudged_last_close` 의 봉과 `deferred` 가 같으면 하나만).

1. 실패하는 시험: 청산 대기 중 봉 → 체결 없음 / 대기가 0 체결로 끝남 → 보류한 봉으로 판단해 익절 신호가 다시 남 / 대기 중 봉 둘 → 마지막 봉만 판단, 필터는 둘 다 갱신 / 보류 봉을 가진 채 재기동 → 한 번만 판단.
2. 실패 확인.
3. 구현.
4. 통과 + 고정 시험 → 커밋.

완료: 설계 5절 '청산 시작 때 물타기 차단 · 매수 정리 뒤 청산 수량 확정(결정 2)' 이 1단계 구조로 지켜짐을 시험이 보인다(미결 매수가 있는 동안 익절 · 손절 판단이 나지 않는다).

## 과제 8 — 체결 기록표에 넣기(한 번만)

파일: `backend/app/runner_fills.py`(새), `backend/app/runner_engine.py`(`apply_acks`), `backend/tests/test_runner_fills_journal.py`

- `record_command_orders(db, row, cmd, ack)`: `ack["orders"]` 중 체결 수량 > 0 인 것을 RunnerFill(source=command)로 넣는다 — 같은 키가 있으면 넣지 않는다. 넣은 뒤 명령의 `executed_qty` · `fill_price` 를 그 명령 체결 행 전부의 합 · 가중평균으로 다시 쓴다(지금은 시도마다 덮어써 부분 청산 재시도의 앞 체결을 잃는다).
- `record_local_fills(db, row, local_fills)`: source=local, command_id 없음.
- 숫자 아님 · 음수 · NaN · 키 없음은 넣지 않고 이벤트로 남긴다.
- `apply_acks`: 정책 세션만 위 함수를 쓴다. 상태 규칙(청산 재시도 · 진입 실패)은 그대로. 취소된 명령에 체결 보고가 오면(늦은 체결) 오류 대신 '서버 장부에 반영해요' 주의 이벤트 — 장부가 그 체결을 접는다.

1. 실패하는 시험: 같은 하트비트 두 번(응답 유실 흉내) → 행 하나 / 부분 청산 재시도(4 → 6) → 행 둘, 명령 체결 10 · 가중평균가 / 보유 없음 ack → 행 없음 / 레벨 20 세션 → 행 없음, 지금처럼 덮어씀 / 늦은 체결 → 주의 이벤트 · 행 하나.
2. 실패 확인.
3. 구현.
4. 통과 → 커밋.

## 과제 9 — 하트비트 흐름(정책 세션)

파일: `backend/app/runner.py`(heartbeat), `backend/app/runner_engine.py`(`exchange_commands` · `check_position_mismatch`), `backend/tests/test_runner_fill_ledger_heartbeat.py`

- heartbeat 가 `snapshot` 에서 `local_fills` 를 꺼내 `exchange_commands` 에 넘긴다(옛 실행기는 안 보낸다 — 빈 목록).
- 정책 세션 `exchange_commands`: ack 반영 → 로컬 체결 넣기 → 로컬 체결이 있으면 대기 중인 진입 명령을 cancelled(그 포지션을 전제로 만든 신호), 실행기가 무포지션으로 보고했으면 대기 중인 청산 명령도 cancelled → 진입 만료 → 보낼 명령. `settle_unexecuted_entries`(옛 되돌리기)는 부르지 않는다.
- `check_position_mismatch` 는 정책 세션에서 안 접은 체결 행이 있거나 ledger 미결 주문이 있으면 건너뛴다(반영 전 잠깐의 차이로 경고하지 않게).

1. 실패하는 시험: 로컬 손절 + 대기 중 익절 · 물타기 → 같은 하트비트에서 둘 다 취소, 응답 명령 없음 / 실행기가 아직 보유(부분 로컬 청산) → 진입만 취소 / 정책 세션은 옛 되돌리기를 부르지 않는다 / 레벨 20 세션은 지금 흐름 그대로(기존 `test_runner_entry_rollback.py` 통과).
2. 실패 확인.
3. 구현.
4. 통과 + 백엔드 전체 → 커밋.

## 과제 10 — 틱마다 접기 · 저장

파일: `backend/app/runner_fills.py`, `backend/app/runner_engine.py`(`_tick_once` · 새 `_fold_once`), `backend/tests/test_runner_fill_ledger_fold.py`

- `load_fold_input(session_id, open_refs)`(워커 스레드): 안 접은 체결 행(applied_ms = 0)을 id 순으로 최대 50개 + 명령의 order_ref · action, 끝 상태가 된 명령 중 order_ref 가 미결 주문에 있는 것. 진입 0 체결 정리에는 `hold_until_ms = 지금 + _retry_minutes(...)분` 을 붙이고 그 명령에 `ROLLED_BACK` 을 남긴다(연속 실패 횟수가 지금처럼 늘어난다).
- 루프 스레드에서 `driver.fold(...)`, 다시 워커에서 `persist_fold(session_id, 접은 행 id, state_json, 이벤트)` — 한 트랜잭션.
- `_tick_once`: 저장 못 한 체결을 먼저 비운 뒤, 시세 틱 전에 `_fold_once`(정책 세션만). 실패하면 기록하고 다음 틱에 다시 — 키가 있어 두 번 접히지 않는다.
- 사람이 읽을 이벤트(문구는 구현 때 원문 용어 그대로 다듬는다):
  - 실제 체결로 맞춤: "실제 체결로 서버 장부를 맞췄어요 · 평단 426 → 427원"
  - 물타기 일부 체결: "물타기 2차가 일부만 체결됐어요 · 남은 예산 1,800원은 409.92원 이하에서만 다시 사요"
  - 먼지: "남은 0.28 GTC(약 120원)는 팔 수 없는 잔량이에요 · 다음 매수 평단에 합쳐요"
  - 청산 무체결: "청산 주문이 체결되지 않아 보유를 그대로 둬요 · 다음 봉에서 다시 판단해요"

1. 실패하는 시험: ack → `_tick_once` → state_json 평단 427 · 행 applied_ms 채워짐 / 저장 실패 → 다음 틱에 한 번만 접힘 / 행을 넣은 뒤 접기 전에 서버 재기동 → 새 드라이버가 한 번 접음 / 접은 상태가 체크포인트로 저장됐는데 applied 표시만 실패 → 다시 접지 않고 표시만 / 드라이버 시작 중 → 행이 기다렸다 접힘.
2. 실패 확인.
3. 구현.
4. 통과 + 백엔드 전체 → 커밋.

## 과제 11 — 서버 장부 · 실행기 수량 대조(경고)

파일: `backend/app/runner_engine.py`, `backend/tests/test_runner_fill_ledger_heartbeat.py`

- 정책 세션 하트비트: 미결 주문 · 안 접은 행이 없으면 서버 보유(먼지 포함)와 실행기 `position_qty` 를 견준다. 차이가 `max(1e-9, 보유 × 0.2%)` 를 넘으면 한 번 경고(`engine.ledger_mismatch` — "서버 장부 수량과 실행기 보유 수량이 달라요 · 서버 11.7 · 실행기 11.2"), 다시 맞으면 다음 불일치에 또 한 번(`_MISMATCH_NOTED` 와 같은 규칙).

1. 실패하는 시험: 맞음 → 경고 없음 / 다름 → 한 번 / 미결 중 → 비교 안 함.
2. 실패 확인 → 구현 → 통과 → 커밋.

## 과제 12 — 끝-끝 시나리오(가짜 실행기)

파일: `backend/tests/test_runner_fill_ledger_flow.py`

start_session(레벨 21) → start_driver(가짜 봉 피드) → 봉 넣기 → `_tick_once` → heartbeat(acks · local_fills) 를 되풀이하는 가짜 실행기로 설계 5절 완료 기준을 그대로 시험한다.

1. 가상 426 → 실제 427, 익절선이 실제 평단 기준.
2. 청산 10 중 4 체결 → 서버 6 · 청산 대기 / 남은 6 체결 → 무포지션. 무체결 확인 → 보유 복원.
3. 물타기 부분 · 무체결 → 차수 잔예산 · 최초 매수가 · 전체 예산.
4. 미결 매수가 있는 동안 익절 판단 없음 → 매수 정리 뒤 판단 · 청산 수량 = 실제 보유.
5. 먼지 → 무포지션 · 먼지 보유 → 다음 진입 평단에 합침, 쿨다운 · 일일 손실 유지.
6. 정책 없는 세션 · 레벨 20 세션은 서버 재기동 뒤에도 옛 경로.
7. 로컬 손절 → 서버 무포지션, 익절 · 재진입 명령 취소.
8. 손절 · 익절 동시 → 먼저 체결된 것만, 나머지 취소.
9. 주문 직후 서버 재기동 · 응답 유실 · 중복 보고 → 한 번 반영, 잔량 일치.
10. 실행기 재시작(새 세션) → 옛 세션 장부는 마지막 보고까지, 새 세션과 섞이지 않는다(키가 세션별).

완료: 열 가지 통과 + 과제 1 고정 시험 + 백엔드 전체 시험.

## 과제 13 — 기록

- `docs/WORK_LOG.md` 한 단락, 설계 문서 상태 줄('1단계 서버 구현 완료').
- 병합할 때 위키: `concepts/` 에 '체결 기준 장부(문제 → 해결)' 한 쪽 + `wiki/log.md` 맨 위 한 줄 + index 등록(병합 뒤에 — 미병합 상태를 사실처럼 적지 않는다).

## 하지 않는 것(1단계)

- 지정가 · IOC(2단계), 거래소 대기 주문(3단계).
- H 외 규칙, 선물 · 숏 H.
- 진행 중인 세션 321 · 레벨 20 세션의 장부 고치기.
- 실행기 앱이 꺼졌다 켜질 때 옛 세션 이어 받기(지금도 새 세션으로 시작한다).
