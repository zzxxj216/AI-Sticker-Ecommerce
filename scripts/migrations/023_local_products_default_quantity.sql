-- 本地默认库存。每个本地产品(master)可单独设置一个默认库存数量,推送/上架时
-- 用作 listing 的 quantity。NULL 表示未设置 → 回退到 env 的 TKSHOP_DEFAULT_QUANTITY。
-- 这是「本地数字」,不直接改线上库存;只影响下次推送写入 TikTok 的初始库存。
ALTER TABLE local_products ADD COLUMN default_quantity INTEGER;
