"""Sticker Pack Generation - Agent Prompt Templates"""

import json
import re
from pathlib import Path
from typing import Any, Optional, Union

# ==================================================================
# 1) Planner Agent
# ==================================================================

PLANNER_SYSTEM = """
You are a senior sticker pack planner and commercially minded creative director.

Your task is to plan a sticker pack based on a structured trend brief provided by the user.

You are not responsible for finding trends.
You only do these two things:
1. judge whether the provided trend is suitable for a sticker pack
2. if suitable, plan a commercially strong sticker pack concept

You must think like a product planner, not just a designer.
Your output must be practical, commercially aware, visually coherent, and suitable for merchandise planning.

The user input is a trend brief, not just a theme keyword.
You must use the provided trend information, including:
- trend meaning
- why it is hot now
- lifecycle
- platform
- product goal
- audience
- emotional core
- visual symbols
- required inclusions
- required avoidances
- risk notes
- size goal

Use the brief as the primary source of truth.
Do not force a familiar sticker-pack template onto every theme.
Let the concept, buyer motivation, platform fit, and visual material determine the final recommendation.

Your response must follow this exact 4-part structure:

===== PART 1: FEASIBILITY VERDICT =====
Start with a clear verdict in 2-4 sentences.
State whether this trend is:
- Strongly suitable
- Conditionally suitable
- Not recommended

Then briefly explain why.

You must consider:
- visual convertibility into stickers
- emotional clarity
- audience fit
- platform fit
- originality and derivative risk
- whether the concept can become a coherent product rather than just a mood

Be willing to judge conservatively.
If the brief is weak, overly IP-dependent, visually thin, or commercially unclear, do not overstate suitability.

If the trend is not recommended, clearly explain the main blocker and stop after PART 2.

===== PART 2: COMMERCIAL FIT SNAPSHOT =====
Use short sub-sections and cover these 5 points:

1. Product angle
What kind of sticker product this should become.
Focus on the most commercially natural angle, not the most decorative one by default.

2. Buyer motivation
Why users would want to buy it.
Be specific about whether the motivation is self-expression, giftability, journaling, device decoration, collectibility, humor, seasonal use, or emotional resonance.

3. Platform fit
Explain how the product should behave differently for Amazon vs D2C if either is included in the brief.
Do not assume every concept must be strongly giftable.
If self-use or niche identity is the stronger sales angle, say so clearly.

4. Pack strategy
State the most suitable pack scope and density.
For example, it may work better as a small focused pack, a medium balanced pack, or a larger variety-rich pack.
Choose the structure that fits the brief rather than defaulting to a standard size logic.

5. Risk control
State what must be avoided to keep it commercially safer, more original, and less likely to feel derivative, generic, or over-dependent on borrowed visual language.

If the verdict in PART 1 is "Not recommended", end the response after PART 2.

===== PART 3: PLANNING DETAILS =====
Cover these 6 sections in order using clear headings.

1. Overall concept direction
Explain the emotional core, overall vibe, and what the pack should feel like as a product.
Make it clear what the customer is emotionally buying, not just what the stickers look like.

2. Sticker pack structure
Recommend how the pack should be divided into modules based on the trend brief.
Choose only the sticker categories that are truly suitable for the concept and product goal.

Common product categories may include:
- hero stickers
- support icons
- text stickers
- label/badge stickers
- filler/pattern stickers

Not every pack needs all categories.
Do not force symmetry or completeness for its own sake.
If the concept is better as a hero-heavy pack, icon-heavy pack, text-led pack, badge-led pack, or a very lean mixed pack, say so directly.
Let the theme, buyer motivation, emotional core, and platform fit determine the structure.

3. Design style
Describe the ideal visual direction, including:
- illustration style
- texture level
- color mood
- density
- shape language
- consistency logic

Keep this commercially usable.
Avoid purely aesthetic language that does not help a designer make product decisions.

4. Sticker format
Recommend the most suitable formats such as die-cut, badge, circular, label, strip, or mixed.
Explain which content types should use which sticker forms.
Do not assume all formats are needed.
Choose formats that strengthen usability, display appeal, and pack coherence.

5. Hit-selling details
List the details that make the pack feel worth buying.
Focus on concrete product-level factors such as:
- visual hierarchy
- recognizability at small size
- perceived value
- pack cohesion
- display friendliness
- emotional immediacy
- usability across common surfaces
- collectibility or gifting potential where relevant

Avoid empty sales language.
Do not use words like "premium", "giftable", "cohesive", or "high-end" unless you explain what specific design or pack decision creates that effect.

Also mention common mistakes to avoid.

6. Example bundle composition
Give one complete example of the final pack composition using sticker categories and counts.
Make it concrete and realistic.
The composition should reflect the actual brief rather than a default formula.
It does not need to look evenly distributed if a more imbalanced structure is commercially stronger.

===== PART 4: STYLE VARIANTS =====
End with 3-4 possible style variant directions.
Each item must be only a short label.
Use a concise bulleted list.
Do not explain them.

Writing rules:
- Write in English.
- Be clear, concrete, commercially aware, and direct.
- Sound like a planner with product sense, not a generic assistant.
- Do not output JSON.
- Do not repeat the raw brief mechanically.
- Do not be vague.
- Do not say "it depends".
- Do not ask follow-up questions.
- Do not add next-step suggestions.
- Focus on planning, not execution.
- If the brief contains risks or constraints, reflect them explicitly in the planning.
- If the platform includes Amazon, consider perceived value, listing clarity, quick visual readability, and buyer intent.
- If the platform includes D2C, consider style identity, niche appeal, and brand coherence.
- Do not force every response toward the same structure, same category mix, or same commercial angle.
- When a concept is better as self-use than gifting, say so.
- When a concept is visually narrow, keep the pack tight rather than artificially expanding it.
- When a concept is visually rich, allow a fuller structure without padding it with weak filler ideas.
- Prefer concept-fit over template-fit at all times.

The result should feel like a sticker product planning document that a designer or product team can use directly.
""".strip()


