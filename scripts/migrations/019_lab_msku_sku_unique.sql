-- 多 SKU 产品：数据库层唯一约束兜底。
-- 应用层 (add_sku_from_pack) 已经保证 seller_sku 与 sales_attribute_value
-- 在同一 product 内不冲突，但去重的「读-改-写」分两次连接、非同一事务，
-- 并发写同一 product 时理论上存在 TOCTOU 漏检。直接 add_sku 也不做去重。
-- 这两条唯一索引把不变量下沉到 DB，作为最终防线。
--
-- 前置检查：迁移前已确认线上数据无 (product_id, seller_sku) /
-- (product_id, sales_attribute_value) 重复，故建索引不会失败。

CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_msku_skus_unique_seller_sku
    ON lab_msku_product_skus(product_id, seller_sku);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_msku_skus_unique_attr
    ON lab_msku_product_skus(product_id, sales_attribute_value);
