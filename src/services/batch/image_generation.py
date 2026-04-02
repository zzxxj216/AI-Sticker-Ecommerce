"""Sticker Image Generation & QC - Standalone module (not integrated into main Pipeline)

Provides:
- IMAGE_GENERATION_SYSTEM + build_image_generation_prompt  -> generation execution plan
- IMAGE_QC_SYSTEM + build_image_qc_prompt                 -> single image QC scoring
- parse_image_generation_output                           -> parse generation plan
- parse_image_qc_output                                   -> parse QC scores
"""

import re
from typing import Any, Optional

from src.services.batch.sticker_prompts import format_trend_brief_for_planner

# ==================================================================
# 1) IMAGE_GENERATION_SYSTEM
# ==================================================================

IMAGE_GENERATION_SYSTEM = """
You are a senior sticker image generation director and production operator.

Your job is to convert an approved sticker prompt package into a generation-ready image production plan.

You are not planning the product.
You are not redefining the sticker lineup.
You are not changing the approved style direction.
You are only responsible for preparing image-generation instructions that are stable, production-oriented, and suitable for building a coherent commercial sticker pack.

The input will include:
- the original trend brief
- the approved pack plan
- the approved sticker specification
- the approved prompt package

You must treat the approved prompt package as the primary generation source of truth.
Do not redesign the pack from scratch.

Your response must follow this exact 4-part structure:

===== PART 1: GENERATION SETUP =====
Define the pack-wide generation setup using these headings:

1. Output goal
2. Background rule
3. Framing rule
4. Sticker edge rule
5. Detail density rule
6. Text handling rule
7. Global consistency rule

Rules:
- Keep this practical and production-ready.
- The goal is to define how all sticker images should be generated consistently.
- Do not become abstract or poetic.

===== PART 2: BATCH GENERATION PLAN =====
Organize the full pack into generation batches.

Use these headings:
1. Batch order
2. First-pass quantity per sticker
3. When to regenerate
4. When to keep variation
5. Batch-level consistency checks

Rules:
- Prioritize hero stickers first, then support stickers, then text/badge/filler unless the approved prompt package clearly suggests another structure.
- Keep the batch plan commercially realistic.
- Focus on stability and review efficiency.

===== PART 3: IMAGE GENERATION TASKS =====
List every sticker as numbered items.

For each sticker, use this exact sub-structure:

[Sticker #{number}]
- Name:
- Role:
- Generation task:
- Output requirements:
- Reject immediately if:
- Variation allowance:

Field rules:
- Name: short working name
- Role: preserve the approved role
- Generation task: write one concise English image-generation instruction using the approved sticker prompt as the base
- Output requirements: specify what must be true in the final image
- Reject immediately if: list hard failure conditions
- Variation allowance: specify what kind of limited variation is acceptable for this sticker

Important rules:
- Do not invent a different sticker.
- Do not add new visual concepts outside the approved prompt package.
- Do not allow style drift across stickers.
- Keep the generation task image-focused and execution-friendly.
- Do not write like marketing copy.
- Do not include unnecessary camera or photography language.
- Text stickers must clearly indicate whether text should be rendered in-image or added later in post-processing.
- When text rendering is likely unstable, say so directly.

===== PART 4: FINAL ASSEMBLY NOTES =====
End with a concise production note section under these headings:

1. What the final pack should look like as a set
2. What must match across all sticker files
3. What should feel intentionally varied
4. What usually causes the pack to feel cheap or inconsistent
5. Final review focus before post-processing

Writing rules:
- Write in English.
- Do not output JSON.
- Be concrete, operational, and production-oriented.
- Do not re-plan the product.
- Do not add next-step suggestions.
- Do not ask follow-up questions.
- Keep the result focused on generation execution and image quality control.

The result should feel like an internal image production document for a commercial sticker pack.
""".strip()


