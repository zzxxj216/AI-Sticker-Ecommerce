"""Prompt templates for A.2 two-step generation.

Style and structure follow ``docs/ChatGPT-毕业季卡贴题材设计.md`` — the
reference doc the operator settled on after manual experimentation.

Step 1 (main): free-form markdown plan, sections and tone are Chinese for
operator-facing fields (定位/适合用户/优先级 etc) and English for
buyer-facing fields (title/audience/sticker text), per §0.5 输出语言约定.

Step 2 (extract): convert markdown into a strict JSON schema that the
service writes to topic_plans.series_payload + pack_series rows.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Step 1 — main creative prompt
# ---------------------------------------------------------------------------

MAIN_SYSTEM_PROMPT = (
    "You are a senior product strategist for an overseas sticker e-commerce "
    "brand selling on TikTok Shop / Etsy / Amazon. The operator is Chinese; "
    "writing about positioning, audience fit, priority — use Chinese. "
    "Anything that will appear on the buyer-facing product (titles, sticker "
    "text, hashtag-style copy, target-audience descriptions in marketing) — "
    "MUST be in English, written for native speakers in the US/UK/AU/CA market. "
    "Write naturally with markdown sections — do NOT output JSON. A separate "
    "extractor will convert your output into structured data."
)


def build_main_prompt(
    *,
    topic_name: str,
    topic_query: str | None,
    topic_evidence_urls: list[str],
    series_count: int,
    previews_per_series: int,
    stickers_per_preview: int,
    region: str = "US",
    extra_brief: str = "",
) -> str:
    """Build the markdown-style creative prompt.

    Asks the model to design ``series_count`` distinct series for the topic;
    each series spec must include enough detail to drive A.3 image generation
    (style anchor + per-preview sticker briefs).
    """
    evidence = "\n".join(f"- {u}" for u in topic_evidence_urls[:8]) or "（无明显证据 URL，凭你对题材的常识判断）"
    extras = ("\n\n### 额外说明\n" + extra_brief) if extra_brief.strip() else ""

    return f"""我现在是一个海外卡贴商家，准备围绕下面这个题材做一批卡贴产品。请你帮我**设计 {series_count} 套**，每套包含 **{previews_per_series} 张预览图**，每张预览图展示 **{stickers_per_preview} 张卡贴**（每套合计 {previews_per_series * stickers_per_preview} 张）。

### 题材
- **topic_name**: `{topic_name}`
- **search query**: `{topic_query or '（无）'}`
- **region**: `{region}`

### 来源证据
{evidence}{extras}

### 我的要求

每套都要包含以下结构（注意：操作向字段用中文，给海外买家看的字段用英文）：

#### 1. 系列定位（中文）
- **series_name**：系列名称（中文标签 + 英文小标，例如 “黑金典礼款 / Black Gold Ceremony”）
- **定位**：一句话说明这套的市场切入点（中文）
- **适合用户**：3-5 个具体用户画像（中文）
- **建议优先级**：high / medium / low

#### 2. 视觉锚点（英文为主，给 image-gen 模型用）
- **style_anchor**：一段 30-80 词的英文风格描述（颜色、风格、材质、版式），要保证这套 {previews_per_series} 张预览图风格统一
- **palette**：核心配色（英文，逗号分隔）
- **pack_archetype**：归档标签（英文小写下划线，如 `ceremony` / `meme` / `aesthetic` / `party_favor` / `memory`）

#### 3. 商品信息（英文，给 TKShop / Etsy 用）
- **title_en**：商品标题（英文，60-100 字符，包含 “Class of XXXX” 之类年份关键词如适用）
- **target_audience_en**：1-2 句英文目标用户描述

#### 4. 预览图清单（{previews_per_series} 张，每张 {stickers_per_preview} 个 sticker）
对每张预览图给出：
- **preview_idx**：从 1 编号到 {previews_per_series}
- **theme**：本张预览图的子主题（中英都行，简短）
- **stickers**：{stickers_per_preview} 个 sticker，每个写成一行 `"贴纸文字 / 简短英文图形描述"`，例如 `"Class of 2026 / graduation cap and gold stars"`

