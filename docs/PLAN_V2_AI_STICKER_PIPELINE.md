# 项目重构计划：AI 卡贴电商数据链 V2

**版本**：v1.0  **创建日期**：2026-04-24  **预计周期**：5 周

---

## 0. 文档说明

### 0.1 项目背景

当前仓库已有 TikTok 视频发布、TikTok 数据抓取、TikTok 选品评审等多条独立链路，但都属于 **探索期实验代码**：页面分散、数据库分散（4 个 sqlite）、各功能未串成端到端数据链。本次重构目标是把"**热点 → 卡贴包 → 视频 → 产品**"四个环节打通成一条可生产的数据链。

### 0.2 范围

| 项目 | 是否在范围内 |
|---|---|
| 卡贴包链路（热点搜集 → 题材 → 预览 → 切分 → 入库） | ✅ |
| TK 视频上传 + AI 文案 + 定时发送 + 数据刷新 | ✅ |
| TKShop 详情页生成 + 主图管理 + 一键发布 + 状态看板 | ✅ |
| 数据库统一到单一 sqlite | ✅ |
| 全部前端页面重构（v2 命名空间） | ✅ |
| 自动化触发（视频高互动 → 自动产品化） | ❌（后期再做，前期人工） |
| Shopify 端 | ❌（已有 product_catalog.py 不动） |

### 0.3 旧代码处置

| 处置 | 范围 |
|---|---|
| **保留** | `trend_fetcher/`（作为热点的备用数据源）；`src/services/ai/` 三个 LLM service 作为底座；`src/services/tiktok/blotato_service.py` 和 `tiktok_display_service.py` 作为发布/数据 API 客户端 |
| **重构** | `src/services/sticker/`、`src/services/batch/`（卡贴生成链路全部按 V2 重写）；`src/web/`（所有模板与路由按新页面框架重写） |
| **废弃** | `agent.db / ops.db / tiktok_trends.db`（数据按需迁移到 ops_workbench.db 后归档） |

### 0.4 验收原则

- 每个模块按 ① 功能说明 ② 前端页面 ③ 记录内容 三件套交付
- 每个数据节点必须有 sqlite 表落地，可在前端查到完整链路
- 所有 AI 生成产物（题材、预览图、单图、文案、详情页）按 `output/packs/{pack_uid}/...` 规范存储，可追溯

### 0.5 输出语言约定（重要）

**操作语言（运营人员看的）= 中文**
- 所有前端 UI 标签、按钮、提示、错误消息
- 题材规划阶段的"题材定位 / 目标用户 / 风格关键词 / 配色描述 / AI 简评"等运营辅助字段
- 数据看板、状态文案

**对外语言（海外用户看的）= 英文**
- TK 视频 caption + hashtags（B.1）
- TKShop 产品 title / description (HTML) / selling_points / keywords（C.1）
- sticker 上的文字内容（A.3 已是英文，沿用）
- AI 主图生成 prompt 中涉及到的图上文字

**实现层面**：
- 题材规划 prompt 同时要求："运营辅助字段用中文，用户可见的 sticker 文字 / sticker 名称 / preview prompt 用英文"
- B.1 / C.1 的生成 prompt 必须显式声明"output in English only, target market is overseas"
- 校验：B.1 / C.1 入库前用正则检测 CJK 字符比例，超阈值标 warning

### 0.6 两步生成模式（Generate → Extract）

**原则**：不让创作型大模型强制输出 JSON（会损失能力 / 增加 hallucination 风险）。改为两步：

1. **主模型（强 / 创作）**：自由格式输出（markdown / 段落 / 列表混排），充分发挥能力
2. **提取模型（轻 / 结构化）**：读主模型输出 + 目标 JSON Schema → 抽取为合规 JSON

**模型选型（建议）**：
- 主模型：`gpt-5.4-pro` / `claude-opus` / `gemini-2.x-pro`（按任务）
- 提取模型：`gpt-4o-mini` / `gpt-4.1-mini` / `claude-haiku`（便宜、JSON mode 稳定）

**适用环节**：

| 环节 | 主模型任务 | 提取模型任务 |
|---|---|---|
| A.2 题材规划 | 自由产出 N 套题材方案（参照毕业季文档风格） | 抽成 `topic_plans.series_payload` JSON |
| B.1 视频文案 | 自由产出多版 caption + hashtag 候选 + 选择理由 | 抽成 `{caption, hashtags[]}` JSON |
| C.1 产品详情 | 自由产出 title 候选 + description 段落 + 卖点列表 + 关键词 | 抽成 `{title, description_html, selling_points[], keywords[]}` JSON |

**记录**：每次任务在 `ai_call_logs` 落 **2 条**记录（main + extract），通过 `related_table + related_id + task` 关联可追溯。

**容错**：
- 提取失败（JSON parse error / Schema 不匹配） → 自动重试 1 次，仍失败时**保留主模型原文**给前端，运营手动整理
- 前端任何字段都允许人工编辑覆盖

---

## 1. 整体架构

### 1.1 数据链总览

```
[A.1 热点搜集] → [A.2 题材规划] → [A.3 预览生成+切分] → [A.4 卡包入库]
                  (人工选+配量)     (GPT image 2.0)            │
                                                       ┌──────┴──────┐
                                                       ↓             ↓
                                            [B 视频文案/发布/数据]  [C TKShop 详情/主图/发布]
                                              ↑                      ↑
                                  (人工选包 + 上传视频)     (人工选包 + 默认模板)
```

### 1.2 模块划分

| 模块 | 职责 | 主要服务文件（计划） |
|---|---|---|
| **公共底座** | DB / 文件存储 / AI Router / 调度 / 前端框架 | `src/core/`、`src/services/ai/`、`src/services/storage/`、`src/scheduler/` |
| **A 卡贴包链路** | 热点 → 题材 → 预览 → 切分 → 卡包 | `src/services/pack/` |
| **B TK 视频链路** | 文案生成 → 发布 → 数据刷新 → 看板 | `src/services/video/`（重写） |
| **C TKShop 链路** | 详情页 → 主图 → 发布 → 状态 | `src/services/tkshop/`（新建） |
| **Web** | FastAPI/Starlette 路由 + Jinja 模板 | `src/web/`（v2 命名空间） |

