"""
Agent 2 — Prompt 生成智能体
接收图片分析结果 + 用户偏好，调用 Claude 生成多条 image_prompt。
"""
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import config
from agents.utils import load_image_as_base64, safe_parse_json

# ── Prompt 生成提示词 ──────────────────────────────────────────
PROMPT_GENERATION_PROMPT = """你是专业的素材图片设计助手。根据以下图片分析和用户需求，生成 {count} 条素材图片设计方案。

## 图片分析
{analysis_text}

## 用户需求
{requirement_summary}

{refinement_context}

严格按以下 JSON 格式输出，不要添加任何其他内容：
```json
[
  {{
    "index": 1,
    "title": "方案名称（中文，5字以内）",
    "concept": "设计概念说明（中文，一句话）",
    "image_prompt": "英文图像生成提示词，详细描述视觉元素、风格、色调，适合 AI 图像生成",
    "negative_prompt": "英文负面提示词，用于规避结构错误、常识性错误或视觉崩坏"
  }}
]
```

image_prompt 编写规则：
- 必须用英文
- 每张图只描述**一个独立的主体**，不要在一个 prompt 中描述多个分散的元素
- 结尾不加额外格式词（系统会自动补充）
- 确保每条 prompt 之间有明显差异，覆盖用户选择的不同维度组合"""

REFINEMENT_PROMPT = """
## 特别注意
用户对之前的部分 prompt 不满意，需要你重新生成。

以下 prompt 已通过审批，请作为参考风格保持一致性：
{approved_prompts}

以下 prompt 被拒绝，需要重新生成：
{rejected_info}

请只输出需要重新生成的 {regen_count} 条 prompt，保持与已通过 prompt 相同的质量和风格。"""


class PromptGeneratorAgent:
    """Prompt 生成智能体：基于图片分析和用户偏好生成 image_prompt"""

    def __init__(self):
        self._claude = None

    @property
    def claude(self):
        if self._claude is None:
            if not config.ANTHROPIC_API_KEY:
                raise ValueError("未配置 ANTHROPIC_API_KEY")
            import anthropic
            kwargs = {"api_key": config.ANTHROPIC_API_KEY}
            if config.ANTHROPIC_BASE_URL:
                kwargs["base_url"] = config.ANTHROPIC_BASE_URL
            self._claude = anthropic.Anthropic(**kwargs)
        return self._claude

    def generate(self, analysis: dict, requirement_summary: str,
                 image_path: str, count: int = 5) -> list[dict]:
        """
        生成多条 image_prompt。

        Args:
            analysis: ImageAnalyzerAgent 返回的分析结果
            requirement_summary: 编排器组合的总体要求描述
            image_path: 原图路径（发送给 Claude 作为参考）
            count: 生成数量

        Returns:
            prompt 列表，每条含 index/title/concept/image_prompt
        """
        count = max(1, min(count, 10))
        analysis_text = self._format_analysis(analysis)

        prompt_text = PROMPT_GENERATION_PROMPT.format(
            count=count,
            analysis_text=analysis_text,
            requirement_summary=requirement_summary,
            refinement_context="",
        )

        return self._call_claude(prompt_text, image_path)

    def regenerate(self, analysis: dict, requirement_summary: str,
                   image_path: str,
                   approved_prompts: list[dict],
                   rejected_prompts: list[dict]) -> list[dict]:
        """
        重新生成被拒绝的 prompt。

        Args:
            analysis: 分析结果
            requirement_summary: 总体要求
            image_path: 原图路径
            approved_prompts: 已通过的 prompt（作为风格参考）
            rejected_prompts: 被拒绝的 prompt（含 feedback）

        Returns:
            新生成的 prompt 列表
        """
        analysis_text = self._format_analysis(analysis)

        approved_text = "\n".join(
            f"  #{p['index']} {p['title']}: {p['image_prompt'][:80]}..."
            for p in approved_prompts
        ) if approved_prompts else "（无）"

        rejected_text = "\n".join(
            f"  #{p['index']} {p['title']}: 被拒原因 → {p.get('feedback', '用户不满意')}"
            for p in rejected_prompts
        )

        refinement = REFINEMENT_PROMPT.format(
            approved_prompts=approved_text,
            rejected_info=rejected_text,
            regen_count=len(rejected_prompts),
        )

        prompt_text = PROMPT_GENERATION_PROMPT.format(
            count=len(rejected_prompts),
            analysis_text=analysis_text,
            requirement_summary=requirement_summary,
            refinement_context=refinement,
        )

        results = self._call_claude(prompt_text, image_path)

        # 重新分配 index 以匹配被拒绝的位置
        for i, result in enumerate(results):
            if i < len(rejected_prompts):
                result["index"] = rejected_prompts[i]["index"]

        return results

    def _call_claude(self, prompt_text: str, image_path: str) -> list[dict]:
        """调用 Claude 并解析结果"""
        ref_data = load_image_as_base64(image_path)

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
            print(f"  [PromptGen] 调用 Claude（{config.CLAUDE_MODEL}）生成 prompt...")
            msg = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = msg.content[0].text
            tokens_in = msg.usage.input_tokens
            tokens_out = msg.usage.output_tokens
            cost = (tokens_in * 3 + tokens_out * 15) / 1_000_000
            print(f"  [PromptGen] Token: {tokens_in}+{tokens_out}，约 ${cost:.4f}")

            return self._parse_ideas(raw)

        except Exception as e:
            print(f"  [PromptGen] 调用失败: {e}")
            return []

    def _format_analysis(self, analysis: dict) -> str:
        """将分析结果格式化为文本供提示词使用"""
        a = analysis.get("analysis", {})
        lines = [
            f"- 主体: {a.get('subject', '未知')}",
            f"- 风格: {a.get('style', '未知')}",
            f"- 配色: {', '.join(a.get('colors', []))}",
            f"- 氛围: {a.get('mood', '未知')}",
            f"- 总结: {a.get('summary', '')}",
        ]
        return "\n".join(lines)

    def _parse_ideas(self, raw_text: str) -> list[dict]:
        """从 Claude 输出中解析 JSON 格式的 prompt 列表（带自动修复）"""
        ideas = safe_parse_json(raw_text, expect_type="array")
        if not ideas:
            print(f"  [PromptGen] JSON 解析失败")
            return []

        for i, idea in enumerate(ideas, 1):
            idea.setdefault("index", i)
            idea.setdefault("title", f"方案{i}")
            idea.setdefault("image_prompt", "")
            idea.setdefault("concept", "")
        return ideas
