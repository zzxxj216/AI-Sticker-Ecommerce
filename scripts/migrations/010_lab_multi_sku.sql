-- 实验室：多 SKU 产品（一个 listing 多个变体）。
-- 独立模块，不影响现有 tkshop_products 单 SKU 流程。

CREATE TABLE IF NOT EXISTS lab_msku_products (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id              INTEGER,                                -- 来自 hot_topics.id
    title                 TEXT    NOT NULL DEFAULT '',
    description_html      TEXT    NOT NULL DEFAULT '',
    selling_points        TEXT    NOT NULL DEFAULT '[]',
    keywords              TEXT    NOT NULL DEFAULT '[]',
    category_id           TEXT    NOT NULL DEFAULT '928016',
    sales_attribute_name  TEXT    NOT NULL DEFAULT 'Theme',
    primary_pack_id       INTEGER,                                -- 主图源 pack
    publish_status        TEXT    NOT NULL DEFAULT 'draft',
    tiktok_product_id     TEXT    NOT NULL DEFAULT '',
    detail_main_raw_text  TEXT    DEFAULT '',
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    published_at          INTEGER
);

CREATE TABLE IF NOT EXISTS lab_msku_product_skus (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id               INTEGER NOT NULL,
    pack_id                  INTEGER,                             -- 来自 packs.id（可选）
    seller_sku               TEXT    NOT NULL,
    sales_attribute_value    TEXT    NOT NULL DEFAULT '',
    price_amount_override    TEXT    NOT NULL DEFAULT '',         -- 空 = 走 FIXED_CONFIG
    stock_override           INTEGER,                             -- NULL = 走 FIXED_CONFIG
    sort_order               INTEGER NOT NULL DEFAULT 1,
    tiktok_sku_id            TEXT    NOT NULL DEFAULT '',
    created_at               INTEGER NOT NULL,
    FOREIGN KEY (product_id) REFERENCES lab_msku_products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lab_msku_skus_product ON lab_msku_product_skus(product_id);
