-- Multi-SKU products: per-SKU variant image override. When set, the multi-
-- channel-api publish call uses this exact path as the SKU thumbnail instead
-- of falling back to the pack cover. Lets the operator pick a freshly-
-- generated AI master image (local_product_images) for each variant.
ALTER TABLE lab_msku_product_skus ADD COLUMN image_path TEXT NOT NULL DEFAULT '';
