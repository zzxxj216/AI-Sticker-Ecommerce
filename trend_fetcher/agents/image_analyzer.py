"""
Agent 1 — 图片分析智能体
调用 Claude Vision 分析用户上传的图片，动态生成适合的选择维度和选项。
"""
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import config
from agents.utils import load_image_as_base64, safe_parse_json

# ── 图片分析提示词 ─────────────────────────────────────────────
IMAGE_ANALYSIS_PROMPT = """你是一位专业的素材图片设计顾问。用户提供了图片和/或文本描述，希望基于这些信息制作一系列素材图片。

请你完成以下两个任务：

## 任务 1：图片分析
仔细观察图片，分析其：
- 主体是什么（角色、物品、场景等）
- 当前的艺术风格
- 配色方案（主色、辅色）
- 整体氛围/情绪
- 这张图适合做什么类型的素材图片

## 任务 2：生成选择维度
根据图片内容，**自行判断**最适合这张图的选择维度（3~10 个维度）。
不要使用固定模板，而是根据图片特点灵活决定。例如：
- 如果图片是一个角色 → 可以有"姿态/动作"、"表情"、"场景"、"画风"等维度
- 如果图片是一个风景 → 可以有"季节"、"时间段"、"氛围"、"构图风格"等维度
- 如果图片是一个物品 → 可以有"使用场景"、"装饰风格"、"配色方案"等维度

每个维度提供 3~5 个具体选项，最后一个选项固定为"其他（自定义）"。

{input_mode_context}

{user_input_context}

{refinement_context}

重要：JSON 字符串值中**不要使用英文双引号**，如需引用请使用中文引号「」。
重要：无论输入是否包含图片，你都必须直接完成分析并输出 JSON。
禁止要求用户补充上传图片，禁止提问，禁止输出解释性文字。

严格按以下 JSON 格式输出，不要添加任何其他内容：
```json
{{
  "analysis": {{
    "subject": "图片主体描述（中文）",
    "style": "当前风格描述（中文）",
    "colors": ["主色", "辅色", "..."],
    "mood": "整体氛围（中文）",
    "summary": "一句话总结这张图适合做什么类型的素材图片（中文）"
  }},
  "dimensions": [
    {{
      "key": "维度英文标识（如 pose, scene, art_style）",
      "label": "维度中文名称",
      "description": "简要说明这个维度让用户选什么",
      "options": [
        {{"id": "1", "name": "选项名称（中文）", "description": "选项说明（中文，一句话）"}},
        {{"id": "2", "name": "选项名称", "description": "选项说明"}},
        {{"id": "3", "name": "选项名称", "description": "选项说明"}},
        {{"id": "other", "name": "其他（自定义）", "description": "输入你想要的内容"}}
      ]
    }}
  ],
  "suggested_count": 5
}}
```"""

REFINEMENT_CONTEXT_TEMPLATE = """
## 特别注意
用户对之前生成的部分维度不满意。

被拒绝的维度（用户不想要这些维度，必须用全新的维度概念替换）：
{rejected_dims}

用户反馈：
{feedback}

以下维度已通过，请原样保留（不要修改、不要重复）：
{approved_dims}

## 重生成规则（必须严格遵守）
1. **禁止复用被拒维度的概念**：被拒维度的 key、label、主题概念都不能再出现。
   例如用户拒绝了「姿态动作」(key=pose)，你不能再输出任何关于姿态/动作/姿势的维度。
2. **必须输出全新的维度**：用完全不同的角度/概念来替换，如换成「材质质感」「画面构图」「光影效果」「季节氛围」等。
3. 新维度的 key 不能与被拒维度或已通过维度的 key 重复。
4. 每个新维度至少提供 3 个具体选项 + 1 个「其他（自定义）」。
5. 仅输出完整 JSON，不要附加解释文字。
"""