### 1.3 技术栈

- **后端**：Python 3.11+、FastAPI/Starlette、Jinja2、sqlite3（单库 WAL 模式）
- **AI**：OpenAI（gpt-5.4 web_search、gpt-image 2.0）+ Gemini（备用）+ Tavily/Perplexity（备用 search）
- **调度**：独立脚本进程 + threading.Timer（不引入 Celery，保持轻量）
- **前端**：服务端渲染 + 少量 JS，统一改造现有风格
- **存储**：sqlite（结构化）+ 本地文件系统（图片/视频）

---

## 2. 公共底座

### 2.1 统一数据库

#### 库文件
- 路径：`data/ops_workbench.db`
- 模式：WAL（已开启）
- 旧库废弃：`agent.db / ops.db / tiktok_trends.db` 数据迁移后归档到 `data/backups/legacy_dbs/{ts}/`

#### 表清单（新增 13 张）

| 模块 | 表名 | 用途 |
|---|---|---|
| A | `hot_topics` | 热点池（多源） |
| A | `topic_plans` | 题材规划方案（含 N 套配置） |
| A | `pack_series` | 单个题材方案下的"一套" |
| A | `pack_previews` | 预览图（每套 N 张） |
| A | `pack_stickers` | 切分后的单 sticker |
| A | `packs` | 卡包主体（聚合视图） |
| B | `tk_videos` | 视频元数据 + 文案 + hashtag + 调度时间 + Blotato post_id |
| B | `tk_video_metrics` | 视频时序数据（每次刷新一行） |
| C | `tkshop_products` | 产品本地映射（关联 pack） |
| C | `tkshop_product_images` | 主图/辅图（含 role/source: manual/ai） |
| C | `tkshop_publish_logs` | 发布尝试日志（含失败原因） |
| 公共 | `ai_call_logs` | 所有 AI 调用记录（model/tokens/cost/latency） |
| 公共 | `scheduled_jobs` | 定时任务运行记录 |

#### 关键表结构

```sql
-- A.1 热点池
CREATE TABLE hot_topics (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,         -- openai_websearch / gpt4o_search / tavily / perplexity / tiktok_cc
  query TEXT,                   -- 触发的搜索词（如 "graduation 2026"）
  topic_name TEXT NOT NULL,     -- 标准化主题名
  raw_payload JSON,             -- 原始返回 + 引用链接
  evidence_urls JSON,           -- 引用证据
  hot_score REAL,               -- 综合热度分（多模型对比用）
  region TEXT,
  fetched_at INTEGER,
  status TEXT DEFAULT 'pending' -- pending / selected / archived
);

-- A.2 题材规划方案
CREATE TABLE topic_plans (
  id INTEGER PRIMARY KEY,
  topic_id INTEGER REFERENCES hot_topics(id),
  config JSON,                  -- {n_series, stickers_per_series, previews_per_series, stickers_per_preview}
  main_raw_text TEXT,           -- 主模型自由格式输出（两步生成第 1 步）
  series_payload JSON,          -- 提取模型抽出的结构化 JSON（两步生成第 2 步）
  status TEXT DEFAULT 'draft',  -- draft / approved / generating / done / extract_failed
  created_at INTEGER,
  updated_at INTEGER
);

-- A.2 单个套
CREATE TABLE pack_series (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER REFERENCES topic_plans(id),
  series_idx INTEGER,
  series_name TEXT,             -- "Class of 2026 Black Gold Ceremony Pack"
  style_anchor TEXT,            -- 统一风格锚点 prompt
  palette TEXT,                 -- 配色描述
  pack_archetype TEXT,
  priority TEXT,                -- high/medium/low
  metadata_json JSON            -- 题材样式（C.1 详情页生成复用）
);

-- A.3 预览图
CREATE TABLE pack_previews (
  id INTEGER PRIMARY KEY,
  series_id INTEGER REFERENCES pack_series(id),
  preview_idx INTEGER,
  prompt_text TEXT,
  image_path TEXT,              -- output/packs/{pack_uid}/series_{n}/previews/{idx}.png
  model_used TEXT,
  generation_status TEXT,       -- pending / done / failed
  generated_at INTEGER
);

-- A.3 切分后的单 sticker
CREATE TABLE pack_stickers (
  id INTEGER PRIMARY KEY,
  preview_id INTEGER REFERENCES pack_previews(id),
  sticker_idx INTEGER,
  name TEXT,                    -- "Class of 2026"
  description TEXT,
  image_path TEXT,
  is_selected INTEGER DEFAULT 1
);

-- A.4 卡包聚合
CREATE TABLE packs (
  id INTEGER PRIMARY KEY,
  pack_uid TEXT UNIQUE,         -- 20260424_graduation_a3f9
  series_id INTEGER REFERENCES pack_series(id),
  display_name TEXT,
  cover_image_path TEXT,
  total_stickers INTEGER,
  status TEXT DEFAULT 'active', -- active / archived
  created_at INTEGER
);

-- B.1 TK 视频
CREATE TABLE tk_videos (
  id INTEGER PRIMARY KEY,
  pack_id INTEGER REFERENCES packs(id),
  account_open_id TEXT,
  local_video_path TEXT,
  video_one_liner TEXT,         -- 人工填的一句话描述
  caption_main_raw_text TEXT,   -- 主模型自由产出（含多版候选 + 选择理由，EN）
  caption TEXT,                 -- 提取后的最终 caption（EN）
  hashtags JSON,                -- 提取后的 hashtags（EN）
  scheduled_at INTEGER,         -- 定时发送时间
  blotato_post_id TEXT,
  publish_status TEXT,          -- pending / scheduled / published / failed
  published_at INTEGER,
  publish_error TEXT
);

-- B.2 视频时序数据
CREATE TABLE tk_video_metrics (
  id INTEGER PRIMARY KEY,
  video_id INTEGER REFERENCES tk_videos(id),
  view_count INTEGER,
  like_count INTEGER,
  comment_count INTEGER,
  share_count INTEGER,
  fetched_at INTEGER
);

-- C.1 产品
CREATE TABLE tkshop_products (
  id INTEGER PRIMARY KEY,
  pack_id INTEGER REFERENCES packs(id),
  tiktok_product_id TEXT,        -- 创建后回写
  detail_main_raw_text TEXT,     -- 主模型自由产出（含 title 候选、description 段落、卖点列表、写作思路，EN）
  title TEXT,                    -- 提取后（EN）
  description_html TEXT,         -- 提取 + bleach 清洗后（EN）
  selling_points JSON,           -- 提取后（EN）
  keywords JSON,                 -- 提取后（EN）
  category_id TEXT DEFAULT '928016',
  default_template_json JSON,    -- 价格/库存/重量/SKU 默认模板
  publish_status TEXT,           -- draft / publishing / live / failed / manual_required
  created_at INTEGER,
  published_at INTEGER
);

-- C.2 主图与辅图
CREATE TABLE tkshop_product_images (
  id INTEGER PRIMARY KEY,
  product_id INTEGER REFERENCES tkshop_products(id),
  role TEXT,                     -- main / secondary / lifestyle / scale
  source TEXT,                   -- manual / ai
  local_path TEXT,
  tiktok_image_uri TEXT,         -- 上传后回写
  sort_order INTEGER,
  ai_prompt TEXT,                -- 若 source=ai
  created_at INTEGER
);

-- C.3 发布日志
CREATE TABLE tkshop_publish_logs (
  id INTEGER PRIMARY KEY,
  product_id INTEGER REFERENCES tkshop_products(id),
  attempt_idx INTEGER,
  api_endpoint TEXT,
  request_payload JSON,
  response_payload JSON,
  success INTEGER,
  error_code TEXT,
  error_message TEXT,
  created_at INTEGER
);

-- 公共
CREATE TABLE ai_call_logs (
  id INTEGER PRIMARY KEY,
  service TEXT,         -- openai / gemini / tavily / etc
  model TEXT,
  task TEXT,            -- hot_search / topic_plan / preview_gen / split / video_caption / product_detail
  related_table TEXT,
  related_id INTEGER,
  prompt_summary TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  latency_ms INTEGER,
  cost_estimate REAL,
  status TEXT,
  error TEXT,
  created_at INTEGER
);

CREATE TABLE scheduled_jobs (
  id INTEGER PRIMARY KEY,
  job_name TEXT,        -- tk_metrics_refresh / tk_video_publish_dispatch / tkshop_status_sync
  started_at INTEGER,
  finished_at INTEGER,
  status TEXT,
  affected_rows INTEGER,
  error TEXT
);
```

