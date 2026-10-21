# 체결 기준 장부 2단계 — 지정가 IOC 로 가격 방어(서버 + 실행기 0.21.0)

작성 2026-10-21 · 설계 [2026-10-21-fill-ledger-limit-orders-design.md](2026-10-21-fill-ledger-limit-orders-design.md) §4 · §6 · 1단계 [서버](2026-10-21-fill-ledger-stage1-server-plan.md) · [실행기](2026-10-21-fill-ledger-stage1-runner-plan.md)
작업 폴더: 서버 `worktrees/gg_parrot/fill-ledger`(`feat/fill-ledger-limit-orders`), 실행기 `worktrees/ggparrot-desktop/fill-ledger`(`feat/fill-ledger-reports`)

## 범위

- 정책 세션(`exec_policy=fill_ledger_v1` — 레벨 21 · H · 롱 · 현물)의 **익절 매도 · 물타기 매수(다시 연 차수 포함)** 만 지정가 IOC.
  첫 매수(봉 종가 진입) · 손절 · 강제 청산 · 최대 보유 · 일일 손실 정리는 시장가 그대로(설계 §4 — 스탑형에 가격 제한을 걸지 않는다).
- 정책 세션은 레벨 21 실행기로만 생기므로, 정책 세션이면 지정가 명령을 보낸다. 레벨 20 이하 세션 · 옛 세션은 명령 모양이 지금과 같다.
- IOC 무체결은 정상 결과다 — 청산 실패로 세지 않고(재시도 없음), 시장가로 바꾸지 않는다. 장부는 대기만 풀고 다음 봉에서 다시 판단한다.
- 백테스트는 그대로(실시간 실행 방식만 바뀐다 — 과제 1 고정 시험이 지킨다).

## 거래소 확인(2026-10-21)

| 거래소 | 지정가 IOC | 호가 단위 | 근거 |
|---|---|---|---|
| 업비트 | `POST /v1/orders` `ord_type=limit` + `time_in_force=ioc` | `GET /v1/orderbook/instruments?markets=` 의 `tick_size`(현재 가격대). 2025-07-31 정책표와 일치 확인(DOGE 110원 → 1 · SAND 77원 → 0.1 · SHIB 0.0069원 → 0.00001) | docs.upbit.com 주문 생성 · 호가 정책 조회 · 2025-07-31 호가 단위 변경 공지 |
| 빗썸 | `POST /v2/orders` `order_type=limit` + `time_in_force=ioc`(원화 마켓만). 일부라도 체결되면 `done`, 전혀 없으면 `cancel` | 조회 API 없음. 실제 호가창 최우선 호가부터: 1,000,000원 이상 1,000 · 100~1,000원 1 · 10~100원 0.01 · 0.01원 미만 0.0001. 확인 못 한 구간은 더 큰 후보(어긋나도 배수라 거래소가 받는 쪽) | apidocs.bithumb.com 주문 요청 · 공개 호가 조회(2026-10-21) |
| 바이낸스 현물 | `type=LIMIT` + `timeInForce=IOC` | exchangeInfo `PRICE_FILTER.tickSize` | 바이낸스 현물 API |

- 매도 지정가는 호가 단위로 **올림**, 매수는 **내림** — 가격 제한을 어기지 않게.
- 가격 단위 오류로 거절되면(주문은 생기지 않았다) 한 단계 큰 단위로 **한 번만** 다시 낸다. 그래도 거절이면 오류로 보고한다.

## 서버 과제

### S1 — 명령에 주문 방식 · 지정가 칸

파일: `backend/app/db.py`, `supabase/migrations/20261021150000_runner_command_limit.sql`, `backend/tests/test_fill_ledger_schema.py` · `test_db_startup_migrations.py`(늘림)

1. 시험: `runnercommand.order_type`(TEXT '' — 빈 값 = 시장가) · `limit_price`(DOUBLE 0)가 새 DB · 옛 DB(기동 보정) · Postgres 보정 문장에 있다.
2. 실패 확인 → 모델 · 기동 보정 · 마이그레이션 → 통과 → 커밋.

### S2 — 익절 · 물타기는 지정가 IOC 명령으로

파일: `backend/app/engine/stepper.py`(`Fill.order_type` · `limit_price`), `backend/app/engine/candles.py`, `backend/app/runner_engine.py`(`command_from_fill` · `upsert_command` · `_view`), `backend/tests/test_fill_ledger_limit.py`

- 정책 sim: H 익절 `_close_all(..., limit=True)` → 청산 주문 `order_type=limit_ioc`, `limit_price=익절선`(반올림 전 — 실행기가 호가 단위로 올림). 물타기 `_open_long(kind="so")` → `limit_ioc`, `limit_price=가격선`(실행기가 내림). 첫 매수 · 손절 · 강제 정리는 비워 둔다(시장가).
- 명령 행에 저장하고, 실행기로 보내는 명령(`_view`)에는 `order_type` 이 있을 때만 `order_type` · `limit_price` 를 싣는다(옛 세션 명령 모양 그대로).
1. 시험: 정책 H 익절 · 물타기 · 다시 연 차수 명령 = limit_ioc + 지정가, 첫 매수 · 손절 · 최대 보유 = 칸 없음 / 정책 꺼진 H · 레벨 20 세션 명령에 칸 없음 / 백테스트 고정 시험 통과.
2. 실패 확인 → 구현 → 통과 → 커밋.