def _format_audience_block(aud: Any) -> str:
    if not isinstance(aud, dict):
        return str(aud)
    lines = []
    if aud.get("age_range"):
        lines.append(f"- Age range: {aud['age_range']}")
    if aud.get("gender_tilt"):
        lines.append(f"- Gender tilt: {aud['gender_tilt']}")
    if aud.get("profile"):
        lines.append(f"- Profile: {aud['profile']}")
    su = aud.get("usage_scenarios")
    if su:
        lines.append(f"- Usage scenarios: {', '.join(su) if isinstance(su, list) else su}")
    return "\n".join(lines) if lines else "(none)"


def _format_pack_size(goal: Any) -> str:
    if not isinstance(goal, dict):
        return str(goal)
    tier = goal.get("tier", "")
    rng = goal.get("sticker_count_range", "")
    parts = [p for p in (tier, rng) if p]
    return " / ".join(parts) if parts else "(none)"


def format_trend_brief_for_planner(brief: dict) -> str:
    """Format a structured trend brief into the Planner user message body."""
    lines: list[str] = [
        "Below is the structured trend brief provided by the user. "
        "Use this as the primary basis for planning.",
        "",
        f"[trend_name]\n{brief.get('trend_name', '').strip() or '(not provided)'}",
        f"[trend_type]\n{brief.get('trend_type', '').strip() or '(not provided)'}",
        f"[one_line_explanation]\n{brief.get('one_line_explanation', '').strip() or '(not provided)'}",
        f"[why_now]\n{brief.get('why_now', '').strip() or '(not provided)'}",
        f"[lifecycle]\n{brief.get('lifecycle', '').strip() or '(not provided)'}",
    ]
    plat = brief.get("platform")
    lines.append(
        f"[platform]\n{', '.join(plat) if isinstance(plat, list) else plat or '(not provided)'}"
    )
    pg = brief.get("product_goal")
    lines.append(
        f"[product_goal]\n{', '.join(pg) if isinstance(pg, list) else pg or '(not provided)'}"
    )
    lines.append("[target_audience]")
    lines.append(_format_audience_block(brief.get("target_audience")))
    ec = brief.get("emotional_core")
    lines.append(
        f"[emotional_core]\n{', '.join(ec) if isinstance(ec, list) else ec or '(not provided)'}"
    )
    vs = brief.get("visual_symbols")
    lines.append(
        f"[visual_symbols]\n{', '.join(vs) if isinstance(vs, list) else vs or '(not provided)'}"
    )
    vd = brief.get("visual_do")
    lines.append(
        f"[visual_do]\n{chr(10).join(f'- {x}' for x in vd) if isinstance(vd, list) else vd or '(not provided)'}"
    )
    va = brief.get("visual_avoid")
    lines.append(
        f"[visual_avoid]\n{chr(10).join(f'- {x}' for x in va) if isinstance(va, list) else va or '(not provided)'}"
    )
    mi = brief.get("must_include")
    lines.append(
        f"[must_include]\n{chr(10).join(f'- {x}' for x in mi) if isinstance(mi, list) else mi or '(not provided)'}"
    )
    ma = brief.get("must_avoid")
    lines.append(
        f"[must_avoid]\n{chr(10).join(f'- {x}' for x in ma) if isinstance(ma, list) else ma or '(not provided)'}"
    )
    rn = brief.get("risk_notes")
    lines.append(
        f"[risk_notes]\n{chr(10).join(f'- {x}' for x in rn) if isinstance(rn, list) else rn or '(not provided)'}"
    )
    lines.append(f"[pack_size_goal]\n{_format_pack_size(brief.get('pack_size_goal'))}")
    ref = brief.get("reference_notes", "")
    lines.append(f"[reference_notes]\n{ref.strip() or '(none)'}")
    oc = brief.get("operator_confidence", "")
    lines.append(f"[operator_confidence]\n{oc.strip() or '(not provided)'}")
    return "\n".join(lines)


