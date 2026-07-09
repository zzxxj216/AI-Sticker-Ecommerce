-- Etsy 一键同步: 一个本地产品(master)对应一个 Etsy listing。
-- 仿 shopify_products(024)/amazon_listings(030)的"master → 平台 listing"映射表模式:
-- pack (1)─(1) local_product (1)─(1) etsy listing。
-- 中间层(multi-channel-api /etsy/sync-pack)建草稿并回 listing_id, 存进 etsy_listing_id。
CREATE TABLE IF NOT EXISTS etsy_products (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id   INTEGER NOT NULL UNIQUE REFERENCES local_products(id),
    etsy_listing_id    TEXT    NOT NULL DEFAULT '',       -- 平台 listing id(同步成功回填)
    seller_sku         TEXT    NOT NULL DEFAULT '',
    price              REAL,                               -- 实际提交价(TK 价 × 倍率)
    copy_json          TEXT    NOT NULL DEFAULT '{}',      -- 缓存 title/description/tags/materials
    status             TEXT    NOT NULL DEFAULT 'draft',   -- Etsy listing 态 draft/active/inactive
    sync_status        TEXT    NOT NULL DEFAULT 'draft',   -- 本地同步态 draft/syncing/synced/failed
    last_error         TEXT    NOT NULL DEFAULT '',
    created_at         INTEGER NOT NULL,
    synced_at          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_etsy_products_lp ON etsy_products(local_product_id);
