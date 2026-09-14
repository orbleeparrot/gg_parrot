-- 일일 퀘스트: 리더보드를 안 하는 회원도 포인트를 벌 수 있게, 하루 한 번씩
-- 백테스트·페이퍼·댓글 행동에 보상한다. (user_id, date_kst, quest_key) 가 멱등 키.
create table if not exists public.dailyquestclaim (
  id serial primary key,
  user_id integer not null,
  date_kst varchar not null,
  quest_key varchar not null,
  reward integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0
);
create index if not exists ix_dailyquestclaim_user_id on public.dailyquestclaim (user_id);
create unique index if not exists ix_dailyquestclaim_user_date_key
  on public.dailyquestclaim (user_id, date_kst, quest_key);

-- gg_parrot 백엔드 전용 테이블: 브라우저는 Supabase Data API 로 접근하지 않는다.
alter table public.dailyquestclaim enable row level security;
revoke all privileges on table public.dailyquestclaim from public, anon, authenticated;
