-- 거래소 공식 공지(빗썸 공식 REST /v1/notices, 이후 업비트 공지 WebSocket). 백엔드만 읽고 쓰는 투영 표 —
-- 방문자는 FastAPI /api/trends?sections=notices 로만 본다. 기능 배포 전에 적용한다.
CREATE TABLE IF NOT EXISTS public.exchangenotice (
    id varchar NOT NULL PRIMARY KEY,
    exchange varchar NOT NULL,
    title varchar NOT NULL,
    url varchar NOT NULL,
    categories_json varchar NOT NULL,
    published varchar NOT NULL,
    published_ms bigint NOT NULL,
    modified_ms bigint NOT NULL,
    collected_ms bigint NOT NULL,
    asset_symbols_json varchar NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_exchangenotice_published_ms
    ON public.exchangenotice (published_ms);

CREATE TABLE IF NOT EXISTS public.exchangenoticestate (
    exchange varchar NOT NULL PRIMARY KEY,
    status varchar NOT NULL,
    last_attempt_ms bigint NOT NULL,
    last_success_ms bigint NOT NULL,
    failures integer NOT NULL,
    error varchar NOT NULL
);

ALTER TABLE public.exchangenotice ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.exchangenoticestate ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.exchangenotice FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.exchangenoticestate FROM PUBLIC;
DO $$
DECLARE target_role text;
BEGIN
    FOREACH target_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.exchangenotice, public.exchangenoticestate FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;
