-- Promote local products to first-class entities.
--
-- Before this migration: tkshop_products was the only "product" table, with
-- one row per (pack, shop) combo. The "local product" concept was a filter
-- (tiktok_product_id = '') rather than a real entity, which meant the same
-- pack's title/description/images were duplicated across shop rows and could
-- drift apart after a clone_to_shop.
--
-- After this migration:
--   * local_products is the master catalog — one row per pack, shop-agnostic,
--     owns title / description / SKU / category / default_template / images.
--   * tkshop_products keeps the same columns it had (so the publish payload
--     builder still works unchanged), but now also references local_products
--     via local_product_id. A tkshop_products row represents "this pack's
--     local product has been (or is being) listed on shop X" and stores the
--     per-shop snapshot that was sent to TikTok.
--   * Editing the master does NOT auto-update listings — operators push via
--     the existing "推送到 TikTok" button (re-snapshots master into listing
--     then PUTs). Matches the user-chosen manual-sync policy.
--
-- Image strategy: local_product_images holds master images; on-disk bytes are
-- shared (local_path is the same file). When a new listing is created from
-- master, tkshop_product_images rows get inserted referencing the same
-- local_path (no byte copy). tiktok_image_uri is per-listing because each
-- shop's TikTok upload yields its own URI.

CREATE TABLE IF NOT EXISTS local_products (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id                INTEGER NOT NULL UNIQUE REFERENCES packs(id),
    title                  TEXT    NOT NULL DEFAULT '',
    description_html       TEXT    NOT NULL DEFAULT '',
    selling_points         TEXT    NOT NULL DEFAULT '[]',
    keywords               TEXT    NOT NULL DEFAULT '[]',
    detail_main_raw_text   TEXT    NOT NULL DEFAULT '',
    seller_sku             TEXT    NOT NULL DEFAULT '',
    category_id            TEXT    NOT NULL DEFAULT '928016',
    default_template_json  TEXT    NOT NULL DEFAULT '{}',
    created_at             INTEGER NOT NULL,
    updated_at             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_products_pack ON local_products(pack_id);

CREATE TABLE IF NOT EXISTS local_product_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id  INTEGER NOT NULL REFERENCES local_products(id),
    role              TEXT    NOT NULL DEFAULT 'main',
    source            TEXT    NOT NULL DEFAULT 'manual',
    local_path        TEXT    DEFAULT '',
    sort_order        INTEGER DEFAULT 0,
    ai_prompt         TEXT    DEFAULT '',
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_product_images_lp
    ON local_product_images(local_product_id, sort_order);

ALTER TABLE tkshop_products ADD COLUMN local_product_id INTEGER REFERENCES local_products(id);
CREATE INDEX IF NOT EXISTS idx_tkshop_products_local ON tkshop_products(local_product_id);

-- Backfill: every existing pack with at least one tkshop_products row gets a
-- master row built from its EARLIEST (lowest id) listing's content. Using
-- MIN(id) is deterministic and matches the conventional "first listing was
-- the original draft" assumption.
INSERT INTO local_products (
    pack_id, title, description_html, selling_points, keywords,
    detail_main_raw_text, seller_sku, category_id, default_template_json,
    created_at, updated_at
)
SELECT pr.pack_id, pr.title, pr.description_html, pr.selling_points,
       pr.keywords, pr.detail_main_raw_text, pr.seller_sku,
       pr.category_id, pr.default_template_json,
       pr.created_at, pr.created_at
  FROM tkshop_products pr
 WHERE pr.pack_id IS NOT NULL
   AND pr.id = (SELECT MIN(id) FROM tkshop_products WHERE pack_id = pr.pack_id);

-- Point every existing tkshop_products row at its pack's new master.
UPDATE tkshop_products
   SET local_product_id = (
         SELECT id FROM local_products WHERE pack_id = tkshop_products.pack_id
       )
 WHERE local_product_id IS NULL;

-- Backfill master images from the earliest listing's image set. We copy
-- metadata rows only — the on-disk file at local_path is shared with the
-- listing's tkshop_product_images row, no byte duplication.
INSERT INTO local_product_images (
    local_product_id, role, source, local_path, sort_order, ai_prompt, created_at
)
SELECT lp.id, ti.role, ti.source, ti.local_path, ti.sort_order, ti.ai_prompt, ti.created_at
  FROM local_products lp
  JOIN tkshop_products pr
    ON pr.pack_id = lp.pack_id
   AND pr.id = (SELECT MIN(id) FROM tkshop_products WHERE pack_id = lp.pack_id)
  JOIN tkshop_product_images ti ON ti.product_id = pr.id;
