"""Base class and shared utilities for AI service wrappers.

All LLM services (Claude, Gemini, OpenAI) share:
- A unified response dict: {"text", "usage", "cost", "model"}
- JSON extraction / repair logic
- Common __init__ parameters (api_key, base_url, model, timeout, max_retries)
- get_model_info()
"""

import json
import re
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

from src.core.logger import get_logger
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES

logger = get_logger("service.ai.base")


class BaseLLMService(ABC):
    """Abstract base for all LLM service wrappers.

    Subclasses must implement ``generate()`` and ``_extract_result()``.
    JSON parsing, repair, and ``generate_json()`` are provided for free.
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        model: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        system: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text completion.

        Returns:
            {"text": str, "usage": {"input_tokens": int, "output_tokens": int},
             "cost": float, "model": str}
        """

    # ------------------------------------------------------------------
    # JSON generation (shared implementation)
    # ------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        system: Optional[str] = None,
        _retries: int = 1,
        **kwargs,
    ) -> Any:
        """Generate a JSON response with auto-repair and retry.

        If the first parse fails, sends a "fix this JSON" follow-up
        up to ``_retries`` times.
        """
        result = self.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            **kwargs,
        )

        text = result["text"]
        parsed = try_parse_json(text)
        if parsed is not None:
            return parsed

        for attempt in range(_retries):
            logger.warning(
                "JSON parse failed, repair retry (%d/%d)", attempt + 1, _retries
            )
            fix_result = self.generate(
                prompt=(
                    "The following text was supposed to be valid JSON but has "
                    "syntax errors. Fix it and return ONLY the corrected JSON, "
                    "no explanation:\n\n" + text[:2000]
                ),
                max_tokens=max_tokens,
                temperature=0.0,
                system="You are a JSON repair tool. Output valid JSON only.",
            )
            parsed = try_parse_json(fix_result["text"])
            if parsed is not None:
                logger.info("JSON repair retry succeeded")
                return parsed

        service_name = self.__class__.__name__
        logger.error("JSON parse ultimately failed, raw text: %s", text[:500])
        from src.core.exceptions import APIError
        raise APIError(
            f"{service_name} response could not be parsed as JSON",
            service=service_name.lower(),
            response=text[:500],
        )

    # ------------------------------------------------------------------
    # Model info
    # ------------------------------------------------------------------

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "api_key": f"{self.api_key[:10]}..." if self.api_key else "",
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


# ======================================================================
# Shared JSON parsing / repair utility
# ======================================================================

def try_parse_json(text: str) -> Optional[Any]:
    """Try to extract and parse JSON from LLM output text.

    Strategy (in order):
    1. Look for a ```json ... ``` fenced block.
    2. Find the outermost { ... } or [ ... ].
    3. Direct parse on the raw text.
    4. Clean trailing commas and curly-quote characters.
    5. Iteratively escape unescaped double-quotes inside string values.

    Returns the parsed object, or None if all attempts fail.
    """
    json_match = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        json_str = json_match.group(1) if json_match else text

    # Attempt 1: direct parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Attempt 2: trailing commas + curly quotes
    cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
    cleaned = cleaned.replace("\u2018", '"').replace("\u2019", '"')
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 3: iteratively escape unescaped inner quotes
    repaired = json_str
    for _ in range(30):
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as e:
            if e.pos >= len(repaired):
                break
            char_at = repaired[e.pos]
            if char_at not in ':,{}[]" \n\r\t':
                qpos = repaired.rfind('"', 0, e.pos)
                if qpos >= 0:
                    repaired = repaired[:qpos] + '\\"' + repaired[qpos + 1:]
                    continue
            break

    return None
