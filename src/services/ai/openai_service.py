"""OpenAI API service wrapper.

Unified OpenAI API interface supporting text generation, JSON output,
auto-retry, and error handling. Interface compatible with ClaudeService
for easy swapping via BaseLLMService.
"""

import json
import re
from typing import Optional, List, Dict, Any

from openai import OpenAI, APITimeoutError, RateLimitError as OpenAIRateLimitError, APIError as OpenAIAPIError

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import APIError, TimeoutError, RateLimitError
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES
from src.services.ai.base import BaseLLMService

logger = get_logger("service.openai")


class OpenAIService(BaseLLMService):
    """OpenAI API Service"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        resolved_key = api_key or config.openai_api_key
        resolved_url = base_url or config.openai_base_url
        resolved_model = model or config.openai_model

        if not resolved_key:
            raise APIError(
                "OpenAI API Key not configured. Set OPENAI_API_KEY in .env",
                service="openai",
                status_code=401,
            )

        super().__init__(
            api_key=resolved_key,
            base_url=resolved_url,
            model=resolved_model,
            timeout=timeout,
            max_retries=max_retries,
        )

        client_kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client = OpenAI(**client_kwargs)

        logger.info("OpenAI service initialized - model: %s", self.model)

    # ------------------------------------------------------------------
    # Text generation
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: int = 64000,
        temperature: float = 0.7,
        system: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate text

        Args:
            prompt: User prompt
            max_tokens: Max output tokens
            temperature: Temperature (0-2)
            system: System prompt
            response_format: Output format constraint, e.g. {"type": "json_object"}

        Returns:
            {"text": str, "usage": {...}, "cost": float, "model": str}
        """
        try:
            messages: List[Dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            logger.debug("Calling OpenAI API - model: %s, max_tokens: %d", self.model, max_tokens)

            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
            }
            if not self._is_beta_model():
                kwargs["temperature"] = temperature
            if response_format:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)
            result = self._extract_result(response)


            logger.info(
                "OpenAI generation done - input: %d tokens, output: %d tokens",
                result["usage"]["input_tokens"],
                result["usage"]["output_tokens"],
            )
            logger.debug("Response text preview: %s", result["text"][:200])

            return result

        except APITimeoutError as e:
            logger.error("OpenAI API timeout: %s", e)
            raise TimeoutError(f"OpenAI API request timeout: {e}", timeout=self.timeout)

        except OpenAIRateLimitError as e:
            logger.error("OpenAI API rate limited: %s", e)
            raise RateLimitError("OpenAI API rate limited", service="openai")

        except OpenAIAPIError as e:
            logger.error("OpenAI API error: %s", e)
            raise APIError(
                f"OpenAI API call failed: {e}",
                service="openai",
                status_code=getattr(e, "status_code", None),
            )

        except Exception as e:
            logger.error("OpenAI service unknown error: %s", e)
            raise APIError(f"OpenAI service error: {e}", service="openai")

    # ------------------------------------------------------------------
    # JSON generation — leverages OpenAI's native JSON mode, then
    # falls back to BaseLLMService repair logic on parse failure.
    # ------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        max_tokens: int = 64000,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Any:
        """Generate JSON using OpenAI's response_format=json_object."""
        from src.services.ai.base import try_parse_json

        effective_system = system or ""
        if "json" not in effective_system.lower() and "json" not in prompt.lower():
            effective_system = effective_system + "\nRespond with JSON." if effective_system else "Respond with JSON."

        result = self.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=effective_system or None,
            response_format={"type": "json_object"},
        )

        text = result["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            parsed = try_parse_json(text)
            if parsed is not None:
                return parsed

        logger.error("JSON parse failed, raw text: %s", text[:500])
        raise APIError(
            "OpenAI response could not be parsed as JSON",
            service="openai",
            response=text[:500],
        )

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    _BETA_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")

    def _is_beta_model(self) -> bool:
        """Beta models forbid temperature, top_p, n, presence/frequency_penalty."""
        m = self.model.lower()
        return any(m.startswith(p) for p in self._BETA_MODEL_PREFIXES)

    def _extract_result(self, response) -> Dict[str, Any]:
        """Extract API response result"""
        choice = response.choices[0]
        text = choice.message.content or ""

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
        }

        return {
            "text": text,
            "usage": usage,
            "cost": 0.0,
            "model": response.model or self.model,
        }

    # get_model_info() is inherited from BaseLLMService
