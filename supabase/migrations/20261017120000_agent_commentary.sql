-- 껄무새 중계 — 실행 중인 매크로 옆 채팅 말풍선(서버가 만든다)과 세션별 중계 상태.
-- 실행 기록(runsessionevent)과 따로 둔다: 기록 상한이 말풍선을 밀어내거나 말풍선이 활동 로그 원본에 섞이지 않게.
-- 서버가 기동 때 자동으로도 만들지만 기록을 남긴다. 백엔드만 읽고 쓴다(정책 · 공개 권한 없음).
CREATE TABLE IF NOT EXISTS public.agentutterance (
    id bigserial PRIMARY KEY,
    session_id integer NOT NULL,
    user_id integer NOT NULL,
    say_key varchar NOT NULL,
    kind varchar NOT NULL DEFAULT '',
    at_ms bigint NOT NULL DEFAULT 0,
    created_ms bigint NOT NULL DEFAULT 0,
    payload_json varchar NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_agentutterance_session_key ON public.agentutterance (session_id, say_key);
CREATE INDEX IF NOT EXISTS ix_agentutterance_session_id_id ON public.agentutterance (session_id, id);
CREATE INDEX IF NOT EXISTS ix_agentutterance_session_id ON public.agentutterance (session_id);
CREATE INDEX IF NOT EXISTS ix_agentutterance_user_id ON public.agentutterance (user_id);
ALTER TABLE public.agentutterance ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.agentutterance FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.agentutterance_id_seq FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS public.agentcommentarystate (
    session_id integer PRIMARY KEY,
    state_json varchar NOT NULL DEFAULT '{}',
    updated_ms bigint NOT NULL DEFAULT 0
);
ALTER TABLE public.agentcommentarystate ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.agentcommentarystate FROM PUBLIC, anon, authenticated;
