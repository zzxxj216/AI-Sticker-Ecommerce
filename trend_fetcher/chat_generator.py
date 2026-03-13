"""
对话式贴纸生成器
用户可用自然语言描述需求（支持中文），并可选附带参考图
流程：
  1. Claude  — 理解用户意图 + 分析参考图风格
              → 输出 N 条结构化 image_prompt（英文，适合图像生成）
  2. Gemini  — 以 image_prompt + 参考图 生成贴纸图片
              → 保存到 output/images/YYYYMMDD/chat_HHMMSS/
"""
import base64
import json
import re
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from config import config
from image_generator import StickerImageGenerator


# ── Claude 提示词 ──────────────────────────────────────────────
CHAT_ANALYSIS_PROMPT = """你是专业的素材图片设计助手。用户给出了一段描述，可能附带了参考图。
请帮用户生成 {count} 套素材图片设计方案。

用户描述：
{user_description}

{ref_image_section}

输出要求（严格按以下 JSON 格式，不要添加任何其他内容）：
```json
[
  {{
    "index": 1,
    "title": "方案名称（中文，5字以内）",
    "concept": "设计概念说明（中文，一句话）",
    "image_prompt": "英文图像生成提示词，详细描述视觉元素、风格、色调，适合 AI 图像生成"
  }},
  ...
]
```

image_prompt 编写规则：
- 必须用英文
- 每张图只描述**一个独立的主体**，不要在一个 prompt 中描述多个分散的元素
- 主体必须是完整的、可独立存在的形象（方便后期分割裁剪用于打印）
- 描述主体形象（角色/物品/场景）
- 描述风格，根据描述内容选择合适风格（如 realistic / 3D render / digital painting / watercolor / flat illustration 等），不要默认使用贴纸风格
- 描述色调（pastel / vibrant / monochrome / warm tones / cool tones 等）
- 不要在 prompt 中出现 sticker、sticker design、kawaii、cute sticker 等贴纸相关词汇
- 结尾不加额外格式词（系统会自动补充）
- 每条 40~80 词左右，具体生动
{style_hint}"""

REF_IMAGE_PROMPT_SECTION = """参考图说明：
用户提供了一张参考图。请仔细分析其：
- 主体角色/元素是什么
- 整体艺术风格（例：扁平卡通、手绘水彩、像素风）
- 配色方案（主色、辅色、高光色）
- 线条特征（粗黑描边、细线、无描边）
- 表情/动作的表现方式

在生成 image_prompt 时，应体现参考图的风格特征，让生成结果在视觉上与参考图保持一致。"""

STYLE_HINT_WITH_REF = "\n请确保 image_prompt 中体现参考图的风格特征（色彩、线条、角色造型等）。"
STYLE_HINT_NO_REF = ""


