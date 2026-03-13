"""Claude AI 服务封装

提供 Claude API 的统一调用接口，支持：
- 文本生成
- 多模态输入（文本 + 图片）
- 流式响应
- 自动重试
- 错误处理
"""

from typing import Optional, List, Dict, Any, Union
import anthropic
from anthropic.types import Message

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import APIError, TimeoutError, RateLimitError
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES

logger = get_logger("service.claude")


class ClaudeService:
    """Claude AI 服务类"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES
    ):
        """初始化 Claude 服务
        
        Args:
            api_key: API Key（默认从配置读取）
            base_url: API Base URL（默认从配置读取）
            model: 模型名称（默认从配置读取）
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
        """
        self.api_key = api_key or config.claude_api_key
        self.base_url = base_url or config.claude_base_url
        self.model = model or config.claude_model
        self.timeout = timeout
        self.max_retries = max_retries
        
        if not self.api_key:
            raise APIError(
                "Claude API Key 未配置",
                service="claude",
                status_code=401
            )
        
        # 初始化客户端
        client_kwargs = {
            "api_key": self.api_key,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        
        self.client = anthropic.Anthropic(**client_kwargs)
        
        logger.info(f"Claude 服务初始化完成 - 模型: {self.model}")
    
    def generate(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """生成文本
        
        Args:
            prompt: 提示词
            max_tokens: 最大 token 数
            temperature: 温度参数（0-1）
            system: 系统提示词
            images: 图片列表（多模态输入）
                格式: [{"type": "base64", "data": "...", "media_type": "image/png"}]
        
        Returns:
            Dict: 包含生成结果和元数据
                {
                    "text": str,
                    "usage": {"input_tokens": int, "output_tokens": int},
                    "cost": float,
                    "model": str
                }
        
        Raises:
            APIError: API 调用失败
            TimeoutError: 请求超时
            RateLimitError: 速率限制
        """
        try:
            # 构建消息内容
            content = self._build_content(prompt, images)
            
            # 构建请求参数
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": content}]
            }
            
            if system:
                kwargs["system"] = system
            
            logger.debug(f"调用 Claude API - 模型: {self.model}, max_tokens: {max_tokens}")
            
            # 调用 API
            message = self.client.messages.create(**kwargs)
            
            # 提取结果
            result = self._extract_result(message)

            logger.info(
                f"Claude 生成完成 - "
                f"输入: {result['usage']['input_tokens']} tokens, "
                f"输出: {result['usage']['output_tokens']} tokens, "
                f"成本: ${result['cost']:.4f}"
            )

            # 记录返回文本的前200字符（用于调试）
            logger.debug(f"返回文本预览: {result['text'][:200]}")

            return result
            
        except anthropic.APITimeoutError as e:
            logger.error(f"Claude API 超时: {e}")
            raise TimeoutError(
                f"Claude API 请求超时: {e}",
                timeout=self.timeout
            )
        
        except anthropic.RateLimitError as e:
            logger.error(f"Claude API 速率限制: {e}")
            raise RateLimitError(
                "Claude API 速率限制",
                service="claude"
            )
        
        except anthropic.APIError as e:
            logger.error(f"Claude API 错误: {e}")
            raise APIError(
                f"Claude API 调用失败: {e}",
                service="claude",
                status_code=getattr(e, 'status_code', None)
            )
        
        except Exception as e:
            logger.error(f"Claude 服务未知错误: {e}")
            raise APIError(
                f"Claude 服务错误: {e}",
                service="claude"
            )
    
    def generate_multiturn(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
        """多轮对话生成

        Args:
            messages: 消息列表 [{"role": "user"/"assistant", "content": "..."}]
            max_tokens: 最大 token 数
            temperature: 温度参数
            system: 系统提示词

        Returns:
            Dict: 与 generate() 返回格式相同
        """
        try:
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system

            logger.debug(
                "调用 Claude API (multiturn) - 模型: %s, turns: %d",
                self.model, len(messages),
            )

            message = self.client.messages.create(**kwargs)
            result = self._extract_result(message)

            logger.info(
                "Claude 生成完成 - "
                "输入: %d tokens, 输出: %d tokens, 成本: $%.4f",
                result["usage"]["input_tokens"],
                result["usage"]["output_tokens"],
                result["cost"],
            )
            logger.debug("返回文本预览: %s", result["text"][:200])
            return result

        except anthropic.APITimeoutError as e:
            logger.error("Claude API 超时: %s", e)
            raise TimeoutError(f"Claude API 请求超时: {e}", timeout=self.timeout)
        except anthropic.RateLimitError as e:
            logger.error("Claude API 速率限制: %s", e)
            raise RateLimitError("Claude API 速率限制", service="claude")
        except anthropic.APIError as e:
            logger.error("Claude API 错误: %s", e)
            raise APIError(
                f"Claude API 调用失败: {e}",
                service="claude",
                status_code=getattr(e, "status_code", None),
            )
        except Exception as e:
            logger.error("Claude 服务未知错误: %s", e)
            raise APIError(f"Claude 服务错误: {e}", service="claude")

    def generate_json(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: Optional[str] = None,
        _retries: int = 1,
    ) -> Dict[str, Any]:
        """生成 JSON 格式的响应（带自动修复和重试）
        
        Args:
            prompt: 提示词
            max_tokens: 最大 token 数
            temperature: 温度参数
            system: 系统提示词
            _retries: 解析失败时重试次数
        
        Returns:
            Dict: 解析后的 JSON 对象
        
        Raises:
            APIError: API 调用失败或 JSON 解析失败
        """
        import json
        import re
        
        result = self.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system
        )
        
        text = result["text"]
        parsed = self._try_parse_json(text)
        if parsed is not None:
            return parsed

        # First parse failed — retry with an explicit "fix this JSON" prompt
        for attempt in range(_retries):
            logger.warning(
                "JSON 解析失败，尝试修复重试 (attempt %d/%d)", attempt + 1, _retries
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
            parsed = self._try_parse_json(fix_result["text"])
            if parsed is not None:
                logger.info("JSON 修复重试成功")
                return parsed

        logger.error("JSON 解析最终失败, 原始文本: %s", text[:500])
        raise APIError(
            f"Claude 返回的内容无法解析为 JSON",
            service="claude",
            response=text[:500],
        )

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Try to extract and parse JSON from text, return None on failure."""
        import json
        import re

        # Try ```json ... ``` fenced block
        json_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = text

        # Attempt 1: direct parse
        try:
            data = json.loads(json_str)
            logger.debug("JSON 解析成功")
            return data
        except json.JSONDecodeError:
            pass

        # Attempt 2: fix common issues — trailing commas, single quotes
        cleaned = re.sub(r',\s*([}\]])', r'\1', json_str)
        cleaned = cleaned.replace("\u2018", '"').replace("\u2019", '"')
        try:
            data = json.loads(cleaned)
            logger.debug("JSON 清洗后解析成功")
            return data
        except json.JSONDecodeError:
            pass

        # Attempt 3: iteratively escape unescaped double quotes inside
        # string values. Common when AI puts Chinese "引号" in JSON text.
        repaired = json_str
        for _ in range(30):
            try:
                data = json.loads(repaired)
                logger.debug("JSON 引号修复后解析成功")
                return data
            except json.JSONDecodeError as e:
                if e.pos >= len(repaired):
                    break
                char_at = repaired[e.pos]
                # Error at a non-structural character → a previous quote
                # prematurely ended the string. Find and escape it.
                if char_at not in ':,{}[]" \n\r\t':
                    qpos = repaired.rfind('"', 0, e.pos)
                    if qpos >= 0:
                        repaired = repaired[:qpos] + '\\"' + repaired[qpos + 1:]
                        continue
                break

        return None
    
    def analyze_image(
        self,
        image_data: str,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """分析图片
        
        Args:
            image_data: Base64 编码的图片数据
            prompt: 分析提示词
            media_type: 图片 MIME 类型
            max_tokens: 最大 token 数
        
        Returns:
            Dict: 分析结果
        """
        images = [{
            "type": "base64",
            "data": image_data,
            "media_type": media_type
        }]
        
        return self.generate(
            prompt=prompt,
            images=images,
            max_tokens=max_tokens
        )
    
    def _build_content(
        self,
        prompt: str,
        images: Optional[List[Dict[str, Any]]] = None
    ) -> Union[str, List[Dict[str, Any]]]:
        """构建消息内容
        
        Args:
            prompt: 文本提示词
            images: 图片列表
        
        Returns:
            str 或 List: 消息内容
        """
        if not images:
            return prompt
        
        # 多模态内容
        content = []
        
        # 添加图片
        for img in images:
            if img["type"] == "base64":
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"]
                    }
                })
        
        # 添加文本
        content.append({
            "type": "text",
            "text": prompt
        })
        
        return content
    
    def _extract_result(self, message: Message) -> Dict[str, Any]:
        """提取 API 响应结果
        
        Args:
            message: Claude API 响应消息
        
        Returns:
            Dict: 提取的结果
        """
        # 提取文本
        text = message.content[0].text if message.content else ""
        
        # 提取使用量
        usage = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens
        }
        
        # 计算成本（Claude Opus 4.6 定价：输入 $3/M, 输出 $15/M）
        cost = (usage["input_tokens"] * 3 + usage["output_tokens"] * 15) / 1_000_000
        
        return {
            "text": text,
            "usage": usage,
            "cost": cost,
            "model": message.model
        }
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息
        
        Returns:
            Dict: 模型信息
        """
        return {
            "model": self.model,
            "api_key": f"{self.api_key[:10]}...",
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries
        }
