-- Video narration (dubbing) for the V2 pipeline.
-- A silent uploaded video (tk_videos.local_video_path) is analyzed by Gemini,
-- given an English voiceover (ElevenLabs) + synced burned-in subtitles, and a
-- narrated mp4 is produced. One row per generation (newest = active).
CREATE TABLE IF NOT EXISTS tk_video_narrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    gemini_model    TEXT    NOT NULL DEFAULT '',
    voice_id        TEXT    NOT NULL DEFAULT '',
    tts_model       TEXT    NOT NULL DEFAULT '',
    duration_s      REAL    NOT NULL DEFAULT 0,
    atempo          REAL    NOT NULL DEFAULT 1.0,
    product_name    TEXT    NOT NULL DEFAULT '',
    summary         TEXT    NOT NULL DEFAULT '',
    narration_text  TEXT    NOT NULL DEFAULT '',
    analysis_json   TEXT    NOT NULL DEFAULT '{}',
    segments_json   TEXT    NOT NULL DEFAULT '[]',
    srt_text        TEXT    NOT NULL DEFAULT '',
    audio_path      TEXT    NOT NULL DEFAULT '',
    video_path      TEXT    NOT NULL DEFAULT '',
    error           TEXT    NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    FOREIGN KEY (video_id) REFERENCES tk_videos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tk_video_narrations_video ON tk_video_narrations(video_id);
