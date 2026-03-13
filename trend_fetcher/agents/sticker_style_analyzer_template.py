"""
自动化贴纸包生成器
根据科技主题自动生成30-50张贴纸，包含三种类型：
1. 纯文本贴纸 (Text-only)
2. 主题元素贴纸 (Element-based)
3. 文字+元素组合贴纸 (Hybrid)

使用 Claude 进行创意生成，Gemini 进行图片生成
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import anthropic
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config


class StickerPackGenerator:
    """贴纸包生成器主类"""

    def __init__(self):
        """初始化生成器"""
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("未配置 ANTHROPIC_API_KEY，请在 .env 中设置")

        # 初始化 Claude 客户端
        self.claude_client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            base_url=config.ANTHROPIC_BASE_URL if config.ANTHROPIC_BASE_URL else None
        )

        # 输出目录
        self.output_dir = config.OUTPUT_DIR / "sticker_packs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        print(f"[StickerPackGenerator] 初始化完成")
        print(f"  - Claude 模型: {config.CLAUDE_MODEL}")
        print(f"  - 输出目录: {self.output_dir}")

    def generate_pack(
        self,
        theme: str,
        total_count: int = 40,
        text_ratio: float = 0.3,
        element_ratio: float = 0.35,
        hybrid_ratio: float = 0.35
    ) -> dict:
        """
        生成完整的贴纸包

        Args:
            theme: 科技主题（如 "AI人工智能"、"区块链"、"元宇宙"）
            total_count: 总贴纸数量（默认40张）
            text_ratio: 纯文本贴纸占比（默认30%）
            element_ratio: 元素贴纸占比（默认35%）
            hybrid_ratio: 组合贴纸占比（默认35%）

        Returns:
            包含生成结果的字典
        """
        print(f"\n{'='*60}")
        print(f"开始生成贴纸包: {theme}")
        print(f"{'='*60}")
        print(f"总数量: {total_count} 张")
        print(f"  - 纯文本: {int(total_count * text_ratio)} 张")
        print(f"  - 元素: {int(total_count * element_ratio)} 张")
        print(f"  - 组合: {int(total_count * hybrid_ratio)} 张")

        start_time = time.time()

        # 计算各类型数量
        text_count = int(total_count * text_ratio)
        element_count = int(total_count * element_ratio)
        hybrid_count = total_count - text_count - element_count

        # Step 1: 使用 Claude 生成创意
        print(f"\n[Step 1/2] 使用 Claude 生成创意...")
        ideas = self._generate_ideas_with_claude(
            theme=theme,
            text_count=text_count,
            element_count=element_count,
            hybrid_count=hybrid_count
        )

        if not ideas:
            return {
                "success": False,
                "error": "创意生成失败",
                "theme": theme,
                "elapsed": time.time() - start_time
            }

        # Step 2: 使用 Gemini 生成图片
        print(f"\n[Step 2/2] 使用 Gemini 生成图片...")
        from image_generator import StickerImageGenerator

        image_gen = StickerImageGenerator()
        image_results = image_gen.generate_batch(
            sticker_ideas=ideas,
            max_workers=3
        )

        # 合并结果
        for i, idea in enumerate(ideas):
            if i < len(image_results):
                idea.update(image_results[i])

        # 保存结果
        elapsed = time.time() - start_time
        result = self._save_pack_result(theme, ideas, elapsed)

        # 打印统计
        success_count = sum(1 for idea in ideas if idea.get("success", False))
        print(f"\n{'='*60}")
        print(f"贴纸包生成完成！")
        print(f"  - 成功: {success_count}/{total_count} 张")
        print(f"  - 耗时: {elapsed:.1f}s")
        print(f"  - 结果文件: {result['result_file']}")
        print(f"{'='*60}\n")

        return result

    def _generate_ideas_with_claude(
        self,
        theme: str,
        text_count: int,
        element_count: int,
        hybrid_count: int
    ) -> list[dict]:
        """使用 Claude 生成贴纸创意"""

        prompt = self._build_creative_prompt(
            theme=theme,
            text_count=text_count,
            element_count=element_count,
            hybrid_count=hybrid_count
        )

        try:
            response = self.claude_client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=8000,
                temperature=0.9,  # 高温度以获得更多创意
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # 解析响应
            content = response.content[0].text
            ideas = self._parse_claude_response(content)

            print(f"  ✓ Claude 生成了 {len(ideas)} 个创意")
            return ideas

        except Exception as e:
            print(f"  ✗ Claude 调用失败: {e}")
            return []

    def _build_creative_prompt(
        self,
        theme: str,
        text_count: int,
        element_count: int,
        hybrid_count: int
    ) -> str:
        """构建 Claude 创意生成提示词"""
        return f"""你是一个专业的贴纸设计师，擅长为科技主题创作有趣、富有创意的贴纸。

