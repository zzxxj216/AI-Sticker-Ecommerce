"""Interactive multi-turn conversation session for sticker pack configuration.

Architecture: **Two-AI split** with multi-pack support

1. **Chat AI** — talks to the user naturally in Chinese. No JSON
   constraints. Can answer casual questions, suggest ideas, guide
   the design process. Just returns plain text.

2. **Extractor AI** — runs silently after each turn. Reads the
   recent conversation + current slot state, extracts new slot
   values, detects user confirmation, and (when history grows long)
   produces a summary to compress context.

Flow:
    Phase 1 (collecting):
        Collect theme, directions, optional visual/color/count/name.
        When user confirms → generate sticker concepts per pack.

    Phase 2 (reviewing_concepts):
        Show per-pack sticker content list.
        User can modify/add/remove stickers per pack.
        When user confirms → ready for generation.

Multi-pack:
    When the user asks for multiple independent packs (e.g. each
    direction as its own pack), the session creates a separate
    GenerationConfig per direction, each with its own concept list.

Usage:
    session = InteractiveSession()
    while True:
        resp = session.process_message(input("> "))
        print(resp.message)
        if resp.ready_for_generation:
            for cfg in resp.configs:
                generate(cfg)
            break
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from src.core.logger import get_logger
from src.services.ai.claude_service import ClaudeService

from src.models.generation import (
    Slot,
    StickerConcept,
    GenerationConfig,
    SessionResponse,
    PHASE_COLLECTING,
    PHASE_REVIEWING_CONCEPTS,
)
from src.services.sticker.session_prompts import (
    CHAT_SYSTEM,
    EXTRACTOR_SYSTEM,
    CONCEPT_GENERATOR,
    CHAT_REVIEW_SYSTEM,
    CONCEPT_EXTRACTOR,
)
from src.services.sticker.session_helpers import (
    Turn,
    build_slot_state_text,
    build_slot_definitions,
    format_recent_turns,
    parse_structured_summary,
)

logger = get_logger("service.interactive_session")

_MAX_RAW_TURNS = 16
_CONTEXT_WINDOW = 10


class InteractiveSession:
    """Multi-turn conversation manager using a two-AI architecture.

    Supports generating one or more independent sticker packs from
    a single conversation session.
    """

    def __init__(self, claude_service: Optional[ClaudeService] = None):
        self.claude = claude_service or ClaudeService()
        self._turns: List[Turn] = []
        self._summary: str = ""
        self._confirmed = False
        self._phase: str = PHASE_COLLECTING
        self._pack_mode: str = "single"
        self._packs: List[GenerationConfig] = []

        self.slots: Dict[str, Slot] = {
            "theme": Slot(
                name="theme",
                label="Theme",
                description="The sticker pack theme/topic, e.g. 'AI & Programmers', 'Pet Camping'.",
                required=True,
            ),
            "directions": Slot(
                name="directions",
                label="Directions",
                description="1-3 creative sub-directions within the theme.",
                required=True,
            ),
            "visual_style": Slot(
                name="visual_style",
                label="Visual Style",
                description="Art style, e.g. 'neon vector', 'hand-drawn watercolor', 'retro comic'.",
                required=False,
                default="auto",
            ),
            "color_mood": Slot(
                name="color_mood",
                label="Color Mood",
                description="Color mood, e.g. 'cyber blue-purple', 'warm earth tones'.",
                required=False,
                default="auto",
            ),
            "sticker_count": Slot(
                name="sticker_count",
                label="Sticker Count",
                description="Number of stickers per pack (integer, typically 6-12).",
                required=False,
                default=10,
            ),
            "pack_name": Slot(
                name="pack_name",
                label="Pack Name",
                description="Pack name. Auto-generated if not provided.",
                required=False,
                default="auto",
            ),
        }

        logger.info("InteractiveSession created (two-AI, multi-pack)")

    # ----- public API -----

    def process_message(self, user_input: str) -> SessionResponse:
        """Process one round of user input.

        Routes to the appropriate phase handler.
        """
        user_input = user_input.strip()
        if not user_input:
            return SessionResponse(
                message="Please tell me what sticker theme or idea you have in mind!"
            )

        self._turns.append(Turn(role="user", content=user_input))

        if self._phase == PHASE_COLLECTING:
            return self._process_collecting_phase()
        else:
            return self._process_review_phase()

    def _process_collecting_phase(self) -> SessionResponse:
        """Handle a turn during slot-collection phase."""
        reply = self._call_chat_ai()
        self._turns.append(Turn(role="assistant", content=reply))

        updated = self._call_extractor_ai()
        missing = self._missing_slot_names()

        if self._confirmed and not missing:
            logger.info(
                "Slots confirmed (pack_mode=%s) — generating sticker concepts",
                self._pack_mode,
            )
            concepts_reply = self._generate_and_present_concepts()
            reply = reply + "\n\n---\n\n" + concepts_reply
            self._confirmed = False
            if self._packs:
                self._phase = PHASE_REVIEWING_CONCEPTS
            else:
                logger.warning("No packs with concepts — staying in collecting phase")

        logger.info(
            "Turn done — phase=%s, updated=%s, missing=%s",
            self._phase, list(updated.keys()), missing,
        )

        return SessionResponse(
            message=reply,
            phase=self._phase,
            updated_slots=updated,
            missing_slots=missing,
            ready_for_generation=False,
        )

    def _process_review_phase(self) -> SessionResponse:
        """Handle a turn during concept-review phase."""
        reply = self._call_review_chat_ai()
        self._turns.append(Turn(role="assistant", content=reply))

        confirmed = self._call_concept_extractor_ai()

        valid_packs = [p for p in self._packs if p.sticker_concepts]
        ready = confirmed and len(valid_packs) > 0
        configs = valid_packs if ready else []

        if confirmed and not valid_packs:
            logger.warning(
                "User confirmed but all packs have empty sticker_concepts — "
                "not marking ready_for_generation"
            )

        logger.info(
            "Turn done — phase=%s, packs=%d, valid=%d, confirmed=%s, ready=%s",
            self._phase, len(self._packs), len(valid_packs), confirmed, ready,
        )

        return SessionResponse(
            message=reply,
            phase=self._phase,
            ready_for_generation=ready,
            configs=configs,
        )

    def get_configs(self) -> List[GenerationConfig]:
        """Build GenerationConfig list from current state."""
        if self._packs:
            return list(self._packs)
        return [GenerationConfig(
            theme=self.slots["theme"].effective_value or "",
            directions=self.slots["directions"].effective_value or [],
            visual_style=self.slots["visual_style"].effective_value or "auto",
            color_mood=self.slots["color_mood"].effective_value or "auto",
            sticker_count=self.slots["sticker_count"].effective_value or 10,
            pack_name=self.slots["pack_name"].effective_value or "auto",
        )]

    def update_slot(self, name: str, value: Any) -> None:
        """Manually set a slot value."""
        if name in self.slots:
            self.slots[name].value = value
            logger.info("Slot '%s' manually set to %s", name, value)
        else:
            logger.warning("Unknown slot: %s", name)

    def reset(self) -> None:
        """Clear all slots, conversation history and summary."""
        for slot in self.slots.values():
            slot.value = None
        self._turns.clear()
        self._summary = ""
        self._confirmed = False
        self._phase = PHASE_COLLECTING
        self._pack_mode = "single"
        self._packs.clear()
        logger.info("Session reset")

    @property
    def is_confirmed(self) -> bool:
        return self._confirmed

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def history(self) -> List[Dict[str, str]]:
        return [{"role": t.role, "content": t.content} for t in self._turns]

    def slots_summary(self) -> str:
        """Human-readable summary of slot states."""
        return build_slot_state_text(self.slots)

    # =================================================================
    # Internal — Chat AI (collecting phase)
    # =================================================================

    def _call_chat_ai(self) -> str:
        """Call Claude as the conversational chat assistant (multi-turn)."""
        system = CHAT_SYSTEM.format(
            slot_state=build_slot_state_text(self.slots),
            summary=self._summary or "(none yet — conversation just started)",
        )

        messages = self._build_chat_messages()

        try:
            result = self.claude.generate_multiturn(
                messages=messages,
                system=system,
                max_tokens=1500,
                temperature=0.7,
            )
            reply = result["text"].strip()
            logger.debug("Chat AI replied (%d chars)", len(reply))
            return reply

        except Exception as e:
            logger.error("Chat AI failed: %s", e)
            return (
                "Hey there! I'm your Sticker Design Assistant. "
                "I can help you brainstorm and plan your sticker pack. "
                "Tell me what theme or idea you have in mind!"
            )

    def _build_chat_messages(self) -> List[Dict[str, str]]:
        """Build a condensed message list for Chat AI.

        If a summary exists, prepend it as context before the recent turns.
        """
        recent = self._turns[-_CONTEXT_WINDOW:]

        msgs: List[Dict[str, str]] = []

        if self._summary:
            msgs.append({
                "role": "user",
                "content": (
                    "[Previous conversation summary]\n"
                    f"{self._summary}\n\n"
                    "IMPORTANT: If there are CONSTRAINTS listed above, "
                    "you MUST respect them in all suggestions."
                ),
            })
            msgs.append({
                "role": "assistant",
                "content": (
                    "Got it, I'm caught up on our previous conversation "
                    "and I'll make sure to follow all constraints. Please continue!"
                ),
            })

        for t in recent:
            msgs.append({"role": t.role, "content": t.content})

        return msgs

    # =================================================================
    # Internal — Concept Generation & Review
    # =================================================================

    def _generate_and_present_concepts(self) -> str:
        """Generate sticker concepts — per-pack when independent."""
        directions = self.slots["directions"].effective_value or []
        count = self.slots["sticker_count"].effective_value or 10
        theme = self.slots["theme"].effective_value or ""
        style = self.slots["visual_style"].effective_value or "auto"
        mood = self.slots["color_mood"].effective_value or "auto"

        failed_directions: List[str] = []

        if self._pack_mode == "independent" and len(directions) > 1:
            self._packs = []
            for direction in directions:
                pack = GenerationConfig(
                    theme=theme,
                    directions=[direction],
                    visual_style=style,
                    color_mood=mood,
                    sticker_count=count,
                    pack_name="auto",
                )
                concepts = self._generate_concepts_for_pack(pack)
                pack.sticker_concepts = concepts
                if not concepts:
                    failed_directions.append(direction)
                self._packs.append(pack)
        else:
            pack = GenerationConfig(
                theme=theme,
                directions=list(directions) if isinstance(directions, list) else [directions],
                visual_style=style,
                color_mood=mood,
                sticker_count=count,
                pack_name=self.slots["pack_name"].effective_value or "auto",
            )
            concepts = self._generate_concepts_for_pack(pack)
            pack.sticker_concepts = concepts
            if not concepts:
                failed_directions.append(", ".join(pack.directions))
            self._packs = [pack]

        self._packs = [p for p in self._packs if p.sticker_concepts]

        logger.info(
            "Generated concepts for %d pack(s), total stickers: %d, failed: %d",
            len(self._packs),
            sum(len(p.sticker_concepts) for p in self._packs),
            len(failed_directions),
        )

        result = self._format_all_packs_for_user()

        if failed_directions:
            fail_text = ", ".join(failed_directions)
            if not self._packs:
                return (
                    f"\u26a0\ufe0f Sorry, sticker concept generation failed for all directions ({fail_text}). "
                    "The AI service may be temporarily unavailable.\n"
                    "You can wait a moment and type anything to retry, "
                    "or type /reset to start over."
                )
            result += (
                f"\n\n\u26a0\ufe0f Note: concept generation failed for direction '{fail_text}' "
                "(AI service temporarily unavailable) — skipped.\n"
                "You can confirm the existing content, or type /reset to start over."
            )

        return result

    def _generate_concepts_for_pack(
        self, pack: GenerationConfig, max_retries: int = 3
    ) -> List[StickerConcept]:
        """Call AI to generate sticker concepts for a single pack (with retry)."""
        direction_str = ", ".join(pack.directions) if pack.directions else pack.theme

        system = CONCEPT_GENERATOR.format(
            theme=pack.theme,
            direction=direction_str,
            visual_style=pack.visual_style,
            color_mood=pack.color_mood,
            count=pack.sticker_count,
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                raw = self.claude.generate_json(
                    prompt="Generate the sticker concepts now. Return JSON only.",
                    system=system,
                    max_tokens=2000,
                    temperature=0.8,
                )
                stickers = raw.get("stickers", [])
                if stickers:
                    if attempt > 1:
                        logger.info(
                            "Concept generation succeeded on attempt %d for direction=%s",
                            attempt, direction_str,
                        )
                    return [
                        StickerConcept(
                            index=i + 1,
                            description=s.get("description", ""),
                            text_overlay=s.get("text_overlay", ""),
                        )
                        for i, s in enumerate(stickers)
                    ]
                last_error = "API returned empty stickers list"
                logger.warning(
                    "Concept generation returned empty result for direction=%s (attempt %d/%d)",
                    direction_str, attempt, max_retries,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Concept generation failed for direction=%s (attempt %d/%d): %s",
                    direction_str, attempt, max_retries, e,
                )

            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info("Retrying in %ds...", wait)
                time.sleep(wait)

        logger.error(
            "Concept generation exhausted all %d retries for direction=%s: %s",
            max_retries, direction_str, last_error,
        )
        return []

    def _format_all_packs_for_user(self) -> str:
        """Format all packs' concepts as a readable message."""
        if not self._packs:
            return "(no sticker content yet)"

        parts = []

        if len(self._packs) == 1:
            parts.append("Awesome! Based on your config, here's the sticker lineup I've planned:\n")
            parts.append(self._format_single_pack(self._packs[0], show_header=False))
        else:
            total = sum(len(p.sticker_concepts) for p in self._packs)
            parts.append(
                f"Awesome! Based on your config, I've planned "
                f"**{len(self._packs)} sticker packs** with {total} stickers total:\n"
            )
            for i, pack in enumerate(self._packs):
                parts.append(self._format_single_pack(pack, pack_num=i + 1))

        parts.append("")
        parts.append(
            "You can:\n"
            '- Say "change #3 to xxx" to replace a sticker\n'
        )
        if len(self._packs) > 1:
            parts.append(
                '- Say "pack 1, sticker #5 text to xxx" to edit\n'
            )
        parts.append(
            '- Say "remove #2" or "add one more about xxx"\n'
            '- When you\'re happy, just say **confirm** and I\'ll start generating!'
        )
        return "\n".join(parts)

    def _format_single_pack(
        self, pack: GenerationConfig, pack_num: int = 0, show_header: bool = True
    ) -> str:
        """Format one pack's concepts for display."""
        lines = []
        if show_header and pack_num > 0:
            direction_label = ", ".join(pack.directions) if pack.directions else pack.theme
            lines.append(f"\n### \ud83d\udce6 Pack {pack_num}: {direction_label} ({len(pack.sticker_concepts)} stickers)\n")

        for c in pack.sticker_concepts:
            text_part = f'  Text: "{c.text_overlay}"' if c.text_overlay else ""
            lines.append(f"**{c.index}.** {c.description}{text_part}")

        return "\n".join(lines)

    def _call_review_chat_ai(self) -> str:
        """Chat AI for the concept-review phase (multi-pack aware)."""
        system = CHAT_REVIEW_SYSTEM.format(
            pack_count=len(self._packs),
            packs_text=self._packs_text_for_prompt(),
            summary=self._summary or "(none)",
        )
        messages = self._build_chat_messages()

        try:
            result = self.claude.generate_multiturn(
                messages=messages,
                system=system,
                max_tokens=2000,
                temperature=0.7,
            )
            return result["text"].strip()
        except Exception as e:
            logger.error("Review Chat AI failed: %s", e)
            return "Oops, ran into a small hiccup. Feel free to keep telling me what changes you'd like!"

    def _call_concept_extractor_ai(self) -> bool:
        """Extractor for concept-review phase. Returns True if user confirmed."""
        packs_data = []
        for i, pack in enumerate(self._packs):
            packs_data.append({
                "pack": i + 1,
                "direction": ", ".join(pack.directions),
                "stickers": [c.to_dict() for c in pack.sticker_concepts],
            })

        system = CONCEPT_EXTRACTOR.format(
            packs_json=json.dumps(packs_data, ensure_ascii=False),
            recent_conversation=format_recent_turns(self._turns, limit=_CONTEXT_WINDOW),
        )
        user_prompt = (
            "Based on the conversation, extract concept modifications "
            "and determine if the user confirmed. Return JSON only."
        )

        try:
            raw = self.claude.generate_json(
                prompt=user_prompt,
                system=system,
                max_tokens=800,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("Concept extractor failed: %s", e)
            return False

        modifications = raw.get("modifications", [])
        self._apply_concept_modifications(modifications)

        raw_summary = raw.get("summary")
        parsed = parse_structured_summary(raw_summary, self._summary)
        if parsed:
            self._summary = parsed
            self._compress_history()

        return bool(raw.get("user_confirmed", False))

    def _apply_concept_modifications(self, modifications: List[Dict[str, Any]]) -> None:
        """Apply user-requested changes to sticker concepts across packs."""
        for mod in modifications:
            action = mod.get("action", "")
            pack_idx = mod.get("pack", 1) - 1
            sticker_idx = mod.get("index", 0)

            if pack_idx < 0 or pack_idx >= len(self._packs):
                logger.warning("Invalid pack index: %d", pack_idx + 1)
                continue

            pack = self._packs[pack_idx]

            if action == "add":
                new_concept = StickerConcept(
                    index=len(pack.sticker_concepts) + 1,
                    description=mod.get("description", ""),
                    text_overlay=mod.get("text_overlay", ""),
                )
                pack.sticker_concepts.append(new_concept)
                pack.sticker_count = len(pack.sticker_concepts)
                logger.info("Pack %d: added concept #%d", pack_idx + 1, new_concept.index)

            elif action == "remove":
                pack.sticker_concepts = [
                    c for c in pack.sticker_concepts if c.index != sticker_idx
                ]
                self._reindex_pack(pack)
                logger.info("Pack %d: removed concept #%d", pack_idx + 1, sticker_idx)

            elif action in ("replace", "edit"):
                for c in pack.sticker_concepts:
                    if c.index == sticker_idx:
                        if mod.get("description") is not None:
                            c.description = mod["description"]
                        if mod.get("text_overlay") is not None:
                            c.text_overlay = mod["text_overlay"]
                        logger.info(
                            "Pack %d: updated concept #%d (%s)",
                            pack_idx + 1, sticker_idx, action,
                        )
                        break

    @staticmethod
    def _reindex_pack(pack: GenerationConfig) -> None:
        """Re-number concepts sequentially after removals."""
        for i, c in enumerate(pack.sticker_concepts):
            c.index = i + 1
        pack.sticker_count = len(pack.sticker_concepts)

    def _packs_text_for_prompt(self) -> str:
        """Readable text version of all packs for review prompts."""
        if not self._packs:
            return "(none)"
        parts = []
        for i, pack in enumerate(self._packs):
            direction_label = ", ".join(pack.directions)
            parts.append(f"--- Pack {i + 1}: {direction_label} ({len(pack.sticker_concepts)} stickers) ---")
            for c in pack.sticker_concepts:
                text_part = f' | text: "{c.text_overlay}"' if c.text_overlay else ""
                parts.append(f"  {c.index}. {c.description}{text_part}")
        return "\n".join(parts)

    # =================================================================
    # Internal — Extractor AI (collecting phase)
    # =================================================================

    def _call_extractor_ai(self) -> Dict[str, Any]:
        """Call Claude as the background slot extractor.

        Returns dict of actually updated slots.
        """
        system = EXTRACTOR_SYSTEM.format(
            slot_definitions=build_slot_definitions(self.slots),
            slot_state=build_slot_state_text(self.slots),
            recent_conversation=format_recent_turns(self._turns, limit=_CONTEXT_WINDOW),
            max_turns=_MAX_RAW_TURNS,
        )

        user_prompt = (
            "Based on the conversation above, extract slot values and "
            "determine if the user confirmed. Return JSON only."
        )

        try:
            raw = self.claude.generate_json(
                prompt=user_prompt,
                system=system,
                max_tokens=800,
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("Extractor AI failed: %s \u2014 attempting lightweight fallback", e)
            raw = self._extractor_fallback()
            if raw is None:
                return {}

        extracted = raw.get("extracted_slots", {})
        updated = self._apply_extracted(extracted)

        pack_mode = raw.get("pack_mode")
        if pack_mode in ("single", "independent"):
            self._pack_mode = pack_mode
            logger.info("Pack mode set to: %s", self._pack_mode)

        if raw.get("user_confirmed", False) and not self._has_missing_required():
            self._confirmed = True
            logger.info("User confirmed \u2014 ready for concept generation")

        raw_summary = raw.get("summary")
        parsed = parse_structured_summary(raw_summary, self._summary)
        if parsed:
            self._summary = parsed
            self._compress_history()
            logger.info("Context compressed \u2014 summary: %s", parsed[:120])

        return updated

    def _extractor_fallback(self) -> Optional[Dict[str, Any]]:
        """Simplified fallback extraction when the main extractor fails."""
        last_user = ""
        last_assistant = ""
        for t in reversed(self._turns):
            if t.role == "user" and not last_user:
                last_user = t.content
            elif t.role == "assistant" and not last_assistant:
                last_assistant = t.content
            if last_user and last_assistant:
                break

        if not last_user:
            return None

        fallback_prompt = (
            "Read this brief exchange and extract slot values.\n\n"
            f"Assistant: {last_assistant[:500]}\n"
            f"User: {last_user}\n\n"
            "Current slot state:\n"
            f"{build_slot_state_text(self.slots)}\n\n"
            "IMPORTANT: user_confirmed should be true ONLY if the user "
            "explicitly says they are ready to proceed with the FULL "
            "configuration (e.g. they say something like: "
            "\"confirm\", \"looks good\", \"approved\", \"let's go\", \"yes\"). "
            "Simply stating a preference does NOT count.\n\n"
            "pack_mode: set to \"independent\" if user wants each direction "
            "as a separate pack, \"single\" if they want one combined pack, "
            "null if not mentioned.\n\n"
            'Return ONLY compact JSON like: '
            '{"extracted_slots":{"theme":null,"directions":null,'
            '"visual_style":null,"color_mood":null,"sticker_count":null,'
            '"pack_name":null},"user_confirmed":false,"pack_mode":null,"summary":null}'
        )

        try:
            raw = self.claude.generate_json(
                prompt=fallback_prompt,
                system="You are a JSON-only extraction bot. Return valid JSON, nothing else. "
                       "Do NOT use double-quote characters inside string values.",
                max_tokens=400,
                temperature=0.0,
            )
            logger.info("Extractor fallback succeeded")
            return raw
        except Exception as e2:
            logger.error("Extractor fallback also failed: %s", e2)
            return None

    # =================================================================
    # Internal — helpers
    # =================================================================

    def _apply_extracted(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """Apply extracted slot values, return dict of actually updated slots."""
        updated = {}
        for name, value in extracted.items():
            if value is None:
                continue
            if name not in self.slots:
                continue
            self.slots[name].value = value
            updated[name] = value
        return updated

    def _compress_history(self) -> None:
        """When summary is available, keep only the most recent turns."""
        if len(self._turns) > _MAX_RAW_TURNS:
            keep = _MAX_RAW_TURNS // 2
            self._turns = self._turns[-keep:]
            logger.debug("History compressed to %d turns", len(self._turns))

    def _missing_slot_names(self) -> List[str]:
        return [s.name for s in self.slots.values() if s.required and not s.is_filled]

    def _has_missing_required(self) -> bool:
        return bool(self._missing_slot_names())
