"""Prompts for TikTok Ads audience-candidate generation + decision summary.

Two-step generation (mirrors src/services/tkshop/prompts.py):
  1. ``build_audience_main_prompt`` → free-form markdown plan describing N
     distinct audience hypotheses for a sticker pack (creative model).
  2. ``AUDIENCE_EXTRACT_SCHEMA`` + ``AUDIENCE_EXTRACT_INSTRUCTIONS`` → a strict
     JSON object the extractor model produces from that plan.

The first-class entity is the AUDIENCE: each candidate is a *hypothesis* about
who would buy this pack, plus a concrete ``targeting`` object the middle layer
can translate into TikTok Marketing API ad-group targeting. We only fill the
human-readable dimensions (age/gender/locations/languages); the platform-side
interest/action category IDs are left empty unless known — the operator or a
later enrichment step resolves them.

All copy is English (the ad platform + buyer market are US/UK/AU/CA).
"""

from __future__ import annotations


AUDIENCE_MAIN_SYSTEM_PROMPT = (
    "You are a performance-marketing strategist for an overseas TikTok Shop "
    "sticker brand (physical waterproof vinyl die-cut sticker packs, shipped "
    "from a small e-commerce seller to the US/UK/AU/CA market). Given a "
    "sticker pack's theme, art style and sample sticker contents, you propose "
    "DISTINCT audience hypotheses to test with small-budget TikTok ad groups. "
    "Each audience must be a genuinely different buyer segment — not the same "
    "people re-labelled. For each, give a short name, a crisp hypothesis "
    "(why THIS audience would buy THESE stickers), and concrete targeting "
    "(age groups, genders, locations, languages). Be specific and realistic; "
    "do not invent platform interest IDs. Output a markdown plan with the "
    "requested sections — do NOT output JSON; a separate extractor will pull "
    "the structured fields."
)


# Allowed targeting vocabulary — kept aligned with TikTok Marketing API
# conventions so the middle layer can map them directly. The model is told to
# choose from these; unknowns are left empty.
AGE_GROUP_VOCAB = [
    "AGE_13_17", "AGE_18_24", "AGE_25_34",
    "AGE_35_44", "AGE_45_54", "AGE_55_100",
]
GENDER_VOCAB = ["MALE", "FEMALE", "UNLIMITED"]
# Common Marketing API location IDs (Geo) for the core English markets. The
# model may also emit ISO-ish names; the extractor keeps whatever it is given.
LOCATION_HINTS = {
    "United States": 6252001,
    "United Kingdom": 2635167,
    "Australia": 2077456,
    "Canada": 6251999,
}


def build_audience_main_prompt(
    *,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    sticker_briefs_sample: list[str],
    video_signals: list[str] | None = None,
    product_signals: list[str] | None = None,
    n: int = 3,
) -> str:
    """Markdown plan: N audience hypotheses with name / hypothesis / targeting.

    Beyond the pack's static theme, this now folds in REAL signals when present:
    the pack's published-video captions/hashtags + actual view/like/comment
    performance, and its shop product selling points/keywords — so the model can
    ground audiences in what already resonated instead of guessing from art alone.
    """
    sample_lines = (
        "\n".join(f"- {b}" for b in sticker_briefs_sample[:14])
        or "- (no sample available)"
    )
    # Optional real-signal sections (omitted entirely if the pack has none).
    video_block = ""
    if video_signals:
        vlines = "\n".join(f"- {s}" for s in video_signals[:8])
        video_block = (
            "\n### This pack's published videos (captions + hashtags + REAL performance)\n"
            "Higher views/likes = this content/angle already resonated; let it inform\n"
            "which audiences to target.\n" + vlines + "\n"
        )
    product_block = ""
    if product_signals:
        plines = "\n".join(f"- {s}" for s in product_signals[:16])
        product_block = (
            "\n### This pack's shop listing — selling points & keywords\n"
            "These were written to convert real buyers; use them as buyer-intent hints.\n"
            + plines + "\n"
        )
    age_vocab = ", ".join(AGE_GROUP_VOCAB)
    loc_hint = ", ".join(
        f"{name} = {gid}" for name, gid in LOCATION_HINTS.items()
    )
    return f"""Propose {n} DISTINCT TikTok ad audiences to test for this sticker pack.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} unique die-cut waterproof vinyl stickers
- palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(general)'}

### Sample sticker contents
{sample_lines}
{video_block}{product_block}
### What we are doing
We run a small-budget experiment: one campaign, {n} ad groups, SAME creative
and bid, varying ONLY the audience. Each ad group's performance then attributes
cleanly to one audience. So the {n} audiences MUST be meaningfully different
buyer segments with different reasons to buy — not minor variations.

### Targeting vocabulary (use these — leave a dimension empty if unsure)
- age_groups (choose from): {age_vocab}
- genders (choose from): MALE, FEMALE, UNLIMITED
- locations: numeric TikTok geo IDs. Common ones: {loc_hint}
- languages: lowercase ISO codes, e.g. "en"
- interest_category_ids / action_category_ids: leave as empty lists unless you
  are certain of a real TikTok category ID — DO NOT fabricate IDs.

### Output (markdown sections, no JSON)
Produce exactly {n} audiences. For EACH audience output this block:

#### audience
- name: a short label, 2-5 words (e.g. "Gen-Z meme lovers")
- hypothesis: 1-2 sentences on why this specific audience buys these specific
  stickers (tie to the pack theme / sticker subjects / use cases).
- age_groups: comma-separated values from the vocabulary above
- genders: comma-separated values from the vocabulary above
- locations: comma-separated geo IDs (or country names if unsure of the ID)
- languages: comma-separated ISO codes
- interests: comma-separated human-readable interest themes (NOT IDs) that
  describe what this audience is into (used as notes only)

Repeat the `#### audience` block {n} times. Make them distinct.
"""


