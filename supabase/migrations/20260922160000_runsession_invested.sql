-- 에이전트 손익을 '투입금 대비 총수익률'로 (2026-09-22): runsession.invested_usdt — 세션 중 들어간 최대 금액(수량×진입가).
alter table public.runsession add column if not exists invested_usdt double precision not null default 0;
