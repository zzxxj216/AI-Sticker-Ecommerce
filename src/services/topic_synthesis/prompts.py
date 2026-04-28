"""Prompts for A.1.5 topic synthesis (two-step generation).

Step 1 (main): given N raw search results, write a markdown plan that
identifies 1-3 distinct sticker pack 题材 and explains why each is
commercially viable.

Step 2 (extract): convert markdown → strict JSON list, ready to insert
as hot_topics rows.
"""

from __future__ import annotations

from typing import Any


SYNTH_MAIN_SYSTEM_PROMPT = (
    "You are a senior product strategist for an overseas sticker e-commerce "
    "brand selling on TikTok Shop / Etsy / Amazon. The operator gives you "
    "raw web search results from a topical query. Your job is to identify "
    "the 1-3 most commercially viable distinct sticker-pack 题材 (themes) "
    "the brand could develop, NOT to summarize the search results. "
    "Operator-facing fields (定位 / 适合用户 / priority) → 中文. Anything that "
    "will end up on a buyer-facing product → English. Output markdown — "
    "do NOT output JSON; a separate extractor will pull the structured fields."
)


def build_synth_main_prompt(
    *,
    common_query: str,
    common_region: str,
    results: list[dict],
    extra_brief: str = "",
) -> str:
    """Markdown-style prompt asking the model to produce 1-3 themes.

    ``results`` is a list of dicts each with keys: id, source, topic_name,
    query, evidence_urls (list[str]), snippet (optional).
    """
    lines: list[str] = []
    for idx, r in enumerate(results, 1):
        urls = r.get("evidence_urls") or []
        snippet = (r.get("snippet") or "")[:300]
        lines.append(
            f"### 候选 {idx}\n"
            f"- **id**: `{r.get('id')}`\n"
            f"- **source**: `{r.get('source')}`\n"
            f"- **title**: {r.get('topic_name')}\n"
            f"- **search query**: `{r.get('query') or '—'}`\n"
            f"- **urls**:\n" + ("\n".join(f"  - {u}" for u in urls[:5]) or "  - (none)") + "\n"
            + (f"- **snippet**: {snippet}\n" if snippet else "")
        )
    block = "\n".join(lines)
    extras = ("\n### 额外说明\n" + extra_brief) if extra_brief.strip() else ""

    return f"""操作员从 web 搜索拿到了下面 {len(results)} 条候选。请你**识别 1-3 个**\
最有商业价值、彼此**风格上拉得开**的 sticker pack 题材（不要做单纯的搜索结果汇总）。

### 共同 query
- query: `{common_query or '（未提供）'}`
- region: `{common_region or 'US'}`{extras}

### 候选信息

{block}

### 输出要求

每个题材按下面的 markdown 结构写：

#### 题材 N: <中文名> / <English Name>

- **定位**：一句话中文，说明这个题材在海外市场的切入点
- **theme_name**: 简短中英混合（操作员复制用）
- **target_audience_en**: 1-2 句英文目标用户描述
- **key_visual_keywords**: 5-10 个英文关键词（逗号分隔），描述视觉/风格方向
- **evidence_ids**: 候选编号列表（如 `[1, 3]`），表示这个题材主要由哪几条候选支撑
- **priority**: high / medium / low（high = 最该立即开发）
- **why_commercial_cn**: 一句中文，说明为什么操作员应该相信这个题材能卖

### 设计原则

1. **少即是多**：宁可只产出 1 个高质量题材，也别凑 3 个低质量的。
2. 题材之间风格要拉开（典礼 vs 派对 vs meme vs 审美 vs 校园回忆，不要全是同一个方向）。
3. **不要重复 raw 候选的标题**——题材是 raw 数据的提炼，要更高一层。
4. 不要使用受版权保护的 IP（迪士尼/三丽鸥/宝可梦 等）。

请直接开始输出，不要前置说明。
"""


SYNTH_EXTRACT_INSTRUCTIONS = """
Mapping rules:
- One JSON object per "#### 题材 N" section in the markdown.
- "theme_name": pull from **theme_name** bullet, plain text.
- "positioning_cn": pull from **定位** bullet (or fall back to title clause), Chinese.
- "target_audience_en": pull from **target_audience_en** bullet, English text.
- "key_visual_keywords": parse the comma-separated bullet into a list of
  trimmed strings. Drop empties.
- "evidence_ids": parse the markdown list (e.g. `[1, 3]` or `1, 3`)
  into a list of INTEGERS. These are 1-based candidate indices, NOT
  hot_topic IDs.
- "priority": exact lowercase enum: high / medium / low. If markdown
  uses ★ stars, ★★★★★→high, ★★★★→medium, ★★★→low.
- "why_commercial_cn": pull from **why_commercial_cn**, Chinese sentence.

Use empty string / empty list for missing fields. Do NOT fabricate
themes that aren't in the markdown.
""".strip()


def build_synth_extract_schema(min_themes: int = 1, max_themes: int = 3) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["themes"],
        "properties": {
            "themes": {
                "type": "array",
                "minItems": min_themes,
                "maxItems": max_themes,
                "items": {
                    "type": "object",
                    "required": ["theme_name", "positioning_cn",
                                 "target_audience_en", "key_visual_keywords",
                                 "evidence_ids", "priority", "why_commercial_cn"],
                    "properties": {
                        "theme_name":          {"type": "string"},
                        "positioning_cn":      {"type": "string"},
                        "target_audience_en":  {"type": "string"},
                        "key_visual_keywords": {"type": "array", "items": {"type": "string"}},
                        "evidence_ids":        {"type": "array", "items": {"type": "integer"}},
                        "priority":            {"type": "string", "enum": ["high", "medium", "low"]},
                        "why_commercial_cn":   {"type": "string"},
                    },
                },
            },
        },
    }