class ChatStickerGenerator:
    """
    对话式贴纸生成器

    用法示例：
        gen = ChatStickerGenerator()

        # 纯文字
        results = gen.generate("帮我做5张可爱的猫咪新年贴纸", count=5)

        # 文字 + 参考图
        results = gen.generate(
            "根据这个风格，帮我做3张不同姿势的龙虾贴纸",
            count=3,
            ref_image="output/images/20260228/sticker_01_coder_lobster.png"
        )
    """

    def __init__(self):
        self._claude = None
        self._gemini = StickerImageGenerator()

    @property
    def claude(self):
        if self._claude is None:
            if not config.ANTHROPIC_API_KEY:
                raise ValueError(
                    "未配置 ANTHROPIC_API_KEY\n"
                    "在 .env 中添加: ANTHROPIC_API_KEY=sk-ant-..."
                )
            import anthropic
            kwargs = {"api_key": config.ANTHROPIC_API_KEY}
            if config.ANTHROPIC_BASE_URL:
                kwargs["base_url"] = config.ANTHROPIC_BASE_URL
            self._claude = anthropic.Anthropic(**kwargs)
        return self._claude

    # ── 主入口 ────────────────────────────────────────────────

    def generate(self,
                 user_description: str,
                 count: int = 5,
                 ref_image: Optional[str] = None) -> list[dict]:
        """
        对话式生成贴纸

        Args:
            user_description: 用户的自然语言描述（中英文均可）
            count:             生成数量（1~10）
            ref_image:         参考图，支持本地路径 / URL / base64 字符串

        Returns:
            与 image_generator.generate_batch 相同格式的结果列表
        """
        count = max(1, min(count, 10))

        print()
        print("=" * 55)
        print(" Step 1 / 2  Claude 分析意图")
        print("=" * 55)

        ideas = self._analyze_with_claude(user_description, count, ref_image)
        if not ideas:
            print("  [!] Claude 分析失败，流程中止")
            return []

        print(f"  [OK] 生成 {len(ideas)} 套设计方案：")
        for idea in ideas:
            print(f"    #{idea['index']:02d} {idea['title']} — {idea.get('concept', '')}")

        print()
        print("=" * 55)
        print(" Step 2 / 2  Gemini 生成图片")
        print("=" * 55)

        # 创建本次对话专属的输出子目录
        today = datetime.now().strftime("%Y%m%d")
        session = datetime.now().strftime("chat_%H%M%S")
        output_dir = config.IMAGE_OUTPUT_DIR / today / session
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"  [Gemini] 保存目录: {output_dir}")

        results = self._gemini.generate_batch(ideas, ref_image=ref_image)

        # 汇总
        success = [r for r in results if r["success"]]
        print()
        print("=" * 55)
        print(f" 完成  成功 {len(success)}/{len(results)} 张")
        if success:
            print(f" 图片目录: {output_dir}")
        print("=" * 55)

        return results

    # ── Claude 分析 ───────────────────────────────────────────

    def _analyze_with_claude(self,
                              user_description: str,
                              count: int,
                              ref_image: Optional[str]) -> list[dict]:
        """调用 Claude 理解意图，返回结构化 sticker_ideas"""
        ref_data = self._gemini._load_reference_image(ref_image) if ref_image else None

        ref_section = REF_IMAGE_PROMPT_SECTION if ref_data else "（用户未提供参考图）"
        style_hint = STYLE_HINT_WITH_REF if ref_data else STYLE_HINT_NO_REF

        prompt_text = CHAT_ANALYSIS_PROMPT.format(
            count=count,
            user_description=user_description,
            ref_image_section=ref_section,
            style_hint=style_hint,
        )

        # 构建 Claude 消息（含参考图时使用多模态）
        if ref_data:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": ref_data["mime_type"],
                        "data": ref_data["data"],
                    },
                },
                {"type": "text", "text": prompt_text},
            ]
        else:
            user_content = prompt_text

        try:
            print(f"  [Claude] 模型: {config.CLAUDE_MODEL}")
            msg = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = msg.content[0].text
            tokens_in = msg.usage.input_tokens
            tokens_out = msg.usage.output_tokens
            cost = (tokens_in * 3 + tokens_out * 15) / 1_000_000
            print(f"  [Claude] Token: {tokens_in}+{tokens_out}，约 ${cost:.4f}")

            return self._parse_ideas(raw)

        except Exception as e:
            print(f"  [Claude] 调用失败: {e}")
            return []

    def _parse_ideas(self, raw_text: str) -> list[dict]:
        """从 Claude 输出中解析 JSON 格式的选题列表"""
        # 提取 ```json ... ``` 代码块
        match = re.search(r'```json\s*(\[.*?\])\s*```', raw_text, re.DOTALL)
        json_str = match.group(1) if match else raw_text.strip()

        # 兜底：直接找第一个 [ ... ]
        if not json_str.startswith("["):
            match2 = re.search(r'\[.*\]', json_str, re.DOTALL)
            if match2:
                json_str = match2.group(0)

        try:
            ideas = json.loads(json_str)
            # 确保每条都有必要字段
            for i, idea in enumerate(ideas, 1):
                idea.setdefault("index", i)
                idea.setdefault("title", f"方案{i}")
                idea.setdefault("image_prompt", "")
                idea.setdefault("concept", "")
            return ideas
        except json.JSONDecodeError as e:
            print(f"  [Claude] JSON 解析失败: {e}")
            print(f"  [Claude] 原始输出预览: {raw_text[:300]}")
            return []
