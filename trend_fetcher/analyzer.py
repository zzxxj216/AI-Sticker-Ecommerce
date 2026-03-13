"""
Claude AI 分析模块
输入: 聚合后的热点数据
输出: 针对贴纸业务的具体设计选题建议
"""
import json
from datetime import datetime
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from config import config


STICKER_ANALYSIS_PROMPT = """你是一个专业的贴纸产品策划师，专注于为跨境电商（TikTok Shop、Amazon、Shopify）选品。

以下是今天从Google Trends、Reddit、新闻等渠道收集到的海外热点数据：

{trends_summary}

请从贴纸产品的角度分析这些热点，为我生成 **5个最有潜力的贴纸设计选题**。

每个选题请严格按以下格式输出（字段名称保持一致，冒号后面填内容）：

---
**选题 1: 主题名称**
- 热点来源: 来自哪些平台
- 目标受众: 例如 18-25岁女性，喜欢可爱风格
- 设计方向: 具体的视觉设计建议，例如 粉色系、手写字体、星星装饰
- 推荐贴纸类型: 防水贴纸/镭射贴纸/透明贴纸/手账贴纸等
- 关键词标签: cute sticker, kawaii, ...（5-8个英文标签）
- image_prompt: A cute [subject] sticker design, flat vector art style, white background, clean bold outline, kawaii aesthetic, pastel colors, no text, isolated sticker format
- 热度评估: 8/10，理由说明
- 版权风险: 低，说明
---

注意：image_prompt 字段必须是英文，直接描述图像内容，适合AI图像生成，要求白底、简洁轮廓、卡通风格。

最后给出一个总体市场机会说明（50字以内）。"""


