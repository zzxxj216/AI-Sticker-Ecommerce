-- Amazon listing 工作台(卡贴包线)。一个本地产品(master)对应一条 Amazon listing。
-- 只做内容侧:文案(copy_json 缓存)+ 图片(amazon_images,本地图→COS 公网 URL)+ 推送跟踪。
-- 变体(Size×设计)留后续:先单变体(listing 自身即 SKU)。详见 docs/amazon_custom_listing_design.md。

CREATE TABLE IF NOT EXISTS amazon_listings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id  INTEGER NOT NULL UNIQUE REFERENCES local_products(id),
    source_type       TEXT    NOT NULL DEFAULT 'pack',     -- 'pack' | 'custom'(半定制后续)
    marketplace_id    TEXT    NOT NULL DEFAULT 'ATVPDKIKX0DER',  -- 美国站
    product_type      TEXT    NOT NULL DEFAULT 'STICKER_DECAL',
    seller_sku        TEXT    NOT NULL DEFAULT '',          -- 跨渠道统一,来自 master.seller_sku
    price             REAL,                                  -- 售价,NULL=未设(推送前必填)
    copy_json         TEXT    NOT NULL DEFAULT '{}',         -- AI 文案缓存(item_name/bullets/description/属性)
    asin              TEXT    NOT NULL DEFAULT '',           -- 推送成功后回填
    status            TEXT    NOT NULL DEFAULT 'draft',      -- 平台态 draft/accepted/active/inactive/error
    sync_status       TEXT    NOT NULL DEFAULT 'draft',      -- 本地态 draft/pushing/pushed/failed
    submission_id     TEXT    NOT NULL DEFAULT '',
    last_error        TEXT    NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_amazon_listings_lp ON amazon_listings(local_product_id);

-- Amazon 商品图(主图 + 副图)。本地图先传 COS 拿公网 URL,Amazon 服务端抓取。
CREATE TABLE IF NOT EXISTS amazon_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id  INTEGER NOT NULL REFERENCES local_products(id),
    role              TEXT    NOT NULL DEFAULT 'main',       -- main / other
    local_path        TEXT    NOT NULL DEFAULT '',
    cos_url           TEXT    NOT NULL DEFAULT '',           -- 公网 URL(传 COS 后回填)
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_amazon_images_lp ON amazon_images(local_product_id);
