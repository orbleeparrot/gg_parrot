-- Bounded shared Korean community headlines. Existing application DB only;
-- no auth/account/order tables are changed. Apply before the feature deploy.
-- Deliberately matches SQLModel's non-null fields without changing old data.
CREATE TABLE IF NOT EXISTS public.domesticcommunityitem (
    id varchar NOT NULL PRIMARY KEY,
    title varchar NOT NULL,
    url varchar NOT NULL,
    source varchar NOT NULL,
    published varchar NOT NULL,
    published_ms bigint NOT NULL,
    collected_ms bigint NOT NULL,
    asset_symbols_json varchar NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_domesticcommunityitem_published_ms
    ON public.domesticcommunityitem (published_ms);

CREATE TABLE IF NOT EXISTS public.domesticcommunitystate (
    scope varchar NOT NULL PRIMARY KEY,
    status varchar NOT NULL,
    last_success_ms bigint NOT NULL,
    last_attempt_ms bigint NOT NULL,
    failures integer NOT NULL,
    sources_json varchar NOT NULL,
    listings_json varchar NOT NULL
);

-- Only the backend owner reads/writes these projections. Public visitors use
-- the bounded, sanitized FastAPI endpoint, not the Supabase Data API tables.
ALTER TABLE public.domesticcommunityitem ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.domesticcommunitystate ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.domesticcommunityitem FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.domesticcommunitystate FROM PUBLIC;
DO $$
DECLARE target_role text;
BEGIN
    FOREACH target_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.domesticcommunityitem, public.domesticcommunitystate FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;

-- Canonical prepared news, shared across regions/users/tickers.
CREATE TABLE IF NOT EXISTS public.trendarticle (
    id varchar NOT NULL PRIMARY KEY,
    region varchar NOT NULL,
    source_id varchar NOT NULL,
    content_type varchar NOT NULL,
    published_ms bigint NOT NULL,
    collected_ms bigint NOT NULL,
    item_json varchar NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trendarticle_region_published
    ON public.trendarticle (region, published_ms);
CREATE TABLE IF NOT EXISTS public.trendsourcestate (
    source_id varchar NOT NULL PRIMARY KEY,
    region varchar NOT NULL,
    status varchar NOT NULL,
    last_attempt_ms bigint NOT NULL,
    last_success_ms bigint NOT NULL,
    next_attempt_ms bigint NOT NULL,
    source_json varchar NOT NULL
);
CREATE TABLE IF NOT EXISTS public.trendsummary (
    day varchar NOT NULL PRIMARY KEY,
    status varchar NOT NULL,
    updated_ms bigint NOT NULL,
    next_attempt_ms bigint NOT NULL,
    overviews_json varchar NOT NULL,
    references_json varchar NOT NULL
);
ALTER TABLE public.trendarticle ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trendsourcestate ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trendsummary ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.trendarticle, public.trendsourcestate, public.trendsummary FROM PUBLIC;
DO $$
DECLARE target_role text;
BEGIN
    FOREACH target_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.trendarticle, public.trendsourcestate, public.trendsummary FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;
