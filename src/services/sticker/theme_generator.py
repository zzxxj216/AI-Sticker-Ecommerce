"""Theme Content Generator

Expands a user-provided theme into rich, trending, English-language
content that can later drive sticker design decisions.

Pipeline:
    theme (any language) -> Claude LLM -> structured ThemeContent
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import GenerationError, ValidationError
from src.services.ai import ClaudeService, build_theme_content_prompt, build_topic_generation_prompt
from src.utils import validate_theme

logger = get_logger("service.theme_generator")


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class TopicIdea:
    """A single topic derived from a theme, with use cases and style info."""

    name: str
    description: str
    use_cases: List[str] = field(default_factory=list)
    style_type: str = "flat vector illustration"
    keywords: List[str] = field(default_factory=list)


@dataclass
class TopicGenerationResult:
    """Result of topic generation from a theme."""

    theme_original: str
    theme_english: str
    topics: List[TopicIdea] = field(default_factory=list)
    generation_time_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Theme: {self.theme_english}",
            f"Topics: {len(self.topics)}",
        ]
        for i, t in enumerate(self.topics, 1):
            lines.append(f"  {i}. {t.name} — style: {t.style_type}, uses: {len(t.use_cases)}")
        lines.append(f"Generated in {self.generation_time_seconds:.1f}s")
        return "\n".join(lines)


@dataclass
class TrendingTopic:
    name: str
    category: str
    description: str
    popularity: str = "medium"
    hashtags: List[str] = field(default_factory=list)


@dataclass
class SlangMeme:
    text: str
    meaning: str
    origin: str = ""


@dataclass
class StickerPhrase:
    text: str
    emotion: str
    use_case: str = ""


@dataclass
class ColorMood:
    mood: str
    colors: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ThemeContent:
    """Structured output of theme expansion."""

    theme_original: str  #主题来源
    theme_english: str   #主题英文
    theme_description: str  #主题描述
    trending_topics: List[TrendingTopic] = field(default_factory=list) #话题趋势
    keywords: List[str] = field(default_factory=list)  #关键词
    slang_and_memes: List[SlangMeme] = field(default_factory=list) #俚语和梗
    sticker_phrases: List[StickerPhrase] = field(default_factory=list) #贴纸短语
    color_moods: List[ColorMood] = field(default_factory=list)
    generation_time_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Theme: {self.theme_english}",
            f"Description: {self.theme_description}",
            f"Trending topics: {len(self.trending_topics)}",
            f"Keywords: {len(self.keywords)}",
            f"Slang / memes: {len(self.slang_and_memes)}",
            f"Sticker phrases: {len(self.sticker_phrases)}",
            f"Color moods: {len(self.color_moods)}",
            f"Generated in {self.generation_time_seconds:.1f}s",
        ]
        return "\n".join(lines)


# ------------------------------------------------------------------
# Generator
# ------------------------------------------------------------------

class ThemeContentGenerator:
    """Generates trending English-language content for a given theme."""

    def __init__(self, claude_service: Optional[ClaudeService] = None):
        self.claude = claude_service or ClaudeService()
        logger.info("ThemeContentGenerator initialized")

    # ------------------------------------------------------------------
    # Step 0: Topic generation (v3 pipeline)
    # ------------------------------------------------------------------

    def generate_topics(
        self,
        theme: str,
        *,
        max_topics: int = 6,
        temperature: float = 0.9,
    ) -> TopicGenerationResult:
        """Generate topic ideas from a theme.

        Given a broad theme, produces several focused topics with
        use cases and recommended style types.

        Args:
            theme: User-provided theme in any language.
            max_topics: How many topic ideas to generate.
            temperature: LLM creativity (0-1).

        Returns:
            TopicGenerationResult containing the list of TopicIdea.

        Raises:
            ValidationError: If theme is invalid.
            GenerationError: If the LLM call or parsing fails.
        """
        theme = validate_theme(theme)
        logger.info(f"Generating topics for theme: {theme}")

        start = time.time()

        prompt = build_topic_generation_prompt(theme=theme, max_topics=max_topics)

        try:
            raw: Dict[str, Any] = self.claude.generate_json(
                prompt=prompt,
                max_tokens=4000,
                temperature=temperature,
            )
        except Exception as exc:
            logger.error(f"Topic generation failed: {exc}")
            raise GenerationError(
                f"Topic generation failed: {exc}",
                stage="topic_generation",
            )

        elapsed = time.time() - start

        result = self._parse_topics(theme, raw, elapsed)

        logger.info(
            f"Topic generation complete in {elapsed:.1f}s — "
            f"{len(result.topics)} topics generated"
        )

        return result

    @staticmethod
    def _parse_topics(
        original_theme: str,
        raw: Dict[str, Any],
        elapsed: float,
    ) -> TopicGenerationResult:
        """Parse the raw JSON dict from Claude into a TopicGenerationResult."""
        topics = [
            TopicIdea(
                name=t.get("name", ""),
                description=t.get("description", ""),
                use_cases=t.get("use_cases", []),
                style_type=t.get("style_type", "flat vector illustration"),
                keywords=t.get("keywords", []),
            )
            for t in raw.get("topics", [])
        ]

        return TopicGenerationResult(
            theme_original=original_theme,
            theme_english=raw.get("theme_english", original_theme),
            topics=topics,
            generation_time_seconds=round(elapsed, 2),
        )

    # ------------------------------------------------------------------
    # Theme content expansion (v2 pipeline)
    # ------------------------------------------------------------------

    def generate(
        self,
        theme: str,
        *,
        max_topics: int = 12,
        max_keywords: int = 20,
        max_phrases: int = 15,
        temperature: float = 0.9,
    ) -> ThemeContent:
        """Expand *theme* into structured English content.

        Args:
            theme: User-provided theme in any language.
            max_topics: How many trending topics to request.
            max_keywords: How many keywords to request.
            max_phrases: How many sticker-ready phrases to request.
            temperature: LLM creativity (0-1). Higher = more creative.

        Returns:
            ThemeContent with all fields populated.

        Raises:
            ValidationError: If theme is invalid.
            GenerationError: If the LLM call or parsing fails.
        """
        theme = validate_theme(theme)
        logger.info(f"Generating theme content for: {theme}")

        start = time.time()

        prompt = build_theme_content_prompt(
            theme=theme,
            max_topics=max_topics,
            max_keywords=max_keywords,
            max_phrases=max_phrases,
        )

        try:
            raw: Dict[str, Any] = self.claude.generate_json(
                prompt=prompt,
                max_tokens=6000,
                temperature=temperature,
            )
        except Exception as exc:
            logger.error(f"Theme content generation failed: {exc}")
            raise GenerationError(
                f"Theme content generation failed: {exc}",
                stage="theme_content",
            )

        elapsed = time.time() - start

        content = self._parse(theme, raw, elapsed)

        logger.info(
            f"Theme content generated in {elapsed:.1f}s — "
            f"{len(content.trending_topics)} topics, "
            f"{len(content.keywords)} keywords, "
            f"{len(content.sticker_phrases)} phrases"
        )

        return content

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(
        original_theme: str,
        raw: Dict[str, Any],
        elapsed: float,
    ) -> ThemeContent:
        """Parse the raw JSON dict from Claude into a ThemeContent."""
        trending = [
            TrendingTopic(
                name=t.get("name", ""),
                category=t.get("category", "other"),
                description=t.get("description", ""),
                popularity=t.get("popularity", "medium"),
                hashtags=t.get("hashtags", []),
            )
            for t in raw.get("trending_topics", [])
        ]

        slang = [
            SlangMeme(
                text=s.get("text", ""),
                meaning=s.get("meaning", ""),
                origin=s.get("origin", ""),
            )
            for s in raw.get("slang_and_memes", [])
        ]

        phrases = [
            StickerPhrase(
                text=p.get("text", ""),
                emotion=p.get("emotion", ""),
                use_case=p.get("use_case", ""),
            )
            for p in raw.get("sticker_phrases", [])
        ]

        moods = [
            ColorMood(
                mood=m.get("mood", ""),
                colors=m.get("colors", []),
                description=m.get("description", ""),
            )
            for m in raw.get("color_moods", [])
        ]

        return ThemeContent(
            theme_original=original_theme,
            theme_english=raw.get("theme_english", original_theme),
            theme_description=raw.get("theme_description", ""),
            trending_topics=trending,
            keywords=raw.get("keywords", []),
            slang_and_memes=slang,
            sticker_phrases=phrases,
            color_moods=moods,
            generation_time_seconds=round(elapsed, 2),
        )
