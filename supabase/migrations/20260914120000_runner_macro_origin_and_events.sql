-- 매크로 파일 서명·출처와 실행 이벤트 로그.
-- 실행기가 시작할 때 파일의 서명을 같이 올리면 서버가 검증해 출처를 남기고,
-- 실행 중 로그(신호·주문·체결·오류)는 heartbeat 에 실려 세션별로 쌓인다.
alter table public.runsession
  add column if not exists macro_origin varchar not null default '',
  add column if not exists macro_digest varchar not null default '';

create table if not exists public.runsessionevent (
  id serial primary key,
  session_id integer not null,
  user_id integer not null,
  ts varchar not null,
  kind varchar not null default 'info',
  message varchar not null default '',
  created_ms bigint not null default 0
);
create index if not exists ix_runsessionevent_session_id on public.runsessionevent (session_id);
create index if not exists ix_runsessionevent_user_id on public.runsessionevent (user_id);

-- gg_parrot 백엔드 전용 테이블: 브라우저는 Supabase Data API 로 접근하지 않는다.
alter table public.runsessionevent enable row level security;
revoke all privileges on table public.runsessionevent from public, anon, authenticated;
