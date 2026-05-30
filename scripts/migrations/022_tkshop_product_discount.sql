-- 商品自动折扣追踪。发布成功后自动给商品挂 N% 折扣(走中间层 → TikTok Promotion
-- 活动)。这几列记录折扣状态,用于:① 幂等(避免重复建活动)② 详情页展示
-- ③ 失败后手动重试。
ALTER TABLE tkshop_products ADD COLUMN discount_percent     REAL    NOT NULL DEFAULT 0;
ALTER TABLE tkshop_products ADD COLUMN discount_status      TEXT    NOT NULL DEFAULT '';
ALTER TABLE tkshop_products ADD COLUMN discount_activity_id TEXT    NOT NULL DEFAULT '';
ALTER TABLE tkshop_products ADD COLUMN discount_applied_at  INTEGER;
