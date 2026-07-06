# 亚马逊 Listing 内容工作台 · 功能设计

> 更新:2026-06-24(替换早期"个性化生产闭环"草案 —— 那部分超范围,已废弃)。
> **本设计只覆盖内容侧**:文案 / 图片 / A+ 三层内容的 **AI 生成 → 本地预览 → 推送上架 Amazon**。
> 不含订单、买家个性化数据回收、出图生产、发货履约(均不归本工具)。
> 决策基线:美国站 + FBM · 品牌已备案 · 图片走对象存储/CDN。

---

## 0. 心智模型:3 个内容层 × 2 类产品

### 三个内容层(每层都 AI 生成 + 本地展示/预览 + 推送)
1. **① 文案层** —— 标题、五点描述(bullet×5)、产品描述、search_terms,**外加变体/父体相关文字**。
2. **② 图片层** —— 主图、副图等(AI 生成 / 合成)。
3. **③ A+ 层** —— A+ 详情页模块(图文)。

每层统一节奏:
```
AI 生成 → 本地页面展示(可编辑) → 预览确认 → 推送上架到 Amazon(默认草稿)
```

### 两类产品(两套独立页面、独立数据源)
| | **A. 卡贴包** | **B. 半定制产品** |
|---|---|---|
| 数据源 | 现有 `packs → local_products`(复用) | **独立产品库**(新建,不走 pack 管线) |
| 页面 | 专属 Amazon 预览/编辑页(一套) | 专属 Amazon 预览/编辑页(另一套) |
| 路由前缀 | `/v2/amazon/packs/...` | `/v2/amazon/custom/...` |
| 三层内容 | 文案 / 图片 / A+ | 文案 / 图片 / A+(同骨架) |

**关键**:两类产品**页面独立、数据源独立**,但底层 3 层 AI 生成工具**共用同一套服务**
(只是喂入的素材源不同)。所以服务层做成 source-agnostic,页面层各做一套。

---

## 1. 三个内容层(共用 AI 生成骨架)

### ① 文案层 —— `amazon/copy_generator.py`
- **两步法**:`text_complete`(亚马逊风格创意稿)→ `extract_json`(结构化)。
- **产出**:`item_name`(≤200,含主词/风格/数量)、`bullet_points×5`、`product_description`、
  `search_terms`(≤250 bytes)、属性枚举(color/material/theme/size…)。
- **变体/父体文字**:父体级总标题/总描述 + 每个子体的差异化短文案;提示里强调
  "买家只选一个变体,文案别暗示全发"(沿用 lab_multi_sku 的约束经验)。
- **缓存**:存 `copy_json`,页面首访生成、之后读缓存;"重新生成文案"覆盖。可编辑后回存。

### ② 图片层 —— `amazon/image_studio.py` + `amazon/cdn.py`
- **主图 / 副图**:AI 生成或在底图上合成(走 Gemini image 管线,复用现有并发限流)。
- **硬前提**:亚马逊服务端抓图,**必须公网 URL** → 成图先 `cdn.upload()` 拿稳定 URL。
- **页面**:图集预览(主图 + 副图 N 张),可重生成/替换/排序。
- 副图常见类型(待你定清单):尺寸示意、使用场景、材质特写、变体一览、包装。

### ③ A+ 层 —— `amazon/aplus.py`
- **模块化**:banner / 对比表 / 场景图 等标准模块;AI 出模块文案 + 按尺寸合成模块设计图(Gemini)→ CDN。
- **层级**:A+ 挂在**父 ASIN**(变体族共享)。
- **前提**:品牌已备案 ✓,允许 A+;中间层需补 A+ 端点(§5)。
- **建议**:放最后做(roadmap 一致)。

---

## 2. 两套页面(各自的预览/编辑工作台)

仿现有"Shopify 有预览、TikTok 有专属预览+可编辑界面"的范式,Amazon 两类各做一套专属页:

### A. 卡贴包 Amazon 工作台
- `GET /v2/amazon/packs/` —— 列表(从 local_products 选源)。
- `GET /v2/amazon/packs/{id}` —— 专属页:三层内容分区展示(文案可编辑 / 图集 / A+),
  顶部"推送到 Amazon(草稿)"。
- 变体族(可选):Size × 设计 二维矩阵(见 §3 备注)。

