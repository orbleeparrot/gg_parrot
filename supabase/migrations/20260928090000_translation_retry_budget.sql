-- 번역·요약 재시도 예산 — 검증에 걸린 제목/글을 5분마다 무한히 다시 사던 것을 막는다.
-- 서버가 기동 때 자동으로도 적용하지만 기록을 남긴다.
ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS next_retry_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT '';
ALTER TABLE communitypostsummary ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE communitypostsummary ADD COLUMN IF NOT EXISTS next_retry_ms BIGINT NOT NULL DEFAULT 0;
