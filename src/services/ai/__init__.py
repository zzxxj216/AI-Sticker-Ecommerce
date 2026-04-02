"""AI service module — unified interface to LLM providers."""

from src.services.ai.base import BaseLLMService, try_parse_json
from src.services.ai.claude_service import ClaudeService
from src.services.ai.gemini_service import GeminiService
from src.services.ai.chat_service import ChatService
from src.services.ai.prompt_builder import (
    PromptBuilder,
    # v3 topic generation
    build_topic_generation_prompt,
    # v2 pipeline
    build_pack_style_guide_prompt,
    build_text_sticker_prompt,
    build_element_sticker_prompt,
    build_combined_sticker_prompt,
    # theme content
    build_theme_content_prompt,
    # preview prompt
    build_preview_prompt_via_claude,
    build_preview_prompt_direct,
    # interactive session config → image prompts
    build_style_guide_from_config_prompt,
    build_concepts_to_image_prompts,
    # legacy
    build_sticker_pack_prompt,
    build_style_analysis_prompt,
    build_variant_generation_prompt,
)

__all__ = [
    "ClaudeService",
    "GeminiService",
    "ChatService",
    "PromptBuilder",
    # v3 topic generation
    "build_topic_generation_prompt",
    # v2 pipeline
    "build_pack_style_guide_prompt",
    "build_text_sticker_prompt",
    "build_element_sticker_prompt",
    "build_combined_sticker_prompt",
    # theme content
    "build_theme_content_prompt",
    # preview prompt
    "build_preview_prompt_via_claude",
    "build_preview_prompt_direct",
    # interactive session config → image prompts
    "build_style_guide_from_config_prompt",
    "build_concepts_to_image_prompts",
    # legacy
    "build_sticker_pack_prompt",
    "build_style_analysis_prompt",
    "build_variant_generation_prompt",
]
