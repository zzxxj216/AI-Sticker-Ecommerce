"""批量生成规划器

负责：
1. 将用户主题拆分为 6 个卡包方向 + 风格分配
2. 为每个卡包规划多话题 + 凑齐 50-60 张
3. 为每个话题生成贴纸概念
"""

from typing import Any, Dict, List, Optional

from src.core.logger import get_logger
from src.services.ai.claude_service import ClaudeService

from src.models.generation import StickerConcept
from src.models.batch import (
    BatchConfig,
    BatchPackConfig,
    TopicGroup,
)
from src.services.batch.batch_prompts import (
    build_six_pack_planner_prompt,
    build_topic_planner_prompt,
    build_topic_concepts_prompt,
    build_topic_ideas_prompt,
)

logger = get_logger("service.batch_planner")


class BatchPlanner:
    """规划 6 卡包的方向、风格、话题和概念。"""

    def __init__(self, claude_service: Optional[ClaudeService] = None):
        self.claude = claude_service or ClaudeService()

    # ------------------------------------------------------------------
    # Step 1: 规划 6 个卡包方向 + 风格
    # ------------------------------------------------------------------

    def plan_packs(
        self,
        theme: str,
        pack_count: int = 6,
        target_per_pack: int = 55,
        stickers_per_topic: int = 8,
        user_style: Optional[str] = None,
        user_color_mood: Optional[str] = None,
        user_extra: str = "",
    ) -> BatchConfig:
        """规划 6 卡包方向和风格。

        Returns:
            BatchConfig with pack_configs populated (directions + styles),
            but topics not yet filled.
        """
        logger.info("Planning %d packs for theme: %s", pack_count, theme)

        prompt = build_six_pack_planner_prompt(
            theme=theme,
            pack_count=pack_count,
            user_style=user_style,
            user_color_mood=user_color_mood,
            user_extra=user_extra,
        )

        raw = self.claude.generate_json(prompt=prompt, max_tokens=2000, temperature=0.8)
        packs_data = raw.get("packs", [])

        if not packs_data:
            logger.error("Pack planning returned empty result")
            raise ValueError("Pack planning failed: no packs returned")

        from datetime import datetime
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        pack_configs = []
        for p in packs_data[:pack_count]:
            pc = BatchPackConfig(
                pack_index=p.get("pack_index", len(pack_configs) + 1),
                pack_name=p.get("pack_name", f"Pack {len(pack_configs) + 1}"),
                theme=theme,
                direction=p.get("direction", ""),
                visual_style=p.get("visual_style", "flat vector illustration"),
                color_mood=p.get("color_mood", "vibrant"),
                target_count=target_per_pack,
            )
            pack_configs.append(pc)

        batch_config = BatchConfig(
            batch_id=batch_id,
            user_theme=theme,
            user_style=user_style,
            user_color_mood=user_color_mood,
            user_extra=user_extra,
            pack_count=pack_count,
            target_per_pack=target_per_pack,
            stickers_per_topic=stickers_per_topic,
            pack_configs=pack_configs,
        )

        logger.info(
            "Pack planning done — %d packs: %s",
            len(pack_configs),
            [pc.pack_name for pc in pack_configs],
        )

        return batch_config

    # ------------------------------------------------------------------
    # Step 2: 为单个卡包规划话题
    # ------------------------------------------------------------------

    def plan_topics_for_pack(
        self,
        pack_config: BatchPackConfig,
        stickers_per_topic: int = 8,
    ) -> List[TopicGroup]:
        """为单个卡包规划话题列表。"""
        logger.info(
            "Planning topics for pack %d (%s)",
            pack_config.pack_index, pack_config.pack_name,
        )

        prompt = build_topic_planner_prompt(
            theme=pack_config.theme,
            direction=pack_config.direction,
            visual_style=pack_config.visual_style,
            target_count=pack_config.target_count,
            per_topic=stickers_per_topic,
        )

        raw = self.claude.generate_json(prompt=prompt, max_tokens=2000, temperature=0.8)
        topics_data = raw.get("topics", [])

        if not topics_data:
            logger.warning("Topic planning returned empty for pack %d", pack_config.pack_index)
            return []

        topic_groups = []
        for t in topics_data:
            tg = TopicGroup(
                topic_id=t.get("topic_id", f"topic_{len(topic_groups)+1:02d}"),
                topic_name=t.get("topic_name", f"Topic {len(topic_groups)+1}"),
                description=t.get("description", ""),
                target_count=t.get("sticker_count", stickers_per_topic),
            )
            topic_groups.append(tg)

        total = sum(tg.target_count for tg in topic_groups)
        logger.info(
            "Topics planned for pack %d — %d topics, %d total stickers",
            pack_config.pack_index, len(topic_groups), total,
        )

        return topic_groups

    # ------------------------------------------------------------------
    # Step 3: 为单个话题生成贴纸概念
    # ------------------------------------------------------------------

    def generate_concepts_for_topic(
        self,
        pack_config: BatchPackConfig,
        topic: TopicGroup,
    ) -> List[StickerConcept]:
        """为一个话题生成贴纸概念。"""
        logger.info(
            "Generating concepts for pack %d, topic '%s' (%d stickers)",
            pack_config.pack_index, topic.topic_name, topic.target_count,
        )

        prompt = build_topic_concepts_prompt(
            theme=pack_config.theme,
            direction=pack_config.direction,
            visual_style=pack_config.visual_style,
            color_mood=pack_config.color_mood,
            topic_name=topic.topic_name,
            topic_description=topic.description,
            sticker_count=topic.target_count,
        )

        raw = self.claude.generate_json(prompt=prompt, max_tokens=2000, temperature=0.8)
        stickers_data = raw.get("stickers", [])

        concepts = [
            StickerConcept(
                index=i + 1,
                description=s.get("description", ""),
                text_overlay=s.get("text_overlay", ""),
                sticker_type=s.get("sticker_type", "combined"),
            )
            for i, s in enumerate(stickers_data)
        ]

        logger.info(
            "Generated %d concepts for topic '%s'",
            len(concepts), topic.topic_name,
        )

        return concepts

    # ------------------------------------------------------------------
    # Step 4: 为单个话题的概念生成 image prompts
    # ------------------------------------------------------------------

    def generate_ideas_for_topic(
        self,
        topic: TopicGroup,
        style_guide: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """将话题内的概念转换为 image prompt ideas。"""
        if not topic.concepts:
            logger.warning("No concepts for topic '%s', skipping", topic.topic_name)
            return []

        concepts_dicts = [c.to_dict() for c in topic.concepts]

        prompt = build_topic_ideas_prompt(
            topic_name=topic.topic_name,
            concepts=concepts_dicts,
            style_guide=style_guide,
        )

        ideas = self.claude.generate_json(prompt=prompt, max_tokens=4000, temperature=0.8)

        if not isinstance(ideas, list):
            logger.error("Expected list from ideas conversion, got %s", type(ideas).__name__)
            return []

        for i, idea in enumerate(ideas):
            idea.setdefault("index", i + 1)
            idea.setdefault("title", f"Sticker {i + 1}")
            idea["type"] = "combined"
            idea.setdefault("image_prompt", "")
            idea.setdefault("concept", "")
            idea["topic_id"] = topic.topic_id
            idea["topic_name"] = topic.topic_name

        logger.info(
            "Generated %d ideas for topic '%s'",
            len(ideas), topic.topic_name,
        )

        return ideas
