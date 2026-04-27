-- =============================================================================
-- tk_videos.tiktok_video_id — the *actual* TikTok video ID (not Blotato's
-- postSubmissionId), captured by polling Blotato GET /posts/{id} after
-- publish completes. Used by B.2 metrics refresh to do precise matching
-- against TikTok Display API's get_video_list output instead of best-effort
-- "newest video on the account".
-- =============================================================================

ALTER TABLE tk_videos ADD COLUMN tiktok_video_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_tk_videos_tiktok_video_id ON tk_videos(tiktok_video_id);
