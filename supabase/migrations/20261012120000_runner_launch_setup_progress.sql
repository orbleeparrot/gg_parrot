-- Runner v13 connection-helper progress.
-- runnerlaunchticket.setup_progress: the latest stage a claimed runner reported
-- (stage name, whether each key slot is filled, the three check rows). Exchange
-- keys, IP addresses and error text are never accepted by the API model.
-- The web quick-run guide mirrors the runner screen from this value.
alter table public.runnerlaunchticket
  add column if not exists setup_progress varchar not null default '',
  add column if not exists setup_progress_at varchar not null default '';
