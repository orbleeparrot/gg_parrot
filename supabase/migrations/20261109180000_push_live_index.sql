-- 앱(PWA) 알림 — 아직 안 끝난 기기별 전송만 담는 부분 인덱스(2026-11-09 리뷰).
-- 발송 바퀴가 엔드포인트마다 차례 20건을 찾을 때(LATERAL … ORDER BY id LIMIT 20) 끝난 기록 · 밀린 줄 전체를 훑지 않게 한다.
-- 쿼리에 state IN ('pending', 'sending') 조건이 함께 있어야 쓰인다. 서버 기동 보정(db._PG_PARTIAL_INDEXES)과 같은 정의.
CREATE INDEX IF NOT EXISTS ix_pushdelivery_live ON public.pushdelivery (subscription_id, id) WHERE state IN ('pending', 'sending');