#### 旧库迁移
- 工具脚本：`scripts/migrate_legacy_dbs.py`（一次性运行）
- 步骤：
  1. 读 `tiktok_trends.db.tk_*` → 写 `ops_workbench.db.hot_topics`（source=tiktok_cc）
  2. 读 `agent.db / ops.db` 的有用业务数据 → 按需迁移
  3. 旧库归档到 `data/backups/legacy_dbs/{ts}/`
- **估时：0.5 d**

### 2.2 文件存储规范

```
output/
└── packs/
    └── {pack_uid}/                  # 例 20260424_graduation_a3f9
        ├── plan.json                # topic_plan.series_payload 镜像
        ├── series_{n}/
        │   ├── style_anchor.txt
        │   ├── previews/
        │   │   ├── 1.png
        │   │   ├── 1.prompt.txt
        │   │   └── ...
        │   └── stickers/
        │       ├── 1.png
        │       ├── 1.meta.json
        │       └── ...
        ├── videos/
        │   └── {video_id}/local.mp4
        └── products/
            └── {tiktok_product_id 或 draft_id}/
                ├── title.txt
                ├── description.html
                ├── selling_points.json
                └── images/
                    ├── main.png
                    ├── secondary_1.png
                    └── ...
```

`pack_uid` 命名：`{YYYYMMDD}_{topic_slug}_{4位hex}`

### 2.3 AI 服务抽象层

#### 目标
- 多模型并行测试（前期 A.1 同时跑 4 个 search 源对比）
- 统一 retry / 计费 / 日志（写 `ai_call_logs`）
- 切换/降级一处改

#### 接口（`src/services/ai/router.py`）

```python
class AIRouter:
    """统一调度 + 落 ai_call_logs"""

    def web_search(self, query: str, providers: list[str], region: str = 'US') -> dict[str, list[Result]]:
        """并行调用多源，返回 {provider: [results]}"""

    def text_complete(self, prompt: str, model: str = 'gpt-5.4-pro', system: str = None) -> str:
        """文本生成（自由格式，不强制 JSON）。用于 A.2 题材规划主模型 / B.1 文案主模型 / C.1 详情主模型"""

    def extract_json(
        self,
        source_text: str,
        schema: dict,                        # JSON Schema
        model: str = 'gpt-4o-mini',          # 轻模型
        related_table: str = None,           # 关联到 ai_call_logs
        related_id: int = None,
    ) -> dict:
        """从主模型自由文本中抽取结构化 JSON。失败自动重试 1 次。"""

    def image_generate(self, prompt: str, model: str = 'gpt-image-2', n: int = 1) -> list[bytes]:
        """图片生成（预览图 / AI 主图）"""

    def image_edit(self, image: bytes, prompt: str, model: str = 'gpt-image-2') -> bytes:
        """图生图（A.3 切分单 sticker / C.2 主图二次加工）"""
```

