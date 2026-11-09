-- 앱(PWA) 알림 — 엔드포인트 배정 차례 · 맡기 인덱스 교체(2026-11-09 리뷰).
--
-- ① pushsubscription.served_ms: 마지막으로 발송 바퀴에 배정된 시각. 맡을 엔드포인트를 이 값이 오랜 순으로 골라
--    성공 여부와 무관하게 돌아가며 배정한다(받은 시각 last_ok_ms 로 고르면 실패만 하는 엔드포인트가 앞자리를 계속 차지한다).
-- ② ix_pushdelivery_live_gen: 엔드포인트마다 차례 20건(LATERAL … ORDER BY id LIMIT 20)을 세대(sub_version)까지 키로 찾는다.
--    세대가 키에 없으면 Postgres 가 '구독 = 세대' 조건의 선택도를 곱해 대기 전체를 비트맵으로 훑는 계획이 나온다.
--    20261109180000 의 ix_pushdelivery_live (subscription_id, id) 는 같은 이름으로 정의를 바꿀 수 없어(IF NOT EXISTS 는
--    있는 인덱스를 그대로 둔다) 새 이름으로 만들고, 다 만든 뒤 옛 것을 지운다.
-- ③ ix_pushdelivery_live_expiry: 만료 정리가 만료 시각 순으로 지난 것만 읽게(만료가 0건이어도 표 전체를 훑지 않게).
-- 서버 기동 보정(db._PG_ADDED_COLUMNS · _PG_PARTIAL_INDEXES · _PG_DROPPED_INDEXES)과 같은 정의.
ALTER TABLE public.pushsubscription ADD COLUMN IF NOT EXISTS served_ms BIGINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_pushdelivery_live_gen ON public.pushdelivery (subscription_id, sub_version, id) WHERE state IN ('pending', 'sending');
CREATE INDEX IF NOT EXISTS ix_pushdelivery_live_expiry ON public.pushdelivery (expires_ms, id) WHERE state IN ('pending', 'sending');
DROP INDEX IF EXISTS public.ix_pushdelivery_live;
