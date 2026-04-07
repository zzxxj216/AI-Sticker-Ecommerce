"""Topic Card 组装 + AI Prompt + 响应解析

职责:
  1. 从原始爬取数据组装 [Topic Card] 文本
  2. 从审核结果组装 [Reviewed Topic Card] 文本
  3. 存放两个 System Prompt
  4. 解析 AI 响应 → 结构化字段
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


# ── Number Formatting ────────────────────────────────────

def _fmt(n: int | float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(int(n))


def _ts_to_date(ts: int | float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


_AGE_LEVEL_MAP = {
    1: "13-17", 2: "18-24", 3: "25-34",
    4: "35-44", 5: "45-54", 6: "55+",
}


def _age_level_to_range(level) -> str:
    return _AGE_LEVEL_MAP.get(int(level), str(level)) if level else ""


# ═══════════════════════════════════════════════════════════
# Topic Card 组装
# ═══════════════════════════════════════════════════════════

def build_topic_card(row: dict) -> str:
    """从数据库行（hashtags 表）组装 [Topic Card] 文本。

    row 须包含: hashtag_name, publish_cnt, video_views,
                list_data_json, detail_data_json (nullable)
    """
    ld = json.loads(row.get("list_data_json") or "{}")
    dd = json.loads(row.get("detail_data_json") or "null") or {}

    name = row.get("hashtag_name") or ld.get("hashtag_name", "")
    posts_7d = ld.get("publish_cnt", 0)
    views_7d = ld.get("video_views", 0)
    posts_all = dd.get("publishCntAll", 0)
    views_all = dd.get("videoViewsAll", 0)
    rank = ld.get("rank", "")
    industry = (ld.get("industry_info") or {}).get("value", "")
    is_promoted = ld.get("is_promoted", False)

    lines = [
        "[Topic Card]",
        f"primary_hashtag: {name}",
        f"rank: {rank}",
        f"posts_last_7d: {_fmt(posts_7d)}",
        f"video_views_7d: {_fmt(views_7d)}",
    ]

    if posts_all:
        lines.append(f"posts_overall: {_fmt(posts_all)}")
    if views_all:
        lines.append(f"video_views_overall: {_fmt(views_all)}")
    if industry:
        lines.append(f"industry: {industry}")
    if is_promoted:
        lines.append("is_promoted: true")

    # 趋势数据
    trend_data = dd.get("trend") or ld.get("trend") or []
    if trend_data:
        lines.append("trend_data:")
        for p in trend_data[-7:]:
            date = _ts_to_date(p["time"]) if isinstance(p.get("time"), (int, float)) else p.get("date", "")
            lines.append(f"  {date}: {p.get('value', 0)}")

    # 相关 hashtag
    related = dd.get("relatedHashtags", [])
    if related:
        names = [h.get("hashtagName", "") for h in related if h.get("hashtagName")]
        lines.append(f"related_hashtags: {', '.join(names[:10])}")

    # 推荐 hashtag
    rec = dd.get("recList", [])
    if rec:
        rec_names = [h.get("hashtagName", "") for h in rec if h.get("hashtagName")]
        if rec_names:
            lines.append(f"recommended_hashtags: {', '.join(rec_names[:8])}")

    # 受众兴趣
    interests = dd.get("audienceInterests", [])
    if interests:
        int_names = [
            a.get("interestInfo", {}).get("value", "")
            for a in interests if a.get("interestInfo")
        ]
        lines.append(f"related_interests: {', '.join(int_names[:8])}")

    # 受众年龄
    ages = dd.get("audienceAges", [])
    if ages:
        age_parts = [_age_level_to_range(a.get("ageLevel")) for a in ages if a.get("score", 0) > 0]
        lines.append(f"audience_ages: {', '.join(filter(None, age_parts))}")

    # 受众地区
    countries = dd.get("audienceCountries", [])
    if countries:
        top_regions = [
            a.get("countryInfo", {}).get("value", "")
            for a in countries[:5] if a.get("countryInfo")
        ]
        lines.append(f"top_regions: {', '.join(top_regions)}")

    # 生命力
    longevity = dd.get("longevity", {})
    if longevity:
        pop_days = longevity.get("popularDays", 0)
        cur_pop = longevity.get("currentPopularity", 0)
        if pop_days:
            lines.append(f"popular_days: {pop_days}")
        if cur_pop:
            lines.append(f"current_popularity: {cur_pop}")

    # 描述
    desc = dd.get("description", "")
    if desc:
        lines.append(f"description: {desc[:300]}")

    # 创作者
    creators = json.loads(row.get("creators_raw_json") or "[]")
    if creators:
        creator_names = [c.get("nick_name", "") for c in creators[:5] if c.get("nick_name")]
        if creator_names:
            lines.append(f"top_creators: {', '.join(creator_names)}")

    return "\n".join(lines)


def build_reviewed_card(row: dict) -> str:
    """从审核结果组装 [Reviewed Topic Card] 文本。

    row 须包含 topic_reviews 表的关键字段。
    """
    lines = [
        "[Reviewed Topic Card]",
        f"Decision: {(row.get('decision') or '').capitalize()}",
        f"Normalized theme: {row.get('normalized_theme', '')}",
        f"Theme type: {row.get('theme_type', '')}",
        f"One-line interpretation: {row.get('one_line_interpretation', '')}",
        f"Recommended pack archetype: {row.get('pack_archetype', '')}",
        f"Best platform: {row.get('best_platform', '')}",
        f"Candidate visual symbols: {row.get('visual_symbols', '')}",
        f"Candidate emotional hooks: {row.get('emotional_hooks', '')}",
        f"Main risk flags: {row.get('risk_flags', '')}",
    ]
    if row.get("score_total"):
        lines.append(f"Score: {row['score_total']}/100")
    if row.get("sticker_fit_level"):
        lines.append(f"Sticker Fit Level: {row['sticker_fit_level']}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# System Prompts
# ═══════════════════════════════════════════════════════════

TOPIC_REVIEW_PROMPT = """You are a strict, skeptical senior sticker product topic reviewer.