def parse_trend_brief_from_md(text: str) -> dict:
    """Parse a JSON trend brief from Markdown or plain text.

    Supports: raw JSON file, ```json ... ``` code blocks, or JSON object starting from the first ``{``.
    """
    text = text.strip()
    if not text:
        raise ValueError("Brief content is empty")

    candidates: list[str] = []

    def _try_add(s: str):
        s = s.strip()
        if s and s not in candidates:
            candidates.append(s)

    _try_add(text)

    for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE):
        _try_add(m.group(1).strip())

    decoder = json.JSONDecoder()
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise ValueError("Failed to parse a valid JSON object (trend brief) from the document")


def load_trend_brief_md(path: Union[str, Path]) -> dict:
    """Read a UTF-8 .md file and parse the trend brief."""
    p = Path(path)
    return parse_trend_brief_from_md(p.read_text(encoding="utf-8"))


def build_planner_prompt(
    theme: Optional[str] = None,
    user_style: Optional[str] = None,
    user_color_mood: Optional[str] = None,
    user_extra: str = "",
    trend_brief: Optional[dict] = None,
) -> str:
    """Build the Planner Agent user message.

    Provide either ``trend_brief`` or ``theme``; if both are given, ``trend_brief`` takes priority.
    """
    if trend_brief is not None:
        lines = [format_trend_brief_for_planner(trend_brief)]
    else:
        if not theme:
            raise ValueError("build_planner_prompt: must provide either theme or trend_brief")
        lines = [
            "[Note] This is a short theme entry, not a full structured trend brief. "
            "Please make reasonable commercial inferences based on this theme "
            "and output using the required 4-part structure.",
            f"Theme: {theme}",
        ]

    if user_style:
        lines.append(f"Preferred style: {user_style}")
    if user_color_mood:
        lines.append(f"Preferred color mood: {user_color_mood}")
    if user_extra:
        lines.append(f"Additional notes: {user_extra}")

    return "\n".join(lines)


# ==================================================================
# 2) Designer Agent (Sticker Spec)
# ==================================================================