def build_image_generation_prompt(
    prompt_builder_output: str,
    *,
    trend_brief: Optional[dict] = None,
    pack_plan_text: Optional[str] = None,
    sticker_spec_text: Optional[str] = None,
    theme: Optional[str] = None,
) -> str:
    """Build the IMAGE_GENERATION_SYSTEM user message with 5 input sections."""
    if trend_brief is not None:
        brief_block = format_trend_brief_for_planner(trend_brief)
    else:
        t = (theme or "").strip() or "(not provided)"
        brief_block = f"[No structured trend brief]\nTheme: {t}"

    plan_block = (pack_plan_text or "").strip() or "(not provided)"
    spec_block = (sticker_spec_text or "").strip() or "(not provided)"

    return (
        "[Task]\n"
        "Prepare a generation-ready image production plan for this approved sticker pack.\n"
        "Do not redesign the pack.\n"
        "Use the approved prompt package as the primary source of truth.\n\n"
        "[Trend Brief]\n"
        f"{brief_block}\n\n"
        "[Approved Pack Plan]\n"
        f"{plan_block}\n\n"
        "[Approved Sticker Specification]\n"
        f"{spec_block}\n\n"
        "[Approved Prompt Package]\n"
        f"{prompt_builder_output}"
    )


def parse_image_generation_output(output: str) -> dict:
    """Parse the 4-part output of IMAGE_GENERATION_SYSTEM.

    Returns::

        {
            "generation_setup": str,      # PART 1
            "batch_plan": str,            # PART 2
            "tasks": [                    # PART 3 per-sticker tasks
                {
                    "index": int,
                    "name": str,
                    "role": str,
                    "generation_task": str,
                    "output_requirements": str,
                    "reject_if": str,
                    "variation_allowance": str,
                },
                ...
            ],
            "assembly_notes": str,        # PART 4
        }
    """
    result: dict[str, Any] = {
        "generation_setup": "",
        "batch_plan": "",
        "tasks": [],
        "assembly_notes": "",
    }

    markers = [
        ("generation_setup", "===== PART 1"),
        ("batch_plan", "===== PART 2"),
        (None, "===== PART 3"),
        ("assembly_notes", "===== PART 4"),
    ]

    positions = []
    for _, marker in markers:
        pos = output.find(marker)
        positions.append(pos)

    def _section_text(start_pos: int, end_pos: int) -> str:
        if start_pos == -1:
            return ""
        nl = output.find("\n", start_pos)
        if nl == -1:
            return ""
        actual_end = end_pos if end_pos > start_pos else len(output)
        return output[nl + 1: actual_end].strip()

    for i, (key, _) in enumerate(markers):
        if key is None:
            continue
        next_pos = -1
        for j in range(i + 1, len(markers)):
            if positions[j] != -1:
                next_pos = positions[j]
                break
        result[key] = _section_text(positions[i], next_pos)

    # ---- PART 3: per-sticker tasks ----
    p3_start = positions[2]
    p4_start = positions[3]
    if p3_start != -1:
        nl = output.find("\n", p3_start)
        p3_end = p4_start if p4_start > p3_start else len(output)
        part3_text = output[nl + 1: p3_end] if nl != -1 else ""

        block_pattern = re.compile(
            r"\[Sticker\s*#(\d+)\](.*?)(?=\[Sticker\s*#\d+\]|$)",
            re.DOTALL | re.IGNORECASE,
        )
        field_pattern = re.compile(
            r"^-\s*(Name|Role|Generation task|Output requirements|"
            r"Reject immediately if|Variation allowance)\s*:\s*",
            re.MULTILINE | re.IGNORECASE,
        )

        for m in block_pattern.finditer(part3_text):
            idx = int(m.group(1))
            block = m.group(2)

            fields: dict[str, str] = {}
            splits = field_pattern.split(block)
            i = 1
            while i < len(splits) - 1:
                key = splits[i].strip().lower()
                val = splits[i + 1].strip()
                fields[key] = val
                i += 2

            task = {
                "index": idx,
                "name": fields.get("name", ""),
                "role": fields.get("role", ""),
                "generation_task": fields.get("generation task", ""),
                "output_requirements": fields.get("output requirements", ""),
                "reject_if": fields.get("reject immediately if", ""),
                "variation_allowance": fields.get("variation allowance", ""),
            }
            if task["name"] or task["generation_task"]:
                result["tasks"].append(task)

    return result


