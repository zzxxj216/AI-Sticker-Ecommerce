-- TikTok 广告人群测试与优质人群沉淀系统 (Phase 1 schema).
--
-- Goal: the early-stage business has little data, so we actively run small-
-- budget ad experiments to *generate* data, auto-analyze per-audience results,
-- and distill the few audiences that actually perform into a reusable library.
--
-- Design notes:
--   * The first-class entity is the AUDIENCE (tk_ad_audiences), not the ad.
--     Products/videos are just carriers used to test audiences. Each experiment
--     fixes creative + offer + bid and varies ONLY the audience across ad groups,
--     so per-adgroup performance attributes cleanly back to one audience.
--   * Both shop-product and video promotions trace back to a pack via pack_id —
--     packs are the real profit unit, so the audience library is pack-aware.
--   * Metrics follow the proven tk_display_video_snapshots pattern: a per-day
--     snapshot table. Unlike display (append-only), ad reports are restated at
--     T+1, so we UNIQUE(adgroup, stat_date) and upsert instead of stacking rows.
--   * TikTok Marketing API ad-group learning phase makes early data noisy. The
--     promotion to status='winning' is gated in the service layer by minimum
--     spend / conversions / days-run thresholds (env-configurable) — schema only
--     carries the status vocabulary.

-- Advertiser accounts (TikTok Marketing API is keyed by advertiser_id, distinct
-- from the Shop OAuth open_id). shop links back to the multi-shop registry.
CREATE TABLE IF NOT EXISTS tk_ads_accounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    advertiser_id  TEXT    NOT NULL UNIQUE,
    name           TEXT    NOT NULL DEFAULT '',
    shop           TEXT    NOT NULL DEFAULT '',
    currency       TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'active',
    created_at     INTEGER NOT NULL
);

-- Audience library — the core asset we want to accumulate and reuse.
CREATE TABLE IF NOT EXISTS tk_ad_audiences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    kind                TEXT    NOT NULL DEFAULT 'targeting',
    targeting_json      TEXT    NOT NULL DEFAULT '{}',
    tiktok_audience_id  TEXT    NOT NULL DEFAULT '',
    hypothesis          TEXT    NOT NULL DEFAULT '',
    source              TEXT    NOT NULL DEFAULT 'ai_generated',
    status              TEXT    NOT NULL DEFAULT 'candidate',
    pack_id             INTEGER REFERENCES packs(id),
    created_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tk_ad_audiences_status ON tk_ad_audiences(status);
CREATE INDEX IF NOT EXISTS idx_tk_ad_audiences_pack   ON tk_ad_audiences(pack_id);

-- One audience experiment == one TikTok campaign (N ad groups, same creative).
CREATE TABLE IF NOT EXISTS tk_ad_experiments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    advertiser_id       TEXT    NOT NULL,
    promote_type        TEXT    NOT NULL,
    promote_ref_id      TEXT    NOT NULL,
    pack_id             INTEGER REFERENCES packs(id),
    tiktok_campaign_id  TEXT    NOT NULL DEFAULT '',
    objective           TEXT    NOT NULL DEFAULT '',
    per_adgroup_budget  REAL    NOT NULL DEFAULT 0,
    currency            TEXT    NOT NULL DEFAULT '',
    status              TEXT    NOT NULL DEFAULT 'draft',
    decision_summary    TEXT    NOT NULL DEFAULT '',
    created_at          INTEGER NOT NULL,
    started_at          INTEGER,
    ended_at            INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tk_ad_experiments_pack   ON tk_ad_experiments(pack_id);
CREATE INDEX IF NOT EXISTS idx_tk_ad_experiments_status ON tk_ad_experiments(status);

-- Test cell: experiment x audience -> one TikTok ad group.
CREATE TABLE IF NOT EXISTS tk_ad_groups (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id      INTEGER NOT NULL REFERENCES tk_ad_experiments(id),
    audience_id        INTEGER NOT NULL REFERENCES tk_ad_audiences(id),
    tiktok_adgroup_id  TEXT    NOT NULL DEFAULT '',
    budget             REAL    NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'pending',
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tk_ad_groups_experiment ON tk_ad_groups(experiment_id);
CREATE INDEX IF NOT EXISTS idx_tk_ad_groups_audience   ON tk_ad_groups(audience_id);
CREATE INDEX IF NOT EXISTS idx_tk_ad_groups_adgroup    ON tk_ad_groups(tiktok_adgroup_id);

-- Per-ad-group per-day report snapshot. T+1 restatement -> upsert on
-- (tiktok_adgroup_id, stat_date). raw_json keeps the full middle-layer
-- response for debugging / backfilling new metric columns later.
CREATE TABLE IF NOT EXISTS tk_ad_metrics_snapshots (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tiktok_adgroup_id  TEXT    NOT NULL,
    stat_date          TEXT    NOT NULL,
    spend              REAL    NOT NULL DEFAULT 0,
    impressions        INTEGER NOT NULL DEFAULT 0,
    clicks             INTEGER NOT NULL DEFAULT 0,
    conversions        INTEGER NOT NULL DEFAULT 0,
    orders             INTEGER NOT NULL DEFAULT 0,
    gmv                REAL    NOT NULL DEFAULT 0,
    ctr                REAL    NOT NULL DEFAULT 0,
    cpa                REAL    NOT NULL DEFAULT 0,
    roas               REAL    NOT NULL DEFAULT 0,
    currency           TEXT    NOT NULL DEFAULT '',
    raw_json           TEXT    NOT NULL DEFAULT '{}',
    fetched_at         INTEGER NOT NULL,
    UNIQUE(tiktok_adgroup_id, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_tk_ad_metrics_adgroup_date ON tk_ad_metrics_snapshots(tiktok_adgroup_id, stat_date);
