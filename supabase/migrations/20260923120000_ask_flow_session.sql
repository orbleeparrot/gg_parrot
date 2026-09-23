-- 물어볼까 v2 — 흐름 세션 컬럼. 서버가 기동 때 자동으로도 적용하지만 기록을 남긴다.
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS candidates_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS chosen_symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS expires_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE askmacrosession ADD COLUMN IF NOT EXISTS ask_count INTEGER NOT NULL DEFAULT 0;
