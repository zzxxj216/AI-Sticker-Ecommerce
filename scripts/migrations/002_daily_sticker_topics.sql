-- =============================================================================
-- Daily sticker topic collector
-- =============================================================================
-- Stores each daily run, its three selected sticker-pack-ready topics, and any
-- source/reference images downloaded from the pages found by web search.
-- =============================================================================

CREATE TABLE IF NOT EXISTS daily_sticker_topic_runs (
  id              TEXT PRIMARY KEY,
  run_date        TEXT NOT NULL,
  started_at      INTEGER NOT NULL,
  finished_at     INTEGER,
  status          TEXT NOT NULL DEFAULT 'running',
  region          TEXT DEFAULT 'US',
  providers       TEXT DEFAULT '[]',
  queries         TEXT DEFAULT '[]',
  total_results   INTEGER DEFAULT 0,
  selected_count  INTEGER DEFAULT 0,
  storage_dir     TEXT DEFAULT '',
  error           TEXT DEFAULT '',
  raw_summary     TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_daily_sticker_runs_date
  ON daily_sticker_topic_runs(run_date, started_at);

CREATE TABLE IF NOT EXISTS daily_sticker_topics (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                     TEXT NOT NULL REFERENCES daily_sticker_topic_runs(id),
  rank                       INTEGER NOT NULL,
  topic_key                  TEXT DEFAULT '',
  title                      TEXT NOT NULL,
  summary                    TEXT DEFAULT '',
  reason_for_sticker_pack    TEXT DEFAULT '',
  sticker_ideas              TEXT DEFAULT '[]',
  keywords                   TEXT DEFAULT '[]',
  source_urls                TEXT DEFAULT '[]',
  score_json                 TEXT DEFAULT '{}',
  risk_notes                 TEXT DEFAULT '',
  metadata_path              TEXT DEFAULT '',
  hot_topic_id               INTEGER,
  created_at                 INTEGER NOT NULL,
  UNIQUE(run_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_daily_sticker_topics_run
  ON daily_sticker_topics(run_id, rank);
CREATE INDEX IF NOT EXISTS idx_daily_sticker_topics_hot_topic
  ON daily_sticker_topics(hot_topic_id);

CREATE TABLE IF NOT EXISTS daily_sticker_topic_images (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id       INTEGER NOT NULL REFERENCES daily_sticker_topics(id),
  source_url     TEXT NOT NULL,
  local_path     TEXT NOT NULL,
  public_url     TEXT DEFAULT '',
  mime_type      TEXT DEFAULT '',
  file_hash      TEXT DEFAULT '',
  size_bytes     INTEGER DEFAULT 0,
  width          INTEGER DEFAULT 0,
  height         INTEGER DEFAULT 0,
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_sticker_images_topic
  ON daily_sticker_topic_images(topic_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_sticker_images_topic_hash
  ON daily_sticker_topic_images(topic_id, file_hash);