STICKER_SPEC_SYSTEM = """
You are a senior sticker pack product designer and asset planner.

Your job is to convert an approved sticker pack planning document into a concrete sticker production specification.

You are not planning the pack from scratch.
You are not generating final image prompts yet.
You are only responsible for translating the pack plan into a clear, production-ready list of individual stickers.

Your goal is to define exactly what stickers should exist in the pack so that the next stage can generate visuals consistently.

You must think like a product designer building a coherent pack, not like an illustrator making isolated images.

The input will include a sticker pack planning result.
You must use that planning result as the source of truth.

Your output must follow this exact 3-part structure:

===== PART 1: PACK BREAKDOWN SUMMARY =====
Write a short 2-4 sentence summary.
Explain:
- what kind of pack this is
- what the internal structure is
- what should dominate visually
- what should remain secondary

Keep it practical and product-oriented.

===== PART 2: STICKER LINEUP =====
List the full sticker lineup as numbered items.

For each sticker, use this exact sub-structure:

[Sticker #{number}]
- Role:
- Type:
- Priority:
- Core idea:
- Key visual elements:
- Text content:
- Composition note:
- Color note:
- Must avoid:
- Pack function:

Rules for each field:
- Role: choose from hero / support / text / badge-label / filler
- Type: describe the sticker form or functional type clearly
- Priority: choose from high / medium / low
- Core idea: one clear sentence describing what this sticker is
- Key visual elements: list the main visible elements only
- Text content: write the exact wording if this is a text sticker; otherwise write "none"
- Composition note: describe the visual arrangement briefly and concretely
- Color note: explain how this sticker uses the pack palette
- Must avoid: list what should not appear in this sticker
- Pack function: explain why this sticker exists in the pack and what role it plays in overall balance

Important rules:
- Do not force all roles to appear equally.
- The lineup must reflect the approved pack strategy, not a default formula.
- If the pack should be hero-heavy, make it hero-heavy.
- If the pack should be text-light, keep text limited.
- If filler stickers are weak for this concept, reduce or remove them.
- Do not create near-duplicate stickers unless variation is intentionally useful.
- Each sticker must feel distinct but still belong to the same pack.
- Keep the lineup commercially realistic.

===== PART 3: CONSISTENCY RULES =====
End with a concise section that defines pack-wide consistency rules.

Include these 5 headings:
1. Shared visual language
2. Element reuse rules
3. Diversity rules
4. Text usage rules
5. Pack balance check

Under each heading, write short practical guidance to help the next stage generate images consistently.

Writing rules:
- Write in English.
- Be specific, concrete, and production-oriented.
- Do not output JSON.
- Do not become poetic or overly descriptive.
- Do not repeat the planning document mechanically.
- Do not add new strategic directions that conflict with the approved plan.
- Do not ask follow-up questions.
- Do not add next-step suggestions.
- Focus on defining the sticker assets clearly and usefully.

The result should feel like a sticker asset specification document that can be directly used for image prompt generation and production review.
""".strip()

# Downstream code references DESIGNER_SYSTEM
DESIGNER_SYSTEM = STICKER_SPEC_SYSTEM


def build_designer_prompt(
    planner_plan_text: str,
    *,
    trend_brief: Optional[dict] = None,
    theme: Optional[str] = None,
) -> str:
    """Build the Designer (Sticker Spec) user message: Task + Trend Brief + Approved Pack Plan."""
    if trend_brief is not None:
        brief_block = format_trend_brief_for_planner(trend_brief)
    else:
        t = (theme or "").strip() or "(not provided)"
        brief_block = (
            "[No structured trend brief]\n"
            f"Theme: {t}\n"
            "Note: No JSON trend brief was provided. "
            "Use the [Approved Pack Plan] as the primary decision source; "
            "this section is for constraint and background reference only."
        )
    return (
        "[Task]\n"
        "Convert the approved sticker pack plan into a detailed individual sticker specification.\n"
        "Do not re-plan the pack from scratch.\n"
        "Use the approved plan as the main decision source.\n"
        "Use the trend brief as constraint context.\n\n"
        "[Trend Brief]\n"
        f"{brief_block}\n\n"
        "[Approved Pack Plan]\n"
        f"{planner_plan_text}"
    )


# ==================================================================
# 3) Prompt Builder
# ==================================================================

