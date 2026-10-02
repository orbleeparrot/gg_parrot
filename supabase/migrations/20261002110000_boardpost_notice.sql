-- 게시판 [공지] — 관리자만 올리고 목록의 모든 쪽 맨 위에 고정한다. 서버 기동 때도 보강하지만 기록을 남긴다.
ALTER TABLE boardpost ADD COLUMN IF NOT EXISTS is_notice BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_boardpost_is_notice ON boardpost (is_notice);
