-- =============================================================================
-- pack_series.is_selected — operator-controlled "include this series in the pack"
-- =============================================================================
-- A.2 generates N series per topic_plan, but the operator may want to exclude
-- weak ones before A.3 spends image-gen budget on previews. Default to 1 so
-- existing rows stay visible; UI toggles via /v2/topic-plans/{id}/series/{idx}/select.
-- =============================================================================

ALTER TABLE pack_series ADD COLUMN is_selected INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_pack_series_selected ON pack_series(plan_id, is_selected);
