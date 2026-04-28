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
import os
import re
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

    # Default models per task family. Pulled from env so we can switch
    # without touching code.
    # Current routing (after operator switched to JieKou):
    #   text     : gpt-4o         via JIEKOU_*  (chat.completions)
    #   extract  : gpt-4o-mini    via JIEKOU_*  (chat.completions, json_object)
    #   image    : gpt-image-2    via JIEKOU_*  (POST /v1/images/generations)
    #   websearch: gpt-5.4:surfing via AIHUBMIX_* (kept separate per operator
    #              instruction "搜索相关 不切换")
    DEFAULT_TEXT_MODEL = os.getenv("V2_AI_TEXT_MODEL", "gpt-4o")
    DEFAULT_EXTRACT_MODEL = os.getenv("V2_AI_EXTRACT_MODEL", "gpt-4o-mini")
    DEFAULT_IMAGE_MODEL = os.getenv("V2_AI_IMAGE_MODEL", "gpt-image-2")

    def __init__(self) -> None:
        self._openai: Optional[OpenAIService] = None

    # ------------------------------------------------------------------
    # Provider accessors (lazy)
    # ------------------------------------------------------------------

    def _text_service_tag(self) -> str:
        """Return the service label used in ai_call_logs for text/extract calls.

        Reflects whichever provider _get_openai actually picked, so the
        logs show "jiekou" / "aihubmix" / "openai" accurately instead of
        always "openai".
        """
        if os.getenv("JIEKOU_API_KEY"):
            return "jiekou"
        if os.getenv("OPENAI_API_KEY"):
            base = os.getenv("OPENAI_BASE_URL", "")
            if "jiekou" in base:
                return "jiekou"
            if "aihubmix" in base:
                return "aihubmix"
            return "openai"
        if os.getenv("AIHUBMIX_API_KEY"):
            return "aihubmix"
        return "unknown"

    def _get_openai(self, model: Optional[str] = None) -> OpenAIService:
        """Return an OpenAIService pointed at JieKou (or fallback).

        JieKou is the operator-chosen default for all non-search GPT calls
        (text + extract). Search calls keep using AiHubMix and bypass this
        method entirely. Fallback chain:
          JIEKOU_API_KEY  → OPENAI_API_KEY (which today also points at JieKou)
                          → AIHUBMIX_API_KEY (last resort)
        """
        api_key = (os.getenv("JIEKOU_API_KEY")
                   or os.getenv("OPENAI_API_KEY")
                   or os.getenv("AIHUBMIX_API_KEY", ""))
        if os.getenv("JIEKOU_API_KEY"):
            base_url = os.getenv("JIEKOU_BASE_URL", "https://api.jiekou.ai/openai")
        else:
            base_url = (os.getenv("OPENAI_BASE_URL")
                        or os.getenv("AIHUBMIX_BASE_URL"))
            if base_url and base_url.rstrip("/").endswith("chat/completions"):
                base_url = base_url.rstrip("/").rsplit("/", 2)[0]
        resolved_model = model or self.DEFAULT_TEXT_MODEL
        # Cheap re-instantiate if model changed; OpenAIService is light.
        if (self._openai is None
                or self._openai.model != resolved_model
                or self._openai.api_key != api_key):
            self._openai = OpenAIService(
                api_key=api_key, base_url=base_url, model=resolved_model,
            )
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
            service=self._text_service_tag(),
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
                service=self._text_service_tag(),
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
                if provider == "aihubmix_surfing":
                    return provider, self._search_aihubmix_surfing(
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

    # ------------------------------------------------------------------
    # Per-provider search implementations
    # ------------------------------------------------------------------

    def _search_openai(
        self,
        query: str,
        region: str,
        max_results: int,
        related_table: str,
        related_id: Optional[int],
    ) -> list[SearchResult]:
        """OpenAI Responses API + web_search_preview tool.

        Requires the configured OPENAI_BASE_URL to actually forward
        Responses API requests. The middleman at jiekou.ai does NOT
        (verified in W1.7 POC: 404). Kept available for the day a native
        OpenAI key is wired in.
        """
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL") or None
        model = os.getenv("OPENAI_MODEL", "gpt-5.4")
        if not api_key:
            raise _NotConfigured("OPENAI_API_KEY not set", service="openai")

        with AICallLog(
            service="openai",
            model=model,
            task="web_search:openai",
            related_table=related_table,
            related_id=related_id,
            prompt_summary=query[:500],
        ) as log:
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
            resp = client.responses.create(
                model=model,
                input=(
                    f"Search the web for: {query}\n\n"
                    f"Return up to {max_results} fresh sources. For each, give title, "
                    "URL, and a one-line summary. Region preference: " + region
                ),
                tools=[{"type": "web_search_preview"}],
            )
            text = getattr(resp, "output_text", "") or ""
            results: list[SearchResult] = []
            try:
                for item in resp.output or []:
                    for c in getattr(item, "content", None) or []:
                        for a in getattr(c, "annotations", None) or []:
                            url = getattr(a, "url", None)
                            if url:
                                results.append(SearchResult(
                                    title=getattr(a, "title", "") or "",
                                    url=url,
                                ))
            except Exception:
                pass
            if not results:
                for url in re.findall(r"https?://[^\s)\]]+", text):
                    results.append(SearchResult(title="", url=url))
            log.set_usage(cost=estimate_search_cost("openai_web_search"))
            return results[:max_results]

    def _search_aihubmix_surfing(
        self,
        query: str,
        region: str,
        max_results: int,
        related_table: str,
        related_id: Optional[int],
    ) -> list[SearchResult]:
        """AiHubMix's gpt-5.4:surfing model — verified working in W1.7 POC.

        The middleman appends ':surfing' to a model name to enable web
        search. We call it via OpenAI-compatible chat.completions, parse
        URLs out of the freeform response text. Citation titles are
        unreliable (the model writes them in markdown-ish prose) so we
        only commit to the URL field; caller can re-extract titles from
        the snippet text via extract_json if needed.
        """
        from openai import OpenAI

        api_key = os.getenv("AIHUBMIX_API_KEY", "")
        base_url = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1/chat/completions")
        model = os.getenv("AIHUBMIX_MODEL", "gpt-5.4:surfing")
        if not api_key:
            raise _NotConfigured("AIHUBMIX_API_KEY not set", service="aihubmix_surfing")

        # OpenAI SDK base_url should end at /v1, not at /v1/chat/completions.
        if base_url.rstrip("/").endswith("chat/completions"):
            base_url = base_url.rstrip("/").rsplit("/", 2)[0]

        with AICallLog(
            service="aihubmix",
            model=model,
            task="web_search:aihubmix_surfing",
            related_table=related_table,
            related_id=related_id,
            prompt_summary=query[:500],
        ) as log:
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": (
                    f"Search the web for: {query}\n\n"
                    f"Return up to {max_results} fresh sources. For each, give title, "
                    "URL on its own line, and a one-line summary. "
                    f"Region preference: {region}."
                )}],
            )
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            if usage:
                log.set_usage(
                    input_tokens=usage.prompt_tokens or 0,
                    output_tokens=usage.completion_tokens or 0,
                    cost=estimate_text_cost(model, usage.prompt_tokens or 0,
                                            usage.completion_tokens or 0),
                )

            results = self._parse_search_text(text, max_results)
            return results

    def _parse_search_text(self, text: str, max_results: int) -> list[SearchResult]:
        """Best-effort: pull title + URL + snippet out of model freeform text.

        Looks for numbered list items where each item contains a URL.
        Title is whatever precedes 'URL:' or the first line; snippet is
        whatever follows. Handles the format from W1.7 POC verified
        output but tolerant of variation.
        """
        results: list[SearchResult] = []
        # Split into items at numbered list markers like "1.", "2." etc.
        items = re.split(r"\n(?=\s*\d+[\.\)]\s)", text)
        for item in items:
            urls = re.findall(r"https?://[^\s)\]\>]+", item)
            if not urls:
                continue
            url = urls[0]
            # Title: text before the URL on the URL's line, or the first non-empty line.
            title = ""
            snippet = ""
            for line in item.splitlines():
                line_stripped = line.strip().lstrip("*-").strip()
                if not line_stripped:
                    continue
                if not title and url not in line_stripped:
                    title = re.sub(r"^\d+[\.\)]\s*", "", line_stripped)
                    title = re.sub(r"\*+", "", title).strip()
                if "summary" in line_stripped.lower() or line_stripped.lower().startswith("description"):
                    snippet = line_stripped.split(":", 1)[-1].strip()
                    break
            results.append(SearchResult(title=title[:200], url=url, snippet=snippet[:500]))
            if len(results) >= max_results:
                break
        # Fallback: if structured parse found nothing, just collect bare URLs.
        if not results:
            for url in re.findall(r"https?://[^\s)\]]+", text):
                results.append(SearchResult(title="", url=url))
                if len(results) >= max_results:
                    break
        return results

    def _search_tavily(self, *args, **kwargs) -> list[SearchResult]:
        raise _NotConfigured(
            "Tavily provider not yet implemented (TAVILY_API_KEY required).",
            service="tavily",
        )

    def _search_perplexity(self, *args, **kwargs) -> list[SearchResult]:
        raise _NotConfigured(
            "Perplexity provider not yet implemented (PERPLEXITY_API_KEY required).",
            service="perplexity",
        )

    # ------------------------------------------------------------------
    # image_generate / image_edit (POC in W1.8)
    # ------------------------------------------------------------------

    def _image_endpoints(self) -> tuple[str, str, str]:
        """Resolve (api_key, text_to_image_url, image_to_image_url).

        JieKou exposes per-model image endpoints under /v3/, NOT the
        OpenAI-compatible /images/generations route. Operator confirmed:
        text-to-image  = https://api.jiekou.ai/v3/gpt-image-2-text-to-image
        image-to-image = https://api.jiekou.ai/v3/gpt-image-2-image-to-image
        """
        api_key = (os.getenv("JIEKOU_API_KEY")
                   or os.getenv("OPENAI_API_KEY")
                   or os.getenv("AIHUBMIX_API_KEY", ""))
        if not api_key:
            raise _NotConfigured(
                "Image API key not set (JIEKOU_API_KEY / OPENAI_API_KEY / AIHUBMIX_API_KEY)",
                service="image",
            )
        t2i = os.getenv(
            "JIEKOU_IMAGE_T2I_URL",
            "https://api.jiekou.ai/v3/gpt-image-2-text-to-image",
        )
        i2i = os.getenv(
            "JIEKOU_IMAGE_EDIT_URL",
            "https://api.jiekou.ai/v3/gpt-image-2-edit",
        )
        return api_key, t2i, i2i

    @staticmethod
    def _decode_image_response(data: dict) -> list[bytes]:
        """Parse image response in BOTH JieKou shape ({images:[url,...]}) and
        OpenAI shape ({data:[{b64_json|url},...]})."""
        import base64
        import httpx
        out: list[bytes] = []
        # JieKou-documented shape: {"images": ["https://...", ...]}
        if isinstance(data.get("images"), list):
            for entry in data["images"]:
                if isinstance(entry, str) and entry.startswith("http"):
                    out.append(httpx.get(entry, timeout=120).content)
                elif isinstance(entry, dict):
                    if entry.get("b64_json"):
                        out.append(base64.b64decode(entry["b64_json"]))
                    elif entry.get("url"):
                        out.append(httpx.get(entry["url"], timeout=120).content)
        # OpenAI-compatible shape: {"data": [{"b64_json":..., "url":...}]}
        elif isinstance(data.get("data"), list):
            for item in data["data"]:
                if item.get("b64_json"):
                    out.append(base64.b64decode(item["b64_json"]))
                elif item.get("url"):
                    out.append(httpx.get(item["url"], timeout=120).content)
        return out

    def image_generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        n: int = 1,
        size: str = "1024x1024",
        quality: Optional[str] = None,
        background: str = "auto",
        output_format: Optional[str] = None,
        task: str = "image_generate",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> list[bytes]:
        """Generate ``n`` images via JieKou's GPT Image 2 endpoint.

        Per https://docs.jiekou.ai/, the endpoint accepts:
          n, size (1024x1024 / 1024x1536 / 1536x1024 / auto),
          prompt, quality (low/medium/high), background (transparent/opaque/auto),
          moderation (low/auto), output_format (png/jpeg), output_compression.

        Returns a list of raw image bytes. Caller should retry on transient
        connection errors.
        """
        import httpx
        model = model or self.DEFAULT_IMAGE_MODEL
        api_key, t2i_url, _ = self._image_endpoints()
        body: dict[str, Any] = {
            "prompt": prompt,
            "n": n,
            "size": size,
            "quality": quality or os.getenv("V2_IMAGE_QUALITY", "medium"),
            "background": background,
            "output_format": output_format or os.getenv("V2_IMAGE_OUTPUT_FORMAT", "png"),
        }

        with AICallLog(
            service="jiekou",
            model=model,
            task=task,
            related_table=related_table,
            related_id=related_id,
            prompt_summary=prompt[:500],
        ) as log:
            try:
                resp = httpx.post(
                    t2i_url,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json=body,
                    timeout=300,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                hint = ""
                if e.response.status_code == 403 and "invalid character" in e.response.text:
                    hint = (" — JieKou forwarded an HTML reject page from upstream. "
                            "Common cause: account lacks GPT-Image permission.")
                raise APIError(
                    f"image_generate HTTP {e.response.status_code}: "
                    f"{e.response.text[:300]}{hint}",
                    service=model,
                )
            data = resp.json()
            log.set_usage(cost=estimate_image_cost(model, n))
            results = self._decode_image_response(data)
            if not results:
                raise APIError(
                    f"image_generate: response had no decodable images. body={str(data)[:300]}",
                    service=model,
                )
            return results

    def image_edit(
        self,
        source_image: bytes,
        prompt: str,
        *,
        model: Optional[str] = None,
        size: str = "1024x1024",
        quality: Optional[str] = None,
        task: str = "image_edit",
        related_table: str = "",
        related_id: Optional[int] = None,
    ) -> bytes:
        """Edit / re-render an image based on a prompt (image-to-image).

        Calls JieKou's standard ``POST /v1/images/edits`` endpoint with
        multipart/form-data per the OpenAI-compatible images.edit shape.
        Used by A.3 to extract a single sticker from a sheet preview.
        """
        import base64
        import httpx
        model = model or self.DEFAULT_IMAGE_MODEL
        api_key, _, i2i_url = self._image_endpoints()

        # Send source as base64-encoded data URL — common JieKou /v3
        # image-to-image convention. If the operator's endpoint expects
        # multipart instead we'll add a fallback once probed.
        b64 = base64.b64encode(source_image).decode("ascii")
        body: dict[str, Any] = {
            "image": f"data:image/png;base64,{b64}",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality or os.getenv("V2_IMAGE_QUALITY", "medium"),
            "output_format": os.getenv("V2_IMAGE_OUTPUT_FORMAT", "png"),
        }

        with AICallLog(
            service="jiekou",
            model=model,
            task=task,
            related_table=related_table,
            related_id=related_id,
            prompt_summary=prompt[:500],
        ) as log:
            try:
                resp = httpx.post(
                    i2i_url,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json=body,
                    timeout=300,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                hint = ""
                if e.response.status_code == 403 and "invalid character" in e.response.text:
                    hint = " — JieKou upstream returned HTML reject; likely account perm."
                raise APIError(
                    f"image_edit HTTP {e.response.status_code}: "
                    f"{e.response.text[:300]}{hint}",
                    service=model,
                )
            data = resp.json()
            log.set_usage(cost=estimate_image_cost(model, 1))
            results = self._decode_image_response(data)
            if not results:
                raise APIError(
                    f"image_edit: response had no decodable images. body={str(data)[:300]}",
                    service=model,
                )
            return results[0]


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
