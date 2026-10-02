-- 내 에이전트 종료 기록 보관 — 보관한 기록은 30건·30일 자동 정리에서 빠진다(계정당 10건까지).
-- 서버가 기동 때 자동으로도 적용하지만 기록을 남긴다.
ALTER TABLE runsession ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;
