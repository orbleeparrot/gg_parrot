-- 껄무새에게 물어볼까? (2026-09-18)
-- ① user.ask_consent_version / ask_consent_at — 고지 동의 버전·시각.
-- ② askmacrosession — 요청(카드 답변)·보여 준 상위 3개 기록. 하루 한도는 (user_id, day_kst) 로 센다.
-- 서버 전용: RLS 켜고 anon/authenticated 권한 회수.

alter table public."user" add column if not exists ask_consent_version varchar not null default '';
alter table public."user" add column if not exists ask_consent_at varchar not null default '';

create table if not exists public.askmacrosession (
  id bigserial primary key,
  user_id integer not null,
  day_kst varchar not null,
  request_json varchar not null default '{}',
  candidate_count integer not null default 0,
  results_json varchar not null default '[]',
  disclaimer_version varchar not null default '',
  ai_used boolean not null default false,
  elapsed_ms integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0
);
create index if not exists ix_askmacrosession_user_id on public.askmacrosession (user_id);
create index if not exists ix_askmacrosession_user_day on public.askmacrosession (user_id, day_kst);

alter table public.askmacrosession enable row level security;
revoke all privileges on table public.askmacrosession from public, anon, authenticated;
revoke all privileges on sequence public.askmacrosession_id_seq from public, anon, authenticated;
