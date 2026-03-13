"""
Agent 共用工具函数
"""
import base64
import json
import mimetypes
import re
import requests
from pathlib import Path
from typing import Optional


def repair_json(json_str: str) -> str:
    """
    修复 LLM 输出中常见的 JSON 格式问题，返回修复后的字符串。

    处理的场景：
      - 字符串值内含未转义的双引号，如 头顶有"IA"标识
      - 尾部多余逗号
    """
    lines = json_str.split('\n')
    repaired = []
    for line in lines:
        # 匹配 "key": "value"[,] 模式的行
        m = re.match(r'^(\s*"[^"]+"\s*:\s*")(.*)(",?\s*)$', line)
        if m:
            prefix, value, suffix = m.group(1), m.group(2), m.group(3)
            # 转义值内未转义的双引号（保留已转义的 \"）
            value = re.sub(r'(?<!\\)"', '\\"', value)
            repaired.append(prefix + value + suffix)
        else:
            repaired.append(line)
    result = '\n'.join(repaired)
    # 去除尾部多余逗号
    result = re.sub(r',\s*([}\]])', r'\1', result)
    return result


def safe_parse_json(json_str: str, expect_type: str = "object"):
    """
    带自动修复的 JSON 解析。

    Args:
        json_str: 待解析的 JSON 字符串
        expect_type: "object" 期望 dict, "array" 期望 list

    Returns:
        解析结果，失败返回 None
    """
    # 先提取 ```json ... ``` 代码块
    if expect_type == "object":
        match = re.search(r'```json\s*(\{.*?\})\s*```', json_str, re.DOTALL)
    else:
        match = re.search(r'```json\s*(\[.*?\])\s*```', json_str, re.DOTALL)
    text = match.group(1) if match else json_str.strip()

    opener = "{" if expect_type == "object" else "["
    if not text.startswith(opener):
        closer = "}" if expect_type == "object" else "]"
        pat = re.escape(opener) + r'.*' + re.escape(closer)
        m2 = re.search(pat, text, re.DOTALL)
        if m2:
            text = m2.group(0)

    # 第一次：原样解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 第二次：修复后解析
    repaired = repair_json(text)
    try:
        result = json.loads(repaired)
        print("  [JSON] 自动修复成功（内嵌引号等问题）")
        return result
    except json.JSONDecodeError as e:
        print(f"  [JSON] 修复后仍失败: {e}")
        print(f"  [JSON] 原始内容预览: {json_str[:300]}")
        return None


def load_image_as_base64(source: str) -> Optional[dict]:
    """
    加载图片为 base64 格式，返回 {"mime_type": "image/png", "data": "<base64>"}

    支持三种输入：
      1. 本地文件路径   e.g. "C:/pics/ref.png"
      2. 网络 URL       e.g. "https://example.com/pic.jpg"
      3. base64 字符串  e.g. "data:image/png;base64,..."
    """
    if not source:
        return None

    try:
        if source.startswith("data:image"):
            match = re.match(r'data:(image/[^;]+);base64,(.+)', source)
            if match:
                return {"mime_type": match.group(1), "data": match.group(2)}
            return None

        if source.startswith("http://") or source.startswith("https://"):
            resp = requests.get(source, timeout=30)
            resp.raise_for_status()
            mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            b64 = base64.b64encode(resp.content).decode("utf-8")
            print(f"    [ref] 已加载 URL 图片 ({len(resp.content)//1024} KB, {mime})")
            return {"mime_type": mime, "data": b64}

        path = Path(source)
        if path.exists():
            raw = path.read_bytes()
            mime, _ = mimetypes.guess_type(str(path))
            mime = mime or "image/png"
            b64 = base64.b64encode(raw).decode("utf-8")
            print(f"    [ref] 已加载本地图片 {path.name} ({len(raw)//1024} KB, {mime})")
            return {"mime_type": mime, "data": b64}

        print(f"    [ref] 警告：图片路径不存在: {source}")
        return None

    except Exception as e:
        print(f"    [ref] 加载图片失败: {e}")
        return None
