-- Shopify 一键同步:① 本地默认价格(每产品单独填,推送时作为变体价格)
-- ② shopify_products 同步跟踪表(单店,一个本地产品一行,记录 Shopify 商品 id /
-- handle / 同步状态)。价格为 NULL 表示未设置 → 同步前必须先填价。
ALTER TABLE local_products ADD COLUMN default_price REAL;

CREATE TABLE IF NOT EXISTS shopify_products (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id   INTEGER NOT NULL UNIQUE REFERENCES local_products(id),
    shopify_product_id TEXT    NOT NULL DEFAULT '',      -- 平台商品 id(同步成功后)
    handle             TEXT    NOT NULL DEFAULT '',      -- 平台 URL handle
    seller_sku         TEXT    NOT NULL DEFAULT '',      -- 变体 SKU(来自 master.seller_sku)
    status             TEXT    NOT NULL DEFAULT 'draft',  -- Shopify 商品状态 draft/active/archived
    sync_status        TEXT    NOT NULL DEFAULT 'draft',  -- 本地同步态 draft/syncing/synced/failed
    last_error         TEXT    NOT NULL DEFAULT '',
    created_at         INTEGER NOT NULL,
    synced_at          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shopify_products_lp ON shopify_products(local_product_id);
