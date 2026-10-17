-- 코인동향 소식 목록 네 개(국내/해외 × 뉴스/커뮤니티)를 DB 에서 한 쪽(20개)씩 읽는다(2026-10-18).
-- 예전엔 요청마다 목록 전체(최대 1,000~2,000건)를 메모리에 만들고 잘라 줬고, 해외 커뮤니티(Binance Square)는
-- 표 newsarticle 전체(11만 행 · 656MB)를 JSON 으로 풀어 첫 쪽이 최대 12.8초였다.
-- 기존 데이터는 바꾸지 않는다 — 새 칸은 기본값으로 생기고, 앱 수집기가 첫 회차에 채운다(검색 칸이 빈 행 · 연결 없는 글).
-- 앱 기동 보정(db._PG_ADDED_COLUMNS · _PG_INDEXES)과 같은 정의다. 병합 전에 적용한다.

ALTER TABLE public.trendarticle ADD COLUMN IF NOT EXISTS visible boolean NOT NULL DEFAULT true;
ALTER TABLE public.trendarticle ADD COLUMN IF NOT EXISTS search_text varchar NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_trendarticle_feed
    ON public.trendarticle (region, content_type, published_ms, id);

-- 글–코인 연결: 보이는 글 하나가 언급한 코인 하나. 코인 거르기 · 코인 이름 검색 · 언급 수를 인덱스로 센다.
CREATE TABLE IF NOT EXISTS public.feedasset (
    feed varchar NOT NULL,
    item_id varchar NOT NULL,
    asset varchar NOT NULL,
    published_ms bigint NOT NULL,
    PRIMARY KEY (feed, item_id, asset)
);
CREATE INDEX IF NOT EXISTS ix_feedasset_lookup
    ON public.feedasset (feed, asset, published_ms, item_id);

-- 백엔드만 읽고 쓴다. 공개 방문자는 FastAPI 를 거친다(Data API 표 직접 접근 없음).
ALTER TABLE public.feedasset ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.feedasset FROM PUBLIC;
DO $$
DECLARE target_role text;
BEGIN
    FOREACH target_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.feedasset FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;
