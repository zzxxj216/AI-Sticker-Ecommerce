"""
Agent 3 — 贴纸生成智能体
封装现有 StickerImageGenerator，接收 prompt 列表 + 参考图，批量生成贴纸。
"""
import sys
import os
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import config
from image_generator import StickerImageGenerator


class StickerGeneratorAgent:
    """贴纸生成智能体：封装 Gemini 图片生成能力"""

    def __init__(self):
        self._gemini = None

    @property
    def gemini(self):
        if self._gemini is None:
            self._gemini = StickerImageGenerator()
        return self._gemini

    def generate(self, prompts: list[dict],
                 ref_image: Optional[str] = None) -> list[dict]:
        """
        批量生成贴纸图片。

        Args:
            prompts: prompt 列表，每条含 index/title/image_prompt
            ref_image: 原图路径作为参考图

        Returns:
            生成结果列表，格式与 StickerImageGenerator.generate_batch() 一致
        """
        if not prompts:
            print("  [StickerGen] 无 prompt 数据，跳过生成")
            return []

        today = datetime.now().strftime("%Y%m%d")
        session = datetime.now().strftime("agent_%H%M%S")
        output_dir = config.IMAGE_OUTPUT_DIR / today / session
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"  [StickerGen] 保存目录: {output_dir}")
        print(f"  [StickerGen] 开始生成 {len(prompts)} 张贴纸...")

        results = self.gemini.generate_batch(prompts, ref_image=ref_image)

        success = [r for r in results if r["success"]]
        print(f"  [StickerGen] 完成！成功 {len(success)}/{len(results)} 张")
        if success:
            print(f"  [StickerGen] 图片目录: {output_dir}")

        return results