class ImageAnalyzerAgent:
    """图片分析智能体：分析图片并生成动态选择维度"""

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

    def analyze(self, image_path: Optional[str],
                user_text: str = "",
                refinement_context: str = "") -> Optional[dict]:
        """
        分析图片并生成动态选择维度。

        Args:
            image_path: 图片路径 / URL / base64（可为空）
            user_text: 用户补充文本描述（可为空）
            refinement_context: 迭代上下文（被拒维度的反馈），首次调用传空

        Returns:
            包含 analysis、dimensions、suggested_count 的字典，失败返回 None
        """
        has_text = bool((user_text or "").strip())
        ref_data = load_image_as_base64(image_path) if image_path else None

        if not ref_data and not has_text:
            print("  [Analyzer] 缺少输入：请至少提供图片或文本描述")
            return None

        prompt_text = IMAGE_ANALYSIS_PROMPT.format(
            input_mode_context=self._build_input_mode_context(bool(ref_data), has_text),
            user_input_context=self._build_user_input_context(user_text),
            refinement_context=refinement_context,
        )

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
            print(f"  [Analyzer] 调用 Claude（{config.CLAUDE_MODEL}）分析输入...")
            msg = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = msg.content[0].text
            tokens_in = msg.usage.input_tokens
            tokens_out = msg.usage.output_tokens
            cost = (tokens_in * 3 + tokens_out * 15) / 1_000_000
            print(f"  [Analyzer] Token: {tokens_in}+{tokens_out}，约 ${cost:.4f}")

            return self._parse_result(raw)

        except Exception as e:
            print(f"  [Analyzer] 调用失败: {e}")
            return None

    def _build_user_input_context(self, user_text: str) -> str:
        """构建用户补充文本上下文。"""
        txt = (user_text or "").strip()
        if not txt:
            return ""
        return (
            "## 用户补充描述\n"
            f"{txt}\n\n"
            "请将以上描述与图片信息（若有）一起纳入分析；"
            "若文本与图片有冲突，以用户补充描述为准。"
        )

    def _build_input_mode_context(self, has_image: bool, has_text: bool) -> str:
        """根据输入类型给模型明确执行约束，避免其拒答。"""
        if has_image and has_text:
            return (
                "## 输入模式\n"
                "- 当前输入同时包含图片与文本描述。\n"
                "- 请综合两者输出结果；若冲突，以文本描述优先。"
            )
        if has_image:
            return (
                "## 输入模式\n"
                "- 当前输入仅包含图片。\n"
                "- 请只基于图像内容完成分析并输出 JSON。"
            )
        if has_text:
            return (
                "## 输入模式\n"
                "- 当前输入仅包含文本描述，未提供图片。\n"
                "- 请仅基于文本推断并完成分析，正常输出 JSON。\n"
                "- 若信息不完整，可做合理假设，但不能要求用户补充图片。"
            )
        return ""

    def build_refinement_context(self, rejected_dims: list[dict],
                                  approved_dims: list[dict]) -> str:
        """构建迭代上下文，用于重新生成被拒维度"""
        if not rejected_dims:
            return ""

        rejected_lines = []
        for d in rejected_dims:
            old_opts = [o.get("name", "") for o in d.get("options", []) if o.get("id") != "other"]
            old_opts_txt = " / ".join([x for x in old_opts if x]) if old_opts else "（无）"
            rejected_lines.append(
                f"- 维度「{d['label']}」(key={d['key']}): {d.get('feedback', '用户不满意')}；原选项：{old_opts_txt}"
            )
        rejected_text = "\n".join(rejected_lines)
        def _fmt_sel(d):
            names = d.get("selection_name", "")
            if isinstance(names, list):
                return " + ".join(names) if names else "—"
            return str(names) if names else "—"

        approved_text = "\n".join(
            f"- 维度「{d['label']}」(key={d['key']}): 已通过，用户选择了「{_fmt_sel(d)}」"
            for d in approved_dims
        )
        return REFINEMENT_CONTEXT_TEMPLATE.format(
            rejected_dims=rejected_text,
            feedback="请用全新的维度概念替换上述被拒维度，不要沿用相同主题。",
            approved_dims=approved_text if approved_text else "（无已通过的维度）",
        )

    def _parse_result(self, raw_text: str) -> Optional[dict]:
        """从 Claude 输出中解析 JSON（带自动修复）"""
        result = safe_parse_json(raw_text, expect_type="object")
        if not result:
            print(f"  [Analyzer] JSON 解析失败")
            return None

        result.setdefault("analysis", {})
        result.setdefault("dimensions", [])
        result.setdefault("suggested_count", 5)

        for dim in result["dimensions"]:
            dim.setdefault("key", "unknown")
            dim.setdefault("label", "未命名")
            dim.setdefault("description", "")
            dim.setdefault("options", [])
            has_other = any(o.get("id") == "other" for o in dim["options"])
            if not has_other:
                dim["options"].append({
                    "id": "other",
                    "name": "其他（自定义）",
                    "description": "输入你想要的内容"
                })

        return result
