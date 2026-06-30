-- Amazon A+ 内容(图文详情页模块)。一个本地产品一份 A+,modules_json 存结构化模块。
-- 内容侧(AI 文案 + 引用 COS 图)由本仓负责;提交到 Amazon 需中间层补 A+ Content API(后续)。
CREATE TABLE IF NOT EXISTS amazon_aplus (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    local_product_id  INTEGER NOT NULL UNIQUE REFERENCES local_products(id),
    modules_json      TEXT    NOT NULL DEFAULT '{}',   -- {modules:[...]} 结构化模块
    content_ref_key   TEXT    NOT NULL DEFAULT '',     -- 提交后回填 Amazon contentReferenceKey
    status            TEXT    NOT NULL DEFAULT 'draft', -- draft/submitted/approved/error
    last_error        TEXT    NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_amazon_aplus_lp ON amazon_aplus(local_product_id);