# ==================================================================
# 2) IMAGE_QC_SYSTEM
# ==================================================================

IMAGE_QC_SYSTEM = """
You are a senior sticker pack quality inspector.

Your job is to evaluate a single generated sticker image against the approved pack specification and scoring rubric.

You are not redesigning the sticker.
You are not suggesting strategic changes.
You are only responsible for objectively scoring the image quality and making a keep/reject decision.

You will receive:
- the pack foundation (pack-level style rules)
- the sticker specification for this particular sticker
- the generated image

You must evaluate the image using a two-layer system:

LAYER 1: HARD REJECT CHECK
If any of these conditions is true, immediately output "Hard Reject: Yes" and explain which condition failed:

1. Not sticker-like — looks like a poster, illustration cover, or social media graphic instead of a sticker asset
2. Subject unclear — main element is not identifiable at small size
3. Cut-unfriendly — outer silhouette is too scattered, fragmented, or irregular for die-cut
4. Style drift — clearly inconsistent with the pack foundation, looks like a different product
5. Obvious AI artifacts — deformation, blending errors, repeated textures, mysterious small objects, dirty edges
6. Violated must_avoid — contains brand, IP, real face, or other prohibited elements from the spec
7. Text unusable — text is wrong, garbled, blurry when it should exist; or phantom text appears when there should be none
8. Role hierarchy broken — a support sticker is more prominent than a hero, or a filler is visually heavy

LAYER 2: DETAILED SCORING (100 points)
Score each dimension:

1. Pack Consistency (20 points)
Does it match the pack-wide unified style?
- Palette within range
- Line weight and outline consistency
- Texture and density consistency
- Feels like the same pack

2. Sticker Readability (20 points)
Is it clear at small size?
- Subject immediately recognizable
- Main vs secondary elements distinct
- Does not require zooming in
- Clean silhouette

3. Sticker-ness / Cut-friendliness (15 points)
Does it look like a sellable sticker?
- Suitable for adding white border
- Outer contour not fragmented
- Not a scene illustration
- Easy to die-cut

4. Role Accuracy (15 points)
Does it fulfill its assigned role?
- Hero has visual impact
- Support assists without competing
- Text sticker is readable
- Badge/label is structured
- Filler is light but not worthless

5. Prompt Fidelity (10 points)
Did it execute the approved spec and prompt?
- Core elements present
- Composition direction correct
- No invented concepts
- No missing key elements

6. Commercial Appeal (10 points)
Does it have product quality?
- Fits in a product bundle
- Has purchase appeal
- Suitable for common use scenarios
- Does not look cheap

7. Technical Cleanliness (10 points)
Is it technically clean?
- Clean edges
- No dirty textures
- No stray fragments
- Natural graphic relationships

OUTPUT FORMAT — you must use this exact structure:

[Image QC Result]
Sticker Name: {name}
Role: {role}

Hard Reject: Yes / No
{If yes, explain which hard reject condition failed}

Scores:
- Pack Consistency (20):
- Sticker Readability (20):
- Cut-friendliness (15):
- Role Accuracy (15):
- Prompt Fidelity (10):
- Commercial Appeal (10):
- Technical Cleanliness (10):

Total: {sum}/100
Decision: Keep / Compare / Regenerate / Reject

Best point: {one sentence}
Main problem: {one sentence, or "none"}
Notes for retry: {one sentence if Regenerate, otherwise "N/A"}

Decision rules:
- 90-100: Keep — directly enters post-processing candidate pool
- 80-89: Compare — usable, light screening needed
- 70-79: Regenerate — barely usable, recommend re-running for comparison
- 60-69: Reject — clear problems, do not include in pack
- Below 60: Reject — hard fail

Writing rules:
- Write in English.
- Be specific and production-oriented.
- Do not be generous — score honestly.
- Do not suggest strategic changes.
- Do not re-plan the sticker.
""".strip()


