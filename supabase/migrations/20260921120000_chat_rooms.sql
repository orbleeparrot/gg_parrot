-- 전략방 (2026-09-21)
-- ① user.room_consent_at — 방 생성 동의 시각.  ② chatmessage.room_id — 방 메시지(NULL = 공개 채팅).
-- ③ chatroom / chatroommember — 방·멤버(멤버 행이 방 읽음 커서). 서버 전용: RLS 켜고 anon/authenticated 권한 회수.

alter table public."user" add column if not exists room_consent_at varchar not null default '';
alter table public.chatmessage add column if not exists room_id integer;
create index if not exists ix_chatmessage_room_id on public.chatmessage (room_id);

create table if not exists public.chatroom (
  id bigserial primary key,
  owner_id integer not null,
  title varchar not null,
  capacity integer not null,
  entry_fee integer not null default 0,
  created_at varchar not null,
  created_ms bigint not null default 0,
  expires_ms bigint not null default 0,
  extended_count integer not null default 0,
  closed_reason varchar not null default '',
  closed_at varchar not null default ''
);
create index if not exists ix_chatroom_owner_id on public.chatroom (owner_id);
create index if not exists ix_chatroom_created_ms on public.chatroom (created_ms);
create index if not exists ix_chatroom_expires_ms on public.chatroom (expires_ms);

create table if not exists public.chatroommember (
  room_id integer not null,
  user_id integer not null,
  paid integer not null default 0,
  joined_at varchar not null,
  joined_ms bigint not null default 0,
  last_seen_id integer not null default 0,
  primary key (room_id, user_id)
);
create index if not exists ix_chatroommember_user_id on public.chatroommember (user_id);

alter table public.chatroom enable row level security;
alter table public.chatroommember enable row level security;
revoke all privileges on table public.chatroom from public, anon, authenticated;
revoke all privileges on table public.chatroommember from public, anon, authenticated;
revoke all privileges on sequence public.chatroom_id_seq from public, anon, authenticated;