Your job is to review a single TikTok topic card and decide whether it is suitable to become a sticker-pack opportunity.

You are not writing the final sticker pack plan.
You are not generating image prompts.
You are not summarizing social-media trends for reporting.

You are only responsible for:
1. judging whether this topic is worth entering the sticker production pipeline
2. abstracting it into a reusable sticker-friendly theme if appropriate
3. identifying risks, visual potential, emotional hooks, and product direction

The input may include:
- a primary hashtag
- related hashtags
- related interests
- related video titles
- signal strength data
- optional region/context notes

CRITICAL MINDSET:
- You must think like a ruthless product selector, not a trend commentator.
- Do not confuse social buzz with sticker product potential.
- Social popularity does NOT equal sticker suitability. Most trending hashtags are NOT good sticker products.
- Your expected approval rate is around 15-25%. If you find yourself approving everything, you are too lenient.
- When in doubt, Reject or Watchlist. Never Approve a borderline topic.
- Ask yourself: "Would a real person actually BUY this as a physical sticker and stick it on their laptop/bottle/phone?" If the answer is uncertain, do not approve.

HARD REJECTION RULES — You MUST reject topics that match ANY of these:
- Person-dependent (specific celebrities, influencers, politicians, athletes by name)
- Celebrity/gossip driven (drama, scandals, relationship news)
- Brand-dependent (cannot work without a specific brand name)
- IP-dependent (requires copyrighted characters, movie/TV/game franchises)
- Sports-score driven (game results, tournament brackets, team standings)
- Pure news events (breaking news, accidents, investigations, court cases, policy changes)
- Too vague or abstract (single generic words like "love", "happy", "life" with no visual anchor)
- Too context-dependent (requires knowing a specific meme, video, or inside joke to make sense)
- Visually thin (cannot generate 6+ distinct sticker designs from this theme)
- Too short-lived (flash trends under 2 weeks with no evergreen reuse potential)
- Purely informational/educational (how-to, tutorials, tips, hacks, DIY instructions)
- Political, controversial, or sensitive (anything that could alienate buyers)
- Generic social media behavior (challenges, duets, reply trends with no visual product angle)