每次调用前自动写一条 `ai_call_logs`，结束更新 latency / tokens / cost。两步生成的任务会落 2 条 log（task 后缀分别是 `:main` 和 `:extract`）。

### 2.4 调度器

#### 形态
- **独立 Python 进程**：`scripts/scheduler.py`
- 内部用 `threading.Timer` + `time.sleep`，不引入 Celery
- 通过 `start.sh` / `stop.sh` 与 web 进程并行启动

#### 当前 Job 列表

| Job | 间隔 | 动作 |
|---|---|---|
| `tk_metrics_refresh` | 2h | 遍历 `tk_videos` where status=published，调 Display API 拉数据写 `tk_video_metrics` |
| `tk_video_publish_dispatch` | 1min | 遍历 `tk_videos` where status=scheduled and scheduled_at<=now，调 Blotato 发送 |
| `tkshop_status_sync` | 30min | 遍历 `tkshop_products` where status=publishing/manual_required，调 TikTok Shop API 同步状态 |

每个 Job 的入参 / 结果 / 错误写 `scheduled_jobs` 表。启动时回查 missed jobs。

### 2.5 通用前端框架

- **路由前缀**：所有新页面挂 `/v2/...`，旧路由保留只读直至下线
- **公共组件**：
  - 顶部导航：`卡包 / 视频 / 产品 / 数据 / 配置`
  - 左侧子菜单：按模块切
  - **任务进度面板**：统一组件，复用于 A.3 出图 / B 调度 / C 发布
  - **数据流面板**：每个对象（pack/video/product）详情页能展开看「调用日志 + 文件路径 + 数据库行」
- **模板规范**：所有新页面以 `v2_` 前缀

---

## 3. 模块 A：卡贴包制作链路

### A.1 热点搜集

#### 功能说明
**输入**：搜索词（如 `graduation 2026`、`summer trends`）+ 区域（默认 US）+ 启用的 source 列表
**处理**：并行调用以下源，统一落 `hot_topics` 表（source 字段区分）：
1. OpenAI Responses API + `web_search_preview` tool（gpt-5.4 / gpt-4o-search-preview）
2. Tavily Search API + GPT 总结
3. Perplexity sonar models
4. （备用）TikTok Creative Center（沿用 `trend_fetcher`）

**输出**：每个 source 各 N 条候选热点，含：标题、引用链接、热度估值（按引用质量+重复度打分）、AI 简评。

> 前期：四源全开做对比测试，跑 1-2 周后选效果最好的 1-2 个固化下来。

#### 前端页面：**热点池工作台** (`/v2/hot-topics`)

```
┌──────────────────────────────────────────────────────┐
│ [搜索框] 关键词 + 区域 + ✓ OpenAI Tavily Perplexity TKCC  [搜索]│
├──────────────────────────────────────────────────────┤
│ Tab: 全部 / OpenAI / Tavily / Perplexity / TKCC      │
├─────────────┬────────────────────────────────────────┤
│ 热点列表     │ 详情面板                                │
│ ┌─────────┐ │ ┌─────────────────────────────┐       │
│ │ Topic 1 │ │ │ 标题 / 区域 / 热度分          │       │
│ │ source  │ │ │ AI 简评                      │       │
│ │ ★ 热度  │ │ │ 引用链接列表                  │       │
│ │ [选用]  │ │ │ raw_payload (折叠)           │       │
│ └─────────┘ │ │ [→ 进入题材规划]              │       │
│ ...         │ └─────────────────────────────┘       │
└─────────────┴────────────────────────────────────────┘
```

主要交互：
- 搜索按钮 → 后台并行调用所有勾选 source，进度条展示
- 热点卡片可标记 selected/archived
- 「→ 进入题材规划」跳转 A.2，预填 topic_id

#### 记录内容
- 表：`hot_topics`
- 文件：无（结果存 DB）
- AI 日志：`ai_call_logs` 每个 source 一条

#### 估时：**2 d**（含 4 个 source 接入与对比测试）

---

### A.2 题材规划

#### 功能说明
**输入**：选中的 hot_topic + 用户配置：

```json
{
  "n_series": 5,
  "stickers_per_series": 50,
  "previews_per_series": 5,
  "stickers_per_preview": 10
}
```

**处理（两步）**：

**第 1 步 — 主模型自由产出**：调用 GPT-5.4-pro（或 claude-opus），prompt 模板参照 `docs/ChatGPT-毕业季卡贴题材设计.md` 的实际产出风格 —— 主模型用 markdown 自由组织：套数标题、定位、风格关键词、配色、目标用户、风格锚点、5 张预览图各自的 prompt + 10 个 sticker 列表。
- **语言要求**：运营辅助字段（定位 / 配色 / 目标用户）= 中文；sticker 名称 / 描述 / preview prompt / style_anchor = 英文

**第 2 步 — 提取模型转 JSON**：把主模型 markdown 输出 + 下方 schema 喂给 gpt-4o-mini → 抽成结构化 JSON 入库：

```json
{
  "series": [
    {
      "name": "Class of 2026 Black Gold Ceremony Pack",      // EN
      "positioning": "黑金典礼感，最稳的基础爆款",              // ZH
      "style_keywords": ["黑金", "典礼感", "高级"],             // ZH
      "palette": "黑色、金色、白色、香槟色",                    // ZH
      "target_audience": "高中/大学毕业生、家长",               // ZH
      "pack_archetype": "seasonal_festival_pack",               // EN enum
      "priority": "high",                                        // EN enum
      "style_anchor": "luxury graduation sticker pack preview, black gold color palette, ...",  // EN
      "previews": [
        {
          "preview_idx": 1,
          "prompt": "luxury graduation sticker pack preview, ...",    // EN
          "stickers": [
            {"idx": 1, "name": "Class of 2026", "description": "with graduation cap and gold stars"}  // EN
          ]
        }
      ]
    }
  ]
}
```

提取失败时保留主模型原文，前端展示供运营手动整理。

