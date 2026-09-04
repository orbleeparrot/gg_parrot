-- One market overview per KST day. The summary used to live only in process
-- memory, so every Render deploy paid for it again and the daily budget ran out.
create table if not exists public.marketnewssummary (
  summary_key varchar primary key,
  overview varchar not null default '',
  prompt_version varchar not null default '',
  updated_at varchar not null default '',
  updated_ms bigint not null default 0
);

-- Server-only table: browsers never need direct Data API access.
alter table public.marketnewssummary enable row level security;
revoke all privileges on table public.marketnewssummary
  from public, anon, authenticated;
