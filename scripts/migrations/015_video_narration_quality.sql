-- Quality signals for video narration (replaces human micro-tuning in full-auto).
-- needs_review flags rows the offline evaluation should sample (heavy speed-up,
-- shot drift > 1s, low voiceover coverage, audio overran the video).
ALTER TABLE tk_video_narrations ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tk_video_narrations ADD COLUMN warnings TEXT NOT NULL DEFAULT '';
ALTER TABLE tk_video_narrations ADD COLUMN quality_json TEXT NOT NULL DEFAULT '{}';