#### 前端页面：**题材规划工作台** (`/v2/topic-plans/new?topic_id=...`)

```
┌─────────────────────────────────────────────────────┐
│ 主题：Graduation 2026     来源：openai_websearch    │
│ ─────────────────────────────────────────────────── │
│ 配置：                                                │
│   套数 [ 5 ]  每套张数 [ 50 ]                       │
│   每套预览数 [ 5 ]  每张预览含 [ 10 ]                │
│   生成模型 [ gpt-5.4-pro ▼ ]                         │
│   [ 生成题材方案 ]                                   │
│ ─────────────────────────────────────────────────── │
│ 生成结果（可手动微调）：                              │
│   ▼ Series 1: Class of 2026 Black Gold Ceremony     │
│     - 风格锚点 (可编辑)                              │
│     - 配色 / 受众 / 优先级                           │
│     - Preview 1-5 (折叠 prompt + 10 sticker 列表)    │
│     - [ ✓ 选用 ] [ ✗ 弃用 ]                          │
│   ▼ Series 2-5...                                    │
│ ─────────────────────────────────────────────────── │
│   [ 保存为草稿 ]    [ 进入预览图生成 → ]             │
└─────────────────────────────────────────────────────┘
```

主要交互：
- 配置后点「生成」→ 流式展示结果
- 每个 series 可勾选/弃用、可编辑 prompt
- 「→ 预览图生成」跳转 A.3，进入选中 series 的批量出图

#### 记录内容
- 表：`topic_plans`（含 config、series_payload 全文、main_raw_text 主模型原文）+ `pack_series`（每套一行）
- 文件：`output/packs/{pack_uid}/plan.json` + `plan.main_raw.md`（主模型自由文本）
- AI 日志：`ai_call_logs` **2 条**（task=`topic_plan:main` + `topic_plan:extract`）

> **DB 微调**：`topic_plans` 表加列 `main_raw_text TEXT`（保留主模型自由输出，便于前端编辑回查）

#### 估时：**2.5 d**（prompt 模板抽取 + 提取模型 schema 调试 + 工作台 + 编辑能力）

---

### A.3 预览图生成 + 切分

#### 功能说明
**两步串行**：
1. **预览图生成**：对每个 selected series，按 5 张预览 prompt 调用 GPT-image 2.0 生成（并发 3-4 张）
2. **切分**：对每张预览图，调用 GPT-image 2.0 的 `image edit` 接口，输入"提取第 N 个 sticker，透明/白底输出"，输出 10 张单图。

切分失败 / 质量差的可单张重试。

#### 前端页面：**预览/切分工作台** (`/v2/packs/{pack_uid}/generate`)

```
┌──────────────────────────────────────────────────────┐
│ Pack: 20260424_graduation_a3f9                       │
│ Series 选择：[Tab] 全部 / S1 / S2 / S3 / ...         │
├──────────────────────────────────────────────────────┤
│ 阶段 1：预览图（5 张）           进度 [4/5] [全部生成]│
│ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐                      │
│ │ ✓ │ │ ✓ │ │ ✓ │ │ ⟳ │ │ - │  缩略图               │
│ │ 1 │ │ 2 │ │ 3 │ │ 4 │ │ 5 │  [重新生成] [查看 prompt]│
│ └───┘ └───┘ └───┘ └───┘ └───┘                      │
├──────────────────────────────────────────────────────┤
│ 阶段 2：切分（每张 → 10 单图）       [全部切分]       │
│ Preview 1:                                           │
│   [缩略图 1] [缩略图 2] ... [缩略图 10]              │
│   每张可单独 [重切] [删除] [✓选用]                    │
│ Preview 2: ...                                       │
├──────────────────────────────────────────────────────┤
│ [ 保存为卡包 → 进入卡包管理 ]                        │
└──────────────────────────────────────────────────────┘
```

主要交互：
- 阶段 1 完成后才能进阶段 2（也允许并行触发）
- 每张图右下角显示状态：pending / generating / done / failed
- 文件直链可下载

#### 记录内容
- 表：`pack_previews` + `pack_stickers`
- 文件：`output/packs/{pack_uid}/series_{n}/previews/` 和 `.../stickers/`
- AI 日志：每张预览图 + 每张切分各一条

#### 估时：**3 d**（含 image-2 接入封装、切分 prompt 调试、并发控制、重试 UI）

---

### A.4 卡包管理 + 卡包画廊

#### 功能说明
- **卡包管理**：管理 packs 表所有记录，含元数据编辑、状态变更（active/archived）、批量操作
- **卡包画廊**：展示所有 active 包，按主题/时间筛选，支持下载、复用题材

#### 前端页面 1：**卡包管理** (`/v2/packs`)
- 表格：pack_uid / 主题 / 创建时间 / sticker 总数 / 状态 / [详情] [归档]
- 详情页 `/v2/packs/{pack_uid}`：展示完整链路（topic→plan→series→previews→stickers）+ 作为 B/C 入口

#### 前端页面 2：**卡包画廊** (`/v2/gallery`)
- 卡片网格：每个 pack 显示封面 + 标题 + 主题 tag
- 顶部筛选：主题分类 / 时间 / 优先级
- 点击 → 详情 modal（看全套 sticker）

#### 记录内容
- 表：`packs`（聚合视图）
- 文件：复用 A.3 已生成的

#### 估时：**1.5 d**

---

## 4. 模块 B：TK 视频

### B.1 视频上传 + AI 文案 + 定时发送

#### 功能说明
1. 用户在「卡包详情」页选「制作视频」→ 跳转视频工作台
2. 上传本地 mp4 + 填写「视频一句话描述」（中文或英文皆可）
3. AI 两步生成 caption + hashtags（**输出强制英文**，面向海外用户）：
   - **第 1 步**：主模型（gpt-5.4-pro）根据「卡包题材 + 视频描述」自由产出 3-5 版 caption 候选 + hashtag 候选 + 选择理由（英文）
   - **第 2 步**：提取模型（gpt-4o-mini）抽成 `{caption, hashtags[], alternates[]}` JSON
   - 前端展示候选可选，可重新生成 / 手动编辑
