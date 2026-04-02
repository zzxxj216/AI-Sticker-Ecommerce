"""End-to-end test: StickerPackPipeline

Usage:
  python -m scripts.test_pipeline --theme "Hot Dog Cartoon"
  python -m scripts.test_pipeline --theme "Route 66 Roadtrip" --skip-images
  python -m scripts.test_pipeline --theme "Cat Daily Life" --style "kawaii" --skip-images
"""

import argparse

from scripts.script_utils import print_header  # noqa: F401 (triggers sys.path setup)
from src.services.ai.openai_service import OpenAIService
from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_pipeline import StickerPackPipeline


def on_progress(event: str, data: dict):
    """Print pipeline progress events."""
    if event == "pipeline_start":
        print(f"\n{'='*60}")
        print(f"Pipeline Start - Theme: {data.get('theme')}")
        print(f"Output: {data.get('output_dir')}")
        print(f"{'='*60}\n")
    elif event == "planner_start":
        print("[1/6] Planner Agent starting...")
    elif event == "planner_done":
        print(f"[1/6] Planner Agent done ({data.get('chars')} chars)")
    elif event == "designer_start":
        print("[2/6] Designer Agent starting...")
    elif event == "designer_done":
        print(f"[2/6] Designer Agent done ({data.get('chars')} chars)")
    elif event == "prompter_start":
        print("[3/6] Prompter Agent starting...")
    elif event == "prompter_done":
        print(f"[3/6] Prompter Agent done ({data.get('chars')} chars)")
    elif event == "preview_start":
        cats = data.get("categories", 0)
        print(f"[4/6] Generating preview images ({cats} categories)...")
    elif event == "preview_done":
        count = data.get("count", 0)
        print(f"[4/6] Preview done ({count} images)")
        paths = data.get("paths", {})
        for cat, p in (paths.items() if isinstance(paths, dict) else enumerate(paths)):
            print(f"       [{cat}] {p}")
    elif event == "images_start":
        print(f"[5/6] Generating sticker images ({data.get('count')})...")
    elif event == "images_done":
        count = data.get("count", 0)
        print(f"[5/6] Image generation done ({count} images)")
    elif event == "pipeline_done":
        print(f"\n{'='*60}")
        print("Pipeline complete!")
        print(f"  Prompts: {data.get('prompts')} (across {data.get('categories')} categories)")
        print(f"  Previews: {data.get('previews')}")
        print(f"  Images: {data.get('images')}")
        print(f"  Duration: {data.get('duration', 0):.1f}s")
        print(f"{'='*60}\n")
    elif event == "pipeline_error":
        print(f"\nPipeline failed: {data.get('error')}")


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
