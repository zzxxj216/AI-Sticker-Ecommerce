"""Interactive session helper utilities

Helper data types and formatting functions used by InteractiveSession.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.models.generation import Slot


@dataclass
class Turn:
    """A single conversation turn (internal use)."""
    role: str   # "user" | "assistant"
    content: str


def build_slot_state_text(slots: Dict[str, Slot]) -> str:
    """Format slot states as readable text for Chat AI and Extractor AI prompts."""
    lines = []
    for s in slots.values():
        if s.is_filled:
            val = json.dumps(s.value, ensure_ascii=False)
            status = "\u2705"
        elif s.required:
            val = "not set"
            status = "\u274c required"
        else:
            val = f"default: {s.default}"
            status = "\u2b1c optional"
        lines.append(f"  {status} {s.label}({s.name}): {val}")
    return "\n".join(lines)


def build_slot_definitions(slots: Dict[str, Slot]) -> str:
    """Format slot definitions as English descriptions for the Extractor AI prompt."""
    lines = []
    for s in slots.values():
        req = "REQUIRED" if s.required else f"OPTIONAL (default: {s.default})"
        lines.append(f"- {s.name} [{req}]: {s.description}")
    return "\n".join(lines)


def format_recent_turns(turns: List[Turn], limit: int = 10) -> str:
    """Format recent conversation turns as text for the Extractor AI prompt."""
    recent = turns[-limit:]
    if not recent:
        return "(empty)"
    lines = []
    for t in recent:
        tag = "User" if t.role == "user" else "Assistant"
        lines.append(f"{tag}: {t.content}")
    return "\n".join(lines)


def parse_structured_summary(
    raw_summary: Any,
    existing_summary: str,
) -> Optional[str]:
    """Parse a structured summary object from the Extractor AI and merge it
    with the existing summary, preserving all accumulated constraints.

    The Extractor returns summary as either:
    - A dict with keys: decisions, constraints, preferences, other_context
    - A plain string (legacy format — use as-is)
    - None (no summary needed)

    Returns a formatted string, or None if raw_summary is empty.
    """
    if not raw_summary:
        return None

    if isinstance(raw_summary, str):
        if len(raw_summary) <= 10:
            return None
        return raw_summary

    if not isinstance(raw_summary, dict):
        return None

    decisions = raw_summary.get("decisions", "")
    new_constraints = raw_summary.get("constraints", "")
    preferences = raw_summary.get("preferences", "")
    other_context = raw_summary.get("other_context", "")

    old_constraints = _extract_constraints_from_summary(existing_summary)
    merged_constraints = _merge_constraints(old_constraints, new_constraints)

    parts = []
    if decisions:
        parts.append(f"Decisions: {decisions}")
    if merged_constraints:
        parts.append(f"CONSTRAINTS (must follow): {merged_constraints}")
    if preferences:
        parts.append(f"Preferences: {preferences}")
    if other_context:
        parts.append(f"Context: {other_context}")

    return "\n".join(parts) if parts else None


def _extract_constraints_from_summary(summary: str) -> str:
    """Pull the constraints line out of a previously formatted summary."""
    if not summary:
        return ""
    for line in summary.split("\n"):
        if line.startswith("CONSTRAINTS"):
            _, _, value = line.partition(":")
            return value.strip()
    return ""


def _merge_constraints(old: str, new: str) -> str:
    """Merge old and new constraint strings, deduplicating by lowercase match."""
    if not old:
        return new
    if not new:
        return old

    old_items = [c.strip() for c in old.split(",") if c.strip()]
    new_items = [c.strip() for c in new.split(",") if c.strip()]

    seen_lower = {c.lower() for c in old_items}
    merged = list(old_items)
    for item in new_items:
        if item.lower() not in seen_lower:
            merged.append(item)
            seen_lower.add(item.lower())

    return ", ".join(merged)
