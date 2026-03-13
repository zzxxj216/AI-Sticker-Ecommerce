"""Gemini AI 服务封装

提供 Gemini API 的统一调用接口，支持：
- 图片生成
- 多模态输入（文本 + 参考图）
- 批量生成
- 自动重试
- 错误处理

支持两种 API 模式：
1. Google 原生 API (generativelanguage.googleapis.com)
2. OpenAI 兼容 API (代理服务)
"""

import base64
import mimetypes
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import APIError, TimeoutError, RateLimitError
from src.core.constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES

logger = get_logger("service.gemini")


class GeminiService:
    """Gemini AI 服务类"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES
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
                status_code=401
            )
        
        # 自动识别 API 模式
        self.is_google_native = (
            self.api_key.startswith("AIza") or
            "googleapis.com" in self.base_url
        )
        
        # 构建 API URL
        if self.is_google_native:
            self.api_url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
            self.headers = {"Content-Type": "application/json"}
            logger.info(f"Gemini 服务初始化完成 - 模式: Google 原生 API")
        else:
            self.api_url = f"{self.base_url}/v1/chat/completions"
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            logger.info(f"Gemini 服务初始化完成 - 模式: OpenAI 兼容 API")
        
        logger.info(f"Gemini 模型: {self.model}")
        logger.info(f"Gemini 端点: {self.api_url}")
    
    def generate_image(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        output_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """生成图片
        
        Args:
            prompt: 图片生成提示词
            reference_image: 参考图（本地路径/URL/base64）
            output_path: 输出路径（可选）
        
        Returns:
            Dict: 生成结果
                {
                    "success": bool,
                    "image_path": str,
                    "image_data": str (base64),
                    "size_kb": int,
                    "elapsed": float,
                    "error": str (if failed)
                }
        
        Raises:
            APIError: API 调用失败
        """
        start_time = time.time()
        
        try:
            # 加载参考图
            ref_data = None
            if reference_image:
                ref_data = self._load_reference_image(reference_image)
                if ref_data:
                    logger.debug(f"已加载参考图: {reference_image[:50]}...")
            
            # 调用 API
            response = self._call_api_with_retry(prompt, ref_data)
            
            if response is None:
                raise APIError(
                    "API 请求失败（已重试多次）",
                    service="gemini"
                )
            
            if response.status_code != 200:
                error_msg = self._extract_error_message(response)
                raise APIError(
                    f"API 返回错误: {error_msg}",
                    service="gemini",
                    status_code=response.status_code,
                    response=error_msg
                )
            
            # 解析响应
            data = response.json()
            image_data = self._extract_image_data(data)
            
            if not image_data:
                raise APIError(
                    "响应中未找到图片数据",
                    service="gemini",
                    response=str(data)[:500]
                )
            
            # 保存图片
            if output_path:
                self._save_image(image_data, output_path)
                size_kb = output_path.stat().st_size // 1024
                image_path = str(output_path.resolve())
            else:
                size_kb = len(base64.b64decode(image_data)) // 1024
                image_path = None
            
            elapsed = time.time() - start_time
            
            logger.info(
                f"图片生成成功 - "
                f"大小: {size_kb} KB, "
                f"耗时: {elapsed:.1f}s"
            )
            
            return {
                "success": True,
                "image_path": image_path,
                "image_data": image_data,
                "size_kb": size_kb,
                "elapsed": elapsed,
                "error": None
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
                "error": str(e)
            }
    
    def analyze_image(
        self,
        image_data: str,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """分析图片（文本响应）

        Args:
            image_data: Base64 编码的图片数据
            prompt: 分析提示词
            media_type: 图片 MIME 类型
            max_tokens: 最大 token 数（仅用于兼容接口）

        Returns:
            Dict: 分析结果
                {
                    "text": str,
                    "usage": {"input_tokens": int, "output_tokens": int},
                    "cost": float,
                    "model": str
                }

        Raises:
            APIError: API 调用失败
        """
        try:
            # 构建参考图数据
            ref_data = {
                "mime_type": media_type,
                "data": image_data
            }

            # 构建请求体（不要求返回图片，只要文本）
            if self.is_google_native:
                parts = [
                    {
                        "inlineData": {
                            "mimeType": ref_data["mime_type"],
                            "data": ref_data["data"]
                        }
                    },
                    {"text": prompt}
                ]

                payload = {
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "responseModalities": ["TEXT"]  # 只要文本响应
                    }
                }
            else:
                # OpenAI 兼容格式
                content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{ref_data['mime_type']};base64,{ref_data['data']}"
                        }
                    },
                    {"type": "text", "text": prompt}
                ]

                payload = {
                    "model": self.model,
                    "stream": False,
                    "messages": [{"role": "user", "content": content}]
                }

            # 调用 API
            url = self.api_url
            if self.is_google_native:
                url = f"{self.api_url}?key={self.api_key}"

            logger.debug(f"调用 Gemini API 分析图片 - 模型: {self.model}")

            response = requests.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )

            if response.status_code != 200:
                error_msg = self._extract_error_message(response)
                raise APIError(
                    f"Gemini API 调用失败: {error_msg}",
                    service="gemini",
                    status_code=response.status_code,
                    response=error_msg
                )

            # 解析响应
            data = response.json()
            text = self._extract_text_response(data)

            if not text:
                raise APIError(
                    "响应中未找到文本内容",
                    service="gemini",
                    response=str(data)[:500]
                )

            # 估算 token 使用（Gemini API 可能不返回准确值）
            usage = {
                "input_tokens": len(prompt) // 4 + 100,  # 粗略估算
                "output_tokens": len(text) // 4
            }

            # 计算成本（Gemini Flash 定价：输入 $0.075/M, 输出 $0.3/M）
            cost = (usage["input_tokens"] * 0.075 + usage["output_tokens"] * 0.3) / 1_000_000

            logger.info(
                f"Gemini 分析完成 - "
                f"输入: ~{usage['input_tokens']} tokens, "
                f"输出: ~{usage['output_tokens']} tokens, "
                f"成本: ${cost:.4f}"
            )

            logger.debug(f"返回文本预览: {text[:200]}")

            return {
                "text": text,
                "usage": usage,
                "cost": cost,
                "model": self.model
            }

        except APIError:
            raise
        except Exception as e:
            logger.error(f"Gemini 图片分析失败: {e}")
            raise APIError(
                f"Gemini 服务错误: {e}",
                service="gemini"
            )

    def _extract_text_response(self, data: Dict[str, Any]) -> Optional[str]:
        """从响应中提取文本内容

        Args:
            data: API 响应数据

        Returns:
            str: 文本内容 或 None
        """
        # Google 原生格式
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                text = part.get("text", "")
                if text:
                    return text

        # OpenAI 兼容格式
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return content

        return None

    def generate_batch(
        self,
        prompts: List[str],
        reference_image: Optional[str] = None,
        output_dir: Optional[Path] = None,
        max_workers: int = 3
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
        
        results_dict = {}
        
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
    
    def _load_reference_image(self, source: str) -> Optional[Dict[str, str]]:
        """加载参考图
        
        Args:
            source: 图片来源（本地路径/URL/base64）
        
        Returns:
            Dict: {"mime_type": str, "data": str (base64)} 或 None
        """
        if not source:
            return None
        
        try:
            # Base64 字符串
            if source.startswith("data:image"):
                match = re.match(r'data:(image/[^;]+);base64,(.+)', source)
                if match:
                    return {
                        "mime_type": match.group(1),
                        "data": match.group(2)
                    }
                return None
            
            # 网络 URL
            if source.startswith("http://") or source.startswith("https://"):
                resp = requests.get(source, timeout=30)
                resp.raise_for_status()
                mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
                b64 = base64.b64encode(resp.content).decode("utf-8")
                logger.debug(f"已加载 URL 参考图 ({len(resp.content)//1024} KB)")
                return {"mime_type": mime, "data": b64}
            
            # 本地文件
            path = Path(source)
            if path.exists():
                raw = path.read_bytes()
                mime, _ = mimetypes.guess_type(str(path))
                mime = mime or "image/png"
                b64 = base64.b64encode(raw).decode("utf-8")
                logger.debug(f"已加载本地参考图 {path.name} ({len(raw)//1024} KB)")
                return {"mime_type": mime, "data": b64}
            
            logger.warning(f"参考图路径不存在: {source}")
            return None
            
        except Exception as e:
            logger.error(f"加载参考图失败: {e}")
            return None
    
    def _call_api_with_retry(
        self,
        prompt: str,
        ref_data: Optional[Dict[str, str]] = None
    ) -> Optional[requests.Response]:
        """调用 API（带重试）
        
        Args:
            prompt: 提示词
            ref_data: 参考图数据
        
        Returns:
            Response 或 None
        """
        payload = self._build_payload(prompt, ref_data)
        last_response = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                url = self.api_url
                if self.is_google_native:
                    url = f"{self.api_url}?key={self.api_key}"
                
                response = requests.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout
                )
                
                last_response = response
                
                # 重试 429/503
                if response.status_code in (429, 503):
                    wait_s = attempt * 5
                    logger.warning(
                        f"HTTP {response.status_code}, "
                        f"等待 {wait_s}s 后重试 ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait_s)
                    continue
                
                return response
                
            except requests.Timeout:
                logger.warning(f"请求超时，重试 ({attempt}/{self.max_retries})")
                time.sleep(3)
            except Exception as e:
                logger.error(f"请求异常: {e}")
                break
        
        return last_response
    
    def _build_payload(
        self,
        prompt: str,
        ref_data: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """构建请求体
        
        Args:
            prompt: 提示词
            ref_data: 参考图数据
        
        Returns:
            Dict: 请求体
        """
        if self.is_google_native:
            # Google 原生格式
            parts = []
            if ref_data:
                parts.append({
                    "inlineData": {
                        "mimeType": ref_data["mime_type"],
                        "data": ref_data["data"]
                    }
                })
            parts.append({"text": prompt})
            
            return {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE", "TEXT"]
                }
            }
        else:
            # OpenAI 兼容格式
            if ref_data:
                content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{ref_data['mime_type']};base64,{ref_data['data']}"
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            else:
                content = prompt
            
            return {
                "model": self.model,
                "stream": False,
                "messages": [{"role": "user", "content": content}]
            }
    
    def _extract_image_data(self, data: Dict[str, Any]) -> Optional[str]:
        """从响应中提取图片数据（base64）
        
        Args:
            data: API 响应数据
        
        Returns:
            str: Base64 图片数据 或 None
        """
        # Google 原生格式
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                inline = part.get("inlineData", {})
                b64_data = inline.get("data", "")
                if b64_data:
                    return b64_data
        
        # OpenAI 兼容格式
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "") or ""
            if content:
                # 提取 data:image/...;base64,... 格式
                pattern = r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)'
                match = re.search(pattern, content)
                if match:
                    return match.group(1)
        
        return None
    
    def _save_image(self, b64_data: str, output_path: Path) -> None:
        """保存图片
        
        Args:
            b64_data: Base64 图片数据
            output_path: 输出路径
        """
        image_bytes = base64.b64decode(b64_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
    
    def _extract_error_message(self, response: requests.Response) -> str:
        """提取错误消息
        
        Args:
            response: 响应对象
        
        Returns:
            str: 错误消息
        """
        try:
            err = response.json()
            return (
                err.get("error", {}).get("message") or
                str(err)[:200]
            )
        except Exception:
            return response.text[:200]
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息
        
        Returns:
            Dict: 模型信息
        """
        return {
            "model": self.model,
            "api_key": f"{self.api_key[:10]}...",
            "base_url": self.base_url,
            "mode": "Google Native" if self.is_google_native else "OpenAI Compatible",
            "timeout": self.timeout,
            "max_retries": self.max_retries
        }