### S3 — IOC 결과는 정상 결과

파일: `backend/app/runner_engine.py`(`apply_acks` · `_execution_report_event`), `backend/app/runner_engine.py`(`_ledger_events`), `backend/tests/test_fill_ledger_limit.py`

- ack `outcome`: `ioc_unfilled`(체결 0) · `ioc_partial`(일부) · `execution_reported`(전부) — 모두 `ok=true` 로 온다 → `acked`(재시도 · 청산 실패 메모 없음).
- 실행 기록: "지정가 IOC — 익절선 435.54원 이상에 살 사람이 없어 팔지 않았어요 · 보유 그대로" 같은 한 줄(무체결) · 일부 체결 줄.
- 장부: 1단계 그대로 — 무체결 청산은 대기만 풀고, 무체결 물타기는 그 차수를 다시 연다(진입 대기 없음 — 오류가 아니다).
1. 시험: 꼬리 재현(익절선 위 고가 · 아래 종가 → IOC 무체결 → 보유 유지 · 손해 매도 없음 · 다음 봉 다시 판단) / 일부 체결 → 남은 수량 관리(재주문 불가면 먼지) / 물타기 IOC 무체결 → 차수 다시 열림 · 대기 없음 / 가격 단위 오류(ok=false) → 지금처럼 오류.
2. 실패 확인 → 구현 → 통과 → 커밋.

### S4 — 끝-끝(가짜 실행기 IOC)

파일: `backend/tests/test_runner_fill_ledger_flow.py`(늘림)

- 1단계 시나리오 중 응답 유실 · 중복 · 재기동 · 부분 체결을 IOC 명령으로 다시. 레벨 20 세션에는 limit_ioc 명령이 가지 않는다.

## 실행기 과제(ggparrot-desktop, runner-core)

### R5 — 호가 단위 · 올림 · 내림

파일: `src-tauri/crates/runner-core/src/brokers/ticks.rs`(새), `brokers/mod.rs`(`Broker::price_tick(price)`), `brokers/domestic.rs`(업비트 instruments 조회 · 빗썸 표), `brokers/binance.rs` · `binance/parse.rs`(PRICE_FILTER)

- `round_price(price, tick, up)` 는 십진 안전(정수 배수로 계산 — 부동소수 0.1 오차 없음).
- 업비트: 주문 가격과 현재가가 같은 가격대면 instruments `tick_size`, 아니면 2025-07-31 표 — 둘 다 있으면 큰 쪽.
- 빗썸: 확인한 표(확인 못 한 구간은 큰 후보). 바이낸스: tickSize.
- 시험: 가격대 경계 · 올림/내림 · 0.0069 같은 작은 값 · 큰 값 · 바이낸스 0.00000100 꼴.

### R6 — 지정가 IOC 주문 내기

파일: `brokers/mod.rs`(`SubmitRequest.limit: Option<LimitIoc>`), `brokers/domestic.rs`(`order_body` 지정가 · `time_in_force=ioc`), `brokers/binance.rs`(LIMIT · IOC · price · quantity), `brokers/mock.rs`(모의: 현재가가 지정가를 넘을 때만 체결), `bot/commands.rs`(`order_type` · `limit_price` 읽기), `bot/orders.rs`(IOC 결과 분류)

- 진입: 금액 = 투자 금액 × 비율(국내는 수수료 포함), 수량 = 금액 ÷ 내림한 지정가(수량 단위로 내림). 청산: 수량은 지금과 같고 가격은 올림.
- 결과: 다 참 → `execution_reported`, 일부 → `ioc_partial`, 0 → `ioc_unfilled` — 모두 `ok=true`. 보유 · 장부는 체결분만. 결과를 모르면(상태 불명) 지금처럼 '포지션 확인 필요'.
- 가격 단위 오류 → 한 단계 큰 단위로 한 번 다시(거절된 주문은 생기지 않았다).
- 활동 기록: "지정가 435.54원 이상에만 팔아요 — 살 사람이 없어 이번엔 팔지 않았어요" 같은 줄(오류 · 위험으로 띄우지 않는다).
- 시험: 가짜 거래소(FakeBroker)로 전부 · 일부 · 0 체결, 국내 본문(업비트 ord_type · 빗썸 order_type · time_in_force · price · volume), 바이낸스 파라미터, 가격 단위 오류 한 번 다시, 모의 체결 조건.

### R7 — 출시 0.21.0(1 · 2단계 실행기 함께)

- 버전 세 곳 0.21.0 · 연동 레벨 21. 출시 절차는 1단계 실행기 계획 R4.
- 배포 순서: ① 서버 마이그레이션 두 개 운영 적용 → ② 서버 병합 · 배포(잠든 채) → ③ 실행기 0.21.0 공개 → ④ 사이트 다운로드 버전. '배포해' 때만.

## 하지 않는 것(2단계)

- 거래소 대기 지정가(3단계). 첫 매수 지정가. 선물 · 숏.
- IOC 무체결 뒤 시장가 전환 · 즉시 재시도.
