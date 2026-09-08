-- Runner version bookkeeping.
-- runsession.runner_version: what the runner reported when it started (v7+ only;
-- older executables leave it empty). runnerlaunchticket.rejected_*: an outdated
-- runner answered a web launch and was refused, so the wizard can say so at once
-- instead of waiting for a claim that never comes.
alter table public.runsession
  add column if not exists runner_version varchar not null default '';

alter table public.runnerlaunchticket
  add column if not exists rejected_at varchar not null default '',
  add column if not exists rejected_version varchar not null default '';
