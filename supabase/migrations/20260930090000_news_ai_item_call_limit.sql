-- Lifetime provider-call caps, separate from prunable translation/summary caches.
CREATE TABLE IF NOT EXISTS public.newsaiitembudget (
    budget_key varchar(96) PRIMARY KEY,
    calls integer NOT NULL DEFAULT 0
);
ALTER TABLE public.newsaiitembudget ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.newsaiitembudget FROM PUBLIC, anon, authenticated;
