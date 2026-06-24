-- Shopify 专属商品图(独立一套,不与 TikTok 共用主版本图集)。每个本地产品一组,
-- 由固定的 Shopify 样式生成器写入(全 AI 图生图)。Shopify 同步/预览优先用这套图,
-- 没有时回退主版本图。style_key 标记固定样式槽位(overview/laptop/bottle/handhold/flatlay)。
CREATE TABLE IF NOT EXISTS shopify_product_images (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id INTEGER NOT NULL REFERENCES local_products(id),
    role             TEXT    NOT NULL DEFAULT 'secondary',   -- main | secondary
    style_key        TEXT    NOT NULL DEFAULT '',            -- overview|laptop|bottle|handhold|flatlay
    source           TEXT    NOT NULL DEFAULT 'shopify_ai',
    local_path       TEXT    NOT NULL DEFAULT '',
    sort_order       INTEGER NOT NULL DEFAULT 0,
    ai_prompt        TEXT    NOT NULL DEFAULT '',
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shopify_product_images_lp
    ON shopify_product_images(local_product_id);