Your response must follow this exact 4-part structure:

===== PART 1: QUICK DECISION =====
First line must be exactly one of (no other text on this line):
Decision: Approve
Decision: Watchlist
Decision: Reject

Then provide 2-4 sentences explaining why.

Decision meaning:
- Approve: strong sticker product potential, clear visual direction, commercially viable
- Watchlist: some potential but not yet strong/stable/safe enough to invest production resources
- Reject: should not enter the sticker production workflow

===== PART 2: TOPIC ANALYSIS =====
Use short sub-sections and cover these 7 points:

1. Topic interpretation
What this topic actually means in product terms.

2. Visual convertibility
Whether it can become sticker visuals naturally. Be skeptical — can you actually draw 6+ distinct stickers?

3. Emotional buyability
Why users would or would not spend real money on this as a sticker product. "It's popular" is not a reason.

4. Visual richness
Whether it has enough motifs, symbols, or variation potential to support a full pack.

5. Platform fit
Whether it fits Amazon, D2C, both, or neither.

6. Lifecycle judgment
Whether it is flash, seasonal, event-based, evergreen-with-boost, or long-tail.

7. Risk and originality
Whether it is too dependent on a real person, brand, copyrighted character, specific meme asset, or borrowed visual language.

===== PART 3: SCORING =====
Score the topic using these categories and a 100-point total.
Be harsh and honest. Most trending topics should score between 25-55.

- Sticker Visualizability: /20
- Emotional Buyability: /15
- Visual Richness: /15
- Platform Fit: /15
- Lifecycle Strength: /10
- Originality Safety: /15
- Trend Strength: /10

After the score breakdown, provide:
- Total Score: /100
- Sticker Fit Level: High / Medium / Low
- Recommended Action: Approve / Watchlist / Reject

MANDATORY score-to-decision rules (you MUST follow these, no exceptions):
- Total Score >= 80 → Approve
- Total Score 60-79 → Watchlist (needs human review)
- Total Score < 60  → Reject
- If Originality Safety < 8 → cannot be Approve regardless of total score
- If Sticker Visualizability < 10 → cannot be Approve regardless of total score

Scoring calibration:
- Do not let raw popularity dominate the result.
- A highly viral but unusable topic should score 20-40.
- An average trending hashtag with some visual potential but nothing special should score 40-60.
- A decent product-friendly topic (e.g. "coffee lover") may score 65-75 (Watchlist range, needs human review).
- Only truly outstanding sticker-friendly topics with clear visual richness, emotional pull, and commercial safety should score 80+.
- Scoring 80+ should feel rare — it means you are confident this can become a sellable sticker pack.

===== PART 4: REVIEW CARD =====
If the topic is Approve or Watchlist, output a concise review card with these headings:

- Normalized theme
- Theme type
- One-line interpretation
- Recommended pack archetype
- Best platform
- Candidate visual symbols
- Candidate emotional hooks
- Main risk flags
- Recommended next step

Theme type must be one of:
- evergreen_emotion
- seasonal_event
- lifestyle_identity
- animal_cute
- aesthetic_visual
- humor_relatable
- nature_outdoors
- food_drink
- object_icon
- label_badge

Recommended pack archetype must be one of:
- aesthetic_pack
- emotion_humor_pack
- seasonal_festival_pack
- lifestyle_identity_pack
- object_icon_pack
- label_badge_pack

If the topic is Reject, instead output:
- Main rejection reason
- Whether it should be ignored completely or only monitored passively

Writing rules:
- Write in Chinese unless the user asks for another language.
- Be direct, concrete, and product-oriented.
- Do not output JSON.
- Do not be vague.
- Do not say "it depends".
- Do not ask follow-up questions.
- Do not add full sticker-pack planning.
- Prefer reusable sticker-product logic over social-media commentary.
- Candidate visual symbols must be conservative and grounded in the input context.
- Do not invent highly specific symbols without support from the input.
"""


BATCH_TOPIC_REVIEW_PROMPT = """You are a strict, skeptical senior sticker product topic reviewer.

