"""贴纸包生成 Pipeline

串联 Planner → Designer → Prompter → 按主题预览图 → 逐张生图 的完整流程。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.logger import get_logger
from src.models.sticker_pack import PipelineResult
from src.services.batch.sticker_prompts import (
    PLANNER_SYSTEM,
    DESIGNER_SYSTEM,
    PROMPTER_SYSTEM,
    build_planner_prompt,
    build_designer_prompt,
    build_prompter_prompt,
    build_preview_prompt,
    extract_part2,
    parse_prompter_output,
    flatten_prompts,
)

logger = get_logger("pipeline")

ProgressCallback = Optional[Callable[[str, Dict[str, Any]], None]]


class StickerPackPipeline:
    """三 Agent + 按主题预览图 + 生图 的完整 Pipeline"""

    def __init__(
        self,
        openai_service,
        gemini_service,
        output_dir: str = "output",
    ):
        self.openai = openai_service
        self.gemini = gemini_service
        self.base_output_dir = Path(output_dir)
        self.output_dir = self.base_output_dir  # will be set per-run

    def run(
        self,
        theme: str,
        user_style: Optional[str] = None,
        user_color_mood: Optional[str] = None,
        user_extra: str = "",
        skip_images: bool = False,
        on_progress: ProgressCallback = None,
    ) -> PipelineResult:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_theme = _sanitize_filename(theme)
        self.output_dir = self.base_output_dir / f"{timestamp}_{safe_theme}"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        result = PipelineResult(theme=theme)
        result.mark_started()

        try:
            self._emit(on_progress, "pipeline_start", {
                "theme": theme,
                "output_dir": str(self.output_dir),
            })

            # Step 1: Planner
            self._emit(on_progress, "planner_start", {})
            planner_text = self._run_planner(theme, user_style, user_color_mood, user_extra)
            result.planner_output = planner_text
            result.planner_part2 = extract_part2(planner_text)
            self._save("planner_output.txt", planner_text)
            self._emit(on_progress, "planner_done", {"chars": len(planner_text)})

            # Step 2: Designer
            self._emit(on_progress, "designer_start", {})
            designer_text = self._run_designer(result.planner_part2)
            result.designer_output = designer_text
            self._save("designer_output.txt", designer_text)
            self._emit(on_progress, "designer_done", {"chars": len(designer_text)})

            # Step 3: Prompter
            self._emit(on_progress, "prompter_start", {})
            prompter_text = self._run_prompter(result.planner_part2, designer_text)
            result.prompter_output = prompter_text
            self._save("prompter_output.txt", prompter_text)
            self._emit(on_progress, "prompter_done", {"chars": len(prompter_text)})

            # Step 4: Parse prompts (grouped by category)
            grouped = parse_prompter_output(prompter_text)
            result.prompts_grouped = grouped
            result.prompts_flat = flatten_prompts(grouped)
            total_count = len(result.prompts_flat)
            logger.info(
                "解析出 %d 条 prompt，分 %d 个主题",
                total_count, len(grouped),
            )

            # Step 5: Per-category preview images (multi-threaded)
            if grouped:
                self._emit(on_progress, "preview_start", {"categories": len(grouped)})
                preview_paths = self._generate_previews_by_category(
                    grouped, result.planner_part2,
                )
                result.preview_paths = preview_paths
                self._emit(on_progress, "preview_done", {
                    "count": len(preview_paths),
                    "paths": preview_paths,
                })

            # Step 6: Generate sticker images (optional)
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
                "previews": len(result.preview_paths),
                "images": len(result.image_paths),
                "duration": result.duration_seconds,
            })

        except Exception as e:
            logger.error("Pipeline 失败: %s", e)
            result.mark_failed(str(e))
            self._emit(on_progress, "pipeline_error", {"error": str(e)})

        return result

    # ------------------------------------------------------------------
    # Agent 调用
    # ------------------------------------------------------------------

    def _run_planner(
        self,
        theme: str,
        user_style: Optional[str],
        user_color_mood: Optional[str],
        user_extra: str,
    ) -> str:
        logger.info("Step 1: Planner Agent — theme=%s", theme)
        user_prompt = build_planner_prompt(theme, user_style, user_color_mood, user_extra)
        result = self.openai.generate(
            prompt=user_prompt,
            system=PLANNER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    def _run_designer(self, planner_part2: str) -> str:
        logger.info("Step 2: Designer Agent")
        user_prompt = build_designer_prompt(planner_part2)
        result = self.openai.generate(
            prompt=user_prompt,
            system=DESIGNER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    def _run_prompter(self, planner_part2: str, designer_output: str) -> str:
        logger.info("Step 3: Prompter Agent")
        user_prompt = build_prompter_prompt(planner_part2, designer_output)
        result = self.openai.generate(
            prompt=user_prompt,
            system=PROMPTER_SYSTEM,
            temperature=0.7,
        )
        return result["text"]

    # ------------------------------------------------------------------
    # 按主题分类生成预览图（多线程）
    # ------------------------------------------------------------------

    def _generate_previews_by_category(
        self,
        grouped_prompts: Dict[str, List[Dict]],
        style_direction: str,
        max_workers: int = 3,
    ) -> Dict[str, str]:
        """为每个主题分类生成一张预览图，多线程并行。

        Returns:
            {category_name: image_path, ...}
        """
        logger.info("Step 5: 按主题生成预览图 (%d 个主题)", len(grouped_prompts))

        preview_dir = self.output_dir / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        results: Dict[str, str] = {}

        def _gen_one(category: str, stickers: List[Dict]) -> tuple[str, Optional[str]]:
            sticker_text = "\n\n".join(
                f"Sticker {s['index']}:\n{s['prompt']}" for s in stickers
            )
            meta_prompt = build_preview_prompt(
                category_name=category,
                sticker_prompts=sticker_text,
                style_direction=style_direction,
            )
            meta_result = self.openai.generate(prompt=meta_prompt, temperature=0.7)
            image_prompt = meta_result["text"]

            safe_name = _sanitize_filename(category)
            out_path = preview_dir / f"preview_{safe_name}.png"

            img = self.gemini.generate_image(
                prompt=image_prompt,
                output_path=out_path,
            )
            if img.get("success"):
                logger.info("预览图完成: %s → %s", category, out_path)
                return category, str(out_path)
            else:
                logger.warning("预览图失败: %s — %s", category, img.get("error", "unknown"))
                return category, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_gen_one, cat, stickers): cat
                for cat, stickers in grouped_prompts.items()
            }
            for future in as_completed(futures):
                cat_name = futures[future]
                try:
                    cat, path = future.result()
                    if path:
                        results[cat] = path
                except Exception as e:
                    logger.error("预览图线程异常 [%s]: %s", cat_name, e)

        return results

    # ------------------------------------------------------------------
    # 逐张生图
    # ------------------------------------------------------------------

    def _generate_images(self, prompts: List[Dict[str, Any]]) -> List[str]:
        """用 Gemini 批量生成贴纸图片。"""
        logger.info("Step 6: 逐张生图 (%d 张)", len(prompts))

        images_dir = self.output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        prompt_texts = [p["prompt"] for p in prompts]
        results = self.gemini.generate_batch(
            prompts=prompt_texts,
            output_dir=images_dir,
            max_workers=3,
        )

        paths = []
        for i, r in enumerate(results):
            if r.get("success") and r.get("image_path"):
                paths.append(str(r["image_path"]))
            else:
                logger.warning("Sticker %d 生成失败: %s", i + 1, r.get("error", "unknown"))

        logger.info("生图完成: %d/%d 成功", len(paths), len(prompts))
        return paths

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _save(self, filename: str, content: str):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        path.write_text(content, encoding="utf-8")
        logger.debug("已保存: %s", path)

    @staticmethod
    def _emit(callback: ProgressCallback, event: str, data: Dict[str, Any]):
        if callback:
            try:
                callback(event, data)
            except Exception as e:
                logger.warning("Progress callback error: %s", e)


def _sanitize_filename(name: str) -> str:
    """将分类名转为安全的文件名片段。"""
    import re as _re
    clean = _re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)
    clean = _re.sub(r"_+", "_", clean).strip("_")
    return clean[:60] or "category"
