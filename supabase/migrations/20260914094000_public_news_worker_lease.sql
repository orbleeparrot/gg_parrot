-- Shared ownership of public news preparation across backend workers.
CREATE TABLE IF NOT EXISTS public.publicnewslease (
    scope TEXT PRIMARY KEY,
    token TEXT NOT NULL DEFAULT '',
    lease_until_ms BIGINT NOT NULL DEFAULT 0,
    next_run_ms BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE public.publicnewslease ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.publicnewslease FROM PUBLIC, anon, authenticated;
