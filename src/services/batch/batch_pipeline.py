"""批量生成管线 —— 6 卡包并行编排器

流程:
  用户主题 → plan_packs → 6 × (plan_topics → concepts → style_guide → ideas → preview) → images
  每步结果可保存/打印
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import GenerationError
from src.services.ai.claude_service import ClaudeService
from src.services.ai.gemini_service import GeminiService
from src.services.sticker.pack_generator import PackGenerator

from src.models.batch import (
    BatchConfig,
    BatchPackConfig,
    BatchPackResult,
    BatchPackStatus,
    BatchResult,
    TopicGroup,
    TopicPreviewResult,
)
from src.services.batch.batch_planner import BatchPlanner
from src.services.batch.batch_prompts import build_topic_preview_meta_prompt
from src.utils import save_json

logger = get_logger("service.batch_pipeline")

ProgressCallback = Callable[[str, Dict[str, Any]], None]


class BatchPipeline:
    """6 卡包并行生成管线。

    每步产生的中间结果可通过 callback 实时打印或保存到磁盘。
    """

    def __init__(
        self,
        claude_service: Optional[ClaudeService] = None,
        gemini_service: Optional[GeminiService] = None,
        output_dir: Optional[Path] = None,
        max_pack_workers: int = 6,
        max_image_workers: int = 3,
    ):
        self.claude = claude_service or ClaudeService()
        self.gemini = gemini_service or GeminiService()
        self.output_dir = output_dir or Path(config.output_dir) / "batch_packs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.max_pack_workers = max_pack_workers
        self.max_image_workers = max_image_workers

        self._planner = BatchPlanner(claude_service=self.claude)
        self._pack_gen = PackGenerator(
            claude_service=self.claude,
            gemini_service=self.gemini,
            output_dir=self.output_dir,
        )

        self._batch_config: Optional[BatchConfig] = None
        self._batch_result: Optional[BatchResult] = None

        logger.info("BatchPipeline initialized (pack_workers=%d)", max_pack_workers)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def batch_config(self) -> Optional[BatchConfig]:
        return self._batch_config

    @property
    def batch_result(self) -> Optional[BatchResult]:
        return self._batch_result

    # ------------------------------------------------------------------
    # Step 1: 规划
    # ------------------------------------------------------------------

    def plan(
        self,
        theme: str,
        pack_count: int = 6,
        target_per_pack: int = 55,
        stickers_per_topic: int = 8,
        user_style: Optional[str] = None,
        user_color_mood: Optional[str] = None,
        user_extra: str = "",
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchConfig:
        """Step 1: 规划 6 包方向 + 风格。"""
        self._emit(on_progress, "plan_start", {"theme": theme, "pack_count": pack_count})

        batch_config = self._planner.plan_packs(
            theme=theme,
            pack_count=pack_count,
            target_per_pack=target_per_pack,
            stickers_per_topic=stickers_per_topic,
            user_style=user_style,
            user_color_mood=user_color_mood,
            user_extra=user_extra,
        )

        self._batch_config = batch_config

        self._batch_result = BatchResult(
            batch_id=batch_config.batch_id,
            user_theme=theme,
            pack_results=[
                BatchPackResult(
                    pack_index=pc.pack_index,
                    pack_name=pc.pack_name,
                    direction=pc.direction,
                    visual_style=pc.visual_style,
                )
                for pc in batch_config.pack_configs
            ],
        )

        self._emit(on_progress, "plan_done", {
            "batch_id": batch_config.batch_id,
            "packs": [
                {
                    "index": pc.pack_index,
                    "name": pc.pack_name,
                    "direction": pc.direction,
                    "style": pc.visual_style,
                    "color_mood": pc.color_mood,
                }
                for pc in batch_config.pack_configs
            ],
        })

        self._save_step("01_plan", batch_config.model_dump(mode="json"))
        return batch_config

    # ------------------------------------------------------------------
    # Step 2: 话题规划 + 概念生成 (并行 6 包)
    # ------------------------------------------------------------------

    def generate_content(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchConfig:
        """Step 2: 为每个卡包规划话题 + 生成概念（包间并行 + 包内话题并行）。"""
        if not self._batch_config:
            raise ValueError("Must call plan() first")

        bc = self._batch_config
        self._emit(on_progress, "content_start", {"pack_count": len(bc.pack_configs)})

        def _gen_concepts_for_topic(pc, topic):
            concepts = self._planner.generate_concepts_for_topic(pc, topic)
            topic.concepts = concepts
            return topic

        def _process_pack(pc: BatchPackConfig) -> BatchPackConfig:
            pack_result = self._get_pack_result(pc.pack_index)
            pack_result.mark_started()
            pack_result.status = BatchPackStatus.GENERATING_CONTENT

            try:
                topics = self._planner.plan_topics_for_pack(
                    pc, stickers_per_topic=bc.stickers_per_topic,
                )

                with ThreadPoolExecutor(max_workers=min(len(topics), 4)) as topic_executor:
                    topic_futures = {
                        topic_executor.submit(_gen_concepts_for_topic, pc, topic): topic
                        for topic in topics
                    }
                    for future in as_completed(topic_futures):
                        try:
                            future.result()
                        except Exception as e:
                            t = topic_futures[future]
                            logger.error("Concept gen failed for topic '%s': %s", t.topic_name, e)

                pc.topics = topics
                pack_result.topics = topics

                self._emit(on_progress, "pack_content_done", {
                    "pack_index": pc.pack_index,
                    "pack_name": pc.pack_name,
                    "topic_count": len(topics),
                    "total_concepts": sum(len(t.concepts) for t in topics),
                })

            except Exception as e:
                logger.error("Content generation failed for pack %d: %s", pc.pack_index, e)
                pack_result.mark_failed(str(e))
                self._emit(on_progress, "pack_content_failed", {
                    "pack_index": pc.pack_index,
                    "error": str(e),
                })

            return pc

        with ThreadPoolExecutor(max_workers=self.max_pack_workers) as executor:
            futures = {
                executor.submit(_process_pack, pc): pc.pack_index
                for pc in bc.pack_configs
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    idx = futures[future]
                    logger.error("Pack %d content thread failed: %s", idx, e)

        self._emit(on_progress, "content_done", {
            "packs": [
                {
                    "index": pc.pack_index,
                    "topics": len(pc.topics),
                    "concepts": pc.total_concept_count,
                }
                for pc in bc.pack_configs
            ],
        })

        self._save_step("02_content", bc.model_dump(mode="json"))
        return bc

    # ------------------------------------------------------------------
    # Step 3: Style guide + Ideas (并行 6 包)
    # ------------------------------------------------------------------

    def generate_ideas(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchConfig:
        """Step 3: 为每个卡包生成 style guide + 按话题并行生成 image prompts。"""
        if not self._batch_config:
            raise ValueError("Must call plan() and generate_content() first")

        bc = self._batch_config
        self._emit(on_progress, "ideas_start", {"pack_count": len(bc.pack_configs)})

        def _gen_ideas_for_topic(topic, style_guide, pack_index):
            ideas = self._planner.generate_ideas_for_topic(topic, style_guide)
            topic.ideas = ideas
            self._emit(on_progress, "topic_ideas_done", {
                "pack_index": pack_index,
                "topic": topic.topic_name,
                "idea_count": len(ideas),
            })
            return topic

        def _process_pack(pc: BatchPackConfig) -> BatchPackConfig:
            pack_result = self._get_pack_result(pc.pack_index)

            if pack_result.status == BatchPackStatus.FAILED:
                return pc

            pack_result.status = BatchPackStatus.STYLE_GUIDE

            try:
                style_guide = self._generate_style_guide(pc)
                pc.style_guide = style_guide
                pack_result.style_guide = style_guide

                self._emit(on_progress, "pack_style_done", {
                    "pack_index": pc.pack_index,
                    "art_style": style_guide.get("art_style", "N/A"),
                })

                with ThreadPoolExecutor(max_workers=min(len(pc.topics), 4)) as topic_executor:
                    topic_futures = {
                        topic_executor.submit(
                            _gen_ideas_for_topic, topic, style_guide, pc.pack_index
                        ): topic
                        for topic in pc.topics
                    }
                    for future in as_completed(topic_futures):
                        try:
                            future.result()
                        except Exception as e:
                            t = topic_futures[future]
                            logger.error("Ideas gen failed for topic '%s': %s", t.topic_name, e)

                pack_result.topics = pc.topics

            except Exception as e:
                logger.error("Ideas generation failed for pack %d: %s", pc.pack_index, e)
                pack_result.mark_failed(str(e))

            return pc

        with ThreadPoolExecutor(max_workers=self.max_pack_workers) as executor:
            futures = {
                executor.submit(_process_pack, pc): pc.pack_index
                for pc in bc.pack_configs
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    idx = futures[future]
                    logger.error("Pack %d ideas thread failed: %s", idx, e)

        self._emit(on_progress, "ideas_done", {
            "packs": [
                {
                    "index": pc.pack_index,
                    "ideas": pc.total_idea_count,
                }
                for pc in bc.pack_configs
            ],
        })

        self._save_step("03_ideas", bc.model_dump(mode="json"))
        return bc

    # ------------------------------------------------------------------
    # Step 4: 话题预览图生成 (并行 6 包)
    # ------------------------------------------------------------------

    def generate_previews(
        self,
        use_claude_prompt: bool = True,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[TopicPreviewResult]:
        """Step 4: 为每个话题并行生成话题总图（包间 + 包内话题均并行）。"""
        if not self._batch_config:
            raise ValueError("Must call generate_ideas() first")

        bc = self._batch_config
        all_previews: List[TopicPreviewResult] = []

        self._emit(on_progress, "previews_start", {
            "total_topics": sum(len(pc.topics) for pc in bc.pack_configs),
        })

        def _preview_one_topic(pc, topic):
            preview = self._generate_topic_preview(
                pc, topic, use_claude_prompt=use_claude_prompt,
            )
            topic.preview_prompt = preview.preview_prompt
            topic.preview_image_path = preview.preview_image_path
            topic.preview_success = preview.success

            self._emit(on_progress, "topic_preview_done", {
                "pack_index": pc.pack_index,
                "topic": topic.topic_name,
                "success": preview.success,
                "image_path": preview.preview_image_path,
            })
            return preview

        def _preview_pack(pc: BatchPackConfig) -> List[TopicPreviewResult]:
            pack_result = self._get_pack_result(pc.pack_index)
            if pack_result.status == BatchPackStatus.FAILED:
                return []

            pack_result.status = BatchPackStatus.GENERATING_PREVIEWS

            topics_with_ideas = [t for t in pc.topics if t.ideas]
            previews = []

            with ThreadPoolExecutor(max_workers=min(len(topics_with_ideas), 3)) as topic_executor:
                topic_futures = {
                    topic_executor.submit(_preview_one_topic, pc, topic): topic
                    for topic in topics_with_ideas
                }
                for future in as_completed(topic_futures):
                    try:
                        preview = future.result()
                        previews.append(preview)
                    except Exception as e:
                        t = topic_futures[future]
                        logger.error("Preview failed for topic '%s': %s", t.topic_name, e)

            pack_result.topic_previews = previews
            return previews

        with ThreadPoolExecutor(max_workers=self.max_pack_workers) as executor:
            futures = {
                executor.submit(_preview_pack, pc): pc.pack_index
                for pc in bc.pack_configs
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    all_previews.extend(result)
                except Exception as e:
                    idx = futures[future]
                    logger.error("Pack %d preview thread failed: %s", idx, e)

        self._emit(on_progress, "previews_done", {
            "total_previews": len(all_previews),
            "success": sum(1 for p in all_previews if p.success),
        })

        self._save_step("04_previews", {
            "previews": [p.model_dump(mode="json") for p in all_previews],
        })

        return all_previews

    # ------------------------------------------------------------------
    # Step 5: 全量单张图片生成 (并行 6 包)
    # ------------------------------------------------------------------

    def generate_images(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchResult:
        """Step 5: 为每个卡包生成全部单张图片（6 包并行）。"""
        if not self._batch_config or not self._batch_result:
            raise ValueError("Must call generate_ideas() first")

        bc = self._batch_config
        br = self._batch_result

        self._emit(on_progress, "images_start", {"pack_count": len(bc.pack_configs)})

        def _gen_pack_images(pc: BatchPackConfig) -> None:
            pack_result = self._get_pack_result(pc.pack_index)
            if pack_result.status == BatchPackStatus.FAILED:
                return

            pack_result.status = BatchPackStatus.GENERATING_IMAGES

            all_ideas = []
            for topic in pc.topics:
                for idea in topic.ideas:
                    all_ideas.append(idea)

            for i, idea in enumerate(all_ideas, 1):
                idea["index"] = i

            if not all_ideas:
                pack_result.mark_failed("No ideas to generate images from")
                return

            try:
                stickers = self._pack_gen._generate_images(
                    ideas=all_ideas,
                    pack_id=f"{bc.batch_id}_pack{pc.pack_index:02d}",
                    max_workers=self.max_image_workers,
                )

                pack_result.sticker_count = len(stickers)
                pack_result.sticker_success_count = sum(
                    1 for s in stickers if s.status == "success"
                )
                pack_result.mark_completed()

                self._emit(on_progress, "pack_images_done", {
                    "pack_index": pc.pack_index,
                    "total": len(stickers),
                    "success": pack_result.sticker_success_count,
                })

            except Exception as e:
                logger.error("Image gen failed for pack %d: %s", pc.pack_index, e)
                pack_result.mark_failed(str(e))

        with ThreadPoolExecutor(max_workers=self.max_pack_workers) as executor:
            futures = {
                executor.submit(_gen_pack_images, pc): pc.pack_index
                for pc in bc.pack_configs
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    idx = futures[future]
                    logger.error("Pack %d image thread failed: %s", idx, e)

        br.mark_completed()

        self._emit(on_progress, "images_done", br.to_summary())
        self._save_step("05_result", br.model_dump(mode="json"))

        return br

    # ------------------------------------------------------------------
    # 一键执行全部步骤
    # ------------------------------------------------------------------

    def run_full_pipeline(
        self,
        theme: str,
        pack_count: int = 6,
        target_per_pack: int = 55,
        stickers_per_topic: int = 8,
        user_style: Optional[str] = None,
        user_color_mood: Optional[str] = None,
        user_extra: str = "",
        use_claude_prompt: bool = True,
        skip_images: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchResult:
        """执行完整管线: plan → (content → ideas → previews) per pack → images。

        使用流水线模式：每个包独立走完 steps 2-4，不必等所有包完成某一步。

        Args:
            skip_images: 若 True，跳过最终单张图片生成（仅到预览阶段）
        """
        self.plan(
            theme=theme,
            pack_count=pack_count,
            target_per_pack=target_per_pack,
            stickers_per_topic=stickers_per_topic,
            user_style=user_style,
            user_color_mood=user_color_mood,
            user_extra=user_extra,
            on_progress=on_progress,
        )

        self._run_pipelined_steps(
            use_claude_prompt=use_claude_prompt,
            on_progress=on_progress,
        )

        if not skip_images:
            self.generate_images(on_progress=on_progress)

        return self._batch_result

    def _run_pipelined_steps(
        self,
        use_claude_prompt: bool = True,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        """每个包独立流水线执行 content → ideas → previews，包间并行。"""
        bc = self._batch_config
        if not bc:
            return

        self._emit(on_progress, "content_start", {"pack_count": len(bc.pack_configs)})

        def _run_pack_pipeline(pc: BatchPackConfig) -> None:
            pack_result = self._get_pack_result(pc.pack_index)
            pack_result.mark_started()

            # --- Step 2: content ---
            pack_result.status = BatchPackStatus.GENERATING_CONTENT
            try:
                topics = self._planner.plan_topics_for_pack(
                    pc, stickers_per_topic=bc.stickers_per_topic,
                )

                with ThreadPoolExecutor(max_workers=min(len(topics), 4)) as te:
                    futs = {
                        te.submit(self._planner.generate_concepts_for_topic, pc, t): t
                        for t in topics
                    }
                    for f in as_completed(futs):
                        t = futs[f]
                        try:
                            t.concepts = f.result()
                        except Exception as e:
                            logger.error("Concept gen failed for topic '%s': %s", t.topic_name, e)

                pc.topics = topics
                pack_result.topics = topics

                self._emit(on_progress, "pack_content_done", {
                    "pack_index": pc.pack_index,
                    "pack_name": pc.pack_name,
                    "topic_count": len(topics),
                    "total_concepts": sum(len(t.concepts) for t in topics),
                })

            except Exception as e:
                logger.error("Content gen failed for pack %d: %s", pc.pack_index, e)
                pack_result.mark_failed(str(e))
                return

            # --- Step 3: ideas ---
            pack_result.status = BatchPackStatus.STYLE_GUIDE
            try:
                style_guide = self._generate_style_guide(pc)
                pc.style_guide = style_guide
                pack_result.style_guide = style_guide

                self._emit(on_progress, "pack_style_done", {
                    "pack_index": pc.pack_index,
                    "art_style": style_guide.get("art_style", "N/A"),
                })

                with ThreadPoolExecutor(max_workers=min(len(pc.topics), 4)) as te:
                    futs = {
                        te.submit(self._planner.generate_ideas_for_topic, t, style_guide): t
                        for t in pc.topics
                    }
                    for f in as_completed(futs):
                        t = futs[f]
                        try:
                            t.ideas = f.result()
                            self._emit(on_progress, "topic_ideas_done", {
                                "pack_index": pc.pack_index,
                                "topic": t.topic_name,
                                "idea_count": len(t.ideas),
                            })
                        except Exception as e:
                            logger.error("Ideas gen failed for topic '%s': %s", t.topic_name, e)

                pack_result.topics = pc.topics

            except Exception as e:
                logger.error("Ideas gen failed for pack %d: %s", pc.pack_index, e)
                pack_result.mark_failed(str(e))
                return

            # --- Step 4: previews ---
            pack_result.status = BatchPackStatus.GENERATING_PREVIEWS
            topics_with_ideas = [t for t in pc.topics if t.ideas]
            previews = []

            with ThreadPoolExecutor(max_workers=min(len(topics_with_ideas), 3)) as te:
                futs = {}
                for t in topics_with_ideas:
                    futs[te.submit(
                        self._generate_topic_preview, pc, t,
                        use_claude_prompt,
                    )] = t

                for f in as_completed(futs):
                    t = futs[f]
                    try:
                        preview = f.result()
                        previews.append(preview)
                        t.preview_prompt = preview.preview_prompt
                        t.preview_image_path = preview.preview_image_path
                        t.preview_success = preview.success

                        self._emit(on_progress, "topic_preview_done", {
                            "pack_index": pc.pack_index,
                            "topic": t.topic_name,
                            "success": preview.success,
                            "image_path": preview.preview_image_path,
                        })
                    except Exception as e:
                        logger.error("Preview failed for topic '%s': %s", t.topic_name, e)

            pack_result.topic_previews = previews

        with ThreadPoolExecutor(max_workers=self.max_pack_workers) as executor:
            futures = {
                executor.submit(_run_pack_pipeline, pc): pc.pack_index
                for pc in bc.pack_configs
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    idx = futures[future]
                    logger.error("Pack %d pipeline failed: %s", idx, e)

        self._emit(on_progress, "content_done", {
            "packs": [
                {
                    "index": pc.pack_index,
                    "topics": len(pc.topics),
                    "concepts": pc.total_concept_count,
                }
                for pc in bc.pack_configs
            ],
        })
        self._emit(on_progress, "ideas_done", {
            "packs": [
                {"index": pc.pack_index, "ideas": pc.total_idea_count}
                for pc in bc.pack_configs
            ],
        })

        all_previews = []
        for pc in bc.pack_configs:
            pr = self._get_pack_result(pc.pack_index)
            all_previews.extend(pr.topic_previews or [])

        self._emit(on_progress, "previews_done", {
            "total_previews": len(all_previews),
            "success": sum(1 for p in all_previews if p.success),
        })

        self._save_step("02_content", bc.model_dump(mode="json"))
        self._save_step("03_ideas", bc.model_dump(mode="json"))
        self._save_step("04_previews", {
            "previews": [p.model_dump(mode="json") for p in all_previews],
        })

    # ------------------------------------------------------------------
    # 单张重生成
    # ------------------------------------------------------------------

    def regenerate_sticker(
        self,
        pack_index: int,
        topic_identifier: str,
        sticker_index_in_topic: int,
        new_prompt: Optional[str] = None,
        modification_intent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按话题 + 序号定位并重生成单张贴纸。

        Args:
            pack_index: 卡包序号 (1-based)
            topic_identifier: 话题名或话题序号 (字符串)
            sticker_index_in_topic: 话题内序号 (1-based)
            new_prompt: 直接替换的新 prompt
            modification_intent: 修改意图（由 AI 转为新 prompt）

        Returns:
            Dict with keys: success, new_prompt, image_path, error
        """
        if not self._batch_config:
            return {"success": False, "error": "No active batch"}

        pack_config = self._find_pack(pack_index)
        if not pack_config:
            return {"success": False, "error": f"Pack {pack_index} not found"}

        topic = self._find_topic(pack_config, topic_identifier)
        if not topic:
            return {"success": False, "error": f"Topic '{topic_identifier}' not found in pack {pack_index}"}

        if sticker_index_in_topic < 1 or sticker_index_in_topic > len(topic.ideas):
            return {"success": False, "error": f"Sticker index {sticker_index_in_topic} out of range (1-{len(topic.ideas)})"}

        idea = topic.ideas[sticker_index_in_topic - 1]
        current_prompt = idea.get("image_prompt", "")

        if new_prompt:
            final_prompt = new_prompt
        elif modification_intent and pack_config.style_guide:
            from src.services.batch.batch_prompts import build_prompt_modifier
            modifier_prompt = build_prompt_modifier(
                current_prompt=current_prompt,
                modification_intent=modification_intent,
                style_guide=pack_config.style_guide,
            )
            result = self.claude.generate(
                prompt=modifier_prompt, max_tokens=500, temperature=0.7,
            )
            final_prompt = result["text"].strip()
        else:
            final_prompt = current_prompt

        idea["image_prompt"] = final_prompt

        try:
            today = datetime.now().strftime("%Y%m%d")
            regen_dir = Path(config.output_dir) / "images" / today / "regen"
            regen_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            output_path = regen_dir / f"regen_p{pack_index}_t{topic.topic_id}_s{sticker_index_in_topic}_{ts}.png"

            img_result = self.gemini.generate_image(
                prompt=final_prompt, output_path=output_path,
            )

            if img_result["success"]:
                return {
                    "success": True,
                    "new_prompt": final_prompt,
                    "image_path": str(output_path),
                }
            else:
                return {
                    "success": False,
                    "new_prompt": final_prompt,
                    "error": img_result.get("error", "Image generation failed"),
                }

        except Exception as e:
            return {"success": False, "new_prompt": final_prompt, "error": str(e)}

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_sticker_info(
        self,
        pack_index: int,
        topic_identifier: str,
        sticker_index_in_topic: int,
    ) -> Optional[Dict[str, Any]]:
        """按话题 + 序号查询单张贴纸信息。"""
        if not self._batch_config:
            return None

        pack_config = self._find_pack(pack_index)
        if not pack_config:
            return None

        topic = self._find_topic(pack_config, topic_identifier)
        if not topic:
            return None

        if sticker_index_in_topic < 1 or sticker_index_in_topic > len(topic.ideas):
            return None

        idea = topic.ideas[sticker_index_in_topic - 1]
        return {
            "pack_index": pack_index,
            "pack_name": pack_config.pack_name,
            "topic_id": topic.topic_id,
            "topic_name": topic.topic_name,
            "sticker_index": sticker_index_in_topic,
            "image_prompt": idea.get("image_prompt", ""),
            "title": idea.get("title", ""),
            "concept": idea.get("concept", ""),
            "text_overlay": idea.get("text_overlay", ""),
        }

    def format_batch_summary(self) -> str:
        """格式化批次摘要为可读文本。"""
        if not self._batch_config:
            return "(no active batch)"

        bc = self._batch_config
        lines = [
            f"=== Batch: {bc.batch_id} ===",
            f"Theme: {bc.user_theme}",
            f"Packs: {len(bc.pack_configs)}",
            "",
        ]

        for pc in bc.pack_configs:
            lines.append(f"--- Pack {pc.pack_index}: {pc.pack_name} ---")
            lines.append(f"  Direction: {pc.direction}")
            lines.append(f"  Style: {pc.visual_style}")
            lines.append(f"  Color mood: {pc.color_mood}")

            if pc.topics:
                lines.append(f"  Topics ({len(pc.topics)}):")
                for t in pc.topics:
                    concept_count = len(t.concepts)
                    idea_count = len(t.ideas)
                    preview = "✓" if t.preview_success else "✗" if t.preview_success is False else "—"
                    lines.append(
                        f"    [{t.topic_id}] {t.topic_name}: "
                        f"{concept_count} concepts, {idea_count} ideas, preview {preview}"
                    )
            lines.append("")

        if self._batch_result:
            br = self._batch_result
            lines.append(f"Result: {br.completed_packs} completed, {br.failed_packs} failed")
            if br.duration_seconds:
                lines.append(f"Duration: {br.duration_seconds:.1f}s")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_pack(self, pack_index: int) -> Optional[BatchPackConfig]:
        for pc in self._batch_config.pack_configs:
            if pc.pack_index == pack_index:
                return pc
        return None

    def _find_topic(
        self, pack_config: BatchPackConfig, identifier: str
    ) -> Optional[TopicGroup]:
        try:
            idx = int(identifier)
            if 1 <= idx <= len(pack_config.topics):
                return pack_config.topics[idx - 1]
        except ValueError:
            pass

        for topic in pack_config.topics:
            if topic.topic_name.lower() == identifier.lower():
                return topic
            if topic.topic_id == identifier:
                return topic

        for topic in pack_config.topics:
            if identifier.lower() in topic.topic_name.lower():
                return topic

        return None

    def _get_pack_result(self, pack_index: int) -> BatchPackResult:
        for pr in self._batch_result.pack_results:
            if pr.pack_index == pack_index:
                return pr
        raise ValueError(f"No result entry for pack {pack_index}")

    def _generate_style_guide(self, pc: BatchPackConfig) -> Dict[str, Any]:
        """生成单个卡包的 style guide（批量模式：强调主题一致而非视觉克隆）。"""
        from src.services.batch.batch_prompts import build_batch_style_guide_prompt
        prompt = build_batch_style_guide_prompt(
            theme=pc.theme,
            direction=pc.direction,
            visual_style=pc.visual_style,
            color_mood=pc.color_mood,
        )
        guide = self.claude.generate_json(prompt=prompt, temperature=0.7)

        if not isinstance(guide, dict):
            raise GenerationError("Style guide response is not a JSON object", stage="style_guide")

        guide.setdefault("art_style", "flat vector illustration")
        guide.setdefault("color_palette", {})
        guide.setdefault("line_style", "")
        guide.setdefault("mood", "")
        guide.setdefault("typography_style", "")
        guide.setdefault("visual_consistency_rules", [])

        return guide

    def _generate_topic_preview(
        self,
        pack_config: BatchPackConfig,
        topic: TopicGroup,
        use_claude_prompt: bool = True,
    ) -> TopicPreviewResult:
        """为单个话题生成话题总图。"""
        result = TopicPreviewResult(
            pack_index=pack_config.pack_index,
            topic_id=topic.topic_id,
            topic_name=topic.topic_name,
        )

        try:
            if use_claude_prompt:
                meta_prompt = build_topic_preview_meta_prompt(
                    topic_name=topic.topic_name,
                    pack_name=pack_config.pack_name,
                    sticker_ideas=topic.ideas,
                    style_guide=pack_config.style_guide or {},
                )
                claude_result = self.claude.generate(
                    prompt=meta_prompt, max_tokens=2000, temperature=0.7,
                )
                preview_prompt = claude_result["text"].strip()
            else:
                preview_prompt = self._pack_gen.generate_preview_prompt(
                    pack_name=f"{pack_config.pack_name} - {topic.topic_name}",
                    sticker_ideas=topic.ideas,
                    style_guide=pack_config.style_guide or {},
                    use_claude=False,
                )

            result.preview_prompt = preview_prompt

            today = datetime.now().strftime("%Y%m%d")
            bc = self._batch_config
            preview_dir = (
                Path(config.output_dir) / "images" / today
                / bc.batch_id / f"pack{pack_config.pack_index:02d}" / "previews"
            )
            preview_dir.mkdir(parents=True, exist_ok=True)

            from src.utils.text_utils import sanitize_filename
            safe_topic = sanitize_filename(topic.topic_name)
            output_path = preview_dir / f"preview_{topic.topic_id}_{safe_topic}.png"

            img_result = self.gemini.generate_image(
                prompt=preview_prompt, output_path=output_path,
            )

            if img_result["success"]:
                result.preview_image_path = str(output_path)
                result.success = True
            else:
                result.error = img_result.get("error", "Image generation failed")

        except Exception as e:
            logger.error(
                "Preview failed for pack %d topic '%s': %s",
                pack_config.pack_index, topic.topic_name, e,
            )
            result.error = str(e)

        return result

    def _emit(
        self,
        callback: Optional[ProgressCallback],
        event: str,
        data: Dict[str, Any],
    ) -> None:
        logger.info("[%s] %s", event, json.dumps(data, ensure_ascii=False, default=str)[:500])
        if callback:
            try:
                callback(event, data)
            except Exception as e:
                logger.warning("Progress callback error: %s", e)

    def _save_step(self, step_name: str, data: Any) -> None:
        if not self._batch_config:
            return
        step_dir = self.output_dir / self._batch_config.batch_id
        step_dir.mkdir(parents=True, exist_ok=True)
        filepath = step_dir / f"{step_name}.json"
        save_json(data, str(filepath))
        logger.info("Step saved: %s", filepath)
