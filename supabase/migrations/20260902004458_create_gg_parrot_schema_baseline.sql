-- SQLModel remains the application model source of truth. This idempotent
-- baseline makes Supabase migration replay self-contained and ensures that the
-- following RLS migration never runs before the gg_parrot tables exist.

create table if not exists public.boardcomment (
  id serial primary key,
  post_id integer not null,
  username varchar not null,
  password_hash varchar not null,
  text varchar not null,
  created_at varchar not null,
  created_ms bigint not null
);
create index if not exists ix_boardcomment_created_ms on public.boardcomment (created_ms);
create index if not exists ix_boardcomment_post_id on public.boardcomment (post_id);

create table if not exists public.boardpost (
  id serial primary key,
  author_user_id integer not null,
  author_name varchar not null,
  title varchar not null,
  body varchar not null,
  image_mime varchar not null,
  image_data bytea,
  created_at varchar not null,
  created_ms bigint not null
);
create index if not exists ix_boardpost_author_user_id on public.boardpost (author_user_id);
create index if not exists ix_boardpost_created_ms on public.boardpost (created_ms);

create table if not exists public.chatmessage (
  id serial primary key,
  username varchar not null,
  text varchar not null,
  created_at varchar not null,
  created_ms bigint not null
);
create index if not exists ix_chatmessage_created_ms on public.chatmessage (created_ms);

create table if not exists public.dailychallenge (
  id serial primary key,
  date_kst varchar not null,
  symbol varchar not null,
  created_at varchar not null
);
create unique index if not exists ix_dailychallenge_date_kst on public.dailychallenge (date_kst);

create table if not exists public.leaderboardentry (
  id serial primary key,
  user_id varchar not null,
  nickname varchar not null,
  username varchar not null,
  password_hash varchar not null,
  owner_user_id integer,
  is_ai boolean not null,
  symbol varchar not null,
  macro_json varchar not null,
  human_summary varchar not null,
  paper_session_id integer,
  created_at varchar not null,
  created_ms bigint not null
);
create index if not exists ix_leaderboardentry_created_ms on public.leaderboardentry (created_ms);
create index if not exists ix_leaderboardentry_owner_user_id on public.leaderboardentry (owner_user_id);
create index if not exists ix_leaderboardentry_user_id on public.leaderboardentry (user_id);

create table if not exists public.leaderboardvote (
  id serial primary key,
  entry_id integer not null,
  user_id varchar not null,
  value integer not null
);
create index if not exists ix_leaderboardvote_entry_id on public.leaderboardvote (entry_id);
create index if not exists ix_leaderboardvote_user_id on public.leaderboardvote (user_id);

create table if not exists public.macrorow (
  id serial primary key,
  macro_id varchar not null,
  share_slug varchar not null,
  symbol varchar not null,
  rule_type varchar not null,
  position_side varchar not null,
  macro_json varchar not null,
  human_summary varchar not null,
  created_at varchar not null,
  rep_return_pct double precision not null,
  rep_win_pct double precision not null,
  rep_mdd_pct double precision not null,
  rep_trades integer not null,
  rep_source varchar not null,
  rep_period_label varchar not null,
  rep_leverage integer not null
);
create unique index if not exists ix_macrorow_macro_id on public.macrorow (macro_id);
create unique index if not exists ix_macrorow_share_slug on public.macrorow (share_slug);

create table if not exists public.macrounlock (
  id serial primary key,
  user_id integer not null,
  entry_id integer not null,
  price integer not null,
  created_at varchar not null
);
create index if not exists ix_macrounlock_entry_id on public.macrounlock (entry_id);
create index if not exists ix_macrounlock_user_id on public.macrounlock (user_id);

create table if not exists public.papersession (
  id serial primary key,
  macro_id varchar not null,
  symbol varchar not null,
  mode varchar not null,
  status varchar not null,
  started_at varchar not null,
  stopped_at varchar,
  virtual_balance double precision not null,
  current_equity double precision not null,
  current_return double precision not null,
  liquidations integer not null,
  liquidated_loss double precision not null,
  macro_json varchar not null
);
create index if not exists ix_papersession_macro_id on public.papersession (macro_id);

create table if not exists public.papertrade (
  id serial primary key,
  session_id integer not null,
  ts varchar not null,
  side varchar not null,
  price double precision not null,
  qty double precision not null,
  return_at_trade double precision not null
);
create index if not exists ix_papertrade_session_id on public.papertrade (session_id);

create table if not exists public.pointledger (
  id serial primary key,
  user_id integer not null,
  delta integer not null,
  balance_after integer not null,
  reason varchar not null,
  ref varchar not null,
  created_at varchar not null,
  created_ms bigint not null
);
create index if not exists ix_pointledger_created_ms on public.pointledger (created_ms);
create index if not exists ix_pointledger_user_id on public.pointledger (user_id);

create table if not exists public.runnerkey (
  id serial primary key,
  user_id integer not null,
  key varchar not null,
  created_at varchar not null
);
create unique index if not exists ix_runnerkey_key on public.runnerkey (key);
create unique index if not exists ix_runnerkey_user_id on public.runnerkey (user_id);

