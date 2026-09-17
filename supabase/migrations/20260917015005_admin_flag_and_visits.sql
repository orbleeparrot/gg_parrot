-- 관리자 대시보드 (2026-09-17)
-- ① user.is_admin — 관리자 플래그(기본 false). 두 계정만 켠다.
-- ② visit — 화면 진입·행동 비콘(세션·체류·채널·기기). IP·UA 없음.
-- ③ macroeventdaily — 매크로별 하루 노출·열람·구매.
-- ④ collectorrun / collectorsourcedaily — 수집 엔진 실행 요약·소스별 하루 누적.
-- ⑤ apiusagedaily — Gemini 토큰·추정 비용 하루 누적.
-- 모두 서버 전용: RLS 켜고 anon/authenticated 권한 회수(public 기본 ACL 이 열려 있으므로).

alter table public."user" add column if not exists is_admin boolean not null default false;
update public."user" set is_admin = true where lower(email) in ('hsrohsro1234@gmail.com', 'clcleh123@gmail.com');

create table if not exists public.visit (
  id bigserial primary key,
  day_kst varchar not null,
  path varchar not null default '',
  referrer_host varchar not null default '',
  utm_source varchar not null default '',
  visitor_hash varchar not null default '',
  user_id integer,
  created_ms bigint not null default 0,
  kind varchar not null default 'view',
  session_key varchar not null default '',
  view_key varchar not null default '',
  dwell_ms bigint not null default 0,
  is_new boolean not null default false,
  is_landing boolean not null default false,
  channel varchar not null default '',
  device varchar not null default '',
  screen_w integer not null default 0
);
create index if not exists ix_visit_day_kst on public.visit (day_kst);
create index if not exists ix_visit_created_ms on public.visit (created_ms);
create index if not exists ix_visit_session_key on public.visit (session_key);
create index if not exists ix_visit_view_key on public.visit (view_key);

create table if not exists public.macroeventdaily (
  day_kst varchar not null,
  entry_id integer not null,
  impressions integer not null default 0,
  opens integer not null default 0,
  unlocks integer not null default 0,
  primary key (day_kst, entry_id)
);

create table if not exists public.collectorrun (
  id bigserial primary key,
  engine varchar not null,
  day_kst varchar not null,
  started_ms bigint not null default 0,
  finished_ms bigint not null default 0,
  status varchar not null default 'ok',
  targets integer not null default 0,
  items integer not null default 0,
  failures integer not null default 0,
  error varchar not null default '',
  summary_json varchar not null default '{}'
);
create index if not exists ix_collectorrun_engine on public.collectorrun (engine);
create index if not exists ix_collectorrun_day_kst on public.collectorrun (day_kst);
create index if not exists ix_collectorrun_started_ms on public.collectorrun (started_ms);

create table if not exists public.collectorsourcedaily (
  day_kst varchar not null,
  engine varchar not null,
  source varchar not null,
  calls integer not null default 0,
  items integer not null default 0,
  failures integer not null default 0,
  targets integer not null default 0,
  last_error varchar not null default '',
  last_success_ms bigint not null default 0,
  updated_ms bigint not null default 0,
  primary key (day_kst, engine, source)
);

create table if not exists public.apiusagedaily (
  day_kst varchar not null,
  provider varchar not null,
  model varchar not null default '',
  purpose varchar not null default '',
  calls integer not null default 0,
  failures integer not null default 0,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cached_tokens bigint not null default 0,
  cost_micro_usd bigint not null default 0,
  updated_ms bigint not null default 0,
  primary key (day_kst, provider, model, purpose)
);

do $$
declare t text;
begin
  foreach t in array array['visit', 'macroeventdaily', 'collectorrun', 'collectorsourcedaily', 'apiusagedaily'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all privileges on table public.%I from public, anon, authenticated', t);
  end loop;
end $$;
revoke all privileges on sequence public.visit_id_seq from public, anon, authenticated;
revoke all privileges on sequence public.collectorrun_id_seq from public, anon, authenticated;