4. 设置发送时间（即时 / 定时）→ 写入 `tk_videos.scheduled_at`
5. 调度器发现到期任务 → 调 Blotato 上传 + 发送 → 回写 post_id

> **校验**：caption 入库前用正则检测 CJK 字符比例 > 5% 时前端 warning（避免误发中文）

#### 前端页面：**TK 视频工作台** (`/v2/videos/new?pack_id=...`)

```
┌────────────────────────────────────────────────────┐
│ 关联卡包：[Class of 2026 Black Gold] [更换]        │
│ TK 账号：[chip] [chip] [+绑定]                     │
│ ────────────────────────────────────────────────── │
│ 视频文件：[拖拽上传 / 选择文件]   [预览]            │
│ 一句话描述：[ 这是黑金毕业贴的开箱视频 ........ ]   │
│ ────────────────────────────────────────────────── │
│ AI 文案：[ caption 文本 ]   [重新生成] [手动编辑]   │
│ AI Hashtag：[ #class2026 #grad ... ]               │
│ ────────────────────────────────────────────────── │
│ 发送时间：[即时] / [定时 yyyy-mm-dd hh:mm]         │
│   [ 保存为草稿 ]   [ 提交 ]                         │
└────────────────────────────────────────────────────┘
```

#### 视频列表页 (`/v2/videos`)
- 表格：缩略图 / 关联包 / 账号 / caption / 状态 / 调度时间 / 已发布时间 / [详情]
- 状态过滤：草稿 / 已调度 / 已发布 / 失败

#### 记录内容
- 表：`tk_videos`（增列 `caption_main_raw_text TEXT` 保留主模型原文）
- 文件：`output/packs/{pack_uid}/videos/{video_id}/local.mp4` + `caption_main_raw.md`
- AI 日志：每次文案生成 **2 条**（`video_caption:main` + `video_caption:extract`）

#### 估时：**2 d**

---

### B.2 2h 数据刷新（独立脚本）

#### 功能说明
- 独立脚本 `scripts/scheduler.py` 中注册 `tk_metrics_refresh` job
- **每 2h 触发**：遍历所有 `publish_status='published'` 的视频 → 调 Display API 拉 view/like/comment/share → 写一行 `tk_video_metrics`
- 失败的单条记录写 `scheduled_jobs.error` 但不阻塞其他视频

#### 前端：见 B.3

#### 记录内容
- 表：`tk_video_metrics`（每次刷新写一行，构成时序）+ `scheduled_jobs`

#### 估时：**1 d**

---

### B.3 视频数据看板

#### 前端页面 1：**单视频分析** (`/v2/videos/{video_id}/analytics`)

```
┌──────────────────────────────────────────────────┐
│ Video: <标题>   账号 <name>   发布于 <time>      │
├──────────────────────────────────────────────────┤
│ 当前指标：    播放 12.3K   赞 800  评 50  分享 30 │
│ 变化（近24h）：+1.2K       +50    +5    +3       │
├──────────────────────────────────────────────────┤
│ [折线图：24h / 7d / 30d 切换]                     │
│   播放 / 点赞 / 互动率 三条曲线                   │
├──────────────────────────────────────────────────┤
│ 互动率：5.6% （高）                              │
│ → [一键创建产品（C 模块）]                        │
└──────────────────────────────────────────────────┘
```

#### 前端页面 2：**聚合数据看板** (`/v2/analytics`)
- 所有视频按互动率降序，标记"高互动建议产品化"
- 顶部筛选：账号 / 时间范围 / 状态
- 列：缩略图 / 标题 / 账号 / 发布时间 / 当前播放 / 互动率 / 趋势 / [详情]

#### 记录内容
- 复用 `tk_video_metrics`，前端做时序图

#### 估时：**1.5 d**

---

## 5. 模块 C：TKShop

### C.1 详情页生成

#### 功能说明
**输入**：pack_id（自动取 series 题材样式 metadata）
**AI 两步生成（输出强制英文，面向海外买家）**：

**第 1 步 — 主模型自由产出**（gpt-5.4-pro / claude-opus）：根据 series 题材 metadata + 默认模板字段，自由产出：
- title 候选 3-5 个
- description 完整 HTML 段落（参照 `TIKTOK_PRODUCT_API_GUIDE.md` 示例的 `<p><strong>...<ul><li>...` 风格）
- 卖点列表 + 关键词 + 写作思路说明

**第 2 步 — 提取模型转 JSON**（gpt-4o-mini）：抽成：
- **title**（≤255 字符，含 SEO 关键词，EN）
- **description_html**（清理后的 HTML，仅保留 TikTok Shop 支持的标签，EN）
- **selling_points**（4-6 条卖点，结构化 JSON，EN）
- **keywords**（10-15 个，结构化 JSON，EN）

> **校验**：title / description / selling_points / keywords 入库前检测 CJK 字符比例 > 1% 时前端 warning（产品详情对纯英文要求更严）
> **HTML 清洗**：description 入库前用 bleach 白名单过滤，仅保留 `<p><br><strong><em><ul><ol><li><h1>-<h6>`

#### 前端页面：**详情页生成工作台** (`/v2/products/new?pack_id=...`)

```
┌────────────────────────────────────────────────┐
│ 关联卡包：[20260424_graduation_a3f9]           │
│ 题材摘要：黑金毕业典礼，海外大学生场景          │
│ ────────────────────────────────────────────── │
│ [ AI 生成详情 ]                                 │
│ ────────────────────────────────────────────── │
│ Title:    [ ........... ]  字符数 120/255       │
│ Description (HTML 编辑器):                      │
│   <p><strong>Premium Quality...</strong></p>    │
│   <ul>...</ul>                                  │
│ Selling Points:                                 │
│   1. [ ............ ] [×]                       │
│   2. [ ............ ] [×]   [+ 添加]            │
│ Keywords: [ chip ] [ chip ] [+]                 │
│ ────────────────────────────────────────────── │
│ 默认模板（只读，可在配置页改）：                  │
│   价格 16.99 / 库存 100 / 重量 60g / 类目 928016│
│ ────────────────────────────────────────────── │
│ [ 保存草稿 ]   [ 进入主图管理 → ]               │
└────────────────────────────────────────────────┘
```

