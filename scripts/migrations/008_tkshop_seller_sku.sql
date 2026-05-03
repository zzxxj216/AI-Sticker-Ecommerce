-- =============================================================================
-- tkshop_products: seller_sku + multi-channel-api self-heal columns
-- =============================================================================
-- Adds the columns needed to talk to the new multi-channel-api endpoint
--   POST /api/v1/tiktok/products/sticker_publish
-- and to support AI-driven self-heal retries on publish failure.
--
--   seller_sku         : seller-side SKU we send to TikTok
--                        (format: INK-{SLUG}-{N}, see prompts.py)
--   tiktok_sku_id      : TikTok-side SKU id returned by sticker_publish
--   auto_fix_attempts  : how many times the AI self-heal loop has rewritten
--                        the payload for this product
--   last_fix_diff      : human-readable trail of what AI changed in each
--                        retry (one line per attempt, newline-separated)
-- =============================================================================

ALTER TABLE tkshop_products ADD COLUMN seller_sku        TEXT NOT NULL DEFAULT '';
ALTER TABLE tkshop_products ADD COLUMN tiktok_sku_id     TEXT NOT NULL DEFAULT '';
ALTER TABLE tkshop_products ADD COLUMN auto_fix_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tkshop_products ADD COLUMN last_fix_diff     TEXT NOT NULL DEFAULT '';
