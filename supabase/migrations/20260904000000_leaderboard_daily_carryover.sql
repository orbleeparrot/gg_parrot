-- 리더보드는 매일 KST 00:00 에 초기화되지만 상위 N등은 다음 날 보드로 이월된다.
-- 이월은 새 행이 아니라 기존 엔트리의 보드 날짜(created_ms)를 미는 방식이라,
-- 며칠째 방어 중인지(streak_days)와 원래 등록 시각(first_created_ms)을 함께 둔다.
alter table public.leaderboardentry
  add column if not exists streak_days integer not null default 1;
alter table public.leaderboardentry
  add column if not exists first_created_ms bigint;

-- 하루 한 번만 이월하기 위한 멱등 키(그날 이월이 이미 돌았는지).
create table if not exists public.leaderboardcarryover (
  id serial primary key,
  date_kst varchar not null,
  carried integer not null default 0,
  created_at varchar not null
);
create unique index if not exists ix_leaderboardcarryover_date_kst
  on public.leaderboardcarryover (date_kst);

-- gg_parrot 백엔드 전용 테이블: 브라우저는 Supabase Data API 로 접근하지 않는다.
alter table public.leaderboardcarryover enable row level security;
revoke all privileges on table public.leaderboardcarryover from public, anon, authenticated;