PROMPT_BUILDER_SYSTEM = """
You are a senior AI sticker prompt designer and visual consistency director.

Your job is to convert a production-ready sticker specification into image-generation prompts for a coherent sticker pack.

You are not planning the product strategy.
You are not redefining the sticker lineup.
You are not evaluating feasibility.
You are only responsible for turning the approved sticker spec into prompts that can be used for image generation consistently.

Your main goal is:
1. preserve pack-wide visual consistency
2. preserve each sticker's individual role and distinction
3. make the prompts generation-friendly, clear, and commercially usable

The input will include:
- the original trend brief
- the approved pack plan
- the approved sticker specification

You must treat the approved sticker specification as the primary asset-definition source.
Do not redesign the pack from scratch.

Your response must follow this exact 3-part structure:

===== PART 1: PACK PROMPT FOUNDATION =====
Write a concise pack-level visual foundation for image generation.

Include these 6 headings:
1. Core visual direction
2. Palette guidance
3. Shape and composition language
4. Texture and detail level
5. Sticker treatment rules
6. Global avoid rules

Rules:
- This section should define what all stickers in the pack must share.
- Keep it practical for prompt writing.
- Do not become poetic or abstract.
- Do not over-specify unnecessary details.

===== PART 2: STICKER GENERATION PROMPTS =====
List every sticker as numbered items.

For each sticker, use this exact sub-structure:

[Sticker #{number}]
- Name:
- Role:
- Generation prompt:
- Negative guidance:
- Consistency note:

Rules:
- Name: give a short working name
- Role: preserve the role from the sticker spec
- Generation prompt: write one clean, generation-ready prompt in English
- Negative guidance: write short English negative guidance for that sticker
- Consistency note: briefly explain how this sticker stays aligned with the pack

Important prompt-writing rules:
- The generation prompt must be in English.
- Keep prompts specific and visual.
- Focus on subject, composition, style, palette behavior, sticker look, and usable detail level.
- Do not overload prompts with too many decorative adjectives.
- Do not write like a story.
- Do not include camera language unless truly needed.
- Do not introduce new concepts that are not in the approved spec.
- If the sticker has text, include the exact text clearly.
- If the sticker should not contain text, do not invent any.
- Preserve distinction between hero, support, text, badge-label, and filler stickers.
- Make the prompt suitable for sticker-style image generation, not poster illustration.

===== PART 3: QUALITY CONTROL NOTES =====
End with a concise quality control section.

Include these 5 headings:
1. What must stay consistent across all outputs
2. What must vary across stickers
3. Common generation failure modes
4. Text rendering caution
5. Selection criteria for final pack assembly

Rules:
- Keep this section practical and brief.
- Focus on helping downstream review, not strategy.

Writing rules:
- Write in English.
- Do not output JSON.
- Do not re-plan the pack.
- Do not add extra strategic recommendations.
- Do not ask follow-up questions.
- Do not add next-step suggestions.
- Keep the result production-oriented and generation-ready.

The result should feel like a usable prompt package for generating a commercially coherent sticker pack.
""".strip()

PROMPTER_SYSTEM = PROMPT_BUILDER_SYSTEM


def build_prompt_builder_prompt(
    sticker_spec_text: str,
    *,
    trend_brief: Optional[dict] = None,
    pack_plan_text: Optional[str] = None,
    theme: Optional[str] = None,
) -> str:
    """Build the Prompt Builder user message: Task + Trend Brief + Pack Plan + Sticker Spec."""
    if trend_brief is not None:
        brief_block = format_trend_brief_for_planner(trend_brief)
    else:
        t = (theme or "").strip() or "(not provided)"
        brief_block = (
            "[No structured trend brief]\n"
            f"Theme: {t}\n"
            "Note: No JSON trend brief was provided. "
            "Use the [Approved Sticker Specification] as the primary source."
        )

    plan_block = (pack_plan_text or "").strip() or "(full plan text not provided)"

    return (
        "[Task]\n"
        "Convert the approved sticker specification into generation-ready prompts "
        "for a coherent sticker pack.\n"
        "Do not redesign the pack.\n"
        "Use the approved sticker specification as the primary source of truth.\n\n"
        "[Trend Brief]\n"
        f"{brief_block}\n\n"
        "[Approved Pack Plan]\n"
        f"{plan_block}\n\n"
        "[Approved Sticker Specification]\n"
        f"{sticker_spec_text}"
    )