You will receive MULTIPLE topic cards in a single request (separated by "---TOPIC---").
For EACH topic, decide whether it is suitable to become a sticker-pack opportunity.

Apply the SAME criteria as a single-topic review:

HARD REJECTION RULES — MUST reject topics matching ANY:
- Person-dependent (celebrities, influencers, politicians, athletes by name)
- Celebrity/gossip driven, brand-dependent, IP-dependent
- Sports-score driven, pure news events
- Too vague or abstract, too context-dependent
- Visually thin (cannot generate 6+ distinct sticker designs)
- Too short-lived, purely informational/educational
- Political, controversial, or sensitive
- Generic social media behavior

SCORING (100-point total):
- Sticker Visualizability: /20
- Emotional Buyability: /15
- Visual Richness: /15
- Platform Fit: /15
- Lifecycle Strength: /10
- Originality Safety: /15
- Trend Strength: /10

MANDATORY score-to-decision rules:
- Total >= 80 → approve
- Total 60-79 → watchlist
- Total < 60 → reject
- Originality Safety < 8 → cannot approve
- Sticker Visualizability < 10 → cannot approve

Expected approval rate: 15-25%. Be harsh.

OUTPUT FORMAT: Return a JSON array. Each element corresponds to one input topic (same order).

```json
[
  {
    "hashtag": "<the primary_hashtag from input>",
    "decision": "approve|watchlist|reject",
    "score_total": <int 0-100>,
    "sticker_fit_level": "High|Medium|Low",
    "reason": "<2-3 sentence explanation in Chinese>",
    "normalized_theme": "<reusable theme name, empty if reject>",
    "theme_type": "<one of: evergreen_emotion, seasonal_event, lifestyle_identity, animal_cute, aesthetic_visual, humor_relatable, nature_outdoors, food_drink, object_icon, label_badge — empty if reject>",
    "one_line_interpretation": "<product-oriented interpretation in Chinese>",
    "pack_archetype": "<one of: aesthetic_pack, emotion_humor_pack, seasonal_festival_pack, lifestyle_identity_pack, object_icon_pack, label_badge_pack — empty if reject>",
    "best_platform": "<amazon/d2c/both — empty if reject>",
    "visual_symbols": "<comma-separated visual elements, empty if reject>",
    "emotional_hooks": "<comma-separated emotional hooks, empty if reject>",
    "risk_flags": "<comma-separated risk flags>"
  }
]
```