### B. 半定制产品 Amazon 工作台
- `GET /v2/amazon/custom/` —— 独立产品库列表。
- `POST /v2/amazon/custom/new` —— **录入一个半定制产品**(字段/来源**待你设计**,见 §7)。
- `GET /v2/amazon/custom/{id}` —— 专属页:同样三层内容 + 推送。
- 半定制特性:Custom 相关字段/定制说明放在文案/属性层(具体看中间层 Custom 能力探针结论)。

> V2 页坑:`.ads-page` 类页面用 CSS `transform`,弹窗须 reparent 到 `<body>`(见 CLAUDE.md)。

---

## 3. 数据模型(迁移 030–039)

做成 **source-agnostic 发布层**:一张 listing 表用 `source_type` 区分两类产品,
3 层内容各自落库,**复用同一套生成/推送服务**。

### 030_amazon_listings.sql —— 可发布单元(单品或变体族父体)
```sql
CREATE TABLE IF NOT EXISTS amazon_listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT    NOT NULL,                 -- 'pack' | 'custom'
    source_id       INTEGER,                          -- pack 的 local_product_id 或 custom 库 id
    marketplace_id  TEXT    NOT NULL DEFAULT 'ATVPDKIKX0DER',
    product_type    TEXT    NOT NULL DEFAULT 'STICKER_DECAL',
    variation_theme TEXT    NOT NULL DEFAULT '',       -- 空=单品;如 'SizeName-PatternName'
    copy_json       TEXT    NOT NULL DEFAULT '{}',     -- ① 文案层缓存(父体级)
    aplus_json      TEXT    NOT NULL DEFAULT '{}',     -- ③ A+ 模块缓存
    parent_asin     TEXT    NOT NULL DEFAULT '',       -- 推送成功后回填
    status          TEXT    NOT NULL DEFAULT 'draft',  -- 平台态 draft/active/inactive/error
    sync_status     TEXT    NOT NULL DEFAULT 'draft',  -- 本地态 draft/syncing/synced/failed
    submission_id   TEXT    NOT NULL DEFAULT '',
    last_error      TEXT    NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_amazon_listings_src ON amazon_listings(source_type, source_id);
```

### 031_amazon_listing_skus.sql —— 子体(变体 = Size × 设计)
```sql
CREATE TABLE IF NOT EXISTS amazon_listing_skus (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    INTEGER NOT NULL REFERENCES amazon_listings(id) ON DELETE CASCADE,
    source_ref    INTEGER,                       -- 该子体的设计来源(pack_id 或 custom item)
    size_name     TEXT    NOT NULL DEFAULT '',   -- 尺寸维:如 3" / 5" / 6"
    design_value  TEXT    NOT NULL DEFAULT '',   -- 设计维:如某 pack 名/图案名
    seller_sku    TEXT    NOT NULL DEFAULT '',   -- INK-{SLUG}-{id}-{SIZE},跨渠道统一
    price         REAL,                          -- NULL=未设,推送前必填
    child_asin    TEXT    NOT NULL DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_amazon_skus_combo
    ON amazon_listing_skus(listing_id, size_name, design_value);
```

### 032_amazon_images.sql —— ② 图片层(主副图 + CDN)
```sql
CREATE TABLE IF NOT EXISTS amazon_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    INTEGER NOT NULL REFERENCES amazon_listings(id) ON DELETE CASCADE,
    sku_id        INTEGER REFERENCES amazon_listing_skus(id),  -- NULL=父体/通用图
    role          TEXT    NOT NULL DEFAULT 'main',  -- main/secondary/size_chart/scene/...
    local_path    TEXT    NOT NULL DEFAULT '',
    cdn_url       TEXT    NOT NULL DEFAULT '',       -- 公网 URL(推送用)
    ai_prompt     TEXT    NOT NULL DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_amazon_images_listing ON amazon_images(listing_id);
```

### 033_amazon_custom_catalog.sql —— B 类独立产品库(字段待细化)
```sql
-- 半定制产品的独立产品库(不走 pack 管线)。具体录入字段/来源待设计,先放最小骨架。
CREATE TABLE IF NOT EXISTS amazon_custom_catalog (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL DEFAULT '',
    attrs_json   TEXT    NOT NULL DEFAULT '{}',   -- 定制项/素材引用等(待设计)
    status       TEXT    NOT NULL DEFAULT 'draft',
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER
);
```

> 034–039 预留:A+ 模块明细表(若 `aplus_json` 不够用)、CDN 资产映射、变体模板等。
> **备注**:变体(Size×设计)是否两类产品都用、还是只卡贴包用 —— 数据模型已支持,UI 按需开。

