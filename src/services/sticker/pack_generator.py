"""Sticker pack generation service

Complete sticker pack pipeline:
1. Topic generation — theme → topics with use cases & style types (Claude call 0)
2. Theme content expansion (ThemeContentGenerator or caller-provided)
3. Pack Style Guide generation (Claude call 1)
4. Text / Element / Combined idea generation (Claude calls 2-4)
5. Batch image generation (Gemini)
6. Progress tracking & result persistence
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import GenerationError, ValidationError
from src.core.constants import StickerType, DEFAULT_STICKER_COUNT
from src.models.sticker import Sticker, StickerPack, StickerMetadata
from src.services.ai import (
    ClaudeService,
    GeminiService,
    build_pack_style_guide_prompt,
    build_text_sticker_prompt,
    build_element_sticker_prompt,
    build_combined_sticker_prompt,
    build_sticker_pack_prompt,
    build_preview_prompt_via_claude,
    build_preview_prompt_direct,
    build_style_guide_from_config_prompt,
    build_concepts_to_image_prompts,
)
from src.services.sticker.theme_generator import (
    ThemeContentGenerator,
    ThemeContent,
    TopicGenerationResult,
)
from src.utils import validate_theme, validate_count, generate_unique_id, save_json

logger = get_logger("service.pack_generator")


class PackGenerator:
    """Sticker pack generator with ThemeContent-driven pipeline."""

    def __init__(
        self,
        claude_service: Optional[ClaudeService] = None,
        gemini_service: Optional[GeminiService] = None,
        output_dir: Optional[Path] = None,
    ):
        self.claude = claude_service or ClaudeService()
        self.gemini = gemini_service or GeminiService()
        self.output_dir = output_dir or Path(config.output_dir) / "sticker_packs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._theme_gen = ThemeContentGenerator(claude_service=self.claude)

        logger.info("PackGenerator initialized")
        logger.info(f"Output dir: {self.output_dir}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_topics(
        self,
        theme: str,
        max_topics: int = 6,
    ) -> TopicGenerationResult:
        """Generate topic ideas from a theme (pipeline Step 0).

        Given a broad theme, produces focused topics with use cases
        and recommended visual style types. This allows callers to
        inspect / select topics before running the full pack pipeline.

        Args:
            theme: Theme name (any language).
            max_topics: Number of topics to generate.

        Returns:
            TopicGenerationResult with a list of TopicIdea.
        """
        return self._theme_gen.generate_topics(theme, max_topics=max_topics)

    def generate(
        self,
        theme: str,
        count: int = DEFAULT_STICKER_COUNT,
        text_ratio: float = 0.3,
        element_ratio: float = 0.4,
        combined_ratio: float = 0.3,
        max_workers: int = 3,
        topic_result: Optional[TopicGenerationResult] = None,
        theme_content: Optional[ThemeContent] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> StickerPack:
        """Generate a full sticker pack.

        Args:
            theme: Theme name (any language).
            count: Total sticker count.
            text_ratio: Fraction of text-only stickers.
            element_ratio: Fraction of element stickers.
            combined_ratio: Fraction of combined stickers.
            max_workers: Gemini concurrency.
            topic_result: Pre-generated TopicGenerationResult (from generate_topics).
                          If None, auto-generated internally.
            theme_content: Pre-generated ThemeContent (interactive mode).
                           If None, auto-generated internally (auto mode).
            progress_callback: callback(stage, current, total)

        Returns:
            StickerPack with all stickers.

        Raises:
            ValidationError: Invalid parameters.
            GenerationError: Pipeline failure.
        """
        theme = validate_theme(theme)
        count = validate_count(count)

        if abs(text_ratio + element_ratio + combined_ratio - 1.0) > 0.01:
            raise ValidationError(
                "Type ratios must sum to 1.0",
                field="ratios",
                value=f"{text_ratio}+{element_ratio}+{combined_ratio}",
            )

        text_count = int(count * text_ratio)
        element_count = int(count * element_ratio)
        combined_count = count - text_count - element_count

        logger.info(
            f"Starting pack generation — theme: {theme}, count: {count} "
            f"(text={text_count}, element={element_count}, combined={combined_count})"
        )

        start_time = time.time()
        total_steps = 7  # topics + theme + style + 3 idea types + images

        pack_id = f"pack_{theme}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pack = StickerPack(
            id=pack_id,
            name=f"{theme} Sticker Pack",
            theme=theme,
            total_count=count,
        )

        try:
            # Step 1: Topic generation
            if progress_callback:
                progress_callback("Generating topics", 0, total_steps)

            if topic_result is None:
                topic_result = self._theme_gen.generate_topics(theme)
                logger.info("TopicGenerationResult auto-generated")
            else:
                logger.info("Using caller-provided TopicGenerationResult")

            pack.topic_result = topic_result.to_dict()
            logger.info(
                f"Topics ready — {len(topic_result.topics)} topics: "
                + ", ".join(t.name for t in topic_result.topics)
            )

            # Step 2: Theme content (informed by topics)
            if progress_callback:
                progress_callback("Expanding theme", 1, total_steps)

            if theme_content is None:
                theme_content = self._theme_gen.generate(theme)
                logger.info("ThemeContent auto-generated")
            else:
                logger.info("Using caller-provided ThemeContent")

            tc_dict = theme_content.to_dict()
            pack.theme_content = tc_dict

            # Step 3: Style guide
            if progress_callback:
                progress_callback("Creating style guide", 2, total_steps)

            style_guide = self._generate_style_guide(tc_dict)
            pack.style_guide = style_guide
            logger.info(f"Style guide created — art_style: {style_guide.get('art_style', 'N/A')}")

            # Steps 4-6: Idea generation (split by type)
            if progress_callback:
                progress_callback("Generating text sticker ideas", 3, total_steps)
            text_ideas = self._generate_text_ideas(style_guide, tc_dict, text_count)

            if progress_callback:
                progress_callback("Generating element sticker ideas", 4, total_steps)
            element_ideas = self._generate_element_ideas(style_guide, tc_dict, element_count)

            if progress_callback:
                progress_callback("Generating combined sticker ideas", 5, total_steps)
            combined_ideas = self._generate_combined_ideas(style_guide, tc_dict, combined_count)

            ideas = self._merge_ideas(text_ideas, element_ideas, combined_ideas)
            logger.info(f"All ideas generated — total: {len(ideas)}")

            # Step 7: Image generation
            if progress_callback:
                progress_callback("Generating images", 6, total_steps)

            stickers = self._generate_images(
                ideas=ideas,
                pack_id=pack_id,
                max_workers=max_workers,
            )

            for sticker in stickers:
                pack.add_sticker(sticker)

            pack.mark_completed()
            self._save_pack(pack)

            logger.info(
                f"Pack complete — success: {pack.success_count}/{pack.total_count}, "
                f"duration: {pack.duration_seconds:.1f}s"
            )

            if progress_callback:
                progress_callback("Done", total_steps, total_steps)

            return pack

        except Exception as e:
            logger.error(f"Pack generation failed: {e}")
            raise GenerationError(
                f"Pack generation failed: {e}",
                stage="pack_generation",
            )

    # ------------------------------------------------------------------
    # Generate from InteractiveSession config
    # ------------------------------------------------------------------

    def generate_from_config(
        self,
        config: "GenerationConfig",
        max_workers: int = 3,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> StickerPack:
        """Generate a full sticker pack from an InteractiveSession config.

        Flow:
            config → style guide (Claude) → image prompts (Claude) → images (Gemini)

        Args:
            config: GenerationConfig from InteractiveSession (with sticker_concepts).
            max_workers: Gemini concurrency.
            progress_callback: callback(stage, current, total).

        Returns:
            StickerPack with all generated stickers.
        """
        from src.models.generation import GenerationConfig

        total_steps = 4
        step = 0

        def _progress(msg: str):
            nonlocal step
            if progress_callback:
                progress_callback(msg, step, total_steps)
            step += 1

        theme = config.theme
        directions = config.directions or [theme]
        pack_name = config.pack_name if config.pack_name != "auto" else f"{theme} Sticker Pack"
        concepts = config.sticker_concepts

        if not concepts:
            logger.warning(
                "Skipping pack '%s' — no sticker_concepts, returning empty pack",
                pack_name,
            )
            pack_id = f"pack_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{theme[:20]}"
            return StickerPack(
                id=pack_id,
                name=pack_name,
                theme=theme,
                total_count=0,
            )

        pack_id = f"pack_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{theme[:20]}"
        pack = StickerPack(
            id=pack_id,
            name=pack_name,
            theme=theme,
            total_count=len(concepts),
        )

        logger.info(
            "Starting generation from config — theme: %s, stickers: %d",
            theme, len(concepts),
        )

        try:
            # Step 1: Style guide
            _progress("Creating style guide")
            style_guide = self._generate_style_guide_from_config(config)
            pack.style_guide = style_guide
            logger.info("Style guide created — art_style: %s", style_guide.get("art_style", "N/A"))

            # Step 2: Convert concepts → image prompts
            _progress("Converting concepts to image prompts")
            ideas = self._convert_concepts_to_ideas(style_guide, config)
            logger.info("Image prompts generated: %d", len(ideas))

            # Step 3: Generate images
            _progress("Generating images")
            stickers = self._generate_images(
                ideas=ideas,
                pack_id=pack_id,
                max_workers=max_workers,
            )

            for sticker in stickers:
                pack.add_sticker(sticker)

            pack.mark_completed()
            self._save_pack(pack)

            logger.info(
                "Pack complete — success: %d/%d, duration: %.1fs",
                pack.success_count, pack.total_count, pack.duration_seconds,
            )

            if progress_callback:
                progress_callback("Done", total_steps, total_steps)

            return pack

        except Exception as e:
            logger.error("Generation from config failed: %s", e)
            raise GenerationError(
                f"Generation from config failed: {e}",
                stage="config_generation",
            )

    def generate_preview_from_config(
        self,
        config: "GenerationConfig",
        *,
        use_claude_prompt: bool = True,
        output_path: Optional[Path] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Generate a single preview image (collection sheet) from an InteractiveSession config.

        Flow:
            config → style guide (Claude) → concept→idea conversion (Claude)
                   → preview prompt (Claude or template) → single preview image (Gemini)

        Args:
            config: GenerationConfig with sticker_concepts.
            use_claude_prompt: Use Claude to craft the preview prompt (higher quality).
            output_path: Where to save the image. Auto-generated if None.
            progress_callback: callback(stage, current, total).

        Returns:
            Dict with keys: pack_name, preview_prompt, preview_image, style_guide, sticker_ideas.
        """
        from src.models.generation import GenerationConfig

        total_steps = 4
        step = 0

        def _progress(msg: str):
            nonlocal step
            if progress_callback:
                progress_callback(msg, step, total_steps)
            step += 1

        theme = config.theme
        directions = config.directions or [theme]
        pack_name = config.pack_name if config.pack_name != "auto" else f"{theme} Sticker Pack"
        concepts = config.sticker_concepts

        if not concepts:
            logger.warning("No sticker_concepts in config — cannot generate preview")
            return {
                "pack_name": pack_name,
                "preview_prompt": "",
                "preview_image": {"success": False, "error": "No sticker concepts"},
                "style_guide": {},
                "sticker_ideas": [],
            }

        logger.info(
            "Starting preview generation from config — theme: %s, stickers: %d",
            theme, len(concepts),
        )

        _progress("Creating style guide")
        style_guide = self._generate_style_guide_from_config(config)
        logger.info("Style guide created — art_style: %s", style_guide.get("art_style", "N/A"))

        _progress("Converting concepts to sticker ideas")
        ideas = self._convert_concepts_to_ideas(style_guide, config)
        logger.info("Sticker ideas generated: %d", len(ideas))

        _progress("Building preview prompt")
        preview_prompt = self.generate_preview_prompt(
            pack_name=pack_name,
            sticker_ideas=ideas,
            style_guide=style_guide,
            use_claude=use_claude_prompt,
        )

        _progress("Generating preview image")
        preview_result = self.generate_preview_image(preview_prompt, output_path=output_path)

        if progress_callback:
            progress_callback("Done", total_steps, total_steps)

        return {
            "pack_name": pack_name,
            "preview_prompt": preview_prompt,
            "preview_image": preview_result,
            "style_guide": style_guide,
            "sticker_ideas": ideas,
        }

    def generate_preview_from_configs(
        self,
        configs: "List[GenerationConfig]",
        *,
        use_claude_prompt: bool = True,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate preview images for a list of configs (multi-pack support).

        Each config produces one preview image (collection sheet).

        Args:
            configs: List of GenerationConfig from InteractiveSession.
            use_claude_prompt: Use Claude for preview prompt.
            progress_callback: callback(stage, current, total).

        Returns:
            List of preview result dicts.
        """
        results = []
        valid_configs = [c for c in configs if getattr(c, "sticker_concepts", None)]

        if not valid_configs:
            logger.warning("No configs with sticker_concepts — nothing to generate")
            return results

        for i, cfg in enumerate(valid_configs):
            logger.info("Generating preview %d/%d: %s", i + 1, len(valid_configs), cfg.theme)

            def _cb(stage: str, cur: int, total: int) -> None:
                label = f"[Pack {i + 1}/{len(valid_configs)}] {stage}"
                if progress_callback:
                    progress_callback(label, cur, total)

            result = self.generate_preview_from_config(
                cfg,
                use_claude_prompt=use_claude_prompt,
                progress_callback=_cb,
            )
            results.append(result)

        return results

    def generate_from_configs(
        self,
        configs: "List[GenerationConfig]",
        max_workers: int = 3,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> "List[StickerPack]":
        """Generate sticker packs for a list of configs (multi-pack support).

        Args:
            configs: List of GenerationConfig from InteractiveSession.
            max_workers: Gemini concurrency per pack.
            progress_callback: callback(stage, current, total).

        Returns:
            List of StickerPack results.
        """
        packs = []
        for i, cfg in enumerate(configs):
            logger.info("Generating pack %d/%d: %s", i + 1, len(configs), cfg.theme)

            def _cb(stage: str, cur: int, total: int) -> None:
                label = f"[Pack {i + 1}/{len(configs)}] {stage}"
                if progress_callback:
                    progress_callback(label, cur, total)

            pack = self.generate_from_config(cfg, max_workers=max_workers, progress_callback=_cb)
            packs.append(pack)

        return packs

    def _generate_style_guide_from_config(self, config: "GenerationConfig") -> Dict[str, Any]:
        """Generate a Pack Style Guide from interactive config fields."""
        visual_style = config.visual_style if config.visual_style != "auto" else "auto (choose best fit)"
        color_mood = config.color_mood if config.color_mood != "auto" else "auto (choose best fit)"

        prompt = build_style_guide_from_config_prompt(
            theme=config.theme,
            directions=config.directions,
            visual_style=visual_style,
            color_mood=color_mood,
        )

        try:
            guide = self.claude.generate_json(prompt=prompt, max_tokens=2000, temperature=0.7)

            if not isinstance(guide, dict):
                raise GenerationError("Style guide response is not a JSON object", stage="style_guide")

            guide.setdefault("art_style", "flat vector illustration")
            guide.setdefault("color_palette", {})
            guide.setdefault("line_style", "")
            guide.setdefault("mood", "")
            guide.setdefault("typography_style", "")
            guide.setdefault("visual_consistency_rules", [])

            return guide

        except GenerationError:
            raise
        except Exception as e:
            logger.error("Style guide from config failed: %s", e)
            raise GenerationError(f"Style guide generation failed: {e}", stage="style_guide")

    def _convert_concepts_to_ideas(
        self, style_guide: Dict[str, Any], config: "GenerationConfig"
    ) -> List[Dict[str, Any]]:
        """Convert StickerConcepts into sticker idea dicts with image_prompt."""
        concepts_dicts = [c.to_dict() for c in config.sticker_concepts]

        prompt = build_concepts_to_image_prompts(
            style_guide=style_guide,
            theme=config.theme,
            concepts=concepts_dicts,
        )

        try:
            ideas = self.claude.generate_json(prompt=prompt, max_tokens=4000, temperature=0.8)
        except Exception as e:
            logger.error("Concept→prompt conversion failed: %s", e)
            raise GenerationError(f"Concept→prompt conversion failed: {e}", stage="concept_conversion")

        if not isinstance(ideas, list):
            raise GenerationError(
                f"Expected list from Claude, got {type(ideas).__name__}",
                stage="concept_conversion",
            )

        for i, idea in enumerate(ideas):
            idea.setdefault("index", i + 1)
            idea.setdefault("title", f"Sticker {i + 1}")
            idea["type"] = "combined"
            idea.setdefault("image_prompt", "")
            idea.setdefault("concept", "")
            # Carry over the original Chinese concept for reference
            if i < len(config.sticker_concepts):
                orig = config.sticker_concepts[i]
                idea["original_description"] = orig.description
                idea["original_text_overlay"] = orig.text_overlay

        return ideas

    # ------------------------------------------------------------------
    # Style guide generation (Claude call 0)
    # ------------------------------------------------------------------

    def _generate_style_guide(self, theme_content: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a unified Pack Style Guide from ThemeContent."""
        logger.info("Generating Pack Style Guide...")

        try:
            prompt = build_pack_style_guide_prompt(theme_content)
            guide = self.claude.generate_json(
                prompt=prompt,
                max_tokens=2000,
                temperature=0.7,
            )

            if not isinstance(guide, dict):
                raise GenerationError(
                    "Style guide response is not a JSON object",
                    stage="style_guide",
                )

            guide.setdefault("art_style", "flat vector illustration")
            guide.setdefault("color_palette", {})
            guide.setdefault("line_style", "")
            guide.setdefault("mood", "")
            guide.setdefault("typography_style", "")
            guide.setdefault("visual_consistency_rules", [])

            return guide

        except Exception as e:
            logger.error(f"Style guide generation failed: {e}")
            raise GenerationError(
                f"Style guide generation failed: {e}",
                stage="style_guide",
            )

    # ------------------------------------------------------------------
    # Type-specific idea generation (Claude calls 1-3)
    # ------------------------------------------------------------------

    def _generate_text_ideas(
        self,
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> List[Dict[str, Any]]:
        """Generate text-only sticker ideas (Claude call 1)."""
        if count <= 0:
            return []

        logger.info(f"Generating {count} text sticker ideas...")

        try:
            prompt = build_text_sticker_prompt(style_guide, theme_content, count)
            ideas = self.claude.generate_json(
                prompt=prompt,
                max_tokens=4000,
                temperature=0.9,
            )
            return self._validate_ideas(ideas, "text", count)

        except Exception as e:
            logger.error(f"Text idea generation failed: {e}")
            raise GenerationError(
                f"Text idea generation failed: {e}",
                stage="text_ideas",
            )

    def _generate_element_ideas(
        self,
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> List[Dict[str, Any]]:
        """Generate element sticker ideas (Claude call 2)."""
        if count <= 0:
            return []

        logger.info(f"Generating {count} element sticker ideas...")

        try:
            prompt = build_element_sticker_prompt(style_guide, theme_content, count)
            ideas = self.claude.generate_json(
                prompt=prompt,
                max_tokens=4000,
                temperature=0.9,
            )
            return self._validate_ideas(ideas, "element", count)

        except Exception as e:
            logger.error(f"Element idea generation failed: {e}")
            raise GenerationError(
                f"Element idea generation failed: {e}",
                stage="element_ideas",
            )

    def _generate_combined_ideas(
        self,
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> List[Dict[str, Any]]:
        """Generate combined sticker ideas (Claude call 3)."""
        if count <= 0:
            return []

        logger.info(f"Generating {count} combined sticker ideas...")

        try:
            prompt = build_combined_sticker_prompt(style_guide, theme_content, count)
            ideas = self.claude.generate_json(
                prompt=prompt,
                max_tokens=4000,
                temperature=0.9,
            )
            return self._validate_ideas(ideas, "combined", count)

        except Exception as e:
            logger.error(f"Combined idea generation failed: {e}")
            raise GenerationError(
                f"Combined idea generation failed: {e}",
                stage="combined_ideas",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_ideas(
        raw: Any, expected_type: str, expected_count: int
    ) -> List[Dict[str, Any]]:
        """Validate and normalise a list of ideas from Claude."""
        if not isinstance(raw, list):
            raise GenerationError(
                f"Expected list from Claude, got {type(raw).__name__}",
                stage=f"{expected_type}_ideas",
            )

        for i, idea in enumerate(raw, 1):
            idea.setdefault("index", i)
            idea.setdefault("title", f"Sticker {i}")
            idea["type"] = expected_type
            idea.setdefault("image_prompt", "")
            idea.setdefault("concept", "")

        return raw

    @staticmethod
    def _merge_ideas(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge idea lists and re-index sequentially."""
        merged: List[Dict[str, Any]] = []
        for group in groups:
            merged.extend(group)

        for i, idea in enumerate(merged, 1):
            idea["index"] = i

        return merged

    # ------------------------------------------------------------------
    # Image generation (unchanged from v1)
    # ------------------------------------------------------------------

    def _generate_images(
        self,
        ideas: List[Dict[str, Any]],
        pack_id: str,
        max_workers: int = 3,
    ) -> List[Sticker]:
        """Generate images for all ideas via Gemini."""
        logger.info(f"Generating {len(ideas)} images (workers: {max_workers})")

        today = datetime.now().strftime("%Y%m%d")
        image_dir = Path(config.output_dir) / "images" / today / pack_id
        image_dir.mkdir(parents=True, exist_ok=True)

        results_dict: Dict[int, Sticker] = {}

        def _gen_one(idea: Dict[str, Any]) -> tuple:
            idx = idea["index"]
            title = idea.get("title", f"sticker_{idx}")
            prompt = idea.get("image_prompt", "")
            sticker_type = idea.get("type", "element")

            sticker_id = f"{pack_id}_sticker_{idx:03d}"
            sticker = Sticker(
                id=sticker_id,
                type=StickerType(sticker_type),
                prompt=prompt,
                theme=idea.get("theme", ""),
                description=idea.get("concept", ""),
                metadata=StickerMetadata(tags=[title], source="pack_generator"),
            )

            if not prompt:
                logger.warning(f"Sticker {idx} has no image_prompt, skipping")
                sticker.mark_failed("Missing image_prompt")
                return idx, sticker

            from src.utils.text_utils import sanitize_filename

            safe_title = sanitize_filename(title)
            filename = f"sticker_{idx:03d}_{safe_title}.png"
            output_path = image_dir / filename

            logger.debug(f"({idx}/{len(ideas)}) Generating: {title}")

            try:
                result = self.gemini.generate_image(
                    prompt=prompt, output_path=output_path
                )

                if result["success"]:
                    sticker.mark_success(str(output_path))
                    logger.debug(f"({idx}/{len(ideas)}) OK: {title}")
                else:
                    sticker.mark_failed(result.get("error", "Unknown error"))
                    logger.warning(
                        f"({idx}/{len(ideas)}) FAIL: {title}: {result.get('error')}"
                    )

            except Exception as e:
                logger.error(f"({idx}/{len(ideas)}) Error: {e}")
                sticker.mark_failed(str(e))

            return idx, sticker

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_gen_one, idea): idea["index"] for idea in ideas
            }
            for future in as_completed(futures):
                idx, sticker = future.result()
                results_dict[idx] = sticker

        stickers = [results_dict[i] for i in sorted(results_dict)]

        success_count = sum(1 for s in stickers if s.status == "success")
        logger.info(f"Image generation complete — success: {success_count}/{len(stickers)}")

        return stickers

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_pack(self, pack: StickerPack) -> None:
        filename = f"{pack.id}.json"
        filepath = self.output_dir / filename
        save_json(pack.to_dict(), str(filepath))
        logger.info(f"Pack saved: {filepath}")

    # ------------------------------------------------------------------
    # Preview prompt + image generation
    # ------------------------------------------------------------------

    def generate_preview_prompt(
        self,
        pack_name: str,
        sticker_ideas: List[Dict[str, Any]],
        style_guide: Dict[str, Any],
        *,
        use_claude: bool = True,
    ) -> str:
        """Generate a Gemini-ready preview prompt for the sticker collection.

        Two modes:
        - use_claude=True  — asks Claude to craft the prompt (higher quality)
        - use_claude=False — builds prompt from a template (faster, no API call)

        Args:
            pack_name: Display name for the pack.
            sticker_ideas: List of sticker idea dicts.
            style_guide: Pack Style Guide dict.
            use_claude: Whether to use Claude for prompt generation.

        Returns:
            str: Image generation prompt for Gemini.
        """
        if not use_claude:
            prompt = build_preview_prompt_direct(pack_name, sticker_ideas, style_guide)
            logger.info(f"Preview prompt built via template ({len(prompt)} chars)")
            return prompt

        logger.info("Generating preview prompt via Claude...")
        meta_prompt = build_preview_prompt_via_claude(pack_name, sticker_ideas, style_guide)

        try:
            result = self.claude.generate(
                prompt=meta_prompt,
                max_tokens=2000,
                temperature=0.7,
            )
            preview_prompt = result["text"].strip()
            logger.info(f"Preview prompt generated via Claude ({len(preview_prompt)} chars)")
            return preview_prompt

        except Exception as e:
            logger.warning(f"Claude preview prompt failed, falling back to template: {e}")
            return build_preview_prompt_direct(pack_name, sticker_ideas, style_guide)

    def generate_preview_image(
        self,
        preview_prompt: str,
        output_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Generate the preview image from a preview prompt.

        Args:
            preview_prompt: Gemini-ready image generation prompt.
            output_path: Where to save the image. Auto-generated if None.

        Returns:
            Dict with keys: success, image_path, image_data, size_kb, elapsed, error.
        """
        if output_path is None:
            today = datetime.now().strftime("%Y%m%d")
            preview_dir = Path(config.output_dir) / "images" / today / "previews"
            preview_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            output_path = preview_dir / f"preview_{ts}.png"

        logger.info(f"Generating preview image → {output_path}")
        result = self.gemini.generate_image(
            prompt=preview_prompt,
            output_path=output_path,
        )

        if result["success"]:
            logger.info(f"Preview image saved: {result['image_path']} ({result['size_kb']} KB)")
        else:
            logger.error(f"Preview image generation failed: {result.get('error')}")

        return result

    def generate_preview(
        self,
        theme: str,
        pack_name: Optional[str] = None,
        count: int = 10,
        text_ratio: float = 0.3,
        element_ratio: float = 0.4,
        combined_ratio: float = 0.3,
        *,
        use_claude_prompt: bool = True,
        topic_result: Optional[TopicGenerationResult] = None,
        theme_content: Optional[ThemeContent] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """End-to-end preview generation pipeline.

        theme → topics → theme_content → style_guide → ideas → preview_prompt → image

        Args:
            theme: Theme name (any language).
            pack_name: Display name for the pack. Auto-generated if None.
            count: Number of sticker ideas to design.
            text_ratio: Fraction of text-only stickers.
            element_ratio: Fraction of element stickers.
            combined_ratio: Fraction of combined stickers.
            use_claude_prompt: Use Claude for preview prompt (True) or template (False).
            topic_result: Pre-generated topics (optional).
            theme_content: Pre-generated theme content (optional).
            progress_callback: callback(stage, current, total).

        Returns:
            Dict containing:
              - preview_prompt: str
              - preview_image: Dict (generate_image result)
              - topic_result: TopicGenerationResult dict
              - theme_content: ThemeContent dict
              - style_guide: Dict
              - sticker_ideas: List[Dict]
              - pack_name: str
        """
        from src.utils import validate_theme, validate_count

        theme = validate_theme(theme)
        count = validate_count(count)

        text_count = int(count * text_ratio)
        element_count = int(count * element_ratio)
        combined_count = count - text_count - element_count

        total_steps = 6
        step = 0

        def _progress(msg: str):
            nonlocal step
            if progress_callback:
                progress_callback(msg, step, total_steps)
            step += 1

        # Step 0: Topics
        _progress("Generating topics")
        if topic_result is None:
            topic_result = self._theme_gen.generate_topics(theme)
        logger.info(f"Topics: {len(topic_result.topics)}")

        # Step 1: Theme content
        _progress("Expanding theme content")
        if theme_content is None:
            theme_content = self._theme_gen.generate(theme)
        tc_dict = theme_content.to_dict()

        # Step 2: Style guide
        _progress("Creating style guide")
        style_guide = self._generate_style_guide(tc_dict)

        # Step 3: Sticker ideas
        _progress("Generating sticker ideas")
        text_ideas = self._generate_text_ideas(style_guide, tc_dict, text_count) if text_count else []
        element_ideas = self._generate_element_ideas(style_guide, tc_dict, element_count) if element_count else []
        combined_ideas = self._generate_combined_ideas(style_guide, tc_dict, combined_count) if combined_count else []
        all_ideas = self._merge_ideas(text_ideas, element_ideas, combined_ideas)
        logger.info(f"Sticker ideas generated: {len(all_ideas)}")

        # Step 4: Preview prompt
        _progress("Building preview prompt")
        if pack_name is None:
            english_theme = tc_dict.get("theme_english", theme)
            pack_name = f"THE {english_theme.upper()} STICKER PACK"

        preview_prompt = self.generate_preview_prompt(
            pack_name=pack_name,
            sticker_ideas=all_ideas,
            style_guide=style_guide,
            use_claude=use_claude_prompt,
        )

        # Step 5: Preview image
        _progress("Generating preview image")
        preview_result = self.generate_preview_image(preview_prompt)

        if progress_callback:
            progress_callback("Done", total_steps, total_steps)

        return {
            "pack_name": pack_name,
            "preview_prompt": preview_prompt,
            "preview_image": preview_result,
            "topic_result": topic_result.to_dict(),
            "theme_content": tc_dict,
            "style_guide": style_guide,
            "sticker_ideas": all_ideas,
        }

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_pack_info(self) -> Dict[str, Any]:
        return {
            "claude_model": self.claude.model,
            "gemini_model": self.gemini.model,
            "output_dir": str(self.output_dir),
        }
