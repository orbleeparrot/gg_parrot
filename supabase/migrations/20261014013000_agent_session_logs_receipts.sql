-- Additive backend-private session audit metadata; preserve all existing events.
-- Legacy event_key stays NULL, so old/runner events are not coalesced or lost.
ALTER TABLE public.runsessionevent
  ADD COLUMN IF NOT EXISTS event_key TEXT,
  ADD COLUMN IF NOT EXISTS details_json TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS ux_runsessionevent_session_key
  ON public.runsessionevent (session_id, event_key);
CREATE INDEX IF NOT EXISTS ix_runsessionevent_session_id_id
  ON public.runsessionevent (session_id, id);

-- Accounts authenticate via the backend, not Supabase Auth UUIDs/Data API.
-- Keep the existing backend-only access model (no new policies or public grants).
ALTER TABLE public.runsessionevent ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.runsessionevent FROM PUBLIC, anon, authenticated;
