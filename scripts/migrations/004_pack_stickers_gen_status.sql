-- =============================================================================
-- pack_stickers: add generation tracking columns
-- =============================================================================
-- A.3 split phase needs the same lifecycle states as pack_previews:
--   pending -> generating -> ok / error, with retry-able state.
--   model_used + prompt_text + generated_at parallel pack_previews columns.
-- =============================================================================

ALTER TABLE pack_stickers ADD COLUMN prompt_text       TEXT    DEFAULT '';
ALTER TABLE pack_stickers ADD COLUMN model_used        TEXT    DEFAULT '';
ALTER TABLE pack_stickers ADD COLUMN generation_status TEXT    NOT NULL DEFAULT 'pending';
ALTER TABLE pack_stickers ADD COLUMN generated_at      INTEGER;
CREATE INDEX IF NOT EXISTS idx_pack_stickers_status ON pack_stickers(generation_status);