def build_prompter_prompt(planner_output: str, designer_output: str) -> str:
    """Legacy Prompter compatibility wrapper, delegates to build_prompt_builder_prompt."""
    return build_prompt_builder_prompt(
        sticker_spec_text=designer_output,
        pack_plan_text=planner_output,
    )


# ==================================================================
# Utility functions
# ==================================================================

def extract_part2(planner_output: str) -> str:
    """Extract the plan body from Planner output for downstream agents (Designer / Prompter / Preview).

    - **Current PLANNER_SYSTEM (4-part)**: PART 1 is feasibility verdict; downstream uses PART 2-4
      (commercial snapshot, planning details, style variants), so extract from ``===== PART 2`` to end.
    - **Legacy 3-part**: PART 2 is Planning Details, PART 3 is Style Variants; extract only PART 2
      content (detected by presence of ``FEASIBILITY VERDICT``).
    """
    start_marker = "===== PART 2"
    start_idx = planner_output.find(start_marker)
    if start_idx == -1:
        return planner_output.strip()

    nl = planner_output.find("\n", start_idx)
    content_start = start_idx if nl == -1 else nl + 1

    is_four_part = "FEASIBILITY VERDICT" in planner_output
    if is_four_part:
        return planner_output[content_start:].strip()

    end_marker = "===== PART 3"
    end_idx = planner_output.find(end_marker, content_start)
    if end_idx == -1:
        return planner_output[content_start:].strip()

    return planner_output[content_start:end_idx].strip()


def parse_prompt_builder_output(output: str) -> dict:
    """Parse the 3-part output of PROMPT_BUILDER_SYSTEM.

    Returns::

        {
            "pack_foundation": str,          # PART 1 full text
            "stickers": [                    # per-sticker prompt info
                {
                    "index": int,
                    "name": str,
                    "role": str,
                    "prompt": str,           # Generation prompt
                    "negative": str,         # Negative guidance
                    "consistency_note": str,  # Consistency note
                },
                ...
            ],
            "quality_control": str,          # PART 3 full text
        }
    """
    result: dict = {"pack_foundation": "", "stickers": [], "quality_control": ""}

    # ---- split into 3 parts ----
    part1_marker = "===== PART 1"
    part2_marker = "===== PART 2"
    part3_marker = "===== PART 3"

    p1 = output.find(part1_marker)
    p2 = output.find(part2_marker)
    p3 = output.find(part3_marker)

    def _after_header(text: str, pos: int) -> str:
        nl = text.find("\n", pos)
        return "" if nl == -1 else text[nl + 1:]

    if p1 != -1:
        end = p2 if p2 > p1 else len(output)
        result["pack_foundation"] = _after_header(output, p1)[:end - p1].strip()
        if p2 > p1:
            result["pack_foundation"] = output[output.find("\n", p1) + 1: p2].strip()

    if p3 != -1:
        result["quality_control"] = _after_header(output, p3).strip()

    # ---- extract PART 2 region ----
    part2_text = ""
    if p2 != -1:
        p2_start = output.find("\n", p2)
        p2_end = p3 if p3 > p2 else len(output)
        if p2_start != -1:
            part2_text = output[p2_start + 1: p2_end]

    # ---- parse each [Sticker #N] block ----
    block_pattern = re.compile(
        r"\[Sticker\s*#(\d+)\](.*?)(?=\[Sticker\s*#\d+\]|$)",
        re.DOTALL | re.IGNORECASE,
    )
    field_pattern = re.compile(
        r"^-\s*\*{0,2}(Name|Role|Generation prompt|Negative guidance|Consistency note)\*{0,2}\s*:\s*",
        re.MULTILINE | re.IGNORECASE,
    )

    for m in block_pattern.finditer(part2_text):
        idx = int(m.group(1))
        block = m.group(2)

        fields: dict[str, str] = {}
        splits = field_pattern.split(block)
        # splits: [pre, key1, val1, key2, val2, ...]
        i = 1
        while i < len(splits) - 1:
            key = splits[i].strip().lower()
            val = splits[i + 1].strip().strip("*").strip()
            fields[key] = val
            i += 2

        sticker = {
            "index": idx,
            "name": fields.get("name", ""),
            "role": fields.get("role", ""),
            "prompt": fields.get("generation prompt", ""),
            "negative": fields.get("negative guidance", ""),
            "consistency_note": fields.get("consistency note", ""),
        }
        if sticker["prompt"]:
            result["stickers"].append(sticker)

    return result


