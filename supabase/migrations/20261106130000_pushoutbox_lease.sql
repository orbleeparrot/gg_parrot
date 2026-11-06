-- 앱(PWA) 알림 대기열 — 보내고 나서야 sent_ms 를 채운다(2026-11-06 리뷰).
-- 꺼낸 프로세스는 lease_ms 까지 맡고(그 사이 다른 바퀴가 꺼내지 않음), 꺼낼 때마다 attempts 를 올려 늦게 끝난 옛 작업이
-- 새 작업의 결과를 덮지 못하게 한다. 잠깐 막힌 기기(429 · 5xx · 연결 실패)만 다시 보내도록 이미 끝낸 기기를 done_json 에 적는다.
-- 20261106120000 이 먼저 적용된 DB 에도 칸을 더한다. 서버 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의.
ALTER TABLE public.pushoutbox ADD COLUMN IF NOT EXISTS lease_ms bigint NOT NULL DEFAULT 0;
ALTER TABLE public.pushoutbox ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE public.pushoutbox ADD COLUMN IF NOT EXISTS done_json varchar NOT NULL DEFAULT '';
