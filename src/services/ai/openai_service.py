"""OpenAI API 服务封装

提供 OpenAI API 的统一调用接口，支持：
- 文本生成
- JSON 格式输出
- 自动重试
- 错误处理

接口与 ClaudeService 保持一致，方便互换。
"""

import json
import re
from typing import Optional, List, Dict, Any

from openai import OpenAI, APITimeoutError, RateLimitError as OpenAIRateLimitError, APIError as OpenAIAPIError

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import APIError, TimeoutError, RateLimitError
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES

logger = get_logger("service.openai")


class OpenAIService:
    """OpenAI API 服务类"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.api_key = api_key or config.openai_api_key
        self.base_url = base_url or config.openai_base_url
        self.model = model or config.openai_model
        self.timeout = timeout
        self.max_retries = max_retries
        print(self.base_url)
        if not self.api_key:
            raise APIError(
                "OpenAI API Key 未配置，请在 .env 中设置 OPENAI_API_KEY",
                service="openai",
                status_code=401,
            )

        client_kwargs: Dict[str, Any] = {
            "api_key": self.api_key,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client = OpenAI(**client_kwargs)

        logger.info("OpenAI 服务初始化完成 - 模型: %s", self.model)

    # ------------------------------------------------------------------
    # 文本生成
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: int = 64000,
        temperature: float = 0.7,
        system: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """生成文本

        Args:
            prompt: 用户提示词
            max_tokens: 最大输出 token 数
            temperature: 温度参数（0-2）
            system: 系统提示词
            response_format: 输出格式约束，如 {"type": "json_object"}

        Returns:
            {"text": str, "usage": {...}, "cost": float, "model": str}
        """
        try:
            messages: List[Dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            logger.debug("调用 OpenAI API - 模型: %s, max_tokens: %d", self.model, max_tokens)

            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort":"medium",
            }
            if response_format:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)
            print(response)

            result = self._extract_result(response)


            logger.info(
                "OpenAI 生成完成 - 输入: %d tokens, 输出: %d tokens",
                result["usage"]["input_tokens"],
                result["usage"]["output_tokens"],
            )
            logger.debug("返回文本预览: %s", result["text"][:200])

            return result

        except APITimeoutError as e:
            logger.error("OpenAI API 超时: %s", e)
            raise TimeoutError(f"OpenAI API 请求超时: {e}", timeout=self.timeout)

        except OpenAIRateLimitError as e:
            logger.error("OpenAI API 速率限制: %s", e)
            raise RateLimitError("OpenAI API 速率限制", service="openai")

        except OpenAIAPIError as e:
            logger.error("OpenAI API 错误: %s", e)
            raise APIError(
                f"OpenAI API 调用失败: {e}",
                service="openai",
                status_code=getattr(e, "status_code", None),
            )

        except Exception as e:
            logger.error("OpenAI 服务未知错误: %s", e)
            raise APIError(f"OpenAI 服务错误: {e}", service="openai")

    # ------------------------------------------------------------------
    # JSON 生成
    # ------------------------------------------------------------------

    def generate_json(
        self,
        prompt: str,
        max_tokens: int = 64000,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Any:
        """生成 JSON 格式的响应

        使用 OpenAI response_format={"type": "json_object"} 保证返回合法 JSON。
        注意：system 或 prompt 中需包含 "JSON" 一词（OpenAI API 要求）。

        Returns:
            解析后的 JSON 对象（dict 或 list）
        """
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
            parsed = self._try_parse_json(text)
            if parsed is not None:
                return parsed

        logger.error("JSON 解析失败, 原始文本: %s", text[:500])
        raise APIError(
            "OpenAI 返回的内容无法解析为 JSON",
            service="openai",
            response=text[:500],
        )

    # ------------------------------------------------------------------
    # JSON 解析（与 ClaudeService 共用逻辑）
    # ------------------------------------------------------------------

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Any]:
        """Try to extract and parse JSON from text, return None on failure."""
        json_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = text

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        cleaned = re.sub(r',\s*([}\]])', r'\1', json_str)
        cleaned = cleaned.replace("\u2018", '"').replace("\u2019", '"')
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

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

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _extract_result(self, response) -> Dict[str, Any]:
        """提取 API 响应结果"""
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

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "api_key": f"{self.api_key[:10]}..." if self.api_key else "",
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
