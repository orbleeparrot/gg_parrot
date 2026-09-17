-- 관리자 권한과 방문 기록(관리자 대시보드의 유입 지표).
-- "user".is_admin — 관리자 계정. 프로필에 '관리자 대시보드' 버튼이 생기고 /api/admin/* 를 쓸 수 있다.
-- visit — 화면 진입 한 건(익명). 회원 id 는 로그인했을 때만, 방문자 구분은 브라우저 익명 id 의 해시로만 남긴다.
--   경로·유입 출처(referrer 호스트)·UTM source 만 기록하고 IP·UA 는 저장하지 않는다. 90일 뒤 정리(admin.prune_visits).
alter table public."user" add column if not exists is_admin boolean not null default false;

create table if not exists public.visit (
  id bigserial primary key,
  day_kst varchar not null,
  path varchar not null default '',
  referrer_host varchar not null default '',
  utm_source varchar not null default '',
  visitor_hash varchar not null default '',
  user_id integer,
  created_ms bigint not null default 0
);
create index if not exists ix_visit_day_kst on public.visit (day_kst);
create index if not exists ix_visit_created_ms on public.visit (created_ms);
-- gg_parrot 백엔드 전용: 브라우저는 Supabase Data API 로 접근하지 않는다.
alter table public.visit enable row level security;
revoke all privileges on table public.visit from public, anon, authenticated;
revoke all privileges on sequence public.visit_id_seq from public, anon, authenticated;