#### 记录内容
- 表：`tkshop_products`（增列 `detail_main_raw_text TEXT` 保留主模型原文）
- 文件：`output/packs/{pack_uid}/products/{tiktok_product_id 或 draft_id}/`
  - `detail_main_raw.md`（主模型自由文本）
  - `title.txt` / `description.html` / `selling_points.json` / `keywords.json`（提取后）
- AI 日志：每次生成 **2 条**（`product_detail:main` + `product_detail:extract`）

#### 估时：**2.5 d**（含两步 prompt 调试 + HTML 清洗 + 候选选择 UI）

---

### C.2 主图与辅图管理

#### 功能说明

**主路径（人工上传）**：
- 用户上传主图、辅图（多张）、生活场景图、尺寸对比图
- 支持拖拽排序、设置 role（main / secondary / lifestyle / scale）
- 自动校验：尺寸 ≥330px、≤5MB、格式 jpg/png/webp

**测试路径（AI 生成）**：
- **素材源**：复用 pack 的 preview / sticker 图作为参考素材
- **Prompt**：默认基于 series 题材自动生成"产品主图风格"prompt（如"将本组贴纸排列成 mockup，白底，电商主图风格..."），可手动编辑
- **生成**：调 GPT-image 2.0 → 进入候选区 → 用户选择采用
- 生成的图同样支持设置 role / 排序 / 删除

**发布前**自动调 TikTok Shop `upload_image` 接口拿 `tiktok_image_uri` 回写。

#### 前端页面：**主图管理** (`/v2/products/{product_id}/images`)

```
┌─────────────────────────────────────────────────┐
│ Product: <title>                                │
│ Tab: [人工上传] [AI 生成测试]                    │
├─────────────────────────────────────────────────┤
│ === Tab 1: 人工上传 ===                          │
│ [拖拽上传区]                                     │
│ 已上传：                                          │
│   ┌────┐ ┌────┐ ┌────┐                          │
│   │main│ │sec │ │life│  拖拽排序                │
│   │ ✓ │ │    │ │    │  右上角下拉设 role        │
│   └────┘ └────┘ └────┘                          │
├─────────────────────────────────────────────────┤
│ === Tab 2: AI 生成测试 ===                       │
│ 素材源：[✓ pack preview] [✓ sticker grid] [+其他]│
│ Prompt: [ 自动生成的 / 可编辑 ]                  │
│ 候选数量：[ 4 ]                                  │
│ [ 生成 ]                                         │
│ 候选图：                                          │
│   ┌────┐ ┌────┐ ┌────┐ ┌────┐                  │
│   │ 1  │ │ 2  │ │ 3  │ │ 4  │  [✓采用为main]   │
│   └────┘ └────┘ └────┘ └────┘                  │
├─────────────────────────────────────────────────┤
│ [ 进入发布 → ]                                   │
└─────────────────────────────────────────────────┘
```

#### 记录内容
- 表：`tkshop_product_images`（含 role / source / local_path / tiktok_image_uri / sort_order / ai_prompt）
- 文件：`output/packs/{pack_uid}/products/{product_id}/images/main.png` 等
- AI 日志：每次 AI 生成一条

#### 估时：**2 d**（含上传组件 + AI 生成 + 排序）

---

### C.3 产品发布 + 失败记录

#### 功能说明

**一键发布流程**：
1. **校验**：必填字段齐全（title / description / 至少 1 主图 / 默认模板字段）
2. **上传图片**：调 `upload_image` 上传所有图片 → 拿 uri 回写 `tkshop_product_images.tiktok_image_uri`
3. **创建产品**：调 `create_product` → 拿 product_id 回写 `tkshop_products.tiktok_product_id`
4. **激活上架**：调 `activate_product`（已知可能 40006，失败则记录 `manual_required` 状态，前端提示去后台手动上架）
5. **日志**：每次 API 调用写 `tkshop_publish_logs`，含完整 request / response / error_code / error_message

#### 前端页面：**产品发布面板** (`/v2/products/{product_id}/publish`)

```
┌─────────────────────────────────────────────────┐
│ Product: <title>                                │
│ ────────────────────────────────────────────── │
│ 校验：                                           │
│   ✓ Title 已填                                   │
│   ✓ Description 已填                             │
│   ✓ 主图已上传                                   │
│   ✗ 辅图缺失（建议补充，不阻塞）                  │
│ ────────────────────────────────────────────── │
│ 默认模板预览：价格 16.99 / 库存 100 / ...        │
│ ────────────────────────────────────────────── │
│ [ 一键发布 ]                                     │
│ ────────────────────────────────────────────── │
│ 发布日志（实时）：                                │
│   [12:00:01] upload_image main.png ... ✓ uri=...│
│   [12:00:03] upload_image sec_1.png ... ✓        │
│   [12:00:05] create_product ... ✓ product_id=... │
│   [12:00:06] activate_product ... ✗ code=40006   │
│   ⚠ 自动上架失败，请到 TikTok 后台手动上架       │
└─────────────────────────────────────────────────┘
```

#### 记录内容
- 表：`tkshop_products.publish_status` + `tkshop_publish_logs`（每步一行）
- 文件：无新增

#### 估时：**2 d**（API 完整接入 + 校验 + 发布日志 UI）

---

### C.4 产品状态看板

#### 前端页面：**产品状态看板** (`/v2/products`)

