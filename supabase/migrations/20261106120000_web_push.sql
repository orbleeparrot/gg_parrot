-- 앱(PWA) 알림 — 알림을 켠 기기(브라우저 푸시 구독)와 보낼 알림 대기열.
-- 껄무새 중계 말풍선(매매 · 손익 · 시세 · 기사 등)을 pushoutbox 에 쌓고(기사는 계정 · 글마다 한 번), 웹 프로세스가 꺼내 보낸다.
-- 보내고 나서야 sent_ms 를 채운다 — 보내는 동안은 lease_ms 로 맡고, 잠깐 막힌 기기는 lease_ms 를 미뤄 그 기기에만 다시 보낸다.
-- 서버가 기동 때 자동으로도 만들지만 기록을 남긴다. 백엔드만 읽고 쓴다(정책 · 공개 권한 없음).
CREATE TABLE IF NOT EXISTS public.pushsubscription (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL,
    endpoint varchar NOT NULL,
    p256dh varchar NOT NULL,
    auth varchar NOT NULL,
    platform varchar NOT NULL DEFAULT '',
    created_ms bigint NOT NULL DEFAULT 0,
    updated_ms bigint NOT NULL DEFAULT 0,
    last_ok_ms bigint NOT NULL DEFAULT 0,
    fail_count integer NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pushsubscription_endpoint ON public.pushsubscription (endpoint);
CREATE INDEX IF NOT EXISTS ix_pushsubscription_user_id ON public.pushsubscription (user_id);
ALTER TABLE public.pushsubscription ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pushsubscription FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.pushsubscription_id_seq FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS public.pushoutbox (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL,
    dedupe_key varchar NOT NULL,
    payload_json varchar NOT NULL DEFAULT '',
    created_ms bigint NOT NULL DEFAULT 0,
    sent_ms bigint NOT NULL DEFAULT 0,
    lease_ms bigint NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    done_json varchar NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pushoutbox_user_key ON public.pushoutbox (user_id, dedupe_key);
CREATE INDEX IF NOT EXISTS ix_pushoutbox_sent_id ON public.pushoutbox (sent_ms, id);
ALTER TABLE public.pushoutbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pushoutbox FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.pushoutbox_id_seq FROM PUBLIC, anon, authenticated;
