-- 알림 전용 스키마. 헤더 종 아이콘에 쌓이는 개인 알림(퀘스트·매크로 판매/등록·댓글·답글·
-- 에이전트·관리자 메시지)과 전체 공지(user_id 가 NULL)를 public 과 분리해 둔다.
-- gg_parrot 백엔드(app/notifications.py)만 읽고 쓴다: Data API 에 노출하지 않고
-- (config.toml api.schemas 에 없음) anon/authenticated 권한도 주지 않는다.
create schema if not exists notifications;
revoke all on schema notifications from public, anon, authenticated;

create table if not exists notifications.message (
  id bigserial primary key,
  user_id integer,                        -- 받는 회원(public."user".id). NULL = 전체 공지
  kind varchar not null,                  -- quest | macro_sold | macro_registered | comment | reply | agent | admin | notice
  title varchar not null,
  body varchar not null default '',
  link varchar not null default '',       -- 누르면 이동할 앱 경로(/agents, /board/12 …)
  data_json text not null default '{}',   -- 종류별 부가 정보(points, entry_id, event …)
  session_id integer,                     -- 에이전트 알림이 붙은 실행 세션(public.runsession.id)
  ref varchar not null default '',        -- 중복 방지 열쇠(수집기가 같은 기사·체결 묶음을 다시 볼 때)
  created_at varchar not null,
  created_ms bigint not null default 0,
  read_ms bigint                          -- 개인 알림을 읽은 시각. 공지는 receipt 에 회원별로 남긴다
);
create index if not exists ix_notifications_message_user_created
  on notifications.message (user_id, created_ms desc);
create index if not exists ix_notifications_message_notice_created
  on notifications.message (created_ms desc) where user_id is null;
create index if not exists ix_notifications_message_user_ref
  on notifications.message (user_id, kind, ref) where ref <> '';

-- 전체 공지를 어느 회원이 읽었는지. 개인 알림은 message.read_ms 하나로 충분하다.
create table if not exists notifications.receipt (
  message_id bigint not null references notifications.message (id) on delete cascade,
  user_id integer not null,
  read_ms bigint not null default 0,
  primary key (message_id, user_id)
);
create index if not exists ix_notifications_receipt_user on notifications.receipt (user_id);

-- gg_parrot 백엔드 전용: 브라우저는 Supabase Data API 로 접근하지 않는다.
alter table notifications.message enable row level security;
alter table notifications.receipt enable row level security;
revoke all privileges on all tables in schema notifications from public, anon, authenticated;
revoke all privileges on all sequences in schema notifications from public, anon, authenticated;
