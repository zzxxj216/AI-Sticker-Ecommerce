# 脚本生成机制文档

> 本文档描述 AI Sticker Ecommerce 项目中「趋势 Brief 生成」与「TikTok 视频脚本生成」两条链路的完整实现，包括文件路径、调用链、提示词原文及输出格式。

---

## 目录

1. [整体架构概览](#整体架构概览)
2. [链路一：趋势 Brief 生成](#链路一趋势-brief-生成)
3. [链路二：TikTok 视频脚本生成](#链路二tiktok-视频脚本生成)
4. [共用配置与品牌约束](#共用配置与品牌约束)
5. [数据库表结构](#数据库表结构)

---

## 整体架构概览

```
前端操作
  │
  ├─ 采纳 Trend → [Brief 生成] → 存入 trend_briefs
  │
  └─ 选择 Job + 模板 → [视频脚本生成] → 存入 video_plans
```

两条链路都通过 `OpenAI` 兼容接口调用 LLM，模型配置读取自 `.env`。

---

## 链路一：趋势 Brief 生成

### 1.1 触发方式

| 入口 | 路径 |
|------|------|
| API 采纳（列表页一键采纳） | `POST /api/trends/{trend_id}/approve` |
| 表单采纳（详情页采纳按钮） | `POST /trends/{trend_id}/approve` |
| 重新生成 | `POST /api/trends/{trend_id}/retry-brief` |

采纳后由 `enqueue_brief_after_approve_if_needed()` 判断是否需要生成，若 Brief 缺失则加入后台任务队列。

### 1.2 完整调用链

```
前端（/trends 或 /trends/{trend_id}）
  └─ POST /api/trends/{trend_id}/approve
       └─ src/web/app.py :: api_trend_approve()
            └─ trend_service.enqueue_brief_after_approve_if_needed()
                 └─ background_tasks.add_task(generate_brief_background, trend_id)
                      └─ src/services/ops/trend_service.py :: generate_brief_background()
                           └─ _generate_brief_on_approve()
                                ├─ source_type == "tiktok"  → _generate_tiktok_brief()  [LLM]
                                └─ source_type == "news"    → _generate_news_brief()    [规则引擎]
```

### 1.3 相关文件路径

```
src/services/ops/trend_service.py          # Brief 生成主逻辑
trend_fetcher/topic_prompts.py             # TikTok Brief 的 System Prompt、解析函数
trend_fetcher/sticker_pipeline/brief_builder.py  # News Brief 规则引擎
src/services/ops/db.py                     # upsert_brief()、get_brief()
src/models/ops.py                          # TrendBriefRecord 数据模型
```

### 1.4 TikTok Brief — System Prompt（完整原文）

> 文件：`trend_fetcher/topic_prompts.py`，常量名：`TOPIC_TO_BRIEF_PROMPT`

```
You are a sticker product brief builder.

Your job is to convert an approved topic review card into a structured trend brief for downstream sticker-pack planning.

You are not reviewing the topic again.
You are not planning the final sticker pack.
You are not generating image prompts.

You are only responsible for:
1. translating the approved review result into a clean, standardized product brief
2. preserving the commercial direction, emotional core, and risk constraints
3. making the brief directly usable by a sticker-pack planner

The input will include:
- the reviewed topic card
- the review decision
- the normalized theme
- the theme type
- the one-line interpretation
- the recommended pack archetype
- the best platform
- candidate visual symbols
- candidate emotional hooks
- risk flags
- optional source context such as hashtag or related interests

Important rules:
- Only build a brief if the reviewed topic is approved.
- If the input is Watchlist or Reject, do not force a brief.
- Keep the brief commercially grounded, not poetic.
- Do not invent unsupported audience details.
- Infer conservatively when needed.

Your response must follow this exact 3-part structure:

===== PART 1: BRIEF STATUS =====
Start with a short status summary in 2-3 sentences.

State one of these clearly:
- Brief Ready
- Brief Not Ready

If Brief Not Ready, explain the blocker briefly and stop after PART 1.

===== PART 2: STANDARDIZED TREND BRIEF =====
Output the final brief using these exact headings:

- trend_name
- trend_type
- one_line_explanation
- why_now
- lifecycle
- platform
- product_goal
- target_audience
- emotional_core
- visual_symbols
- visual_do
- visual_avoid
- must_include
- must_avoid
- risk_notes
- pack_size_goal
- reference_notes

Field rules:
- trend_name: short, product-usable theme name
- trend_type: must match the reviewed theme type
- one_line_explanation: concise and product-relevant
- why_now: explain why this topic is timely in market terms, not in vague social terms
- lifecycle: choose one of flash / seasonal / event_based / evergreen_with_boost / long_tail
- platform: choose from amazon / d2c / both
- product_goal: choose the most natural product goals from impulse_buy / giftable / collectible / functional_decoration / journal_use / device_decoration / emotional_expression
- target_audience: write as a short structured profile including likely buyer type and usage scenario
- emotional_core: 3-5 reusable emotional words
- visual_symbols: 6-12 grounded visual elements where possible
- visual_do: 2-4 practical visual directions to emphasize
- visual_avoid: 2-4 practical visual directions to avoid
- must_include: only what is truly necessary for downstream planning
- must_avoid: must reflect risk flags and obvious commercial safety concerns
- risk_notes: concise but explicit
- pack_size_goal: suggest small / medium / large plus a reasonable sticker count range
- reference_notes: optional short guidance, not strategy overload

===== PART 3: BRIEF QUALITY CHECK =====
End with a short self-check section using these headings:

1. Why this brief is commercially usable
2. What makes it visually supportable
3. What risk must remain controlled

Keep each item short and practical.

Writing rules:
- Write in Chinese unless the user asks for another language.
- Do not output JSON.
- Do not restate the full review card mechanically.
- Do not add next-step suggestions.
- Do not ask follow-up questions.
- Be specific, restrained, and product-oriented.
- Preserve risk controls explicitly.
- The result should feel like a clean internal brief ready for sticker-pack planning.
```

### 1.5 TikTok Brief — User Prompt（动态拼接）

> 函数：`trend_fetcher/topic_prompts.py :: build_reviewed_card(row)`

拼接格式如下：

```
[Reviewed Topic Card]
Decision: {decision}
Normalized theme: {normalized_theme}
Theme type: {theme_type}
One-line interpretation: {one_line_interpretation}
Recommended pack archetype: {pack_archetype}
Best platform: {best_platform}
Candidate visual symbols: {visual_symbols}
Candidate emotional hooks: {emotional_hooks}
Main risk flags: {risk_flags}
Score: {score_total}/100
Sticker Fit Level: {sticker_fit_level}

【工作台强制指令】运营已在系统内点击「采纳」，必须交付下游卡贴可用的完整 Brief。
无论卡片上原决策为 Approve 或 Watchlist，均须输出「Brief Ready」
并写满 PART 2 全部字段；保持 ===== PART 1/2/3 ===== 三段结构；
PART 2 中每个字段以「-- 字段名」单独起行（与系统模板一致）。
禁止仅输出 Brief Not Ready 或省略 PART 2。
```

> 注：工作台强制指令在 `src/services/ops/trend_service.py :: _generate_tiktok_brief()` 中追加，确保人工采纳后模型一定输出完整 Brief。

### 1.6 TikTok Brief — 调用参数

| 参数 | 值 |
|------|-----|
| 模型 | `trend_fetcher/config.py :: OPENAI_MODEL`（`.env` 配置） |
| temperature | `0.5` |
| max_tokens | `3000` |
| 接口 | `openai.OpenAI.chat.completions.create` |

### 1.7 Brief 输出格式（解析后写入数据库）

> 函数：`trend_fetcher/topic_prompts.py :: parse_brief_response(text)`

AI 返回非 JSON 的三段结构文本，解析后得到以下字典：

```python
{
    "brief_status":        "ready" | "not_ready",
    "trend_name":          str,
    "trend_type":          str,
    "one_line_explanation": str,
    "why_now":             str,
    "lifecycle":           str,   # flash / seasonal / event_based / evergreen_with_boost / long_tail
    "platform":            str,   # amazon / d2c / both
    "product_goal":        str,
    "target_audience":     str,
    "emotional_core":      str,
    "visual_symbols":      str,
    "visual_do":           str,
    "visual_avoid":        str,
    "must_include":        str,
    "must_avoid":          str,
    "risk_notes":          str,
    "pack_size_goal":      str,
    "reference_notes":     str,
}
```

存入 `trend_briefs.brief_json`（SQLite JSON 列），`brief_status` 同步存入 `trend_briefs.brief_status`。

### 1.8 News Brief — 规则引擎（无 LLM）

News 类型的 Brief 不调用 LLM，由 `trend_fetcher/sticker_pipeline/brief_builder.py :: BriefBuilder._card_to_brief()` 从 opportunity card 字段直接映射生成，写入同一张 `trend_briefs` 表，`brief_status = "generated"`。

---

## 链路二：TikTok 视频脚本生成

### 2.1 触发方式

| 入口 | 路径 |
|------|------|
| Job 详情页 | `src/web/templates/job_detail.html` → `POST /api/video-plans/generate` |
| Pack Manager 页 | `src/web/templates/pack_manager.html` → `POST /api/video-plans/generate` |

请求体格式：
```json
{
  "job_ids": ["job_xxx"],
  "template_ids": ["template_a"]
}
```

### 2.2 完整调用链

```
前端（job_detail.html / pack_manager.html）
  └─ POST /api/video-plans/generate
       └─ src/web/app.py :: api_generate_video_plans()
            └─ video_script_agent.generate_batch(job_ids, template_ids, created_by)
                 └─ src/services/video/script_agent.py :: VideoScriptAgent.generate_batch()
                      └─ generate_plan(job_id, template_id)
                           ├─ db.get_video_script_template(template_id)
                           ├─ db.get_job(job_id)
                           ├─ _load_trend_brief(trend_id)      ← 读 trend_briefs.brief_json
                           ├─ _load_sticker_descriptions(job_id) ← 读 generation_outputs.metadata_json
                           ├─ build_video_script_prompt(...)   ← 拼接 User Prompt
                           ├─ OpenAIService.generate(prompt, system=VIDEO_SCRIPT_SYSTEM, temperature=0.7)
                           ├─ _parse_json_response(raw_text)
                           └─ db.insert_video_plan(plan_record)
```

### 2.3 相关文件路径

```
src/services/video/script_agent.py         # VideoScriptAgent 主类
src/services/video/script_prompts.py       # VIDEO_SCRIPT_SYSTEM + build_video_script_prompt()
src/services/ai/openai_service.py          # OpenAIService.generate()
src/web/app.py                             # /api/video-plans/generate 等路由
src/web/templates/job_detail.html          # 前端触发页面（Job 详情）
src/web/templates/pack_manager.html        # 前端触发页面（Pack Manager）
config/store_profile.yaml                  # 品牌约束配置
src/services/ops/db.py                     # insert_video_plan()、get_video_script_template() 等
```

### 2.4 视频脚本 — System Prompt（完整原文）

> 文件：`src/services/video/script_prompts.py`，常量名：`VIDEO_SCRIPT_SYSTEM`

```
You are a senior TikTok US-market short video script planner.

Your job is to create a detailed, production-ready video script for promoting sticker products on TikTok.

You are given:
1. A video template structure (segments with time ranges and purposes)
2. A sticker pack context (trend brief, sticker descriptions, style info)
3. Brand constraints (tone, material claims)

Your output must be a single valid JSON object with the following top-level keys:

{
  "title": "short working title for this video plan",
  "hook_line": "the opening line or text that grabs attention in the first 1-2 seconds",
  "segments": [
    {
      "time_range": "0-2s",
      "purpose": "hook_text",
      "visual_description": "what appears on screen — be specific about framing, motion, sticker placement",
      "text_overlay": "exact English text shown on screen, or empty string if none",
      "voiceover": "spoken words if any, or empty string",
      "transition": "cut / zoom / swipe / fade — how this segment ends"
    }
  ],
  "cta_text": "the final call-to-action text",
  "hashtags": ["#tag1", "#tag2", "..."],
  "music_mood": "short description of ideal background music vibe",
  "sticker_hero": "which sticker design should be the hero/focus of this video",
  "production_notes": "any extra notes for filming or editing"
}

Rules:
- All text content (hook_line, text_overlay, voiceover, cta_text, hashtags) must be in natural American English.
- The script must feel native to TikTok US — casual, direct, visually driven.
- Follow the template segment structure exactly (same number of segments, same time ranges).
- Be specific in visual descriptions — describe camera angles, sticker placement surfaces, motion.
- Keep voiceover optional and short; TikTok sticker content often works better with text overlays + music.
- Hashtags should mix trend-specific tags with sticker/product tags (6-10 total).
- Output ONLY the JSON object, no markdown fences, no extra text.
```

### 2.5 视频脚本 — User Prompt（动态拼接）

> 函数：`src/services/video/script_prompts.py :: build_video_script_prompt()`

拼接四个上下文区块：

```
[VIDEO TEMPLATE]
Template: {template.name}
Test Goal: {structure.test_goal}
Total Duration: {structure.total_duration}
CTA Style: {structure.cta_style}
Best For: {structure.suitable_for}

Segments:
  - {time_range} | {purpose} | {guidance}
  - ...

[STICKER PACK]
Pack Name: {job.trend_name}
Image Count: {job.image_count}
Sticker Designs:
  1. {sticker_name} — {prompt[:120]}
  2. ...（最多 12 个）

[TREND CONTEXT]                    ← 仅当 trend_brief 存在时
  trend_name: ...
  one_line_explanation: ...
  why_now: ...
  lifecycle: ...
  emotional_core: ...
  visual_symbols: ...
  platform: ...
  product_goal: ...
  audience_profile: ...
  usage_scenarios: ...

[BRAND CONSTRAINTS]                ← 读自 config/store_profile.yaml
  Voice: Friendly, enthusiastic sticker lover...
  Material Claims: waterproof, durable, vibrant colors, ...
  Avoid Claims: dishwasher safe, permanent, ...

[TASK]
Generate a complete video script following the template structure above.
Output ONLY a valid JSON object.
```

### 2.6 视频脚本 — 调用参数

| 参数 | 值 |
|------|-----|
| 模型 | `src/core/config.py :: config.openai_model`（`.env` 中 `OPENAI_MODEL`） |
| temperature | `0.7` |
| 接口 | `src/services/ai/openai_service.py :: OpenAIService.generate()` |

### 2.7 视频脚本 — 输出格式（存入数据库）

> 解析函数：`src/services/video/script_agent.py :: _parse_json_response(text)`

AI 返回纯 JSON，解析后存入 `video_plans.plan_json`：

```json
{
  "title": "工作标题",
  "hook_line": "开场抓眼球的文字（1-2秒）",
  "segments": [
    {
      "time_range": "0-2s",
      "purpose": "hook_text",
      "visual_description": "画面描述，镜头角度、贴纸摆放位置",
      "text_overlay": "屏幕上显示的文字",
      "voiceover": "旁白（可为空）",
      "transition": "cut / zoom / swipe / fade"
    }
  ],
  "cta_text": "最终行动号召文字",
  "hashtags": ["#tag1", "#tag2"],
  "music_mood": "背景音乐风格描述",
  "sticker_hero": "主角贴纸设计名称",
  "production_notes": "拍摄/剪辑备注"
}
```

解析失败时退回：
```json
{ "raw_text": "...", "_parse_error": true }
```

### 2.8 视频脚本模板（Template）

模板存储在 `video_script_templates` 表，`structure_json` 字段示例结构：

```json
{
  "test_goal": "测试 Hook 效果",
  "total_duration": "6-10s",
  "cta_style": "soft",
  "suitable_for": "展示单张贴纸或小套装",
  "segments": [
    { "time_range": "0-2s", "purpose": "hook_text", "guidance": "开场一句话抓注意力" },
    { "time_range": "2-6s", "purpose": "product_reveal", "guidance": "展示贴纸实物" },
    { "time_range": "6-8s", "purpose": "cta", "guidance": "引导点击/购买" }
  ]
}
```

默认种子模板（`template_a` / `template_b` / `template_c`）在 `src/services/ops/db.py :: _seed_video_script_templates()` 中定义。

---

## 共用配置与品牌约束

### 品牌档案

> 文件：`config/store_profile.yaml`

```yaml
business:
  type: "ai_sticker_generator"

brand:
  voice: |
    Friendly, enthusiastic sticker lover who talks like a fellow hobbyist.
    Use first-person ("I", "we"), casual tone, contractions ("you're", "isn't").
    Never sound corporate or salesy. Think "cool friend recommending stuff",
    not "brand pushing products".

platform:
  type: "shopify"
  domain: "inkelligent.myshopify.com"

materials:
  type: "premium vinyl"
  safe_claims:
    - "waterproof"
    - "durable"
    - "vibrant colors"
    - "easy to apply"
    - "perfect for laptops, water bottles, and more"
  avoid_claims:
    - "dishwasher safe"
    - "permanent"
    - "indestructible"
    - "military-grade"
```

### 模型配置

| 配置项 | 文件 | 环境变量 |
|--------|------|----------|
| 视频脚本模型 | `src/core/config.py` | `OPENAI_MODEL` |
| Brief 生成模型 | `trend_fetcher/config.py` | `OPENAI_MODEL` |
| API Key | `.env` | `OPENAI_API_KEY` |
| Base URL（兼容接口） | `.env` | `OPENAI_BASE_URL` |

---

## 数据库表结构

数据库文件：`data/ops_workbench.db`

### `trend_briefs`

| 字段 | 类型 | 说明 |
|------|------|------|
| `trend_id` | TEXT PK | 关联 `trend_items.id` |
| `brief_status` | TEXT | `missing` / `generating` / `generated` / `ready` / `failed` |
| `brief_json` | TEXT | JSON 字符串，Brief 结构化内容 |
| `source_ref` | TEXT | 来源标记（如 `tiktok_brief`、`news_brief`） |
| `edited_by` | TEXT | 最后编辑人 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |

### `video_plans`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | `vp_` 前缀 + 12位 UUID |
| `job_id` | TEXT | 关联 `generation_jobs.id` |
| `template_id` | TEXT | 关联 `video_script_templates.id` |
| `plan_json` | TEXT | JSON 字符串，视频脚本完整内容 |
| `status` | TEXT | `completed` / `failed` |
| `created_by` | TEXT | 操作人 |
| `created_at` | TEXT | 创建时间 |

### `video_script_templates`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 模板 ID（如 `template_a`） |
| `name` | TEXT | 模板显示名称 |
| `structure_json` | TEXT | JSON 字符串，含 `segments`、`test_goal` 等 |
| `is_active` | INTEGER | `1` = 启用 |

---

*文档生成时间：2026-04-08*
