-- 앱(PWA) 알림을 세 갈래로 나눈다(2026-11-09 설계 리뷰).
--   알림 이벤트(pushoutbox): 누구에게 무슨 일이 — 만료 시각(expires_ms)을 쌓을 때 고정한다.
--   기기 구독(pushsubscription): 계정에 묶인 세대(version) — 계정이 바뀌면 오르고, 옛 세대를 기대한 요청은 서버가 거절한다.
--   기기별 전송(pushdelivery): 알림 하나 × 구독 세대 하나 — 시도 · 다음 시각 · 상태를 기기마다 따로. 맡은 바퀴의 토큰으로만 결과를 쓴다.
-- 이벤트 표의 lease_ms · attempts · done_json 은 기기별 전송 표로 옮겨 지운다. 서버 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의.
ALTER TABLE public.pushsubscription ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE public.pushoutbox ADD COLUMN IF NOT EXISTS expires_ms bigint NOT NULL DEFAULT 0;
ALTER TABLE public.pushoutbox DROP COLUMN IF EXISTS lease_ms;
ALTER TABLE public.pushoutbox DROP COLUMN IF EXISTS attempts;
ALTER TABLE public.pushoutbox DROP COLUMN IF EXISTS done_json;

CREATE TABLE IF NOT EXISTS public.pushdelivery (
    id bigserial PRIMARY KEY,
    event_id integer NOT NULL,
    user_id integer NOT NULL,
    subscription_id integer NOT NULL,
    sub_version bigint NOT NULL DEFAULT 0,
    endpoint varchar NOT NULL DEFAULT '',
    state varchar NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    next_ms bigint NOT NULL DEFAULT 0,
    lease_ms bigint NOT NULL DEFAULT 0,
    claim_token varchar NOT NULL DEFAULT '',
    expires_ms bigint NOT NULL DEFAULT 0,
    created_ms bigint NOT NULL DEFAULT 0,
    finished_ms bigint NOT NULL DEFAULT 0,
    last_outcome varchar NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pushdelivery_event_sub ON public.pushdelivery (event_id, subscription_id, sub_version);
CREATE INDEX IF NOT EXISTS ix_pushdelivery_state_next ON public.pushdelivery (state, next_ms);
CREATE INDEX IF NOT EXISTS ix_pushdelivery_sub ON public.pushdelivery (subscription_id);
CREATE INDEX IF NOT EXISTS ix_pushdelivery_event_id ON public.pushdelivery (event_id);
CREATE INDEX IF NOT EXISTS ix_pushdelivery_user_id ON public.pushdelivery (user_id);
ALTER TABLE public.pushdelivery ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pushdelivery FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.pushdelivery_id_seq FROM PUBLIC, anon, authenticated;
