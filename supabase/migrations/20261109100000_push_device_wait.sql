-- 앱(PWA) 알림 — 기기별 다음 발송 시각과 알림이 끝난 까닭(2026-11-09 리뷰).
-- next_send_ms: 푸시 서비스가 Retry-After 로 기다리라고 한 시각(응답을 받은 시각 기준). 알림이 아니라 기기(엔드포인트)에
--   두어, 한 기기의 대기가 같은 알림의 다른 기기를 늦추지 않고 그사이 새로 쌓인 알림도 그 기기에는 기다리게 한다.
-- result: delivered · gave_up · expired · no_device · broken — '알림이 안 왔다' 문의를 가를 때 본다.
-- 서버 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의.
ALTER TABLE public.pushsubscription ADD COLUMN IF NOT EXISTS next_send_ms bigint NOT NULL DEFAULT 0;
ALTER TABLE public.pushoutbox ADD COLUMN IF NOT EXISTS result varchar NOT NULL DEFAULT '';
