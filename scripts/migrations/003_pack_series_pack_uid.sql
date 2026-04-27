-- =============================================================================
-- pack_series.pack_uid — minted on first preview generation (A.3)
-- =============================================================================
-- A pack_uid scopes ALL artifacts a single series will eventually produce
-- (previews, stickers, video, product, images). Schema-wise pack_uid was
-- previously only on packs (the A.4 aggregate row), but artifacts start
-- materialising during A.3 — so we lift the uid to pack_series and let
-- the packs row reuse it when the operator saves the series as a pack.
--
-- Allowed empty until A.3 runs; minted as ``{YYYYMMDD}_{slug}_{4hex}``
-- by PreviewGenService.ensure_pack_uid().
-- =============================================================================

ALTER TABLE pack_series ADD COLUMN pack_uid TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pack_series_pack_uid ON pack_series(pack_uid);
