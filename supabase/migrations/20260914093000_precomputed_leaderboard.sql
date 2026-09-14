-- Backend-owned leaderboard read models. HTTP readers apply current permissions;
-- private strategy JSON never belongs in the shared public_json projection.
CREATE TABLE IF NOT EXISTS public.leaderboardsnapshotcontrol (
    key TEXT PRIMARY KEY,
    current_version TEXT NOT NULL DEFAULT '',
    claim_token TEXT NOT NULL DEFAULT '',
    lease_until_ms BIGINT NOT NULL DEFAULT 0,
    next_refresh_ms BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS public.leaderboardsnapshotversion (
    id TEXT PRIMARY KEY,
    date_kst TEXT NOT NULL,
    created_ms BIGINT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_leaderboardsnapshotversion_date_kst
    ON public.leaderboardsnapshotversion (date_kst);
CREATE INDEX IF NOT EXISTS ix_leaderboardsnapshotversion_created_ms
    ON public.leaderboardsnapshotversion (created_ms);
CREATE TABLE IF NOT EXISTS public.leaderboardsnapshotitem (
    version_id TEXT NOT NULL,
    entry_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    public_json TEXT NOT NULL,
    PRIMARY KEY (version_id, entry_id)
);
CREATE INDEX IF NOT EXISTS ix_leaderboard_snapshot_rank
    ON public.leaderboardsnapshotitem (version_id, rank);
CREATE TABLE IF NOT EXISTS public.leaderboardentrystats (
    entry_id INTEGER PRIMARY KEY,
    likes INTEGER NOT NULL DEFAULT 0,
    dislikes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS public.leaderboardchallengebot (
    date_kst TEXT NOT NULL,
    slot INTEGER NOT NULL,
    entry_id INTEGER NOT NULL,
    PRIMARY KEY (date_kst, slot)
);
ALTER TABLE public.leaderboardsnapshotcontrol ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leaderboardsnapshotversion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leaderboardsnapshotitem ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leaderboardentrystats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leaderboardchallengebot ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.leaderboardsnapshotcontrol,
    public.leaderboardsnapshotversion, public.leaderboardsnapshotitem,
    public.leaderboardentrystats, public.leaderboardchallengebot
    FROM PUBLIC, anon, authenticated;
