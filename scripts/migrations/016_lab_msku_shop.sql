-- Multi-SKU products: shop dimension (which TikTok store this listing belongs
-- to) so multi-SKU products respect the same multi-shop routing as the
-- single-SKU tkshop_products flow.
ALTER TABLE lab_msku_products ADD COLUMN shop TEXT NOT NULL DEFAULT '';