AUDIENCE_EXTRACT_INSTRUCTIONS = """
Mapping rules:
- Produce one object in "audiences" per `#### audience` block in the input.
- "name": pull from the name line. Short plain string.
- "hypothesis": pull from the hypothesis line(s). Plain string, 1-3 sentences.
- "targeting": an object with these keys, all present:
  - "age_groups": list of strings from the age line. Keep only values that
    look like AGE_x_y tokens; drop anything else. Empty list if none.
  - "genders": list of strings (MALE / FEMALE / UNLIMITED). Empty list if none.
  - "locations": list of integers parsed from the locations line. If a country
    NAME is given instead of a numeric ID, map it: United States=6252001,
    United Kingdom=2635167, Australia=2077456, Canada=6251999; otherwise drop
    it. Empty list if none parse.
  - "languages": list of lowercase ISO code strings (e.g. "en"). Empty if none.
  - "interest_category_ids": ALWAYS an empty list (we never fabricate IDs).
  - "action_category_ids": ALWAYS an empty list.
  - "interests": list of the human-readable interest theme strings from the
    interests line (notes only). Empty list if none.
- If a section is missing, use empty string / empty list but still include
  the field. Do not invent audiences beyond what the input describes.
""".strip()


AUDIENCE_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["audiences"],
    "properties": {
        "audiences": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "hypothesis", "targeting"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "hypothesis": {"type": "string", "maxLength": 600},
                    "targeting": {
                        "type": "object",
                        "properties": {
                            "age_groups": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "genders": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "locations": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "languages": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "interest_category_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "action_category_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "interests": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Decision summary: after evaluate_experiment computes per-audience verdicts,
# the creative model writes a short human-facing summary onto the experiment.
# ---------------------------------------------------------------------------

DECISION_SUMMARY_SYSTEM_PROMPT = (
    "You are a performance-marketing analyst. You will be given the results of "
    "a TikTok audience experiment: per-audience spend, conversions, ROAS, and "
    "days-with-data, plus the verdict (winning / candidate / inconclusive) the "
    "system assigned against the promotion thresholds. Write a SHORT, concrete "
    "summary for the operator (3-5 sentences, English): which audiences won and "
    "why, which are inconclusive and what's missing (more spend? more days?), "
    "and a clear next action. No fluff, no JSON, no markdown headers."
)


def build_decision_summary_prompt(
    *,
    pack_display_name: str,
    thresholds: dict,
    experiment_status: str,
    audience_rows: list[dict],
) -> str:
    """Plain-text prompt feeding the per-audience aggregates to the model."""
    import json as _json

    lines = []
    for a in audience_rows:
        lines.append(
            f"- {a.get('name', '?')} [{a.get('verdict', '?')}]: "
            f"spend={a.get('spend', 0):.2f}, conversions={a.get('conversions', 0)}, "
            f"roas={a.get('roas', 0):.2f}, days_with_data={a.get('days', 0)}"
        )
    body = "\n".join(lines) or "- (no audience data yet)"
    th = _json.dumps(thresholds, ensure_ascii=False)
    return f"""Experiment for pack "{pack_display_name}" (status: {experiment_status}).

Promotion thresholds (an audience must meet ALL to be 'winning'):
{th}

Per-audience results:
{body}

Write the operator-facing decision summary now.
"""
