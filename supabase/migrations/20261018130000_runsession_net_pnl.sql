-- 내 에이전트: 실행기(0.20.13+)가 보내는 수수료 반영 손익을 세션에 남긴다(2026-10-18).
-- 실행기 화면의 '평가손익 · 지금 정리하면'은 사고팔 때 수수료를 뺀 값인데, 서버는 수수료 전(평단 대비 가격)만
-- 받아 웹이 같은 이름으로 다른 숫자를 보였다(KRW-SAND 실행기 +1.23% · 웹 +1.33%, 업비트 수수료 0.05% × 2).
-- unrealized_net · unrealized_net_pct · realized_net — 마지막 heartbeat 값. 실행기가 모르거나 보내지 않으면 null.
-- final_unrealized_net(_pct) — 종료 보고 때 복사해 둔 청산 직전 포지션의 값(final_unrealized_pct 와 짝).
-- 기존 행은 null 로 남는다 — 웹은 null 이면 수수료 전 값을 '수수료 전'이라 밝혀 보인다.
-- 앱 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의다. 병합 전에 적용한다.
alter table public.runsession
  add column if not exists unrealized_net double precision,
  add column if not exists unrealized_net_pct double precision,
  add column if not exists realized_net double precision,
  add column if not exists final_unrealized_net double precision,
  add column if not exists final_unrealized_net_pct double precision;
