-- 개발자 노트 — 관리자 [공지]를 AI 가 노트 양식으로 정리한 결과. 화면은 서버 API 로만 읽는다(Data API 비공개).
CREATE TABLE IF NOT EXISTS public.devnote (
    id serial PRIMARY KEY,
    post_id integer NOT NULL,
    payload_json text NOT NULL DEFAULT '',
    created_at varchar NOT NULL DEFAULT '',
    created_ms bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_devnote_post_id ON public.devnote (post_id);
CREATE INDEX IF NOT EXISTS ix_devnote_created_ms ON public.devnote (created_ms);
ALTER TABLE public.devnote ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.devnote FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.devnote_id_seq FROM PUBLIC, anon, authenticated;
