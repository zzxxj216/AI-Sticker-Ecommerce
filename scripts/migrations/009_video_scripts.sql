-- Phase 1+2 video script feature for the V2 tk_videos pipeline.
-- Caption-only (no voiceover), adjustable scene durations, docx export.
-- Note: table names prefixed tk_ to avoid clashing with the legacy V1
-- ``video_scripts`` table owned by src/services/ops/db.py.

CREATE TABLE IF NOT EXISTS tk_video_script_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    DEFAULT '',
    music_style     TEXT    DEFAULT '',
    scene_blueprint TEXT    NOT NULL DEFAULT '[]',
    is_default      INTEGER NOT NULL DEFAULT 0,
    is_archived     INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tk_video_scripts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          INTEGER NOT NULL,
    template_id       INTEGER,
    template_name     TEXT    NOT NULL DEFAULT '',
    variant_label     TEXT    NOT NULL DEFAULT 'A',
    status            TEXT    NOT NULL DEFAULT 'draft',
    total_duration_s  INTEGER NOT NULL DEFAULT 0,
    music_style       TEXT    NOT NULL DEFAULT '',
    music_suggestions TEXT    NOT NULL DEFAULT '[]',
    scenes_json       TEXT    NOT NULL DEFAULT '[]',
    ai_model          TEXT    DEFAULT '',
    raw_main_text     TEXT    DEFAULT '',
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    FOREIGN KEY (video_id)    REFERENCES tk_videos(id)                  ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES tk_video_script_templates(id)  ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tk_video_scripts_video_id ON tk_video_scripts(video_id);
