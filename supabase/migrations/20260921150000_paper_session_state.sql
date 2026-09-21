-- 리더보드 실시간 상태 (2026-09-21): papersession.state_json — 포지션·체결 요약(JSON). 체크포인트마다 갱신.
alter table public.papersession add column if not exists state_json text not null default '';
