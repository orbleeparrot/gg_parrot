-- Run ONLY in disposable PostgreSQL, never in the linked/production database.
\set ON_ERROR_STOP on
CREATE ROLE anon;
CREATE ROLE authenticated;
CREATE TABLE public.runsessionevent (
  id BIGSERIAL PRIMARY KEY, session_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
  ts TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, created_ms BIGINT NOT NULL
);
INSERT INTO public.runsessionevent(session_id,user_id,ts,kind,message,created_ms)
  VALUES (1,42,'2026-10-12T01:00:00+00:00','fill','legacy execution retained',1791766800000);
\ir ../../supabase/migrations/20261012013512_agent_session_logs_receipts.sql
\ir ../../supabase/migrations/20261013050656_agent_session_log_structured_fields.sql
-- Reapplying each additive migration must remain safe.
\ir ../../supabase/migrations/20261012013512_agent_session_logs_receipts.sql
\ir ../../supabase/migrations/20261013050656_agent_session_log_structured_fields.sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.runsessionevent WHERE id=1 AND user_id=42
    AND message='legacy execution retained' AND event_key IS NULL
    AND severity='' AND component='' AND event_name='' AND operation_id='') THEN
    RAISE EXCEPTION 'legacy event was rewritten';
  END IF;
  IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid='public.runsessionevent'::regclass) THEN
    RAISE EXCEPTION 'RLS disabled';
  END IF;
  IF has_table_privilege('anon','public.runsessionevent','SELECT')
     OR has_table_privilege('authenticated','public.runsessionevent','SELECT') THEN
    RAISE EXCEPTION 'public API roles can read private logs';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE tablename='runsessionevent'
      AND indexname='ix_runsessionevent_session_operation_id_id') THEN
    RAISE EXCEPTION 'operation index missing';
  END IF;
END $$;
INSERT INTO public.runsessionevent(session_id,user_id,ts,kind,message,created_ms,event_key,severity,component,event_name,operation_id)
  VALUES (1,42,'2026-10-13T01:00:00+00:00','order','reported',1791853200000,
    'command:5:reported:1','INFO','runner','command.execution_reported','command:5')
  ON CONFLICT (session_id,event_key) DO NOTHING;
INSERT INTO public.runsessionevent(session_id,user_id,ts,kind,message,created_ms,event_key)
  VALUES (1,42,'2026-10-13T01:00:00+00:00','order','retry must not replace',1791853200000,
    'command:5:reported:1') ON CONFLICT (session_id,event_key) DO NOTHING;
DO $$ BEGIN
  IF (SELECT count(*) FROM public.runsessionevent) <> 2 THEN
    RAISE EXCEPTION 'retry duplicate persisted';
  END IF;
END $$;
SELECT 'legacy preserved; idempotent; RLS/private privileges; operation index; retry dedup verified' AS result;
