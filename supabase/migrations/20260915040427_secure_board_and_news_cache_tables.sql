-- 게시판 사진·추천·신고와 브라우저 뉴스 캐시는 gg_parrot 백엔드 전용 테이블이다.
-- 기준 스키마(20260902) 뒤에 앱의 create_all 로만 생겨서 RLS 가 꺼진 채 public 스키마의
-- 기본 권한(anon/authenticated 에 모든 권한)이 남아 있었다 — Data API 로 읽고 쓸 수 있는 상태.
-- 다른 백엔드 전용 테이블과 같은 계약으로 맞춘다: RLS 켜고 Data API 역할 권한 회수.
-- 새 DB 에서는 테이블부터 만든다(운영에는 이미 있어 if not exists 로 건너뛴다).
create table if not exists public.boardimage (
  id serial primary key,
  post_id integer not null,
  position integer not null default 0,
  image_mime varchar not null default '',
  image_data bytea not null,
  created_ms bigint not null
);
create index if not exists ix_boardimage_post_id on public.boardimage (post_id);

create table if not exists public.boardpostvote (
  id serial primary key,
  post_id integer not null,
  user_id integer not null,
  value integer not null,
  created_ms bigint not null
);
create index if not exists ix_boardpostvote_post_id on public.boardpostvote (post_id);
create index if not exists ix_boardpostvote_user_id on public.boardpostvote (user_id);

create table if not exists public.boardreport (
  id serial primary key,
  target_type varchar not null,
  target_id integer not null,
  reporter_user_id integer not null,
  reason varchar not null default '',
  detail varchar not null default '',
  created_ms bigint not null
);
create index if not exists ix_boardreport_target_type on public.boardreport (target_type);
create index if not exists ix_boardreport_target_id on public.boardreport (target_id);
create index if not exists ix_boardreport_reporter_user_id on public.boardreport (reporter_user_id);

create table if not exists public.browsernewspagecache (
  cache_key varchar(512) primary key,
  payload_json varchar not null,
  expires_ms bigint not null,
  updated_ms bigint not null
);
create index if not exists ix_browsernewspagecache_expires_ms on public.browsernewspagecache (expires_ms);
create index if not exists ix_browsernewspagecache_updated_ms on public.browsernewspagecache (updated_ms);

alter table public.boardimage enable row level security;
alter table public.boardpostvote enable row level security;
alter table public.boardreport enable row level security;
alter table public.browsernewspagecache enable row level security;
revoke all privileges on table public.boardimage, public.boardpostvote, public.boardreport,
  public.browsernewspagecache from public, anon, authenticated;
-- serial 시퀀스에도 같은 기본 권한이 붙어 있다.
revoke all privileges on sequence public.boardimage_id_seq, public.boardpostvote_id_seq,
  public.boardreport_id_seq from public, anon, authenticated;
