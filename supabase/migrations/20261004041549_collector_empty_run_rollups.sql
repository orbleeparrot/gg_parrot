-- Additive upgrade: old releases keep writing individual rows with run_count=1.
-- Empty automatic rounds share one row per engine/status/start hour; NULL
-- buckets leave all work, errors and manual diagnostics as individual history.
SET LOCAL lock_timeout = '2s';
ALTER TABLE public.collectorrun ADD COLUMN IF NOT EXISTS run_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE public.collectorrun ADD COLUMN IF NOT EXISTS empty_bucket_ms BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_collectorrun_empty_bucket
    ON public.collectorrun (engine, status, empty_bucket_ms);
