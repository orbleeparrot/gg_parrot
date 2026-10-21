# 체결 기준 장부 1단계 — 실행기 구현 계획(ggparrot-desktop)

작성 2026-10-21 · 설계 [2026-10-21-fill-ledger-limit-orders-design.md](2026-10-21-fill-ledger-limit-orders-design.md) §5 · §9 · 짝 [서버 계획](2026-10-21-fill-ledger-stage1-server-plan.md)('실행기 ↔ 서버 약속' 절이 이 계획의 인터페이스다)

## 범위

- ack 에 주문 사실(`orders`), 하트비트에 로컬 체결(`local_fills`), 먼지를 다음 진입 평단에 합치기(결정 3).
- **버전은 아직 올리지 않는다.** 0.21.0(연동 레벨 21)은 2단계 실행기 기능(지정가 IOC)까지 끝난 뒤 한 번에 올린다 — 레벨 21 이 '체결 보고 + 지정가' 둘 다를 뜻해야 서버가 레벨 하나로 가른다.
- 새 칸은 옛 서버가 무시한다(서버는 모르는 ack · 본문 칸을 읽지 않는다). 그래서 이 계획의 변경은 레벨 20 서버와도 안전하다.

## 작업 위치 · 시험

- 새 작업 폴더: `git -C /data/aires/playground/clcleh123/worktrees/ggparrot-desktop/main worktree add ../fill-ledger -b feat/fill-ledger-reports origin/main` (main 체크아웃은 다른 세션 브랜치라 건드리지 않는다).
- runner-core 시험만 이 서버에서 돈다: `CARGO_TARGET_DIR=/data/aires/playground/clcleh123/worktrees/ggparrot-desktop/.cargo-target cargo test -p runner-core` — 끝나면 `.cargo-target` 을 지운다. 앱 crate(libdbus)는 PR CI 로 확인.
- 참고 시험: `src-tauri/crates/runner-core/src/bot/tests.rs` 의 `FakeBroker` · `bot_with` · `filled`, 가짜 서버로 도는 `full_loop_executes_server_commands_and_stops_on_remote_request`.

## 파일 배치

| 파일 | 맡는 일 |
|---|---|
| `src-tauri/crates/runner-core/src/bot/orders.rs` | `place()` 가 주문 사실(`OrderFact`)을 남긴다 · 명령 밖 주문이면 로컬 체결 대기열 · `apply_fill` 먼지 합치기 |
| `src-tauri/crates/runner-core/src/bot/commands.rs` | 명령 실행 중 생긴 주문 사실을 ack `orders` 로 |
| `src-tauri/crates/runner-core/src/bot.rs` | Bot 칸: `command_orders` · `pending_local_fills` · `dust_entry_price` |
| `src-tauri/crates/runner-core/src/bot/report.rs`, `src-tauri/crates/runner-core/src/server.rs` | 하트비트 본문 `local_fills`, 실패하면 ack 와 같이 다시 보냄 |
| `src-tauri/crates/runner-core/src/bot/tests.rs` | 시험 |

## 과제 R1 — ack 에 주문 사실 싣기

1. 실패하는 시험(tests.rs):
   - 매수 명령이 체결되면 ack `orders` 에 하나: `client_order_id`(`ggp-` 로 시작) · `exchange_order_id` · `status` · `side` · `executed_qty` · `acquired_qty` · `fill_price` · `fee_quote` · `fee_known` · `ts_ms`.
   - 일부만 체결되고 끝난 매수(`ok=false`, `partially_executed`) → `orders` 에 체결분 하나.
   - 보유 없음 청산 · 중복 명령 · 로컬 리스크 보류 → `orders` 는 빈 목록.
   - 바이낸스 기초자산 수수료(acquired < executed) → 두 값이 따로 실린다.
2. `cargo test` 로 실패 확인.
3. `place()` 가 주문 결과마다 `OrderFact` 를 만든다(값은 지금 `runner.order_result` 원격 기록에 이미 있는 것 + `fee.quote` · `ts_ms`). 수수료를 모르면 `fee_known=false` 에 지금 어림값(`fee_rate_estimate`)을 싣는다.
4. `execute_command` 시작 때 `command_orders` 를 비우고, ack 를 만들 때 그 목록을 `orders` 로 싣는다(`position_uncertain` 으로 끝나는 ack 포함 — run_loop 의 그 경로도).
5. 통과 → 커밋.

완료: 서버 계획 '약속' 절의 ack 예시와 같은 모양이 시험으로 확인된다.

## 과제 R2 — 로컬 체결 보고(local_fills)

1. 실패하는 시험:
   - 로컬 손절(`enforce_safety_net`)로 판 체결 → 다음 하트비트 본문 `local_fills` 에 하나(`reason: "stop_loss"`), 안전 규칙 정리는 `"forced"`.
   - 하트비트 실패 → 다음 본문에 같은 항목이 다시 실린다. 성공 → 비워진다.
   - 한 본문 안의 순서: ack 가 모두 앞, local_fills 가 뒤에 쌓인 일(서버가 이 순서로 넣는다).
   - 서버 명령 실행 중 주문은 local_fills 에 들어가지 않는다.
2. 실패 확인.
3. `place()` 가 명령 밖(`logger.operation()` 이 비었거나 `order:` 로 시작)에서 체결이 있으면 `pending_local_fills` 에 넣는다. 이유는 `enforce_safety_net` 이 정리 전에 남긴다.
4. `heartbeat()` 가 본문에 `local_fills` 를 싣고 실패 · 백오프 · 410 때 `unsent_acks` 와 같은 자리에 보관한다(`unsent_local_fills`).
5. 통과 → 커밋.

완료: 로컬 손절 체결이 응답 유실 · 하트비트 실패에도 다음 성공 때 정확히 한 번 실린다(서버는 키로 한 번만 넣는다).

## 과제 R3 — 먼지를 다음 진입 평단에 합치기(결정 3)

1. 실패하는 시험:
   - 정리 뒤 먼지(`position_dust_qty`)가 남은 상태에서 새 진입 체결 → `held_qty = 먼지 + 새 수량`, `entry_price = (먼지 × 먼지 원가 + 새 수량 × 체결가) / 합`.
   - 그 뒤 전량 정리 주문 수량이 먼지까지 포함한다(`round_step` 후 남는 것만 다시 먼지).
   - 세션 종료 보고의 '최소 주문 단위 미만 잔여 수량' 은 지금과 같다.
2. 실패 확인.
3. `apply_fill`: 먼지가 생길 때 그 원가를 `dust_entry_price` 로 남기고, 무포지션에서 새 진입이 체결되면 먼지를 합친다(지금은 버린다). 손절 판단 평단도 합친 값.
4. 통과 → 커밋.

완료: 서버 장부(과제 6 먼지 합치기)와 실행기 평단이 같은 규칙으로 움직인다.

## 과제 R4 — 출시(2단계 실행기 기능까지 끝난 뒤)

- 버전 세 곳을 0.21.0 으로(`gg-parrot-desktop-runner` 기억: 세 곳 같이), 서버 `RUNNER_FILL_LEDGER_MIN_VERSION` 기본 21 과 맞는지 확인.
- 출시 절차는 지금과 같다: PR CI → 스쿼시 병합 → release.yml 초안(파일 6개) → 공개 → 설치 파일 206 · latest.json 확인 → 사이트 다운로드 버전 줄 단위로 올림(`runner_release.py` · `frontend/src/lib/runnerDownload.js` · `render.yaml` · `test_runner_release.py`).
- '배포해' 때만.