create table if not exists public.runnerlaunchticket (
  id serial primary key,
  user_id integer not null,
  user_macro_id integer not null,
  token_hash varchar not null,
  testnet boolean not null,
  created_at varchar not null,
  expires_at varchar not null,
  expires_ms bigint not null,
  claimed_at varchar not null
);
create index if not exists ix_runnerlaunchticket_expires_ms on public.runnerlaunchticket (expires_ms);
create unique index if not exists ix_runnerlaunchticket_token_hash on public.runnerlaunchticket (token_hash);
create index if not exists ix_runnerlaunchticket_user_id on public.runnerlaunchticket (user_id);
create index if not exists ix_runnerlaunchticket_user_macro_id on public.runnerlaunchticket (user_macro_id);

create table if not exists public.runsession (
  id serial primary key,
  user_id integer not null,
  user_macro_id integer,
  symbol varchar not null,
  position_side varchar not null,
  leverage integer not null,
  market varchar not null,
  testnet boolean not null,
  human_summary varchar not null,
  macro_json varchar not null,
  status varchar not null,
  stop_mode varchar not null,
  in_position boolean not null,
  last_price double precision not null,
  entry_price double precision not null,
  position_qty double precision not null,
  realized_pnl double precision not null,
  unrealized_pct double precision not null,
  note varchar not null,
  started_at varchar not null,
  last_heartbeat_at varchar not null,
  stopped_at varchar
);
create index if not exists ix_runsession_status on public.runsession (status);
create index if not exists ix_runsession_user_id on public.runsession (user_id);
create index if not exists ix_runsession_user_macro_id on public.runsession (user_macro_id);

create table if not exists public.tickernewsaibudget (
  budget_date_kst varchar primary key,
  used integer not null,
  updated_at varchar not null
);

create table if not exists public.tickernewssnapshot (
  id serial primary key,
  snapshot_key varchar not null,
  asset_symbol varchar not null,
  coin_name varchar not null,
  query varchar not null,
  news_json varchar not null,
  analysis_json varchar not null,
  item_count integer not null,
  processing_status varchar not null,
  analysis_status varchar not null,
  analysis_source varchar not null,
  prompt_version varchar not null,
  model varchar not null,
  collected_at varchar not null,
  collected_ms bigint not null,
  claimed_at varchar not null,
  claimed_ms bigint not null,
  claim_token varchar not null,
  last_observed_at varchar not null,
  last_observed_ms bigint not null,
  last_observation_seq bigint not null,
  analysis_attempts integer not null,
  next_retry_ms bigint not null,
  completed_at varchar not null,
  completed_ms bigint not null
);
create index if not exists ix_tickernewssnapshot_asset_symbol on public.tickernewssnapshot (asset_symbol);
create index if not exists ix_tickernewssnapshot_claimed_ms on public.tickernewssnapshot (claimed_ms);
create index if not exists ix_tickernewssnapshot_collected_ms on public.tickernewssnapshot (collected_ms);
create index if not exists ix_tickernewssnapshot_processing_status on public.tickernewssnapshot (processing_status);
create unique index if not exists ix_tickernewssnapshot_snapshot_key on public.tickernewssnapshot (snapshot_key);

create table if not exists public.tickernewsstate (
  asset_symbol varchar primary key,
  latest_snapshot_id integer,
  observation_seq bigint not null,
  latest_observation_seq bigint not null,
  latest_observed_ms bigint not null,
  collection_status varchar not null,
  last_error varchar not null,
  consecutive_failures integer not null,
  last_attempt_at varchar not null,
  last_attempt_ms bigint not null,
  last_success_at varchar not null,
  last_success_ms bigint not null,
  updated_at varchar not null
);
create index if not exists ix_tickernewsstate_collection_status on public.tickernewsstate (collection_status);
create index if not exists ix_tickernewsstate_last_success_ms on public.tickernewsstate (last_success_ms);
create index if not exists ix_tickernewsstate_latest_snapshot_id on public.tickernewsstate (latest_snapshot_id);

create table if not exists public."user" (
  id serial primary key,
  email varchar not null,
  username varchar not null,
  password_hash varchar not null,
  points_balance integer not null,
  created_at varchar not null
);
create unique index if not exists ix_user_email on public."user" (email);
create unique index if not exists ix_user_username on public."user" (username);

create table if not exists public.usermacro (
  id serial primary key,
  user_id integer not null,
  name varchar not null,
  symbol varchar not null,
  rule_type varchar not null,
  position_side varchar not null,
  macro_json varchar not null,
  human_summary varchar not null,
  source_type varchar not null,
  source_ref varchar not null,
  schema_version varchar not null,
  created_at varchar not null,
  updated_at varchar not null
);
create index if not exists ix_usermacro_source_ref on public.usermacro (source_ref);
create index if not exists ix_usermacro_source_type on public.usermacro (source_type);
create index if not exists ix_usermacro_symbol on public.usermacro (symbol);
create index if not exists ix_usermacro_user_id on public.usermacro (user_id);

create table if not exists public.whaleholderbalance (
  id serial primary key,
  coin varchar not null,
  wallet varchar not null,
  balance_raw varchar not null,
  updated_at varchar not null
);
create index if not exists ix_whaleholderbalance_coin on public.whaleholderbalance (coin);
create index if not exists ix_whaleholderbalance_wallet on public.whaleholderbalance (wallet);

create table if not exists public.whaleobservation (
  coin varchar primary key,
  observed_at varchar not null,
  buys integer not null,
  sells integer not null,
  tracked integer not null
);