```
┌──────────────────────────────────────────────────────┐
│ 状态过滤：[全部] [draft] [publishing] [live] [failed]│
│           [manual_required]                          │
├──────────────────────────────────────────────────────┤
│ 表格：                                                │
│   主图 | title | pack | 状态 | TKshop ID | 发布时间 | 操作│
│   ─────┼──────┼──────┼──────┼──────────┼─────────┼──────│
│   [img] Class.. pack.. live    1732..    2026-..   [详情]│
│   [img] ...     pack.. failed  -          -        [重试]│
│   [img] ...     pack.. manual  1732..    2026-..   [指引]│
├──────────────────────────────────────────────────────┤
│ 详情 modal：含完整 publish_logs 时间线                │
└──────────────────────────────────────────────────────┘
```

定时同步：`tkshop_status_sync` job 每 30min 拉一次 LIVE/PENDING/FAILED 状态更新。

#### 估时：**1.5 d**

---

## 6. 模块间数据流

```
hot_topics ─→ topic_plans ─→ pack_series ─→ pack_previews ─→ pack_stickers
                                  ↓                                  ↓
                                  └────→ packs ←────────────────────┘
                                            │
                                ┌───────────┼───────────┐
                                ↓                       ↓
                            tk_videos              tkshop_products
                                ↓                       ↓
                          tk_video_metrics       tkshop_product_images
                                                 tkshop_publish_logs
```

每个对象详情页都能反向追溯（产品 → pack → series → topic）。

---

## 7. 时间评估

### 阶段划分（5 周排期，单人）

| 周 | 内容 | 工作日 |
|---|---|---|
| W1 | 公共底座（DB / 文件 / AI Router / 调度 / 前端框架）+ 旧库迁移 + POC | 5 d |
| W2 | A.1（2d）+ A.2（2.5d，含两步生成）+ A.3（3d，含切分 POC 收口） | 7.5 d |
| W3 | A.4（1.5d）+ B.1（2d，含两步生成）+ B.2（1d）+ B.3（1.5d） | 6 d |
| W4 | C.1（2.5d，含两步生成）+ C.2（2d）+ C.3（2d）+ C.4（1.5d） | 8 d |
| W5 | 集成测试 + 数据流串联验证 + bug 修复 + 部署文档 | 5 d |

**合计 ~31.5 d，含缓冲**

### POC 优先项（W1 末必须验证）
1. **OpenAI web_search tool** 能否正常返回引用 → 影响 A.1
2. **GPT-image 2.0 image edit** 切单 sticker 的成功率（虽已验证，要做出量化标准 + 失败兜底） → 影响 A.3
3. **TikTok Shop create_product + upload_image** 端到端 → 影响 C.3（沙箱或正式店）

### 里程碑
- **M1（W1 末）**：底座 + POC 通过
- **M2（W2 末）**：A 链路跑通，能产出可用卡包
- **M3（W3 末）**：B 链路跑通，能发出视频并看到时序数据
- **M4（W4 末）**：C 链路跑通，能发出第一个产品
- **M5（W5 末）**：端到端验收

---

## 8. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| OpenAI web_search 中转不可用 | 中 | A.1 失效 | 前期同时接 4 个 source，任一可用即可；OpenAI key 准备好后切原生 |
| image-2 切分某些 series 效果差 | 中 | A.3 单图质量 | UI 留重切按钮 + 后期允许人工本地 PS 替换覆盖 image_path |
| `activate_product` 持续 40006 | 高 | C.3 半自动 | 前端明确提示"已建品，请去后台手动上架"，标记 `manual_required` 状态而非 failed |
| 调度器进程崩溃漏跑 | 低 | 数据缺失 | scheduled_jobs 表记录每次运行，启动时回查最近 missed |
| 单 sqlite 并发瓶颈 | 低 | 慢 | 已用 WAL；监控 ai_call_logs 行数，超 100w 时归档 |
| AI 成本失控 | 中 | 钱 | ai_call_logs 必填 cost_estimate，前端有日成本看板 |
| 多源 search 结果难对比 | 中 | A.1 选型困难 | hot_topics 留 hot_score + 人工 selected 计数，1-2 周后看哪个 source 命中率高 |
| 两步生成的提取模型抽错字段 | 中 | 数据需手动整理 | 提取失败保留主模型原文 + 前端可编辑；schema 严格定义 + 单测覆盖 |
| 提取模型成本叠加 | 低 | 每任务多 1 次 AI 调用 | 用 gpt-4o-mini 等便宜模型，单次 < $0.001；ai_call_logs 监控 |
| C.1 / B.1 输出混入中文 | 中 | 海外用户困惑 | prompt 显式强约束 + 入库前 CJK 字符比例校验 + 前端 warning |

---

## 9. 验收清单

- [ ] 单一 `ops_workbench.db` 含全部 13 张新表，旧库已归档到 `data/backups/legacy_dbs/`
- [ ] **完整链路 demo**：选热点 → 出 5 套题材 → 选 1 套 → 出 5 张预览 → 切 50 张 → 入卡包 → 选包做视频 → 视频发布 → 看到 2 次以上时序数据 → 选包做产品 → AI 详情页 + 上传主图 → 发布产品 → 在状态看板看到结果
- [ ] 所有 AI 调用都有 `ai_call_logs` 记录，含 cost_estimate
- [ ] 每个对象详情页能展开看完整调用链 + 文件路径 + 关联表行
- [ ] 前端所有 v2 页面统一风格、统一导航
- [ ] 失败路径（图生失败 / 发布失败 / 切分失败）UI 友好，可重试
- [ ] 调度器能稳定跑 24h 不漏 job，启动有 missed-jobs 回查
- [ ] 部署文档：`start.sh` 能拉起 web + scheduler 双进程，`stop.sh` 能干净停止

---

**文档维护**：本计划随实现推进可能调整，主要变更需记录在文档底部的「变更日志」。所有表结构最终以 `scripts/migrations/` 下的 SQL 为准。
