"""Gemini AI service wrapper.

Based on google-genai SDK, provides a unified interface supporting
text generation, JSON output, image generation, multimodal input,
batch generation, auto-retry, and error handling.
"""

import base64
import json
import mimetypes
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List

from google import genai
from google.genai import types as genai_types
from google.genai.types import GenerateContentConfig, Modality, Part

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import APIError, TimeoutError, RateLimitError
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES
from src.services.ai.base import BaseLLMService

logger = get_logger("service.gemini")

_GLOBAL_IMAGE_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "6"))
_global_image_semaphore = threading.Semaphore(_GLOBAL_IMAGE_CONCURRENCY)


class GeminiService(BaseLLMService):
    """Gemini AI Service (based on google-genai SDK)"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        resolved_key = api_key or config.gemini_api_key
        resolved_url = (base_url or config.gemini_base_url).rstrip("/")
        resolved_model = model or config.gemini_model

        if not resolved_key:
            raise APIError(
                "Gemini API Key not configured",
                service="gemini",
                status_code=401,
            )

        super().__init__(
            api_key=resolved_key,
            base_url=resolved_url,
            model=resolved_model,
            timeout=timeout,
            max_retries=max_retries,
        )

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url and self.base_url != "https://generativelanguage.googleapis.com":
            client_kwargs["http_options"] = genai_types.HttpOptions(
                base_url=self.base_url,
            )

        self.client = genai.Client(**client_kwargs)

        logger.info("Gemini service initialized - model: %s", self.model)
        logger.info("Gemini endpoint: %s", self.base_url)

    # ================================================================
    # Text generation
    # ================================================================

    def generate(
        self,
        prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate text

        Args:
            prompt: Prompt
            max_tokens: Max tokens
            temperature: Temperature (0-1)
            system: System prompt

        Returns:
            Dict: {
                "text": str,
                "usage": {"input_tokens": int, "output_tokens": int},
                "cost": float,
                "model": str
            }
        """
        try:
            cfg = GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
                response_modalities=[Modality.TEXT],
            )
            if system:
                cfg.system_instruction = system

            logger.debug(f"Calling Gemini API - model: {self.model}, max_tokens: {max_tokens}")

            response = self._call_with_retry(prompt, config=cfg)
            text = self._extract_text(response)

            if not text:
                raise APIError("No text content found in response", service="gemini")

            usage = self._get_usage(response, prompt, text)
            cost = self._calculate_cost(usage)

            logger.info(
                f"Gemini text generation complete - "
                f"input: {usage['input_tokens']} tokens, "
                f"output: {usage['output_tokens']} tokens, "
                f"cost: ${cost:.4f}"
            )
            logger.debug(f"Response text preview: {text[:200]}")

            return {"text": text, "usage": usage, "cost": cost, "model": self.model}

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Gemini text generation failed: {e}")
            raise APIError(f"Gemini service error: {e}", service="gemini")

    # generate_json() is inherited from BaseLLMService

    # ================================================================
    # Image generation
    # ================================================================

    # Suffix appended to every image-generation prompt to enforce a
    # clean white background, making downstream background removal trivial.
    _BG_CONSTRAINT = (
        "\n\nCRITICAL BACKGROUND REQUIREMENT: "
        "The image MUST have a perfectly plain, pure white (#FFFFFF) background. "
        "Do NOT render any environment, scene, surface, table, texture, shadow on "
        "the ground, bokeh, gradient, decorative elements, or any objects behind "
        "the subject. The subject must be completely isolated — floating on a "
        "flat white void with absolutely nothing else visible."
    )

    def generate_image(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        output_path: Optional[Path] = None,
        *,
        enforce_white_bg: bool = True,
    ) -> Dict[str, Any]:
        """Generate image

        Args:
            prompt: Image generation prompt
            reference_image: Reference image (local path/URL/base64)
            output_path: Output path (optional)
            enforce_white_bg: Append a strict white-background constraint
                to the prompt (default True).  Set False for special use
                cases like blog hero images that intentionally need a scene.

        Returns:
            Dict: {
                "success": bool,
                "image_path": str,
                "image_data": str (base64),
                "size_kb": int,
                "elapsed": float,
                "error": str (if failed)
            }
        """
        start_time = time.time()

        if enforce_white_bg:
            prompt = prompt + self._BG_CONSTRAINT

        try:
            contents: list = []
            if reference_image:
                ref_part = self._load_reference_as_part(reference_image)
                if ref_part:
                    contents.append(ref_part)
                    logger.debug(f"Loaded reference image: {reference_image[:50]}...")
            contents.append(prompt)

            cfg = GenerateContentConfig(
                response_modalities=[Modality.IMAGE, Modality.TEXT],
            )

            response = self._call_with_retry(contents, config=cfg)
            image_bytes = self._extract_image_bytes(response)

            if not image_bytes:
                raise APIError("No image data found in response", service="gemini")

            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            if output_path:
                output_path = Path(output_path)
                self._save_image_bytes(image_bytes, output_path)
                size_kb = output_path.stat().st_size // 1024
                image_path = str(output_path.resolve())
            else:
                size_kb = len(image_bytes) // 1024
                image_path = None

            elapsed = time.time() - start_time
            logger.info(f"Image generation success - size: {size_kb} KB, elapsed: {elapsed:.1f}s")

            return {
                "success": True,
                "image_path": image_path,
                "image_data": image_b64,
                "size_kb": size_kb,
                "elapsed": elapsed,
                "error": None,
            }

        except APIError:
            raise
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Image generation failed: {e}")
            return {
                "success": False,
                "image_path": None,
                "image_data": None,
                "size_kb": 0,
                "elapsed": elapsed,
                "error": str(e),
            }

    # ================================================================
    # Image analysis (multimodal)
    # ================================================================

    def analyze_image(
        self,
        image_data: str,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Analyze image

        Args:
            image_data: Base64 encoded image data
            prompt: Analysis prompt
            media_type: Image MIME type
            max_tokens: Max tokens

        Returns:
            Dict: {"text": str, "usage": dict, "cost": float, "model": str}
        """
        try:
            image_bytes = base64.b64decode(image_data)
            image_part = Part.from_bytes(data=image_bytes, mime_type=media_type)

            cfg = GenerateContentConfig(
                max_output_tokens=max_tokens,
                response_modalities=[Modality.TEXT],
            )

            logger.debug(f"Calling Gemini API for image analysis - model: {self.model}")

            response = self._call_with_retry([image_part, prompt], config=cfg)
            text = self._extract_text(response)

            if not text:
                raise APIError("No text content found in response", service="gemini")

            usage = self._get_usage(response, prompt, text)
            cost = self._calculate_cost(usage)

            logger.info(
                f"Gemini image analysis complete - "
                f"input: {usage['input_tokens']} tokens, "
                f"output: {usage['output_tokens']} tokens, "
                f"cost: ${cost:.4f}"
            )
            logger.debug(f"Response text preview: {text[:200]}")

            return {"text": text, "usage": usage, "cost": cost, "model": self.model}

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Gemini image analysis failed: {e}")
            raise APIError(f"Gemini service error: {e}", service="gemini")

    # ================================================================
    # Batch generation
    # ================================================================

    def generate_batch(
        self,
        prompts: List[str],
        reference_image: Optional[str] = None,
        output_dir: Optional[Path] = None,
        max_workers: int = 3,
    ) -> List[Dict[str, Any]]:
        """Batch generate images

        Args:
            prompts: List of prompts
            reference_image: Reference image (applies to all prompts)
            output_dir: Output directory
            max_workers: Max workers

        Returns:
            List[Dict]: List of generation results
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not prompts:
            logger.warning("Prompt list is empty")
            return []

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        total = len(prompts)
        logger.info(f"Starting batch generation of {total} images (workers: {max_workers})")

        results_dict: Dict[int, Dict[str, Any]] = {}

        def _gen_one(idx: int, prompt: str) -> tuple:
            output_path = None
            if output_dir:
                from src.utils.text_utils import sanitize_filename
                from datetime import datetime

                safe_name = sanitize_filename(prompt[:30])
                filename = f"image_{idx:02d}_{safe_name}_{datetime.now().strftime('%H%M%S')}.png"
                output_path = output_dir / filename

            with _global_image_semaphore:
                logger.debug(f"({idx}/{total}) Generating: {prompt[:50]}...")
                result = self.generate_image(prompt, reference_image, output_path)
            result["index"] = idx
            result["prompt"] = prompt
            return idx, result

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_gen_one, i, prompt): i
                for i, prompt in enumerate(prompts, 1)
            }

            for future in as_completed(futures):
                idx, result = future.result()
                results_dict[idx] = result

        results = [results_dict[i] for i in sorted(results_dict)]
        success_count = sum(1 for r in results if r["success"])

        logger.info(f"Batch generation done - success: {success_count}/{total}")

        return results

    # get_model_info() is inherited from BaseLLMService

    # ================================================================
    # Internal methods
    # ================================================================

    def _call_with_retry(
        self,
        contents,
        config: Optional[GenerateContentConfig] = None,
    ):
        """API call with retry

        Args:
            contents: Request content (string or Part list)
            config: Generation config

        Returns:
            SDK response object

        Raises:
            APIError: Raised after all retries fail
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                return response

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                if "resource" in error_str and "exhausted" in error_str or "429" in error_str:
                    wait_s = attempt * 5
                    logger.warning(
                        f"Rate limited, waiting {wait_s}s before retry ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait_s)
                elif "deadline" in error_str or "timeout" in error_str:
                    logger.warning(f"Request timeout, retrying ({attempt}/{self.max_retries})")
                    time.sleep(3)
                elif attempt < self.max_retries:
                    wait_s = attempt * 2
                    is_ssl = "ssl" in error_str or "eof" in error_str
                    log_fn = logger.debug if is_ssl else logger.warning
                    log_fn(
                        f"API error: {e}, waiting {wait_s}s before retry ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait_s)
                else:
                    break

        raise APIError(
            f"Gemini API request failed (retried {self.max_retries} times): {last_error}",
            service="gemini",
        )

    def _extract_text(self, response) -> Optional[str]:
        """Extract text from SDK response"""
        try:
            if hasattr(response, "text") and response.text:
                return response.text
        except (ValueError, AttributeError):
            pass

        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
        except (IndexError, AttributeError):
            pass

        return None

    def _extract_image_bytes(self, response) -> Optional[bytes]:
        """Extract raw image bytes from SDK response"""
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    return part.inline_data.data
        except (IndexError, AttributeError):
            pass
        return None

    def _load_reference_as_part(self, source: str) -> Optional[Part]:
        """Load reference image and convert to SDK Part object

        Args:
            source: Image source (local path/URL/base64 data URL)

        Returns:
            Part object or None
        """
        if not source:
            return None

        try:
            if source.startswith("data:image"):
                match = re.match(r"data:(image/[^;]+);base64,(.+)", source)
                if match:
                    mime_type = match.group(1)
                    data = base64.b64decode(match.group(2))
                    return Part.from_bytes(data=data, mime_type=mime_type)
                return None

            if source.startswith("http://") or source.startswith("https://"):
                req = urllib.request.Request(source, headers={"User-Agent": "GeminiService/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    content_type = resp.headers.get("Content-Type", "image/png")
                    mime = content_type.split(";")[0].strip()
                logger.debug(f"Loaded URL reference image ({len(data) // 1024} KB)")
                return Part.from_bytes(data=data, mime_type=mime)

            path = Path(source)
            if path.exists():
                raw = path.read_bytes()
                mime, _ = mimetypes.guess_type(str(path))
                mime = mime or "image/png"
                logger.debug(f"Loaded local reference image {path.name} ({len(raw) // 1024} KB)")
                return Part.from_bytes(data=raw, mime_type=mime)

            logger.warning(f"Reference image path not found: {source}")
            return None

        except Exception as e:
            logger.error(f"Failed to load reference image: {e}")
            return None

    @staticmethod
    def _save_image_bytes(image_bytes: bytes, output_path: Path) -> None:
        """Save image bytes to file"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)

    def _get_usage(self, response, prompt_text, response_text) -> Dict[str, int]:
        """Get token usage (prefers real values from SDK response)"""
        try:
            meta = getattr(response, "usage_metadata", None)
            if meta:
                input_tokens = getattr(meta, "prompt_token_count", 0) or 0
                output_tokens = getattr(meta, "candidates_token_count", 0) or 0
                if input_tokens > 0 or output_tokens > 0:
                    return {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
        except Exception:
            pass

        return {
            "input_tokens": len(str(prompt_text)) // 4 + 100,
            "output_tokens": len(str(response_text)) // 4,
        }

    @staticmethod
    def _calculate_cost(usage: Dict[str, int]) -> float:
        """Calculate cost (Gemini Flash pricing: input $0.075/M, output $0.3/M)"""
        return (
            usage["input_tokens"] * 0.075 + usage["output_tokens"] * 0.3
        ) / 1_000_000

    # _try_parse_json is available as src.services.ai.base.try_parse_json