### 设计原则

1. **{series_count} 套之间风格要拉开差距**（典礼感、回忆感、派对感、梗图感、审美感等不同方向），不要做成同一种风格的微调。
2. 每套内部 {previews_per_series} 张预览图**风格必须统一**——靠 style_anchor + palette 锁定。
3. 全部 {series_count * previews_per_series * stickers_per_preview} 个 sticker 不要重复。
4. 不要使用任何受版权保护的角色（迪士尼 / 三丽鸥 / 宝可梦 / Stitch 等），可以用原创动物 / 通用符号。
5. sticker 文案要适合海外文化语境，不要直译中文梗。

### 输出格式

用 markdown，按 `## 套装 1：xxx` / `## 套装 2：xxx` 分节，每节内部用上述 1-4 的子标题。**不要输出 JSON**，写成可读的 markdown 即可。
"""


# ---------------------------------------------------------------------------
# Step 2 — extract schema + instructions
# ---------------------------------------------------------------------------

def build_extract_schema(
    *,
    series_count: int,
    previews_per_series: int,
    stickers_per_preview: int,
) -> dict[str, Any]:
    """JSON schema (used as a contract in the extraction prompt).

    Kept loose intentionally — the extractor model gets the full markdown
    and we'd rather have permissive shape + downstream defaults than fail
    on missing fields.
    """
    return {
        "type": "object",
        "required": ["series"],
        "properties": {
            "series": {
                "type": "array",
                "minItems": series_count,
                "maxItems": series_count,
                "items": {
                    "type": "object",
                    "required": [
                        "series_name", "style_anchor", "palette",
                        "pack_archetype", "priority", "preview_briefs",
                    ],
                    "properties": {
                        "series_name":         {"type": "string", "description": "中文+英文混合系列名"},
                        "positioning_cn":      {"type": "string", "description": "中文一句话定位"},
                        "target_users_cn":     {"type": "array",  "items": {"type": "string"}},
                        "priority":            {"type": "string", "enum": ["high", "medium", "low"]},
                        "style_anchor":        {"type": "string", "description": "english style anchor for image-gen"},
                        "palette":             {"type": "string", "description": "comma-separated english color names"},
                        "pack_archetype":      {"type": "string", "description": "english snake_case tag"},
                        "title_en":            {"type": "string"},
                        "target_audience_en":  {"type": "string"},
                        "preview_briefs": {
                            "type": "array",
                            "minItems": previews_per_series,
                            "maxItems": previews_per_series,
                            "items": {
                                "type": "object",
                                "required": ["preview_idx", "theme", "stickers"],
                                "properties": {
                                    "preview_idx": {"type": "integer"},
                                    "theme":       {"type": "string"},
                                    "stickers": {
                                        "type": "array",
                                        "minItems": stickers_per_preview,
                                        "maxItems": stickers_per_preview,
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


EXTRACT_INSTRUCTIONS = """
Mapping rules:
- "series_name": copy verbatim from the markdown's "## 套装 N：xxx" heading or the **series_name** field. Keep both Chinese and English parts if both present.
- "priority": parse from "建议优先级" — high/medium/low. If markdown uses ★ stars, ★★★★★ → high, ★★★★ → medium, ★★★ → low.
- "positioning_cn", "target_users_cn": pull from the 中文 sections.
- "style_anchor", "palette", "pack_archetype", "title_en", "target_audience_en":
  pull from the 英文 sections — keep them in English exactly as written.
- "preview_briefs": one object per preview. Each "stickers" entry is the full
  line including any " / english description" suffix. Preserve order.
- If a field is missing, use a sensible default (empty string or empty list)
  but still include the field. Do NOT invent series that aren't in the markdown.
""".strip()