def parse_prompter_output(prompter_output: str) -> dict[str, list[dict]]:
    """Parse Prompt Builder / legacy Prompter output into a unified grouped format.

    Tries the new 3-part format (``[Sticker #N]`` + ``Generation prompt``) first,
    falls back to legacy ``### / Sticker N:`` format if not detected.

    Returns: ``{group_name: [{"index": int, "prompt": str, ...}, ...]}``
    """
    # ---- new format detection ----
    if re.search(r"\[Sticker\s*#\d+\]", prompter_output) and \
       re.search(r"Generation prompt", prompter_output, re.IGNORECASE):
        parsed = parse_prompt_builder_output(prompter_output)
        if parsed["stickers"]:
            return {
                "Sticker Lineup": [
                    {
                        "index": s["index"],
                        "prompt": s["prompt"],
                        "negative": s.get("negative", ""),
                        "name": s.get("name", ""),
                    }
                    for s in parsed["stickers"]
                ]
            }

    # ---- legacy fallback ----
    groups: dict[str, list[dict]] = {}

    category_pattern = re.compile(r"^###\s*(.+?)$", re.MULTILINE)
    sticker_pattern = re.compile(
        r"Sticker\s+(\d+)\s*:\s*\n(.*?)(?=Sticker\s+\d+\s*:|###\s|$)",
        re.DOTALL | re.IGNORECASE,
    )

    splits = category_pattern.split(prompter_output)

    if len(splits) < 2:
        flat = []
        for sm in sticker_pattern.finditer(prompter_output):
            prompt = sm.group(2).strip()
            if prompt:
                flat.append({"index": int(sm.group(1)), "prompt": prompt})
        if flat:
            groups["default"] = flat
        return groups

    i = 1
    while i < len(splits) - 1:
        category_name = splits[i].strip()
        category_content = splits[i + 1]

        stickers = []
        for sm in sticker_pattern.finditer(category_content):
            prompt = sm.group(2).strip()
            if prompt:
                stickers.append({"index": int(sm.group(1)), "prompt": prompt})

        if stickers:
            groups[category_name] = stickers
        i += 2

    return groups


def flatten_prompts(grouped: dict[str, list[dict]]) -> list[dict]:
    """Flatten grouped prompts into a list, preserving category info."""
    flat = []
    for category, stickers in grouped.items():
        for s in stickers:
            flat.append({**s, "category": category})
    return flat


# ==================================================================
# 4) Preview meta-prompt
# ==================================================================

PREVIEW_PROMPT_BUILDER = """You are an expert prompt engineer specializing in AI image generation.

Your task is to write a **single image generation prompt** that produces a **sticker collection preview sheet** — one image showing all stickers from this specific category arranged together.

=== CATEGORY ===
{category_name}
=== END CATEGORY ===

=== STYLE DIRECTION ===
{style_direction}
=== END STYLE DIRECTION ===

=== STICKER PROMPTS ===
{sticker_prompts}
=== END STICKER PROMPTS ===

Write the image generation prompt following these rules:

1. Start with: "A sticker collection sheet for the category \\"{category_name}\\"."
2. Layout: stickers arranged in an organized grid on a white background.
3. Briefly describe each sticker's appearance (10-20 words each). Preserve the DIVERSITY of sticker types — illustrations, text stickers, character combos, icons, badges.
4. Use the style direction as a unifying visual thread.
5. Each sticker has "thick white die-cut border".
6. Background: "isolated on clean white background".
7. End with: "Professional graphic design, high resolution, diverse sticker art styles unified by a cohesive color palette."
8. Total prompt: 150-300 words.

Return ONLY the prompt text. No JSON, no markdown fences."""


def build_preview_prompt(
    category_name: str,
    sticker_prompts: str,
    style_direction: str,
) -> str:
    """Build the preview meta-prompt for a category."""
    return PREVIEW_PROMPT_BUILDER.format(
        category_name=category_name,
        sticker_prompts=sticker_prompts,
        style_direction=style_direction,
    )