class StickerIdeaAnalyzer:

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not config.ANTHROPIC_API_KEY:
                raise ValueError("未配置 ANTHROPIC_API_KEY，请在 .env 文件中添加")
            import anthropic
            kwargs = {"api_key": config.ANTHROPIC_API_KEY}
            if config.ANTHROPIC_BASE_URL:
                kwargs["base_url"] = config.ANTHROPIC_BASE_URL
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def analyze(self, aggregated_result: dict) -> Optional[dict]:
        """
        使用 Claude 分析热点并生成贴纸选题

        Args:
            aggregated_result: aggregator.py 输出的聚合结果

        Returns:
            包含 AI 分析结果的字典，或 None（API 未配置时）
        """
        if not config.ANTHROPIC_API_KEY:
            print("  [Claude] 未配置 API Key，跳过 AI 分析")
            print("  [Claude] 提示: 在 .env 文件中设置 ANTHROPIC_API_KEY 即可启用")
            return None

        print("  [Claude] 开始 AI 分析选题...")

        trends_summary = self._format_trends_for_prompt(aggregated_result)
        prompt = STICKER_ANALYSIS_PROMPT.format(trends_summary=trends_summary)

        try:
            message = self.client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            analysis_text = message.content[0].text

            result = {
                "analyzed_at": datetime.now().isoformat(),
                "model": config.CLAUDE_MODEL,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "analysis": analysis_text,
                "sticker_ideas": self._extract_ideas(analysis_text),
            }

            idea_count = len(result["sticker_ideas"])
            cost_usd = (message.usage.input_tokens * 3 + message.usage.output_tokens * 15) / 1_000_000
            print(f"  [Claude] 生成 {idea_count} 个选题，Token 消耗: {message.usage.input_tokens}+{message.usage.output_tokens}，约 ${cost_usd:.4f}")

            return result

        except Exception as e:
            print(f"  [Claude] 分析失败: {e}")
            return None

    def _format_trends_for_prompt(self, aggregated_result: dict) -> str:
        """将聚合数据格式化为 Claude 可读的文本"""
        lines = []

        # 综合 Top 热点
        top_trends = aggregated_result.get("top_trends", [])[:15]
        if top_trends:
            lines.append("【综合热点 Top 15（按热度评分排序）】")
            for i, t in enumerate(top_trends, 1):
                sources = ", ".join(t.get("sources", []))
                traffic = t.get("google_traffic", "")
                reddit_score = t.get("top_reddit_score", 0)
                detail = []
                if traffic:
                    detail.append(f"Google搜索量:{traffic}")
                if reddit_score > 0:
                    detail.append(f"Reddit:{reddit_score:,}赞")
                detail_str = f" ({', '.join(detail)})" if detail else ""
                lines.append(f"{i}. {t['keyword']}{detail_str} [来源: {sources}]")

        # 跨源共现热点（最可信）
        cross = aggregated_result.get("cross_source_trends", [])[:8]
        if cross:
            lines.append("\n【多平台共现热点（最可靠信号）】")
            for t in cross:
                lines.append(f"- {t['keyword']} [出现在: {', '.join(t['sources'])}]")

        # Reddit 高赞内容
        reddit_top = aggregated_result.get("by_source", {}).get("reddit", [])[:5]
        if reddit_top:
            lines.append("\n【Reddit 高赞内容（社区最爱）】")
            for p in reddit_top:
                lines.append(f"- r/{p.get('subreddit', '?')}: {p['keyword']} ({p.get('score', 0):,} 赞)")

        return "\n".join(lines)

    def _extract_ideas(self, analysis_text: str) -> list[dict]:
        """从 Claude 输出中提取结构化选题，包含 image_prompt 字段"""
        import re
        ideas = []
        blocks = analysis_text.split("**选题")
        for idx, block in enumerate(blocks[1:], 1):
            lines = block.strip().split("\n")
            title_line = lines[0] if lines else ""

            # 提取标题
            title_match = re.search(r'\d+:\s*(.+?)(?:\*\*|$)', title_line)
            title = title_match.group(1).strip().strip("*") if title_match else title_line.strip("*: ")

            # 提取各字段（以 "- 字段名:" 格式）
            field_map = {
                "image_prompt": "",
                "design_direction": "",
                "keywords": "",
                "sticker_type": "",
                "heat_score": "",
                "copyright_risk": "",
            }

            field_patterns = {
                "image_prompt": r'-\s*image_prompt\s*:\s*(.+?)(?=\n-|\n---|\Z)',
                "design_direction": r'-\s*设计方向\s*:\s*(.+?)(?=\n-|\n---|\Z)',
                "keywords": r'-\s*关键词标签\s*:\s*(.+?)(?=\n-|\n---|\Z)',
                "sticker_type": r'-\s*推荐贴纸类型\s*:\s*(.+?)(?=\n-|\n---|\Z)',
                "heat_score": r'-\s*热度评估\s*:\s*(.+?)(?=\n-|\n---|\Z)',
                "copyright_risk": r'-\s*版权风险\s*:\s*(.+?)(?=\n-|\n---|\Z)',
            }

            block_text = "\n".join(lines)
            for field, pattern in field_patterns.items():
                m = re.search(pattern, block_text, re.DOTALL)
                if m:
                    field_map[field] = m.group(1).strip()

            # 如果没有解析到 image_prompt，用设计方向生成兜底提示词
            if not field_map["image_prompt"] and field_map["design_direction"]:
                field_map["image_prompt"] = (
                    f"{field_map['design_direction']}, cute sticker design, "
                    "flat vector art, white background, clean outline, kawaii style, "
                    "no text, isolated sticker format, digital illustration"
                )

            ideas.append({
                "index": idx,
                "title": title,
                "image_prompt": field_map["image_prompt"],
                "design_direction": field_map["design_direction"],
                "keywords": field_map["keywords"],
                "sticker_type": field_map["sticker_type"],
                "heat_score": field_map["heat_score"],
                "copyright_risk": field_map["copyright_risk"],
                "raw": block.strip(),
            })
        return ideas
