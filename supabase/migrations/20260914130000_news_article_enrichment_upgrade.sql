-- Upgrade installations that applied the initial article schema already.
-- Preserve every existing row and queue it for one enrichment check.
ALTER TABLE public.newsarticle
    ADD COLUMN IF NOT EXISTS enrichment_pending BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS ix_newsarticle_enrichment
    ON public.newsarticle (enrichment_pending, last_seen_ms);
