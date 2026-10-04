-- 근거용 뉴스 제목 아카이브 — 제목·날짜·출처·URL 만 무기한 보관한다(본문은 기존대로 30 일).
-- asset_symbol 은 코인(BTC)이다. 서버가 기동 때 자동으로도 만들지만 기록을 남긴다.
CREATE TABLE IF NOT EXISTS public.newsheadlinearchive (
    archive_key varchar(64) PRIMARY KEY,
    asset_symbol varchar(20) NOT NULL DEFAULT '',
    published_ms bigint NOT NULL DEFAULT 0,
    title varchar(500) NOT NULL DEFAULT '',
    source varchar(120) NOT NULL DEFAULT '',
    url varchar(1000) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_newsheadlinearchive_asset_published
    ON public.newsheadlinearchive (asset_symbol, published_ms);
ALTER TABLE public.newsheadlinearchive ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.newsheadlinearchive FROM PUBLIC, anon, authenticated;
