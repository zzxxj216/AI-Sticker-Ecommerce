"""Gemini AI 服务封装

基于 google-genai SDK，提供统一调用接口，支持：
- 文本生成
- JSON 格式输出
- 图片生成
- 多模态输入（文本 + 参考图）
- 批量生成
- 自动重试
- 错误处理
"""

import base64
import json
import mimetypes
import re
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

logger = get_logger("service.gemini")


class GeminiService:
    """Gemini AI 服务类（基于 google-genai SDK）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        """初始化 Gemini 服务

        Args:
            api_key: API Key（默认从配置读取）
            base_url: API Base URL（默认从配置读取）
            model: 模型名称（默认从配置读取）
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
        """
        self.api_key = api_key or config.gemini_api_key
        self.base_url = (base_url or config.gemini_base_url).rstrip("/")
        self.model = model or config.gemini_model
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.api_key:
            raise APIError(
                "Gemini API Key 未配置",
                service="gemini",
                status_code=401,
            )

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url and self.base_url != "https://generativelanguage.googleapis.com":
            client_kwargs["http_options"] = genai_types.HttpOptions(
                base_url=self.base_url,
            )

        self.client = genai.Client(**client_kwargs)

        logger.info(f"Gemini 服务初始化完成 - 模型: {self.model}")
        logger.info(f"Gemini 端点: {self.base_url}")

    # ================================================================
    # 文本生成
    # ================================================================

    def generate(
        self,
        prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成文本

        Args:
            prompt: 提示词
            max_tokens: 最大 token 数
            temperature: 温度参数（0-1）
            system: 系统提示词

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

            logger.debug(f"调用 Gemini API - 模型: {self.model}, max_tokens: {max_tokens}")

            response = self._call_with_retry(prompt, config=cfg)
            text = self._extract_text(response)

            if not text:
                raise APIError("响应中未找到文本内容", service="gemini")

            usage = self._get_usage(response, prompt, text)
            cost = self._calculate_cost(usage)

            logger.info(
                f"Gemini 文本生成完成 - "
                f"输入: {usage['input_tokens']} tokens, "
                f"输出: {usage['output_tokens']} tokens, "
                f"成本: ${cost:.4f}"
            )
            logger.debug(f"返回文本预览: {text[:200]}")

            return {"text": text, "usage": usage, "cost": cost, "model": self.model}

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Gemini 文本生成失败: {e}")
            raise APIError(f"Gemini 服务错误: {e}", service="gemini")

    def generate_json(
        self,
        prompt: str,
        max_tokens: int = 8192,
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
        result = self.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

        text = result["text"]
        parsed = self._try_parse_json(text)
        if parsed is not None:
            return parsed

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
            "Gemini 返回的内容无法解析为 JSON",
            service="gemini",
            response=text[:500],
        )

    # ================================================================
    # 图片生成
    # ================================================================

    def generate_image(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        output_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """生成图片

        Args:
            prompt: 图片生成提示词
            reference_image: 参考图（本地路径/URL/base64）
            output_path: 输出路径（可选）

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

        try:
            contents: list = []
            if reference_image:
                ref_part = self._load_reference_as_part(reference_image)
                if ref_part:
                    contents.append(ref_part)
                    logger.debug(f"已加载参考图: {reference_image[:50]}...")
            contents.append(prompt)

            cfg = GenerateContentConfig(
                response_modalities=[Modality.IMAGE, Modality.TEXT],
            )

            response = self._call_with_retry(contents, config=cfg)
            image_bytes = self._extract_image_bytes(response)

            if not image_bytes:
                raise APIError("响应中未找到图片数据", service="gemini")

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
            logger.info(f"图片生成成功 - 大小: {size_kb} KB, 耗时: {elapsed:.1f}s")

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
            logger.error(f"图片生成失败: {e}")
            return {
                "success": False,
                "image_path": None,
                "image_data": None,
                "size_kb": 0,
                "elapsed": elapsed,
                "error": str(e),
            }

    # ================================================================
    # 图片分析（多模态）
    # ================================================================

    def analyze_image(
        self,
        image_data: str,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """分析图片

        Args:
            image_data: Base64 编码的图片数据
            prompt: 分析提示词
            media_type: 图片 MIME 类型
            max_tokens: 最大 token 数

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

            logger.debug(f"调用 Gemini API 分析图片 - 模型: {self.model}")

            response = self._call_with_retry([image_part, prompt], config=cfg)
            text = self._extract_text(response)

            if not text:
                raise APIError("响应中未找到文本内容", service="gemini")

            usage = self._get_usage(response, prompt, text)
            cost = self._calculate_cost(usage)

            logger.info(
                f"Gemini 分析完成 - "
                f"输入: {usage['input_tokens']} tokens, "
                f"输出: {usage['output_tokens']} tokens, "
                f"成本: ${cost:.4f}"
            )
            logger.debug(f"返回文本预览: {text[:200]}")

            return {"text": text, "usage": usage, "cost": cost, "model": self.model}

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Gemini 图片分析失败: {e}")
            raise APIError(f"Gemini 服务错误: {e}", service="gemini")

    # ================================================================
    # 批量生成
    # ================================================================

    def generate_batch(
        self,
        prompts: List[str],
        reference_image: Optional[str] = None,
        output_dir: Optional[Path] = None,
        max_workers: int = 3,
    ) -> List[Dict[str, Any]]:
        """批量生成图片

        Args:
            prompts: 提示词列表
            reference_image: 参考图（对所有提示词生效）
            output_dir: 输出目录
            max_workers: 最大并发数

        Returns:
            List[Dict]: 生成结果列表
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not prompts:
            logger.warning("提示词列表为空")
            return []

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        total = len(prompts)
        logger.info(f"开始批量生成 {total} 张图片（并发数: {max_workers}）")

        results_dict: Dict[int, Dict[str, Any]] = {}

        def _gen_one(idx: int, prompt: str) -> tuple:
            output_path = None
            if output_dir:
                from src.utils.text_utils import sanitize_filename
                from datetime import datetime

                safe_name = sanitize_filename(prompt[:30])
                filename = f"image_{idx:02d}_{safe_name}_{datetime.now().strftime('%H%M%S')}.png"
                output_path = output_dir / filename

            logger.debug(f"({idx}/{total}) 生成: {prompt[:50]}...")
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

        logger.info(f"批量生成完成 - 成功: {success_count}/{total}")

        return results

    # ================================================================
    # 模型信息
    # ================================================================

    def get_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息"""
        return {
            "model": self.model,
            "api_key": f"{self.api_key[:10]}...",
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

    # ================================================================
    # 内部方法
    # ================================================================

    def _call_with_retry(
        self,
        contents,
        config: Optional[GenerateContentConfig] = None,
    ):
        """带重试的 API 调用

        Args:
            contents: 请求内容（字符串或 Part 列表）
            config: 生成配置

        Returns:
            SDK 响应对象

        Raises:
            APIError: 所有重试失败后抛出
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
                        f"速率限制，等待 {wait_s}s 后重试 ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait_s)
                elif "deadline" in error_str or "timeout" in error_str:
                    logger.warning(f"请求超时，重试 ({attempt}/{self.max_retries})")
                    time.sleep(3)
                elif attempt < self.max_retries:
                    wait_s = attempt * 2
                    is_ssl = "ssl" in error_str or "eof" in error_str
                    log_fn = logger.debug if is_ssl else logger.warning
                    log_fn(
                        f"API 错误: {e}，等待 {wait_s}s 重试 ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait_s)
                else:
                    break

        raise APIError(
            f"Gemini API 请求失败（已重试 {self.max_retries} 次）: {last_error}",
            service="gemini",
        )

    def _extract_text(self, response) -> Optional[str]:
        """从 SDK 响应中提取文本"""
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
        """从 SDK 响应中提取图片原始字节"""
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    return part.inline_data.data
        except (IndexError, AttributeError):
            pass
        return None

    def _load_reference_as_part(self, source: str) -> Optional[Part]:
        """加载参考图并转为 SDK Part 对象

        Args:
            source: 图片来源（本地路径/URL/base64 data URL）

        Returns:
            Part 对象 或 None
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
                logger.debug(f"已加载 URL 参考图 ({len(data) // 1024} KB)")
                return Part.from_bytes(data=data, mime_type=mime)

            path = Path(source)
            if path.exists():
                raw = path.read_bytes()
                mime, _ = mimetypes.guess_type(str(path))
                mime = mime or "image/png"
                logger.debug(f"已加载本地参考图 {path.name} ({len(raw) // 1024} KB)")
                return Part.from_bytes(data=raw, mime_type=mime)

            logger.warning(f"参考图路径不存在: {source}")
            return None

        except Exception as e:
            logger.error(f"加载参考图失败: {e}")
            return None

    @staticmethod
    def _save_image_bytes(image_bytes: bytes, output_path: Path) -> None:
        """保存图片字节到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)

    def _get_usage(self, response, prompt_text, response_text) -> Dict[str, int]:
        """获取 token 使用量（优先从 SDK 响应获取真实值）"""
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
        """计算成本（Gemini Flash 定价：输入 $0.075/M, 输出 $0.3/M）"""
        return (
            usage["input_tokens"] * 0.075 + usage["output_tokens"] * 0.3
        ) / 1_000_000

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """尝试从文本中提取并解析 JSON"""
        json_match = re.search(
            r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL
        )
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = text

        try:
            data = json.loads(json_str)
            logger.debug("JSON 解析成功")
            return data
        except json.JSONDecodeError:
            pass

        cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
        cleaned = cleaned.replace("\u2018", '"').replace("\u2019", '"')
        try:
            data = json.loads(cleaned)
            logger.debug("JSON 清洗后解析成功")
            return data
        except json.JSONDecodeError:
            pass

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
                if char_at not in ':,{}[]" \n\r\t':
                    qpos = repaired.rfind('"', 0, e.pos)
                    if qpos >= 0:
                        repaired = repaired[:qpos] + '\\"' + repaired[qpos + 1 :]
                        continue
                break

        return None
