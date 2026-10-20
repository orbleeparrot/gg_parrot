-- 내 에이전트: 지금 포지션에 들어간 돈(실행기 0.20.16+ 이 보내는 invested — 수량×평단 + 진입 수수료)을 세션에 남긴다(2026-10-20).
-- 웹 '투입금'이 invested_usdt(세션 중 최대 금액 · 총수익률 분모)라 마틴게일처럼 나눠 사는 매크로에서 실행기와 크게 달랐다
-- (KRW-GTC 세션 301: 실행기 약 10,199 · 웹 25,485). 무포지션 · 옛 실행기는 null — 웹은 옛 실행기면 수량×평단으로 보인다.
-- 앱 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의다. 병합 전에 적용한다.
alter table public.runsession
  add column if not exists position_invested double precision;
