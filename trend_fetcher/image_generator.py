"""
Gemini 贴纸图片生成模块
支持两种 API 模式（自动识别）：
  - Google 原生格式：IMAGE_BASE_URL=https://generativelanguage.googleapis.com
                     KEY 为 AIza 开头的 Google AI Studio Key
  - OpenAI 兼容格式：IMAGE_BASE_URL=https://api.apiyi.com 等代理
                     KEY 为代理平台的 sk- 开头 Key

参考图（reference_image）用法：
  - 本地文件路径：  "/path/to/ref.png"
  - 网络图片 URL：  "https://example.com/ref.jpg"
  - base64 字符串： "data:image/png;base64,..."
  可在 idea 字典中加入 "reference_image" 字段，或调用时传入 ref_image 参数。
"""
import base64
import mimetypes
import re
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from config import config


class StickerImageGenerator:

    # 素材图片专用提示词后缀（确保主体孤立、边缘清晰，便于后期分割打印）
    # STICKER_SUFFIX = (
    #     ", single isolated subject centered on pure white background, "
    #     "clean sharp edges with high contrast boundary, "
    #     "no background elements, no shadows on background, no ground plane, "
    #     "generous margin around the subject, "
    #     "include a clear continuous die-cut outline (cut line) around the subject, "
    #     "die-cut ready, print-ready asset, "
    #     "no text, no watermark, no overlapping elements, "
    #     "high resolution, professional quality, commercial use"
    # )
    STICKER_SUFFIX = ''
    def __init__(self):
        api_key = config.IMAGE_API_KEY
        base_url = config.IMAGE_BASE_URL.rstrip("/")

        if not api_key:
            raise ValueError(
                "未配置图片 API Key。\n"
                "Google AI Studio Key 申请: https://aistudio.google.com/apikey\n"
                "在 .env 中设置 IMAGE_API_KEY=AIza..."
            )
        if not base_url:
            raise ValueError("未配置 IMAGE_BASE_URL")

        self.api_key = api_key
        self.base_url = base_url

        # 自动识别 API 模式
        # Google 原生：key 以 AIza 开头 或 base_url 包含 googleapis.com
        self.is_google_native = (
            api_key.startswith("AIza") or
            "googleapis.com" in base_url
        )

        if self.is_google_native:
            # Google 原生格式端点
            model = config.IMAGE_MODEL
            self.api_url = (
                f"{base_url}/v1beta/models/{model}:generateContent"
            )
            # Google AI Studio 用 query param 传 key
            self.headers = {"Content-Type": "application/json"}
            print(f"  [Gemini] 模式: Google 原生 API")
        else:
            # OpenAI 兼容格式端点
            self.api_url = base_url + "/v1/chat/completions"
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            print(f"  [Gemini] 模式: OpenAI 兼容模式")

        print(f"  [Gemini] 端点: {self.api_url}")
        print(f"  [Gemini] 模型: {config.IMAGE_MODEL}")

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def generate_batch(self, sticker_ideas: list[dict],
                       ref_image: Optional[str] = None,
                       max_workers: int = 3) -> list[dict]:
        """
        批量并发生成贴纸图片

        Args:
            sticker_ideas: 选题列表，每条可含 "reference_image" 字段
            ref_image:     全局参考图（对所有选题生效）
            max_workers:   最大并发数（默认 3，避免 API 限流）
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not sticker_ideas:
            print("  [Gemini] 无选题数据，跳过图片生成")
            return []

        today = datetime.now().strftime("%Y%m%d")
        output_dir = config.IMAGE_OUTPUT_DIR / today
        output_dir.mkdir(parents=True, exist_ok=True)

        total = len(sticker_ideas)
        ref_hint = f"（含参考图: {str(ref_image)[:40]}...）" if ref_image else ""
        print(f"  [Gemini] 开始并发生成 {total} 张贴纸（workers={max_workers}）{ref_hint}")
        print(f"  [Gemini] 保存目录: {output_dir}")

        results_dict: dict[int, dict] = {}

        def _gen_one(i: int, idea: dict) -> tuple[int, dict]:
            title = idea.get("title", f"sticker_{i}")
            image_prompt = idea.get("image_prompt", "")
            if not image_prompt:
                print(f"  [Gemini] ({i}/{total}) 跳过 '{title}'：无 image_prompt")
                return i, self._error_result(idea.get("index", i), title, "", "缺少 image_prompt", 0)
            effective_ref = idea.get("reference_image") or ref_image
            print(f"  [Gemini] ({i}/{total}) 生成: {title}" + (" [+参考图]" if effective_ref else ""))
            result = self.generate_sticker(idea, output_dir, ref_image=effective_ref)
            return i, result

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_gen_one, i, idea): i for i, idea in enumerate(sticker_ideas, 1)}
            for future in as_completed(futures):
                idx, result = future.result()
                results_dict[idx] = result

        results = [results_dict[i] for i in sorted(results_dict)]
        success_count = sum(1 for r in results if r["success"])
        print(f"  [Gemini] 完成！成功 {success_count}/{total} 张")
        return results

    def generate_one(self, idea: dict, ref_image: Optional[str] = None) -> dict:
        """生成单张图片（供单条重新生成调用）。"""
        today = datetime.now().strftime("%Y%m%d")
        output_dir = config.IMAGE_OUTPUT_DIR / today
        output_dir.mkdir(parents=True, exist_ok=True)
        effective_ref = idea.get("reference_image") or ref_image
        return self.generate_sticker(idea, output_dir, ref_image=effective_ref)

    def generate_sticker(self, idea: dict, output_dir: Path,
                         ref_image: Optional[str] = None) -> dict:
        """
        生成单张贴纸图片

        Args:
            idea:      选题字典，需含 image_prompt、title；
                       可含 reference_image（本地路径/URL/base64）
            output_dir: 图片保存目录
            ref_image:  外部传入的参考图，优先级低于 idea["reference_image"]
        """
        title = idea.get("title", "material")
        raw_prompt = idea.get("image_prompt", "")
        idx = idea.get("index", 0)
        effective_ref = idea.get("reference_image") or ref_image

        full_prompt = raw_prompt + self.STICKER_SUFFIX

        safe_title = re.sub(r'[^\w\s-]', '', title)[:30].strip().replace(' ', '_').lower()
        filename = f"material_{idx:02d}_{safe_title}_{datetime.now().strftime('%H%M%S')}.png"
        output_path = output_dir / filename

        # 加载参考图（转为 base64 + mimeType）
        ref_data = self._load_reference_image(effective_ref) if effective_ref else None

        start_time = time.time()
        response = self._call_api_with_retry(full_prompt, ref_data=ref_data)
        elapsed = round(time.time() - start_time, 1)

        if response is None:
            msg = "请求失败（已重试3次）"
            print(f"    [!] {msg}")
            return self._error_result(idx, title, raw_prompt, msg, elapsed)

        if response.status_code != 200:
            error_msg = f"HTTP {response.status_code}"
            try:
                err = response.json()
                detail = (
                    err.get("error", {}).get("message")
                    or str(err)[:200]
                )
                error_msg += f": {detail}"
            except Exception:
                error_msg += f": {response.text[:200]}"
            print(f"    [!] 生成失败 ({elapsed}s): {error_msg}")
            return self._error_result(idx, title, raw_prompt, error_msg, elapsed)

        # 解析响应中的图片
        try:
            data = response.json()
        except Exception as e:
            msg = f"响应解析失败: {e}"
            print(f"    [!] {msg}")
            return self._error_result(idx, title, raw_prompt, msg, elapsed)

        image_path = self._extract_image(data, output_path)

        if image_path:
            size_kb = image_path.stat().st_size // 1024
            print(f"    [OK] 已保存: {image_path.name} ({size_kb} KB, {elapsed}s)")
            return {
                "index": idx,
                "title": title,
                "image_prompt": raw_prompt,
                "full_prompt": full_prompt,
                "image_path": str(image_path.resolve()),
                "filename": image_path.name,
                "size_kb": size_kb,
                "success": True,
                "error": None,
                "elapsed": elapsed,
            }
        else:
            # 打印更多调试信息
            preview = str(data)[:300]
            msg = "响应中未找到图片数据"
            print(f"    [!] {msg}")
            print(f"    [!] 响应预览: {preview}")
            return self._error_result(idx, title, raw_prompt, msg, elapsed)

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _load_reference_image(self, source: str) -> Optional[dict]:
        """
        加载参考图，返回 {"mime_type": "image/png", "data": "<base64>"}

        支持三种输入：
          1. 本地文件路径   e.g. "C:/pics/ref.png"
          2. 网络 URL       e.g. "https://example.com/pic.jpg"
          3. base64 字符串  e.g. "data:image/png;base64,..."
        """
        if not source:
            return None

        try:
            # --- base64 字符串 ---
            if source.startswith("data:image"):
                match = re.match(r'data:(image/[^;]+);base64,(.+)', source)
                if match:
                    return {"mime_type": match.group(1), "data": match.group(2)}
                return None

            # --- 网络 URL ---
            if source.startswith("http://") or source.startswith("https://"):
                resp = requests.get(source, timeout=30)
                resp.raise_for_status()
                mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
                b64 = base64.b64encode(resp.content).decode("utf-8")
                print(f"    [ref] 已加载 URL 参考图 ({len(resp.content)//1024} KB, {mime})")
                return {"mime_type": mime, "data": b64}

            # --- 本地文件 ---
            path = Path(source)
            if path.exists():
                raw = path.read_bytes()
                mime, _ = mimetypes.guess_type(str(path))
                mime = mime or "image/png"
                b64 = base64.b64encode(raw).decode("utf-8")
                print(f"    [ref] 已加载本地参考图 {path.name} ({len(raw)//1024} KB, {mime})")
                return {"mime_type": mime, "data": b64}

            print(f"    [ref] 警告：参考图路径不存在: {source}")
            return None

        except Exception as e:
            print(f"    [ref] 加载参考图失败: {e}")
            return None

    def _call_api_with_retry(self, prompt: str, max_retries: int = 3,
                             ref_data: Optional[dict] = None) -> Optional[requests.Response]:
        """发送 API 请求，自动重试 503/429"""
        payload = self._build_payload(prompt, ref_data=ref_data)
        last_response = None

        for attempt in range(1, max_retries + 1):
            try:
                url = self.api_url
                if self.is_google_native:
                    # Google 原生：key 通过 query param 传递
                    url = f"{self.api_url}?key={self.api_key}"

                response = requests.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=config.IMAGE_TIMEOUT,
                )
                last_response = response

                if response.status_code in (429, 503):
                    wait_s = attempt * 8
                    print(f"    [~] HTTP {response.status_code}，{wait_s}s 后重试 ({attempt}/{max_retries})...")
                    time.sleep(wait_s)
                    continue

                return response

            except requests.Timeout:
                print(f"    [~] 超时，重试 ({attempt}/{max_retries})...")
                time.sleep(3)
            except Exception as e:
                print(f"    [~] 请求异常: {e}")
                break

        return last_response

    def _build_payload(self, prompt: str, ref_data: Optional[dict] = None) -> dict:
        """
        根据 API 模式构建请求体

        Args:
            prompt:   文字提示词
            ref_data: 参考图数据 {"mime_type": "image/png", "data": "<base64>"}
                      若为 None 则纯文字生成
        """
        if self.is_google_native:
            # Google 原生格式：参考图作为 inlineData part 放在文字前面
            parts = []
            if ref_data:
                parts.append({
                    "inlineData": {
                        "mimeType": ref_data["mime_type"],
                        "data": ref_data["data"],
                    }
                })
            parts.append({"text": prompt})
            return {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE", "TEXT"],
                },
            }
        else:
            # OpenAI 兼容格式：参考图作为 image_url content block
            if ref_data:
                content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{ref_data['mime_type']};base64,{ref_data['data']}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ]
            else:
                content = prompt
            return {
                "model": config.IMAGE_MODEL,
                "stream": False,
                "messages": [{"role": "user", "content": content}],
            }

    def _extract_image(self, data: dict, output_path: Path) -> Optional[Path]:
        """
        从 API 响应中提取图片并保存，兼容两种响应格式：

        Google 原生格式响应：
          data["candidates"][0]["content"]["parts"][N]["inlineData"]["data"]
          data["candidates"][0]["content"]["parts"][N]["inlineData"]["mimeType"]

        OpenAI 兼容格式响应：
          data["choices"][0]["message"]["content"] 含 base64 字符串
        """
        # --- 方式 1: Google 原生格式 ---
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                inline = part.get("inlineData", {})
                b64_data = inline.get("data", "")
                mime = inline.get("mimeType", "image/png")
                if b64_data:
                    return self._save_b64(b64_data, mime, output_path)

        # --- 方式 2: OpenAI 兼容格式（content 字段含 base64 URI）---
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "") or ""
            if content:
                return self._extract_from_content_string(content, output_path)

        return None

    def _extract_from_content_string(self, content: str, output_path: Path) -> Optional[Path]:
        """从字符串内容中提取 base64 图片（data:image/... 格式）"""
        pattern = r'data:image/([^;]+);base64,([A-Za-z0-9+/=]+)'
        match = re.search(pattern, content)
        if not match:
            return None
        mime_ext = match.group(1)
        b64_data = match.group(2)
        return self._save_b64(b64_data, f"image/{mime_ext}", output_path)

    def _save_b64(self, b64_data: str, mime_type: str, output_path: Path) -> Optional[Path]:
        """解码 base64 并保存图片文件"""
        try:
            image_bytes = base64.b64decode(b64_data)
            if len(image_bytes) < 500:
                return None

            # 根据 mimeType 确定后缀
            ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
            actual_path = output_path.with_suffix(f".{ext}")
            actual_path.write_bytes(image_bytes)
            return actual_path
        except Exception:
            return None

    def _error_result(self, idx: int, title: str, prompt: str,
                      error: str, elapsed: float) -> dict:
        return {
            "index": idx,
            "title": title,
            "image_prompt": prompt,
            "full_prompt": "",
            "image_path": None,
            "filename": None,
            "size_kb": 0,
            "success": False,
            "error": error,
            "elapsed": elapsed,
        }