CRITICAL:
- Output ONLY the JSON array, no other text before or after.
- The array length MUST match the number of input topics.
- Keep the same order as input topics.
"""

TOPIC_TO_BRIEF_PROMPT = """You are a sticker product brief builder.

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
"""


# ═══════════════════════════════════════════════════════════
# AI 响应解析
# ═══════════════════════════════════════════════════════════

def _split_parts(text: str) -> dict[int, str]:
    """把响应按 ===== PART N: ... ===== 拆分。"""
    pattern = r'={3,}\s*PART\s+(\d+)\s*:.*?={3,}'
    markers = list(re.finditer(pattern, text))
    result: dict[int, str] = {}
    for i, m in enumerate(markers):
        part_num = int(m.group(1))
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        result[part_num] = text[start:end].strip()
    return result


def _normalize_kv_key(raw: str) -> str:
    k = raw.strip().lower().replace(" ", "_").replace("-", "_")
    k = re.sub(r"^\*+|\*+$", "", k)
    return k


def _extract_kv(text: str) -> dict[str, str]:
    """从 brief 文本提取键值对。

    支持:
      1) 单行: - key: value 或 -- key: value
      2) TOPIC_TO_BRIEF 约定标题行: -- trend_name（无冒号，值在后续行）
      3) Markdown: **trend_name**: value
      4) 多行值: 标题行后缩进/正文直到下一标题
    """
    result: dict[str, str] = {}
    lines = text.split("\n")
    current_key: str | None = None
    current_val_lines: list[str] = []

    def _flush():
        nonlocal current_key, current_val_lines
        if current_key:
            val = "\n".join(current_val_lines).strip()
            if val:
                result[current_key] = val
        current_key = None
        current_val_lines = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Markdown **field**: value
        md = re.match(r"^\*\*([^*]+)\*\*\s*:\s*(.*)$", stripped)
        if md:
            _flush()
            key = _normalize_kv_key(md.group(1))
            rest = md.group(2).strip()
            if key:
                current_key = key
                current_val_lines = [rest] if rest else []
            continue

        # -- field (与 TOPIC_TO_BRIEF_PROMPT 中「-- trend_name」一致；旧逻辑误跳过 --)
        if stripped.startswith("--"):
            _flush()
            content = stripped[2:].strip()
            if ":" in content:
                key_part, _, val_part = content.partition(":")
                key = _normalize_kv_key(key_part)
                val_part = val_part.strip()
                if key:
                    current_key = key
                    current_val_lines = [val_part] if val_part else []
            else:
                key = _normalize_kv_key(content)
                if key:
                    current_key = key
                    current_val_lines = []
            continue

        # 单行 - key: value（排除 --- 分隔线）
        is_single_dash_heading = (
            stripped.startswith("-")
            and not stripped.startswith("--")
            and not stripped.startswith("---")
        )
        if is_single_dash_heading:
            _flush()
            content = stripped[1:].strip()
            if ":" in content:
                key_part, _, val_part = content.partition(":")
                key = _normalize_kv_key(key_part)
                val_part = val_part.strip()
                if key:
                    current_key = key
                    current_val_lines = [val_part] if val_part else []
            else:
                key = _normalize_kv_key(content)
                if key:
                    current_key = key
                    current_val_lines = []
            continue

        if current_key is not None:
            current_val_lines.append(stripped)

    _flush()
    return result


def parse_batch_review_response(text: str) -> list[dict[str, Any]]:
    """解析 BATCH_TOPIC_REVIEW_PROMPT 的批量 JSON 响应。"""
    cleaned = text.strip()
    # Strip markdown code fence if present
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n")
        last_fence = cleaned.rfind("```")
        if last_fence > first_nl:
            cleaned = cleaned[first_nl + 1:last_fence].strip()

    items = json.loads(cleaned)
    results: list[dict[str, Any]] = []
    for item in items:
        score_total = int(item.get("score_total", 0))
        decision = (item.get("decision") or "reject").lower()

        # Enforce score-based rules
        if score_total > 0:
            if score_total < 60:
                decision = "reject"
            elif score_total < 80 and decision == "approve":
                decision = "watchlist"

        results.append({
            "decision": decision,
            "normalized_theme": item.get("normalized_theme", ""),
            "theme_type": item.get("theme_type", ""),
            "one_line_interpretation": item.get("one_line_interpretation", ""),
            "pack_archetype": item.get("pack_archetype", ""),
            "best_platform": item.get("best_platform", ""),
            "visual_symbols": item.get("visual_symbols", ""),
            "emotional_hooks": item.get("emotional_hooks", ""),
            "risk_flags": item.get("risk_flags", ""),
            "score_total": score_total,
            "sticker_fit_level": item.get("sticker_fit_level", ""),
        })
    return results


def parse_review_response(text: str) -> dict[str, Any]:
    """解析 TOPIC_REVIEW_PROMPT 的 AI 响应。"""
    parts = _split_parts(text)

    # PART 1 → decision (prefer structured "Decision: Xxx" line)
    decision = "unknown"
    part1 = parts.get(1, "")
    decision_match = re.search(r'^Decision\s*[:：]\s*(Approve|Watchlist|Reject)', part1, re.IGNORECASE | re.MULTILINE)
    if decision_match:
        decision = decision_match.group(1).lower()
    else:
        p1_lower = part1.lower()
        if "reject" in p1_lower:
            decision = "reject"
        elif "watchlist" in p1_lower:
            decision = "watchlist"
        elif "approve" in p1_lower:
            decision = "approve"

    # PART 3 → scoring
    part3 = parts.get(3, "")
    kv3 = _extract_kv(part3)

    score_total = 0
    m = re.search(r'total\s*score\s*[:：]\s*(\d+)', part3, re.IGNORECASE)
    if m:
        score_total = int(m.group(1))

    orig_safety = 0
    m_orig = re.search(r'originality\s*safety\s*[:：]\s*(\d+)', part3, re.IGNORECASE)
    if m_orig:
        orig_safety = int(m_orig.group(1))

    viz_score = 0
    m_viz = re.search(r'sticker\s*visualizability\s*[:：]\s*(\d+)', part3, re.IGNORECASE)
    if m_viz:
        viz_score = int(m_viz.group(1))

    sticker_fit = kv3.get("sticker_fit_level", "")
    recommended_action = kv3.get("recommended_action", "")
    if recommended_action and decision == "unknown":
        decision = recommended_action.lower()

    # Enforce score-based decision override
    if score_total > 0:
        if score_total < 60:
            decision = "reject"
        elif score_total < 80:
            if decision == "approve":
                decision = "watchlist"
        if orig_safety > 0 and orig_safety < 8 and decision == "approve":
            decision = "watchlist"
        if viz_score > 0 and viz_score < 10 and decision == "approve":
            decision = "watchlist"

    # PART 4 → review card
    part4 = parts.get(4, "")
    kv4 = _extract_kv(part4)

    return {
        "decision": decision,
        "normalized_theme": kv4.get("normalized_theme", ""),
        "theme_type": kv4.get("theme_type", ""),
        "one_line_interpretation": kv4.get("one-line_interpretation",
                                           kv4.get("one_line_interpretation", "")),
        "pack_archetype": kv4.get("recommended_pack_archetype", ""),
        "best_platform": kv4.get("best_platform", ""),
        "visual_symbols": kv4.get("candidate_visual_symbols", ""),
        "emotional_hooks": kv4.get("candidate_emotional_hooks", ""),
        "risk_flags": kv4.get("main_risk_flags", ""),
        "score_total": score_total,
        "sticker_fit_level": sticker_fit,
    }


def parse_brief_response(text: str) -> dict[str, Any]:
    """解析 TOPIC_TO_BRIEF_PROMPT 的 AI 响应。"""
    parts = _split_parts(text)

    # PART 1 → brief status
    part1 = parts.get(1, "")
    brief_status = "not_ready"
    if "brief ready" in part1.lower():
        brief_status = "ready"

    # PART 2 → structured brief
    part2 = parts.get(2, "")
    kv = _extract_kv(part2)
    # 模型未打 PART 标记时，从全文再抽一遍（常见：直接输出 -- trend_name 列表）
    if not any(kv.get(k) for k in (
        "trend_name", "one_line_explanation", "trend_type", "visual_symbols",
    )):
        kv_full = _extract_kv(text)
        for k, v in kv_full.items():
            if v and not kv.get(k):
                kv[k] = v

    return {
        "brief_status": brief_status,
        "trend_name": kv.get("trend_name", ""),
        "trend_type": kv.get("trend_type", ""),
        "one_line_explanation": kv.get("one_line_explanation", ""),
        "why_now": kv.get("why_now", ""),
        "lifecycle": kv.get("lifecycle", ""),
        "platform": kv.get("platform", ""),
        "product_goal": kv.get("product_goal", ""),
        "target_audience": kv.get("target_audience", ""),
        "emotional_core": kv.get("emotional_core", ""),
        "visual_symbols": kv.get("visual_symbols", ""),
        "visual_do": kv.get("visual_do", ""),
        "visual_avoid": kv.get("visual_avoid", ""),
        "must_include": kv.get("must_include", ""),
        "must_avoid": kv.get("must_avoid", ""),
        "risk_notes": kv.get("risk_notes", ""),
        "pack_size_goal": kv.get("pack_size_goal", ""),
        "reference_notes": kv.get("reference_notes", ""),
    }
