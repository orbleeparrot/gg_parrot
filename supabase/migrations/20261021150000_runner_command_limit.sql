-- 체결 기준 장부 2단계 — 지정가 IOC(2026-10-21 설계 §6).
-- runnercommand.order_type: 빈 값 = 시장가(기존 명령 전부). 'limit_ioc' = 지정가 IOC — fill_ledger_v1 세션의 익절 · 물타기만.
-- runnercommand.limit_price: 전략이 정한 가격선(호가 단위 반올림 전). 실행기가 매도는 올림 · 매수는 내림으로 낸다.
-- 앱 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의다. 병합 전에 적용한다.
alter table public.runnercommand
  add column if not exists order_type text not null default '',
  add column if not exists limit_price double precision not null default 0;
