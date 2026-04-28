-- =============================================================================
-- tk_videos.blotato_account_id — operator-selected Blotato account ID
-- =============================================================================
-- Distinct from account_open_id (which is a TikTok Display API open_id used
-- by B.2 metrics refresh). For B.1 publish we now pass Blotato's own
-- numeric account id straight through to publish_tiktok_video — operator
-- picks from the dropdown sourced from Blotato.get_tiktok_accounts().
-- =============================================================================

ALTER TABLE tk_videos ADD COLUMN blotato_account_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_tk_videos_blotato_account ON tk_videos(blotato_account_id);
