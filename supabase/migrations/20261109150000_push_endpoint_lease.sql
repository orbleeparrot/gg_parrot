-- 앱(PWA) 알림 — 엔드포인트 발송 권한(2026-11-09 리뷰).
-- 일반 · 시험 알림, 모든 웹 프로세스가 이 칸으로 조율한다: 한 바퀴가 send_token 을 적고 send_lease_ms 까지 맡은 동안만 그
-- 엔드포인트로 보낸다. 결과를 반영할 때 풀고, 바퀴가 죽으면 시각이 지나 풀린다. 결과(구독의 실패 누적 · 삭제 포함)는
-- 아직 이 토큰을 가진 바퀴만 쓴다. 서버 기동 보정(db._PG_ADDED_COLUMNS)과 같은 정의.
ALTER TABLE public.pushsubscription ADD COLUMN IF NOT EXISTS send_token varchar NOT NULL DEFAULT '';
ALTER TABLE public.pushsubscription ADD COLUMN IF NOT EXISTS send_lease_ms bigint NOT NULL DEFAULT 0;
