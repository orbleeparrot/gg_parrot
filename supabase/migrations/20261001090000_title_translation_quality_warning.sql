-- 제목 번역 검증 완화 — 숫자·통화·티커·영어 잔존 검사는 번역을 막지 않고 사유만 남긴다.
-- 서버가 기동 때 자동으로도 적용하지만 기록을 남긴다.
ALTER TABLE newstitletranslation ADD COLUMN IF NOT EXISTS quality_warning TEXT NOT NULL DEFAULT '';
