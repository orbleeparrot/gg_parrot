-- Additive log search metadata; never rewrite legacy facts or trading state.
ALTER TABLE public.runsessionevent
  ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS component TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS event_name TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS operation_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_runsessionevent_session_operation_id_id
  ON public.runsessionevent (session_id, operation_id, id);

-- Remain backend-private. Application owners are integer IDs, not auth.uid().
ALTER TABLE public.runsessionevent ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.runsessionevent FROM PUBLIC, anon, authenticated;