def build_image_qc_prompt(
    sticker_name: str,
    sticker_role: str,
    sticker_spec_block: str,
    pack_foundation: str,
) -> str:
    """Build the text prompt for single-sticker QC scoring (sent alongside the image to the vision model)."""
    return (
        "[Task]\n"
        "Evaluate this generated sticker image against the approved specification "
        "and pack foundation.\n"
        "Use the two-layer evaluation system: hard reject check first, "
        "then detailed 100-point scoring.\n\n"
        f"[Sticker Info]\n"
        f"Name: {sticker_name}\n"
        f"Role: {sticker_role}\n\n"
        f"[Pack Foundation]\n"
        f"{pack_foundation}\n\n"
        f"[Sticker Specification]\n"
        f"{sticker_spec_block}"
    )


def parse_image_qc_output(qc_text: str) -> dict:
    """Parse the scoring output from IMAGE_QC_SYSTEM.

    Returns::

        {
            "sticker_name": str,
            "role": str,
            "hard_reject": bool,
            "hard_reject_reason": str,
            "scores": {
                "pack_consistency": int,
                "sticker_readability": int,
                "cut_friendliness": int,
                "role_accuracy": int,
                "prompt_fidelity": int,
                "commercial_appeal": int,
                "technical_cleanliness": int,
            },
            "total": int,
            "decision": str,
            "best_point": str,
            "main_problem": str,
            "notes_for_retry": str,
        }
    """
    result: dict[str, Any] = {
        "sticker_name": "",
        "role": "",
        "hard_reject": False,
        "hard_reject_reason": "",
        "scores": {},
        "total": 0,
        "decision": "",
        "best_point": "",
        "main_problem": "",
        "notes_for_retry": "",
    }

    def _extract(pattern: str, text: str, group: int = 1) -> str:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return m.group(group).strip() if m else ""

    result["sticker_name"] = _extract(r"Sticker Name:\s*(.+)", qc_text)
    result["role"] = _extract(r"^Role:\s*(.+)", qc_text)

    hr = _extract(r"Hard Reject:\s*(.+)", qc_text)
    result["hard_reject"] = hr.lower().startswith("yes")
    if result["hard_reject"]:
        hr_m = re.search(
            r"Hard Reject:\s*Yes[^\n]*\n(.*?)(?=Scores:|$)",
            qc_text, re.DOTALL | re.IGNORECASE,
        )
        if hr_m:
            result["hard_reject_reason"] = hr_m.group(1).strip()

    score_map = {
        "pack consistency": "pack_consistency",
        "sticker readability": "sticker_readability",
        "cut-friendliness": "cut_friendliness",
        "cut friendliness": "cut_friendliness",
        "role accuracy": "role_accuracy",
        "prompt fidelity": "prompt_fidelity",
        "commercial appeal": "commercial_appeal",
        "technical cleanliness": "technical_cleanliness",
    }

    for label, key in score_map.items():
        score_val = _extract(
            rf"-\s*{re.escape(label)}\s*\(\d+\)\s*:\s*(\d+)",
            qc_text,
        )
        if score_val:
            result["scores"][key] = int(score_val)
        elif key not in result["scores"]:
            result["scores"][key] = 0

    total_str = _extract(r"Total:\s*(\d+)", qc_text)
    result["total"] = int(total_str) if total_str else sum(result["scores"].values())

    result["decision"] = _extract(r"Decision:\s*(.+)", qc_text)
    result["best_point"] = _extract(r"Best point:\s*(.+)", qc_text)
    result["main_problem"] = _extract(r"Main problem:\s*(.+)", qc_text)
    result["notes_for_retry"] = _extract(r"Notes for retry:\s*(.+)", qc_text)

    return result
