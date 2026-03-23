"""批量贴纸包生成

对多个主题依次运行 StickerPackPipeline，支持断点续跑和跳过已完成主题。

用法:
  python -m scripts.batch_run
  python -m scripts.batch_run --skip-images
  python -m scripts.batch_run --only "Hot Dog Cartoon" "Cowboy Western"
  python -m scripts.batch_run --resume
"""

import sys
import json
import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.openai_service import OpenAIService
from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_pipeline import StickerPackPipeline

# ── 主题列表 ──────────────────────────────────────────────
THEMES = [
    "Cowboy Western",
    "American Traditional Tattoo",
    "Hot Dog Cartoon",
    "Street Graffiti Monsters",
    "American Eagle Symbol",
]

BATCH_OUTPUT_DIR = "output/batch"
MANIFEST_FILE = "batch_manifest.json"


def make_progress_printer(theme: str, idx: int, total: int):
    """为单个主题创建 progress callback，带全局序号前缀。"""
    tag = f"[{idx}/{total} {theme}]"

    def on_progress(event: str, data: dict):
        if event == "pipeline_start":
            print(f"\n{'='*70}")
            print(f"{tag} Pipeline Start")
            print(f"  Output: {data.get('output_dir')}")
            print(f"{'='*70}")
        elif event == "planner_start":
            print(f"{tag} [1/6] Planner Agent ...")
        elif event == "planner_done":
            print(f"{tag} [1/6] Planner done ({data.get('chars')} chars)")
        elif event == "designer_start":
            print(f"{tag} [2/6] Designer Agent ...")
        elif event == "designer_done":
            print(f"{tag} [2/6] Designer done ({data.get('chars')} chars)")
        elif event == "prompter_start":
            print(f"{tag} [3/6] Prompter Agent ...")
        elif event == "prompter_done":
            print(f"{tag} [3/6] Prompter done ({data.get('chars')} chars)")
        elif event == "preview_start":
            print(f"{tag} [4/6] Preview ({data.get('categories')} categories) ...")
        elif event == "preview_done":
            print(f"{tag} [4/6] Preview done ({data.get('count')} images)")
        elif event == "images_start":
            print(f"{tag} [5/6] Generating {data.get('count')} sticker images ...")
        elif event == "images_done":
            print(f"{tag} [5/6] Images done ({data.get('count')} images)")
        elif event == "pipeline_done":
            d = data.get("duration", 0) or 0
            print(f"{tag} [6/6] DONE  prompts={data.get('prompts')}  "
                  f"previews={data.get('previews')}  images={data.get('images')}  "
                  f"time={d:.0f}s")
        elif event == "pipeline_error":
            print(f"{tag} FAILED: {data.get('error')}")

    return on_progress


def load_manifest(batch_dir: Path) -> dict:
    path = batch_dir / MANIFEST_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"started_at": None, "themes": {}}


def save_manifest(batch_dir: Path, manifest: dict):
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / MANIFEST_FILE
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Batch sticker pack generation")
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="Only run these themes (space-separated)",
    )
    parser.add_argument(
        "--skip-images", action="store_true",
        help="Skip individual sticker image generation (text + preview only)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last batch, skip already-completed themes",
    )
    parser.add_argument(
        "--output-dir", default=BATCH_OUTPUT_DIR,
        help="Base output directory",
    )
    args = parser.parse_args()

    themes = args.only if args.only else THEMES
    batch_dir = Path(args.output_dir)

    if args.resume:
        manifest = load_manifest(batch_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = Path(args.output_dir) / timestamp
        manifest = {"started_at": timestamp, "themes": {}}

    batch_dir.mkdir(parents=True, exist_ok=True)
    save_manifest(batch_dir, manifest)

    openai_svc = OpenAIService()
    gemini_svc = GeminiService()

    total = len(themes)
    results_summary = []
    batch_start = time.time()

    print(f"\n{'#'*70}")
    print(f"  BATCH RUN — {total} themes")
    print(f"  Output: {batch_dir}")
    print(f"  Skip images: {args.skip_images}")
    print(f"{'#'*70}\n")

    for idx, theme in enumerate(themes, 1):
        existing = manifest["themes"].get(theme, {})
        if args.resume and existing.get("status") == "completed":
            print(f"[{idx}/{total}] {theme} — already completed, skipping")
            results_summary.append({"theme": theme, "status": "skipped (completed)"})
            continue

        pipeline = StickerPackPipeline(
            openai_service=openai_svc,
            gemini_service=gemini_svc,
            output_dir=str(batch_dir),
        )

        progress_cb = make_progress_printer(theme, idx, total)

        result = pipeline.run(
            theme=theme,
            skip_images=args.skip_images,
            on_progress=progress_cb,
        )

        entry = {
            "status": result.status,
            "output_dir": str(pipeline.output_dir),
            "prompts": len(result.prompts_flat),
            "categories": len(result.prompts_grouped),
            "previews": len(result.preview_paths),
            "images": len(result.image_paths),
            "duration_seconds": result.duration_seconds,
            "error": result.error,
        }
        manifest["themes"][theme] = entry
        save_manifest(batch_dir, manifest)

        results_summary.append({
            "theme": theme,
            "status": result.status,
            "prompts": len(result.prompts_flat),
            "previews": len(result.preview_paths),
            "images": len(result.image_paths),
            "duration": result.duration_seconds,
        })

    batch_elapsed = time.time() - batch_start

    print(f"\n{'#'*70}")
    print(f"  BATCH COMPLETE — {total} themes in {batch_elapsed:.0f}s")
    print(f"{'#'*70}\n")
    print(f"{'Theme':<35} {'Status':<12} {'Prompts':>8} {'Previews':>9} {'Images':>7} {'Time':>8}")
    print("-" * 85)
    for r in results_summary:
        d = r.get("duration")
        t_str = f"{d:.0f}s" if d else "-"
        print(f"{r['theme']:<35} {r['status']:<12} {r.get('prompts', '-'):>8} "
              f"{r.get('previews', '-'):>9} {r.get('images', '-'):>7} {t_str:>8}")
    print()
    print(f"Manifest: {batch_dir / MANIFEST_FILE}")
    print(f"Output:   {batch_dir}")


if __name__ == "__main__":
    main()
