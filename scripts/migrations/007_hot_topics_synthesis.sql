-- =============================================================================
-- hot_topics: synthesis fields (A.1.5)
-- =============================================================================
-- Adds two columns so a synthesized theme is just another hot_topic row:
--   theme_summary       : markdown that the synthesis AI produced (operator
--                         reads this to decide whether to send the theme to A.2)
--   parent_topic_ids    : JSON array of the source hot_topic IDs that fed
--                         the synthesis (lets us trace evidence)
--
-- Synthesized rows have source='synthesized' so the existing source filter
-- on /v2/hot-topics shows them as their own bucket.
-- =============================================================================

ALTER TABLE hot_topics ADD COLUMN theme_summary    TEXT NOT NULL DEFAULT '';
ALTER TABLE hot_topics ADD COLUMN parent_topic_ids TEXT NOT NULL DEFAULT '[]';
