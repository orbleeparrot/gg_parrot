-- Claim a KST challenge day before Binance/Anthropic/paper-session work. The
-- unique date row is the cross-process singleflight marker.
alter table public.dailychallenge
  add column if not exists status varchar not null default 'ready',
  add column if not exists claim_token varchar not null default '',
  add column if not exists claimed_ms bigint not null default 0,
  add column if not exists last_error varchar not null default '';
