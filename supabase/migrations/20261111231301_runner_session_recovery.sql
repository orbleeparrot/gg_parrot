-- Durable desktop recovery identity and process fencing. These are metadata,
-- not exchange credentials. Only authenticated backend endpoints use them.
alter table public.runsession
  add column if not exists recovery_id text not null default '',
  add column if not exists generation integer not null default 0,
  add column if not exists last_resume_id text not null default '';

-- Reconnect tickets are bound to an existing execution, even if its saved
-- library macro was edited or deleted. They cannot become a new macro launch.
alter table public.runnerlaunchticket
  add column if not exists session_id integer;
alter table public.runnerlaunchticket alter column user_macro_id drop not null;
