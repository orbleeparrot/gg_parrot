-- 껄무새에게 물어볼까 — 포인트로 횟수 추가 (2026-09-22)
-- ① askmacrosession.paid — 추가권으로 물어본 세션(무료 한도에서 제외).
-- ② askextracredit — 포인트로 산 추가 1회(하루 단위, 쓰면 used_session_id). 서버 전용: RLS 켜고 권한 회수.

alter table public.askmacrosession add column if not exists paid boolean not null default false;

create table if not exists public.askextracredit (
  id bigserial primary key,
  user_id integer not null,
  day_kst varchar not null,
  price integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0,
  used_session_id integer
);
create index if not exists ix_askextracredit_user_id on public.askextracredit (user_id);
create index if not exists ix_askextracredit_user_day on public.askextracredit (user_id, day_kst);

alter table public.askextracredit enable row level security;
revoke all privileges on table public.askextracredit from public, anon, authenticated;
revoke all privileges on sequence public.askextracredit_id_seq from public, anon, authenticated;
