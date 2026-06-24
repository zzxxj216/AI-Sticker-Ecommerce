-- 缓存 AI 生成的 Shopify 文案(hero/卖点/场景/SEO),避免每次打开预览页都重新
-- 调用 AI(慢 ~18s 且每次内容都不同)。NULL = 尚未生成 → 预览页首次生成并写入,
-- 之后直接读缓存;点「重新生成文案」会覆盖此列。body_html 由该内容每次实时拼装
-- (纯函数,瞬时),所以只缓存内容本身。
ALTER TABLE local_products ADD COLUMN shopify_content_json TEXT;
