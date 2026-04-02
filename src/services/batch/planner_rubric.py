"""Planner evaluation: hard gates + 100-point rubric template and label rules."""

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Hard gates (any hit → FAIL or forced REWRITE; checked manually)
# ---------------------------------------------------------------------------
HARD_GATE_KEYS = (
    "ignores_risk_constraints",  # ignores must_avoid / risk_notes
    "obvious_templating",  # obvious template fill-in
    "no_negative_judgment",  # weak topic still marked Strongly suitable
    "hollow_commercial_copy",  # hollow gift/premium claims without evidence
    "structure_mismatch",  # structure clearly mismatches the topic
)

# ---------------------------------------------------------------------------
# Weighted dimensions (100 points total)
# ---------------------------------------------------------------------------
RUBRIC_DIMENSIONS = (
    ("feasibility_judgment", 15, "Feasibility judgment sound and conservatively honest"),
    ("brief_alignment", 20, "Driven by brief, not generic templating"),
    ("commercial_sense", 20, "Clear purchase motivation and platform logic"),
    ("structural_fit", 15, "Sticker-pack structure fits the topic"),
    ("design_direction_usefulness", 10, "Visual direction is actionable"),
    ("risk_awareness", 10, "IP / trademark / sameness risks addressed"),
    ("distinctiveness", 5, "Anti-template / topic differentiation"),
    ("clarity_actionability", 5, "Handoff-ready for the next stage"),
)

# MVP: five items scored 1–5, sum 25, ×4 → 100
MVP_DIMENSIONS = (
    ("feasibility_judgment", "Feasibility judgment"),
    ("brief_alignment", "Brief alignment"),
    ("commercial_sense", "Commercial appeal"),
    ("structural_fit", "Structural fit"),
    ("risk_awareness", "Risk awareness"),
)


def empty_score_record() -> dict[str, Any]:
    """Placeholder for one scored output (fill manually, then save)."""
    rec: dict[str, Any] = {
        "hard_gates": {k: False for k in HARD_GATE_KEYS},
        "hard_gate_notes": "",
        "weighted": {key: None for key, _, _ in RUBRIC_DIMENSIONS},
        "weighted_notes": "",
        "mvp_1_to_5": {key: None for key, _ in MVP_DIMENSIONS},
        "batch_only": {
            "structure_repetition": None,
            "commercial_angle_repetition": None,
            "batch_notes": "",
        },
        "best_part": "",
        "worst_part": "",
        "ok_for_next_stage": None,
        "reviewer": "",
    }
    return rec


def mvp_total_to_100(mvp_scores: dict[str, Optional[int]]) -> Optional[float]:
    """Map five 1–5 scores to a 0–100 scale (×4)."""
    vals = [mvp_scores.get(k) for k, _ in MVP_DIMENSIONS]
    if any(v is None for v in vals):
        return None
    if not all(isinstance(v, int) and 1 <= v <= 5 for v in vals):
        return None
    return float(sum(vals) * 4)


def weighted_total(weighted: dict[str, Optional[int]]) -> Optional[int]:
    parts = []
    for key, max_pts, _ in RUBRIC_DIMENSIONS:
        v = weighted.get(key)
        if v is None:
            return None
        if not isinstance(v, int) or v < 0 or v > max_pts:
            return None
        parts.append(v)
    return sum(parts)


def finalize_score_record(rec: dict[str, Any]) -> dict[str, Any]:
    """From filled hard_gates, weighted, or mvp_1_to_5, set label / total fields."""
    gates = rec.get("hard_gates") or {}
    any_gate = any(bool(gates.get(k)) for k in HARD_GATE_KEYS)

    w = rec.get("weighted") or {}
    wt = weighted_total(w)
    if wt is not None:
        rec["weighted_total_100"] = wt
        total_f: Optional[float] = float(wt)
    else:
        mvp = rec.get("mvp_1_to_5") or {}
        m = mvp_total_to_100(mvp)
        rec["mvp_total_x4_to_100"] = m
        total_f = m

    rec["label"] = result_label(any_hard_gate=any_gate, total_100=total_f)
    return rec


def result_label(
    *,
    any_hard_gate: bool,
    total_100: Optional[float],
    threshold_pass: float = 80.0,
    threshold_review: float = 70.0,
    threshold_rewrite: float = 60.0,
) -> str:
    """PASS / REVIEW / REWRITE / FAIL."""
    if any_hard_gate:
        return "FAIL"
    if total_100 is None:
        return "REVIEW"
    if total_100 >= threshold_pass:
        return "PASS"
    if total_100 >= threshold_review:
        return "REVIEW"
    if total_100 >= threshold_rewrite:
        return "REWRITE"
    return "FAIL"
