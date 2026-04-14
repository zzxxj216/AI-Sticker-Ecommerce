"""Sticker Pack Generation Pipeline

Planner -> Designer(Spec) -> Prompt Builder -> Image Generation
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.logger import get_logger
from src.models.sticker_pack import PipelineResult
from src.services.batch.sticker_prompts import (
    PLANNER_SYSTEM,
    DESIGNER_SYSTEM,
    PROMPT_BUILDER_SYSTEM,
    build_planner_prompt,
    build_designer_prompt,
    build_prompt_builder_prompt,
    extract_part2,
    parse_prompt_builder_output,
    parse_prompter_output,
    flatten_prompts,
)

logger = get_logger("pipeline")

ProgressCallback = Optional[Callable[[str, Dict[str, Any]], None]]


class StickerPackPipeline:
    """Planner -> Designer(Spec) -> Prompt Builder -> Image Generation Pipeline"""

    DEFAULT_IMAGE_WORKERS = 5

    def __init__(
        self,
        openai_service,
        gemini_service,
        output_dir: str = "output",
        image_workers: int | None = None,
    ):
        self.openai = openai_service
        self.gemini = gemini_service
        self.base_output_dir = Path(output_dir)
        self.output_dir = self.base_output_dir
        self.image_workers = image_workers or self.DEFAULT_IMAGE_WORKERS

    def run(
        self,
        theme: str,
        user_style: Optional[str] = None,
        user_color_mood: Optional[str] = None,
        user_extra: str = "",
        trend_brief: Optional[Dict[str, Any]] = None,
        skip_images: bool = False,
        on_progress: ProgressCallback = None,
    ) -> PipelineResult:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        display_theme = theme
        if trend_brief and isinstance(trend_brief.get("trend_name"), str):
            tn = trend_brief["trend_name"].strip()
            if tn:
                display_theme = tn
        safe_theme = _sanitize_filename(display_theme)
        self.output_dir = self.base_output_dir / f"{timestamp}_{safe_theme}"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        result = PipelineResult(theme=display_theme)
        result.mark_started()

        try:
            self._emit(on_progress, "pipeline_start", {
                "theme": display_theme,
                "output_dir": str(self.output_dir),
            })

            # Step 1: Planner
            self._emit(on_progress, "planner_start", {})
            planner_text = self._run_planner(
                theme, user_style, user_color_mood, user_extra, trend_brief
            )
            result.planner_output = planner_text
            result.planner_part2 = extract_part2(planner_text)
            self._save("planner_output.txt", planner_text)
            self._emit(on_progress, "planner_done", {"chars": len(planner_text)})

            # Step 2: Designer
            self._emit(on_progress, "designer_start", {})
            designer_text = self._run_designer(
                result.planner_output,
                trend_brief,
                display_theme,
            )
            result.designer_output = designer_text
            self._save("designer_output.txt", designer_text)
            self._emit(on_progress, "designer_done", {"chars": len(designer_text)})

            # Step 3: Prompt Builder
            self._emit(on_progress, "prompter_start", {})
            prompt_builder_text = self._run_prompt_builder(
                trend_brief=trend_brief,
                pack_plan_text=result.planner_output,
                sticker_spec_text=designer_text,
                theme=display_theme,
            )
            result.prompter_output = prompt_builder_text
            self._save("prompt_builder_output.txt", prompt_builder_text)
            self._emit(on_progress, "prompter_done", {"chars": len(prompt_builder_text)})

            # Step 4: Parse prompts
            builder_parsed = parse_prompt_builder_output(prompt_builder_text)
            if builder_parsed.get("pack_foundation"):
                self._save("pack_foundation.txt", builder_parsed["pack_foundation"])
            if builder_parsed.get("quality_control"):
                self._save("quality_control.txt", builder_parsed["quality_control"])

            grouped = parse_prompter_output(prompt_builder_text)
            result.prompts_grouped = grouped
            result.prompts_flat = flatten_prompts(grouped)
            total_count = len(result.prompts_flat)
            logger.info(
                "Parsed %d prompts in %d groups",
                total_count, len(grouped),
            )

            # Step 5: Generate sticker images
            if not skip_images and result.prompts_flat:
                self._emit(on_progress, "images_start", {"count": total_count})
                image_paths = self._generate_images(result.prompts_flat)
                result.image_paths = image_paths
                self._emit(on_progress, "images_done", {
                    "count": len(image_paths),
                    "paths": image_paths,
                })

            result.mark_completed()
            self._emit(on_progress, "pipeline_done", {
                "prompts": total_count,
                "categories": len(grouped),
                "images": len(result.image_paths),
                "duration": result.duration_seconds,
            })

        except Exception as e:
            logger.error("Pipeline failed: %s", e)
            result.mark_failed(str(e))
            self._emit(on_progress, "pipeline_error", {"error": str(e)})

        return result

    # ------------------------------------------------------------------
    # Agent calls
    # ------------------------------------------------------------------

    def _run_planner(
        self,
        theme: str,
        user_style: Optional[str],
        user_color_mood: Optional[str],
        user_extra: str,
        trend_brief: Optional[Dict[str, Any]],
    ) -> str:
        logger.info("Step 1: Planner Agent — theme=%s", theme)
        user_prompt = build_planner_prompt(
            theme=theme if trend_brief is None else None,
            user_style=user_style,
            user_color_mood=user_color_mood,
            user_extra=user_extra,
            trend_brief=trend_brief,
        )
        result = self.openai.generate(
            prompt=user_prompt,
            system=PLANNER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    def _run_designer(
        self,
        planner_full_output: str,
        trend_brief: Optional[Dict[str, Any]],
        theme: str,
    ) -> str:
        logger.info("Step 2: Designer Agent (Sticker Spec)")
        user_prompt = build_designer_prompt(
            planner_full_output,
            trend_brief=trend_brief,
            theme=theme,
        )
        result = self.openai.generate(
            prompt=user_prompt,
            system=DESIGNER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    def _run_prompt_builder(
        self,
        *,
        trend_brief: Optional[Dict[str, Any]],
        pack_plan_text: str,
        sticker_spec_text: str,
        theme: str,
    ) -> str:
        logger.info("Step 3: Prompt Builder")
        user_prompt = build_prompt_builder_prompt(
            sticker_spec_text,
            trend_brief=trend_brief,
            pack_plan_text=pack_plan_text,
            theme=theme,
        )
        result = self.openai.generate(
            prompt=user_prompt,
            system=PROMPT_BUILDER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    # ------------------------------------------------------------------
    # Image generation
    # ------------------------------------------------------------------

    def _generate_images(self, prompts: List[Dict[str, Any]]) -> List[str]:
        """Generate sticker images via Gemini in batch."""
        n = len(prompts)
        logger.info(
            "Step 5: generating images (%d stickers). "
            "Disk stays empty until the first API call returns; see Gemini logs for (1/%d) … progress.",
            n,
            n,
        )

        images_dir = self.output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        prompt_texts = [p["prompt"] for p in prompts]
        results = self.gemini.generate_batch(
            prompts=prompt_texts,
            output_dir=images_dir,
            max_workers=self.image_workers,
        )

        paths = []
        for i, r in enumerate(results):
            if r.get("success") and r.get("image_path"):
                paths.append(str(r["image_path"]))
            else:
                logger.warning("Sticker %d generation failed: %s", i + 1, r.get("error", "unknown"))

        logger.info("Image generation done: %d/%d succeeded", len(paths), len(prompts))
        return paths

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def _save(self, filename: str, content: str):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        path.write_text(content, encoding="utf-8")
        logger.debug("Saved: %s", path)

    @staticmethod
    def _emit(callback: ProgressCallback, event: str, data: Dict[str, Any]):
        if callback:
            try:
                callback(event, data)
            except Exception as e:
                logger.warning("Progress callback error: %s", e)


def _sanitize_filename(name: str) -> str:
    """Convert a name into a filesystem-safe filename segment."""
    import re as _re
    clean = _re.sub(r"[^\w-]", "_", name)
    clean = _re.sub(r"_+", "_", clean).strip("_")
    return clean[:60] or "category"