---

## 4. 服务层(`src/services/amazon/`)

route 薄、service 直接操作 sqlite、AI 全走 `AIRouter`;3 层工具 source-agnostic。

```
src/services/amazon/
  __init__.py
  copy_generator.py   # ① 文案:两步 AI(父体+子体差异),缓存 copy_json
  image_studio.py     # ② 图片:主副图生成/合成(Gemini)
  aplus.py            # ③ A+:模块文案 + 合成模块图
  cdn.py              # 对象存储上传 → 公网 URL(②③ 共用)
  listings.py         # 建/组织 listing + 变体矩阵(source: pack | custom)
  custom_catalog.py   # B 类独立产品库 CRUD
  payload.py          # 拼 SP-API attributes(父 parentage_level + variation_theme + 子 child_parent 关系)
  sync.py             # build_preview(无网络) + push(父+子一起,默认 draft)
```

---

## 5. 跨仓:中间层(multi-channel-api :8000)需补端点

本仓只走 HTTP。建议新建 `docs/amazon_contract.md` 记录契约(仿 `tiktok_ads_contract.md`)。
响应沿用 `{success, message, data}` 信封。

| 端点 | 用途 | 现状 |
|---|---|---|
| `GET /amazon/product-types/STICKER_DECAL` | 实时字段 schema(权威) + 合法 variation_theme | 待核实 |
| `PUT /amazon/listings/{sku}` | 建/改 listing(父/子,含变体关系) | 部分? |
| `GET /amazon/listings/{sku}` | 查处理结果/状态 | 待核实 |
| `POST /amazon/images` 或随 listing | 关联图片(公网 URL) | 待核实 |
| `POST /amazon/aplus/*` | A+ 建文档/上图/关联 ASIN/提交 | **缺,后做** |
| (半定制)Custom 配置端点 | 若 B 类需定制器配置 | **待探针确认** |

---

## 6. 触发点 & 流程

- **触发点 = 各专属页的按钮**(仿 Shopify 隐藏 form POST → `run_async` 异步派发 → service):
  - 每层:`生成 / 重新生成`(文案 / 图片 / A+ 各一组)。
  - 整体:`推送到 Amazon(草稿)` —— 父+子一起提交,默认 draft,激活是独立一步。
- **列表页**:批量"推送 Amazon"(跳过未设价/缺图的)。
- **调度**(可选):无需常驻;推送是即时操作。同步状态查询可做一个轻 job。

---

## 7. 仍需你设计/拍板的细节

1. **半定制独立产品库的录入** —— B 类产品**从哪来、录入哪些字段**(`amazon_custom_catalog.attrs_json` 现是占位)。这是 B 线的起点,优先级最高。
2. **三层 AI 的具体细节** —— 五点描述/属性枚举的字段清单;副图类型清单;A+ 模块清单。
3. **尺寸档位** —— 卖哪几个尺寸(3″/5″/6″?),决定变体矩阵的尺寸维。
4. **CDN 选型** —— S3 / R2 / 七牛(阻塞 `cdn.py` 和图片层)。
5. **价格策略** —— 各 SKU 定价。
6. **能力探针(阶段 0)** —— 试调 `GET product-types` + 试建草稿,确认变体 theme 合法值、
   以及半定制(Custom)到底能否纯 API 配。**解锁后续的关键不确定项,建议先做。**

---

## 8. 分期建议

- **阶段 0 · 探针**:product-type schema + 变体 theme 合法值 + (半定制)Custom 可编程性。
- **阶段 1 · 卡贴包 · 文案+图片(单变体)**:CDN + `copy_generator` + `image_studio` + 单品上品(draft)。先把卡贴包能上美国站跑通。
- **阶段 2 · 卡贴包 · 变体矩阵**:Size × 设计 父子体合并提交。
- **阶段 3 · 半定制产品库 + 专属页**:B 类录入 + 三层内容(依赖 §7.1 你定录入)。
- **阶段 4 · A+ 层**:两类共用,品牌已备案,中间层补 A+ 端点后做。

---

## 9. 护栏

- 上品默认 **draft**,激活独立显式一步。
- 价格 NULL / 缺主图 不允许推送(快速失败 + 提示)。
- 图片必须 **CDN 公网 URL**(硬前提)。
- 变体一致性校验(theme 合法、SKU 组合唯一、变体数不超限)。
- SP-API / CDN 凭证只在中间层;本仓不碰 OAuth/HMAC。
