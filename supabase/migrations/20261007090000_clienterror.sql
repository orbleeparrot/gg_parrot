-- 화면 오류 — 브라우저가 보낸 오류를 하루·지문별 한 행으로 모은다(30일 보관, 관리자 '화면 오류' 탭).
-- 오류 문장·익명 화면 경로·빌드만 두고 계정·IP·UA 는 저장하지 않는다. 서버가 기동 때 자동으로도 만들지만 기록을 남긴다.
CREATE TABLE IF NOT EXISTS public.clienterror (
    id bigserial PRIMARY KEY,
    day_kst varchar NOT NULL DEFAULT '',
    fingerprint varchar NOT NULL DEFAULT '',
    kind varchar NOT NULL DEFAULT '',
    route varchar NOT NULL DEFAULT '',
    message varchar NOT NULL DEFAULT '',
    build varchar NOT NULL DEFAULT '',
    count integer NOT NULL DEFAULT 0,
    first_ms bigint NOT NULL DEFAULT 0,
    last_ms bigint NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_clienterror_day_fingerprint ON public.clienterror (day_kst, fingerprint);
CREATE INDEX IF NOT EXISTS ix_clienterror_day_kst ON public.clienterror (day_kst);
CREATE INDEX IF NOT EXISTS ix_clienterror_last_ms ON public.clienterror (last_ms);
ALTER TABLE public.clienterror ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.clienterror FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.clienterror_id_seq FROM PUBLIC, anon, authenticated;
