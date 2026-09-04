-- Headline translations are mandatory after source-level deduplication. This
-- cache lets every web instance and deploy reuse one paid translation.
create table if not exists public.newstitletranslation (
  title_hash varchar primary key,
  original_title varchar not null,
  translated_title varchar not null default '',
  processing_status varchar not null default 'ready',
  claim_token varchar not null default '',
  claimed_ms bigint not null default 0,
  updated_at varchar not null default '',
  updated_ms bigint not null default 0
);

alter table public.newstitletranslation
  add column if not exists processing_status varchar not null default 'ready',
  add column if not exists claim_token varchar not null default '',
  add column if not exists claimed_ms bigint not null default 0;

create index if not exists ix_newstitletranslation_updated_ms
  on public.newstitletranslation (updated_ms);
create index if not exists ix_newstitletranslation_processing_status
  on public.newstitletranslation (processing_status);
create index if not exists ix_newstitletranslation_claimed_ms
  on public.newstitletranslation (claimed_ms);

-- Server-only table: browsers never need direct Data API access.
alter table public.newstitletranslation enable row level security;
revoke all privileges on table public.newstitletranslation
  from public, anon, authenticated;
