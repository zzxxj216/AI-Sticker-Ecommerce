"""端到端测试: StickerPackPipeline

串联 Planner → Designer → Prompter → 预览图 → 生图 的完整流程。

用法:
  python -m scripts.test_pipeline --theme "Hot Dog Cartoon"
  python -m scripts.test_pipeline --theme "Route 66 Roadtrip" --skip-images
  python -m scripts.test_pipeline --theme "猫咪日常" --style "kawaii" --skip-images
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.openai_service import OpenAIService
from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_pipeline import StickerPackPipeline


def on_progress(event: str, data: dict):
    """打印 pipeline 进度事件"""
    if event == "pipeline_start":
        print(f"\n{'='*60}")
        print(f"Pipeline Start — Theme: {data.get('theme')}")
        print(f"Output: {data.get('output_dir')}")
        print(f"{'='*60}\n")
    elif event == "planner_start":
        print("[1/6] Planner Agent 开始...")
    elif event == "planner_done":
        print(f"[1/6] Planner Agent 完成 ({data.get('chars')} chars)")
    elif event == "designer_start":
        print("[2/6] Designer Agent 开始...")
    elif event == "designer_done":
        print(f"[2/6] Designer Agent 完成 ({data.get('chars')} chars)")
    elif event == "prompter_start":
        print("[3/6] Prompter Agent 开始...")
    elif event == "prompter_done":
        print(f"[3/6] Prompter Agent 完成 ({data.get('chars')} chars)")
    elif event == "preview_start":
        cats = data.get("categories", 0)
        print(f"[4/6] 按主题生成预览图 ({cats} 个主题)...")
    elif event == "preview_done":
        count = data.get("count", 0)
        print(f"[4/6] 预览图完成 ({count} 张)")
        paths = data.get("paths", {})
        for cat, p in (paths.items() if isinstance(paths, dict) else enumerate(paths)):
            print(f"       [{cat}] {p}")
    elif event == "images_start":
        print(f"[5/6] 逐张生图开始 ({data.get('count')} 张)...")
    elif event == "images_done":
        count = data.get("count", 0)
        print(f"[5/6] 逐张生图完成 ({count} 张)")
    elif event == "pipeline_done":
        print(f"\n{'='*60}")
        print("Pipeline 完成!")
        print(f"  Prompts: {data.get('prompts')} (across {data.get('categories')} categories)")
        print(f"  Previews: {data.get('previews')}")
        print(f"  Images: {data.get('images')}")
        print(f"  Duration: {data.get('duration', 0):.1f}s")
        print(f"{'='*60}\n")
    elif event == "pipeline_error":
        print(f"\nPipeline 失败: {data.get('error')}")


def main():
    parser = argparse.ArgumentParser(description="Test StickerPackPipeline")
    parser.add_argument("--theme", default="Hot Dog Cartoon sticker pack", help="Sticker pack theme")
    parser.add_argument("--style", default=None, help="User style preference")
    parser.add_argument("--color-mood", default=None, help="User color mood preference")
    parser.add_argument("--extra", default="", help="Extra instructions")
    parser.add_argument("--skip-images", action="store_true", help="Skip individual sticker image generation")
    parser.add_argument("--output-dir", default="output/pipeline_test", help="Output directory")
    args = parser.parse_args()

    openai_svc = OpenAIService()
    gemini_svc = GeminiService()

    pipeline = StickerPackPipeline(
        openai_service=openai_svc,
        gemini_service=gemini_svc,
        output_dir=args.output_dir,
    )

    result = pipeline.run(
        theme=args.theme,
        user_style=args.style,
        user_color_mood=args.color_mood,
        user_extra=args.extra,
        skip_images=args.skip_images,
        on_progress=on_progress,
    )

    print(f"Status: {result.status}")
    if result.error:
        print(f"Error: {result.error}")

    run_dir = pipeline.output_dir
    print(f"\nOutput files saved to: {run_dir}/")
    print(f"  planner_output.txt")
    print(f"  designer_output.txt")
    print(f"  prompter_output.txt")
    if result.prompts_grouped:
        print(f"\n  Prompt categories:")
        for cat, stickers in result.prompts_grouped.items():
            print(f"    [{cat}] {len(stickers)} prompts")
    if result.preview_paths:
        print(f"\n  previews/ ({len(result.preview_paths)} files)")
        for cat, p in result.preview_paths.items():
            print(f"    [{cat}] {p}")
    if result.image_paths:
        print(f"  images/ ({len(result.image_paths)} files)")

    print("\n=== Pipeline Test COMPLETE ===")


if __name__ == "__main__":
    main()
