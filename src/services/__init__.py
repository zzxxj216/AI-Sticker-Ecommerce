"""Business services module.

AI Services:
    - BaseLLMService  — abstract base class for all LLM wrappers
    - ClaudeService   — Anthropic Claude API
    - GeminiService   — Google Gemini API (text, image, multimodal)
    - OpenAIService   — OpenAI / compatible API
    - ChatService     — multi-turn chat session manager
    - PromptBuilder   — prompt construction helpers
"""

from src.services.ai import (
    BaseLLMService,
    ClaudeService,
    GeminiService,
    PromptBuilder,
)
from src.services.ai.openai_service import OpenAIService
from src.services.ops.trend_service import BriefService, TrendService

__all__ = [
    "BaseLLMService",
    "ClaudeService",
    "GeminiService",
    "OpenAIService",
    "PromptBuilder",
    "TrendService",
    "BriefService",
]
