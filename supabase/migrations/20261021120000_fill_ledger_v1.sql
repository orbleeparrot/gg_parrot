-- 체결 기준 장부(fill_ledger_v1) — 서버 전략 장부를 실행기가 보고한 실제 체결로 확정한다(2026-10-21 설계 §9).
-- ① runsession.exec_policy: 세션을 만들 때 한 번 정하는 실행 방식. 기존 행은 '' — 지금 방식 그대로 돈다(결정 4).
-- ② runnercommand.order_ref: 서버 전략이 주문에 매긴 번호("o17"). 체결 보고를 장부의 그 주문에 잇는다.
-- ③ runnerfill: 실행기가 보고한 실제 체결 한 건씩(ack 의 orders · 하트비트의 local_fills). (session_id, fill_key) 유일 —
--    같은 보고가 두 번 와도 한 행. applied_ms 0 = 아직 장부에 안 접음. 백엔드만 읽고 쓴다(정책 · 공개 권한 없음).
-- 앱 기동 보정(db._PG_ADDED_COLUMNS · create_all)과 같은 정의다. 병합 전에 적용한다.
alter table public.runsession
  add column if not exists exec_policy text not null default '';
alter table public.runnercommand
  add column if not exists order_ref text not null default '';

create table if not exists public.runnerfill (
    id serial primary key,
    session_id integer not null,
    command_id integer,
    fill_key varchar not null,
    exchange_order_id varchar not null default '',
    source varchar not null default '',
    side varchar not null default '',
    status varchar not null default '',
    executed_qty double precision not null default 0,
    acquired_qty double precision not null default 0,
    price double precision not null default 0,
    fee_quote double precision not null default 0,
    fee_known boolean not null default false,
    reason varchar not null default '',
    runner_ts_ms bigint not null default 0,
    created_ms bigint not null default 0,
    applied_ms bigint not null default 0
);
create unique index if not exists ux_runnerfill_session_key on public.runnerfill (session_id, fill_key);
create index if not exists ix_runnerfill_session_applied on public.runnerfill (session_id, applied_ms, id);
alter table public.runnerfill enable row level security;
revoke all privileges on table public.runnerfill from public, anon, authenticated;
revoke all privileges on sequence public.runnerfill_id_seq from public, anon, authenticated;
