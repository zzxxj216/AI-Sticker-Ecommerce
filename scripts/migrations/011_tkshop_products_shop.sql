-- Add shop column to tkshop_products so each row belongs to a TikTok shop.
-- Existing rows default to 'main' (the original single-shop setup).
-- The sibling multi-channel-api wrapper resolves credentials per-shop via
-- TIKTOK_SHOPS_JSON; our service passes ?shop=<name> on every product call.

ALTER TABLE tkshop_products ADD COLUMN shop TEXT NOT NULL DEFAULT 'main';
CREATE INDEX IF NOT EXISTS idx_tkshop_products_shop ON tkshop_products(shop);