主题: {theme}

请为这个主题设计 {text_count + element_count + hybrid_count} 张贴纸，分为三种类型：

1. **纯文本贴纸** ({text_count} 张)
   - 只包含文字，不含图形元素
   - 文字要简短、有力、有趣
   - 可以是口号、梗、流行语、技术术语等
   - 例如: "AI赋能"、"代码改变世界"、"Debug人生"

2. **主题元素贴纸** ({element_count} 张)
   - 只包含与主题相关的视觉元素，不含文字
   - 可以是图标、符号、卡通形象、抽象图形等
   - 风格要统一、简洁、易识别
   - 例如: 机器人图标、芯片图案、数据流动画

3. **文字+元素组合贴纸** ({hybrid_count} 张)
   - 同时包含文字和视觉元素
   - 文字和图形要相互呼应、融合
   - 整体设计要平衡、美观
   - 例如: 带"AI"字样的机器人、写着"创新"的灯泡

**设计要求:**
- 贴纸风格要现代、年轻化、有科技感
- 色彩鲜明，适合在聊天软件中使用
- 每张贴纸要有独特性，避免重复
- 文字要简洁（1-5个字为佳）
- 适合表达情绪、态度、观点

**输出格式（JSON）:**
请严格按照以下 JSON 格式输出，不要添加任何其他文字：

```json
[
  {{
    "index": 1,
    "type": "text",
    "title": "贴纸标题",
    "text_content": "贴纸上的文字内容（纯文本类型必填）",
    "image_prompt": "详细的英文图片生成提示词，描述贴纸的视觉效果、风格、颜色、布局等"
  }},
  {{
    "index": 2,
    "type": "element",
    "title": "贴纸标题",
    "text_content": "",
    "image_prompt": "详细的英文图片生成提示词"
  }},
  {{
    "index": 3,
    "type": "hybrid",
    "title": "贴纸标题",
    "text_content": "贴纸上的文字内容",
    "image_prompt": "详细的英文图片生成提示词，要包含文字内容的描述"
  }}
]
```

**image_prompt 编写要点:**
- 使用英文
- 描述要具体、详细（颜色、风格、构图、元素位置等）
- 对于纯文本类型：描述文字的字体、排版、装饰效果
- 对于元素类型：描述图形的形状、风格、细节
- 对于组合类型：描述文字和图形如何结合
- 添加风格关键词：modern, tech, minimalist, colorful, cartoon, flat design 等
- 示例: "Bold white text 'AI' on vibrant gradient background, modern tech style, neon glow effect, centered composition"

