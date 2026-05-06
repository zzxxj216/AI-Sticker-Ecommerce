-- Caption variants: AI now generates multiple styled captions in one
-- shot (POV / showcase / trend / story / question), the operator picks
-- one. We store the full set as JSON so the operator can re-pick later
-- without re-generating, plus which one was picked.

ALTER TABLE tk_videos ADD COLUMN caption_variants TEXT NOT NULL DEFAULT '[]';
ALTER TABLE tk_videos ADD COLUMN caption_variant_idx INTEGER;
