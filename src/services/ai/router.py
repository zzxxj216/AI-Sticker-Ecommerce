"""Unified AI Router for the V2 pipeline.

A thin layer on top of provider services (OpenAIService / GeminiService /
ClaudeService / Tavily / Perplexity) that:

1. Records every call in ``ai_call_logs`` via ``AICallLog``
2. Implements the **two-step generation** pattern: a creative model
   produces free-form text, a cheap extraction model converts that text
   into structured JSON matching a schema.
3. Provides a ``web_search`` facade that fans out to multiple providers
   in parallel (POC will be done in W1.7; for now OpenAI-only path is
   wired and Tavily / Perplexity raise NotConfigured).
4. Exposes ``image_generate`` and ``image_edit`` for the gpt-image-2
   path used by A.3 preview generation and sticker splitting (POC in
   W1.8).

Methods are intentionally synchronous + simple; the W2/W3 callers can
parallelize via threads / asyncio at the call site.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.config import config
from src.core.exceptions import APIError
from src.core.logger import get_logger
from src.services.ai.base import try_parse_json
from src.services.ai.call_logger import AICallLog
from src.services.ai.cost import (
    estimate_image_cost,
    estimate_search_cost,
    estimate_text_cost,
)
from src.services.ai.openai_service import OpenAIService

logger = get_logger("service.ai.router")


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------

@dataclass
class SearchResult:
    """Single hit from a web_search provider."""
    title: str
    url: str
    snippet: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebSearchResponse:
    """Aggregated multi-provider web_search response."""
    by_provider: dict[str, list[SearchResult]]
    errors: dict[str, str]  # provider -> error message


# ----------------------------------------------------------------------
# Provider stubs (filled in during W1.7 / W1.8 POCs)
# ----------------------------------------------------------------------

class _NotConfigured(APIError):
    """Provider is referenced but its API key / config is missing."""


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------

class AIRouter:
    """Single entry point for all AI calls in the V2 pipeline.

    Provider instances are lazy: created on first use, cached afterwards.
    Logging always succeeds (failures are logged but never raise) so the
    actual call's success/failure is preserved upstream.
    """

    # Default models per task family. Override at call site if needed.
    DEFAULT_TEXT_MODEL = "gpt-5.4-pro"
    DEFAULT_EXTRACT_MODEL = "gpt-4o-mini"
    DEFAULT_IMAGE_MODEL = "gpt-image-2"

    def __init__(self) -> None:
        self._openai: Optional[OpenAIService] = None

    # ------------------------------------------------------------------
    # Provider accessors (lazy)
    # ------------------------------------------------------------------

    def _get_openai(self, model: Optional[str] = None) -> OpenAIService:
        """Return an OpenAIService bound to ``model`` (or the default).

        We instantiate per-model rather than caching a single client because
        OpenAIService stores the model on the instance.
        """
        if self._openai is None or (model and self._openai.model != model):
            self._openai = OpenAIService(model=model) if model else OpenAIService()
        return self._openai

    # ------------------------------------------------------------------
    # text_complete (free-form, used as the "main" step in 2-step gen)
    # ------------------------------------------------------------------

    def text_complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: int = 64000,
        temperature: float = 0.7,
        task: str = "text_complete",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> str:
        """Free-form text completion. Returns the raw text string.

        Used as the **main** step in two-step generation: the creative model
        writes naturally (markdown, sections, lists) without being forced
        into JSON. The output is then handed to ``extract_json`` to
        produce structured data.
        """
        model = model or self.DEFAULT_TEXT_MODEL
        client = self._get_openai(model)

        with AICallLog(
            service="openai",
            model=model,
            task=task,
            related_table=related_table,
            related_id=related_id,
            prompt_summary=prompt[:500],
        ) as log:
            result = client.generate(
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            usage = result.get("usage") or {}
            log.set_usage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost=estimate_text_cost(
                    model,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                ),
            )
            return result["text"]

    # ------------------------------------------------------------------
    # extract_json (the "extract" step in 2-step gen)
    # ------------------------------------------------------------------

    def extract_json(
        self,
        source_text: str,
        schema: dict,
        *,
        model: Optional[str] = None,
        instructions: str = "",
        max_retries: int = 1,
        task: str = "extract_json",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> dict:
        """Convert free-form ``source_text`` into structured JSON.

        ``schema`` is a dict describing the expected shape (used as a
        contract in the prompt; we don't strictly validate via jsonschema
        to keep dependencies light — the consumer should validate as
        needed).

        Retries up to ``max_retries`` extra times on JSON parse failure.
        On terminal failure, raises ``APIError`` so the caller can
        fall back to the raw source_text.
        """
        model = model or self.DEFAULT_EXTRACT_MODEL
        client = self._get_openai(model)

        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
        system = (
            "You are a strict JSON extraction tool. Read the input text and "
            "produce a single valid JSON object that matches the schema. "
            "Do not invent fields. If a field has no clear value, use a "
            "sensible default (empty string, empty list, 0). "
            "Output ONLY the JSON, no prose, no code fences."
        )
        if instructions:
            system += "\n\nAdditional instructions:\n" + instructions
        prompt = (
            f"SCHEMA:\n{schema_str}\n\n"
            f"INPUT TEXT:\n{source_text}\n\n"
            "Return the JSON now."
        )

        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            with AICallLog(
                service="openai",
                model=model,
                task=task if attempt == 0 else f"{task}:retry{attempt}",
                related_table=related_table,
                related_id=related_id,
                prompt_summary=f"extract -> {schema_str[:200]}",
            ) as log:
                result = client.generate(
                    prompt=prompt,
                    system=system,
                    max_tokens=8000,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                usage = result.get("usage") or {}
                log.set_usage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cost=estimate_text_cost(
                        model,
                        usage.get("input_tokens", 0),
                        usage.get("output_tokens", 0),
                    ),
                )

            parsed = try_parse_json(result["text"])
            if parsed is not None and isinstance(parsed, dict):
                return parsed
            last_error = APIError(
                f"extract_json: model {model} returned unparseable output "
                f"(attempt {attempt + 1}/{max_retries + 1})",
                service="openai",
                response=result["text"][:500],
            )
            logger.warning(str(last_error))

        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------
    # web_search (POC in W1.7 — currently OpenAI path only)
    # ------------------------------------------------------------------

    def web_search(
        self,
        query: str,
        providers: list[str],
        *,
        region: str = "US",
        max_results: int = 10,
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> WebSearchResponse:
        """Fan out a web search to multiple providers in parallel.

        Implementations are wired in W1.7 POC. For now this is a stub
        that returns NotConfigured for any provider.
        """
        by_provider: dict[str, list[SearchResult]] = {}
        errors: dict[str, str] = {}

        def _call_one(provider: str) -> tuple[str, list[SearchResult] | str]:
            try:
                if provider == "openai":
                    return provider, self._search_openai(
                        query, region, max_results, related_table, related_id
                    )
                if provider == "tavily":
                    return provider, self._search_tavily(
                        query, region, max_results, related_table, related_id
                    )
                if provider == "perplexity":
                    return provider, self._search_perplexity(
                        query, region, max_results, related_table, related_id
                    )
                return provider, f"unknown provider: {provider}"
            except _NotConfigured as e:
                return provider, f"not_configured: {e}"
            except Exception as e:
                return provider, f"error: {e}"

        with ThreadPoolExecutor(max_workers=len(providers) or 1) as ex:
            futures = [ex.submit(_call_one, p) for p in providers]
            for fut in as_completed(futures):
                provider, result = fut.result()
                if isinstance(result, list):
                    by_provider[provider] = result
                else:
                    errors[provider] = result

        return WebSearchResponse(by_provider=by_provider, errors=errors)

    # Per-provider implementations are stubs filled in by W1.7 POC.
    def _search_openai(self, *args, **kwargs) -> list[SearchResult]:
        raise _NotConfigured(
            "OpenAI web_search not yet implemented (planned in W1.7 POC).",
            service="openai_web_search",
        )

    def _search_tavily(self, *args, **kwargs) -> list[SearchResult]:
        raise _NotConfigured(
            "Tavily provider not yet implemented (planned in W1.7 POC).",
            service="tavily",
        )

    def _search_perplexity(self, *args, **kwargs) -> list[SearchResult]:
        raise _NotConfigured(
            "Perplexity provider not yet implemented (planned in W1.7 POC).",
            service="perplexity",
        )

    # ------------------------------------------------------------------
    # image_generate / image_edit (POC in W1.8)
    # ------------------------------------------------------------------

    def image_generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        n: int = 1,
        size: str = "1024x1024",
        task: str = "image_generate",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> list[bytes]:
        """Generate ``n`` images from a text prompt. Returns raw PNG bytes.

        Implementation wired in W1.8 POC. Currently raises NotConfigured.
        """
        raise _NotConfigured(
            "image_generate not yet implemented (planned in W1.8 POC).",
            service="openai_image",
        )

    def image_edit(
        self,
        source_image: bytes,
        prompt: str,
        *,
        model: Optional[str] = None,
        size: str = "1024x1024",
        task: str = "image_edit",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> bytes:
        """Edit / re-render an image based on a prompt (image-to-image).

        Used by A.3 to extract a single sticker from a 10-sticker preview.
        Wired in W1.8 POC.
        """
        raise _NotConfigured(
            "image_edit not yet implemented (planned in W1.8 POC).",
            service="openai_image",
        )


# ----------------------------------------------------------------------
# Module-level singleton (most callers don't need their own instance)
# ----------------------------------------------------------------------

_router: Optional[AIRouter] = None


def get_router() -> AIRouter:
    """Return the process-wide AIRouter singleton."""
    global _router
    if _router is None:
        _router = AIRouter()
    return _router