现在请开始创作，直接输出 JSON 数组，不要有其他内容。"""

    def _parse_claude_response(self, content: str) -> list[dict]:
        """解析 Claude 返回的 JSON 响应"""
        try:
            # 尝试提取 JSON 代码块
            import re
            json_match = re.search(r'```json\s*(\[.*?\])\s*```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析整个内容
                json_str = content.strip()

            ideas = json.loads(json_str)

            # 验证和清理数据
            cleaned_ideas = []
            for idea in ideas:
                if not isinstance(idea, dict):
                    continue
                if "image_prompt" not in idea or not idea["image_prompt"]:
                    continue

                cleaned_idea = {
                    "index": idea.get("index", len(cleaned_ideas) + 1),
                    "type": idea.get("type", "hybrid"),
                    "title": idea.get("title", f"Sticker {len(cleaned_ideas) + 1}"),
                    "text_content": idea.get("text_content", ""),
                    "image_prompt": idea["image_prompt"],
                }
                cleaned_ideas.append(cleaned_idea)

            return cleaned_ideas

        except json.JSONDecodeError as e:
            print(f"  ✗ JSON 解析失败: {e}")
            print(f"  原始内容: {content[:500]}...")
            return []
        except Exception as e:
            print(f"  ✗ 响应解析失败: {e}")
            return []

    def _save_pack_result(self, theme: str, ideas: list[dict], elapsed: float) -> dict:
        """保存贴纸包生成结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_theme = "".join(c for c in theme if c.isalnum() or c in (' ', '_')).strip()
        safe_theme = safe_theme.replace(' ', '_')[:30]

        result_file = self.output_dir / f"pack_{safe_theme}_{timestamp}.json"

        result = {
            "theme": theme,
            "timestamp": timestamp,
            "total_count": len(ideas),
            "success_count": sum(1 for idea in ideas if idea.get("success", False)),
            "elapsed": round(elapsed, 2),
            "ideas": ideas,
            "statistics": self._calculate_statistics(ideas)
        }

        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        result["result_file"] = str(result_file)
        return result

    def _calculate_statistics(self, ideas: list[dict]) -> dict:
        """计算统计信息"""
        stats = {
            "total": len(ideas),
            "success": sum(1 for idea in ideas if idea.get("success", False)),
            "failed": sum(1 for idea in ideas if not idea.get("success", False)),
            "by_type": {
                "text": sum(1 for idea in ideas if idea.get("type") == "text"),
                "element": sum(1 for idea in ideas if idea.get("type") == "element"),
                "hybrid": sum(1 for idea in ideas if idea.get("type") == "hybrid"),
            },
            "total_size_kb": sum(idea.get("size_kb", 0) for idea in ideas),
            "avg_generation_time": round(
                sum(idea.get("elapsed", 0) for idea in ideas) / len(ideas) if ideas else 0,
                2
            )
        }
        return stats


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="自动化贴纸包生成器")
    parser.add_argument("theme", help="科技主题（如：AI人工智能、区块链、元宇宙）")
    parser.add_argument("--count", type=int, default=40, help="总贴纸数量（默认40）")
    parser.add_argument("--text-ratio", type=float, default=0.3, help="纯文本占比（默认0.3）")
    parser.add_argument("--element-ratio", type=float, default=0.35, help="元素占比（默认0.35）")
    parser.add_argument("--hybrid-ratio", type=float, default=0.35, help="组合占比（默认0.35）")

    args = parser.parse_args()

    # 验证比例总和
    total_ratio = args.text_ratio + args.element_ratio + args.hybrid_ratio
    if abs(total_ratio - 1.0) > 0.01:
        print(f"错误: 三种类型占比之和必须为1.0，当前为 {total_ratio}")
        return

    # 生成贴纸包
    generator = StickerPackGenerator()
    result = generator.generate_pack(
        theme=args.theme,
        total_count=args.count,
        text_ratio=args.text_ratio,
        element_ratio=args.element_ratio,
        hybrid_ratio=args.hybrid_ratio
    )

    if result.get("success", True):
        print(f"\n✓ 生成成功！")
        print(f"  结果文件: {result['result_file']}")
    else:
        print(f"\n✗ 生成失败: {result.get('error', '未知错误')}")


if __name__ == "__main__":
    main()
