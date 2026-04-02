"""Trend brief field contract: required vs recommended fields and validation."""

from typing import Any

# Canonical input shape (aligned with product)
REQUIRED_FIELDS = (
    "trend_name",
    "one_line_explanation",
    "why_now",
    "emotional_core",
    "visual_symbols",
    "must_avoid",
)

REQUIRED_NESTED = (
    ("target_audience", "profile"),
)

RECOMMENDED_FIELDS = (
    "lifecycle",
    "platform",
    "product_goal",
    "pack_size_goal",
    "risk_notes",
    "trend_type",
    "target_audience",  # subfields beyond profile are still recommended; see validate
)


def _non_empty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _non_empty_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and any(
        (isinstance(x, str) and x.strip()) or x for x in v
    )


def validate_trend_brief(brief: dict) -> tuple[list[str], list[str]]:
    """Validate a trend brief.

    Returns:
        (errors, warnings) — errors: missing required fields; warnings: missing recommended fields.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(brief, dict):
        return (["brief must be a dict"], [])

    for key in REQUIRED_FIELDS:
        val = brief.get(key)
        if key in ("emotional_core", "visual_symbols", "must_avoid"):
            if not _non_empty_list(val):
                errors.append(f"required field {key} must be a non-empty list")
        elif not _non_empty_str(val):
            errors.append(f"required field {key} must be a non-empty string")

    ta = brief.get("target_audience")
    if not isinstance(ta, dict):
        errors.append("required target_audience must be an object with profile")
    elif not _non_empty_str(ta.get("profile")):
        errors.append("required target_audience.profile must be a non-empty string")
    else:
        for sub in ("age_range", "gender_tilt", "usage_scenarios"):
            if sub == "usage_scenarios":
                us = ta.get(sub)
                if not _non_empty_list(us):
                    warnings.append(f"recommended: target_audience.{sub}")
            elif not _non_empty_str(ta.get(sub)):
                warnings.append(f"recommended: target_audience.{sub}")

    for key in RECOMMENDED_FIELDS:
        if key == "target_audience":
            continue
        val = brief.get(key)
        if val is None or val == "" or val == []:
            warnings.append(f"recommended: {key}")
            continue
        if key == "pack_size_goal" and isinstance(val, dict):
            if not val.get("tier") and not val.get("sticker_count_range"):
                warnings.append("recommended: pack_size_goal.tier or sticker_count_range")

    return (errors, warnings)
