-- 서버 신호 실행기 (2026-09-22)
-- ① runsession.state_json — 드라이버 상태(JSON). ② runnercommand — 서버가 실행기에 내리는 주문 명령.
-- 서버 전용 테이블: RLS 켜고 anon/authenticated 권한 회수(다른 서버 전용 테이블과 같은 정책).

alter table public.runsession add column if not exists state_json text not null default '';

create table if not exists public.runnercommand (
  id bigserial primary key,
  session_id integer not null,
  seq integer not null default 0,
  action varchar not null default '',
  notional_frac double precision not null default 0,
  qty_frac double precision not null default 0,
  signal_price double precision not null default 0,
  reason varchar not null default '',
  status varchar not null default 'pending',
  attempts integer not null default 0,
  created_at varchar not null default '',
  created_ms bigint not null default 0,
  expires_ms bigint not null default 0,
  acked_at varchar not null default '',
  executed_qty double precision not null default 0,
  fill_price double precision not null default 0,
  error varchar not null default ''
);
create index if not exists ix_runnercommand_session_id on public.runnercommand (session_id);
create index if not exists ix_runnercommand_status on public.runnercommand (status);

alter table public.runnercommand enable row level security;
revoke all privileges on table public.runnercommand from public, anon, authenticated;
revoke all privileges on sequence public.runnercommand_id_seq from public, anon, authenticated;
