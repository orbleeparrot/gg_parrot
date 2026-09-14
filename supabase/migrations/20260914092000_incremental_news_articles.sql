-- Prepared article projections are shared by public news and agent readers.
-- Direct API access is disabled; the backend applies ownership/position mapping.
CREATE TABLE IF NOT EXISTS public.newsarticlefeed (
    asset_symbol TEXT PRIMARY KEY,
    revision BIGINT NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    ready_count INTEGER NOT NULL DEFAULT 0,
    updated_ms BIGINT NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS public.newsarticle (
    asset_symbol TEXT NOT NULL,
    article_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    ready BOOLEAN NOT NULL DEFAULT FALSE,
    enrichment_pending BOOLEAN NOT NULL DEFAULT FALSE,
    item_json TEXT NOT NULL,
    assessment_json TEXT NOT NULL DEFAULT '{}',
    analysis_source TEXT NOT NULL DEFAULT 'rule',
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    first_seen_ms BIGINT NOT NULL,
    last_seen_ms BIGINT NOT NULL,
    PRIMARY KEY (asset_symbol, article_id)
);
CREATE INDEX IF NOT EXISTS ix_newsarticle_feed_revision
    ON public.newsarticle (asset_symbol, ready, revision);
CREATE INDEX IF NOT EXISTS ix_newsarticle_last_seen ON public.newsarticle (last_seen_ms);
CREATE INDEX IF NOT EXISTS ix_newsarticle_enrichment ON public.newsarticle (enrichment_pending, last_seen_ms);
CREATE TABLE IF NOT EXISTS public.newsmaintenancelease (
    name TEXT PRIMARY KEY,
    next_run_ms BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE public.newsarticlefeed ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.newsarticle ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.newsmaintenancelease ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.newsarticlefeed, public.newsarticle, public.newsmaintenancelease
    FROM PUBLIC, anon, authenticated;
