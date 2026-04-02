"""Batch sticker pack generation

Run StickerPackPipeline for multiple themes sequentially, with resume and skip support.

Usage:
  python -m scripts.batch_run
  python -m scripts.batch_run --skip-images
  python -m scripts.batch_run --only "Hot Dog Cartoon" "Cowboy Western"
  python -m scripts.batch_run --resume
  python -m scripts.batch_run --extra "brief text..."
  python -m scripts.batch_run --extra-file path/to/brief.txt

Brief mode (.md files containing JSON trend structure, see src.services.batch.sticker_prompts):
  python -m scripts.batch_run --brief-dir path/to/briefs
  python -m scripts.batch_run --brief-dir ./briefs --only "Cherry Coded"
"""

import json
import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from scripts.script_utils import PROJECT_ROOT  # noqa: F401 (triggers sys.path + dotenv)
from src.services.ai.openai_service import OpenAIService
from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_pipeline import StickerPackPipeline
from src.services.batch.sticker_prompts import load_trend_brief_md

# ── Theme list ──────────────────────────────────────────────
THEMES = [
    "FIFA World Cup 2026 Fan Stickers",
    "Award Night Reaction Mood Stickers",
    "Cherry Blossom Hanami Picnic",
    "Chaotic Cute Office AI Buddy",
]

BATCH_OUTPUT_DIR = "output/batch"
MANIFEST_FILE = "batch_manifest.json"


def make_progress_printer(theme: str, idx: int, total: int):
    """Create a progress callback for a single theme with a global index prefix."""
    tag = f"[{idx}/{total} {theme}]"

    def on_progress(event: str, data: dict):
        if event == "pipeline_start":
            print(f"\n{'='*70}")
            print(f"{tag} Pipeline Start")
            print(f"  Output: {data.get('output_dir')}")
            print(f"{'='*70}")
        elif event == "planner_start":
            print(f"{tag} [1/5] Planner Agent ...")
        elif event == "planner_done":
            print(f"{tag} [1/5] Planner done ({data.get('chars')} chars)")
        elif event == "designer_start":
            print(f"{tag} [2/5] Designer Agent (Sticker Spec) ...")
        elif event == "designer_done":
            print(f"{tag} [2/5] Designer done ({data.get('chars')} chars)")
        elif event == "prompter_start":
            print(f"{tag} [3/5] Prompt Builder ...")
        elif event == "prompter_done":
            print(f"{tag} [3/5] Prompt Builder done ({data.get('chars')} chars)")
        elif event == "images_start":
            print(f"{tag} [4/5] Generating {data.get('count')} sticker images ...")
        elif event == "images_done":
            print(f"{tag} [4/5] Images done ({data.get('count')} images)")
        elif event == "pipeline_done":
            d = data.get("duration", 0) or 0
            print(f"{tag} [5/5] DONE  prompts={data.get('prompts')}  "
                  f"images={data.get('images')}  time={d:.0f}s")
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
    parser.add_argument(
        "--extra", default="",
        help="Background/trend brief text, passed as Additional notes to the Planner for all themes",
    )
    parser.add_argument(
        "--extra-file",
        default=None,
        metavar="PATH",
        help="UTF-8 text file, content merged with --extra as Additional notes",
    )
    parser.add_argument(
        "--style", default=None,
        help="Preferred style, passed to Planner (optional)",
    )
    parser.add_argument(
        "--color-mood", default=None,
        help="Preferred color mood, passed to Planner (optional)",
    )
    parser.add_argument(
        "--brief-dir",
        default=None,
        metavar="DIR",
        help="Each .md file in this directory is parsed as a JSON trend brief and run through "
        "the pipeline; manifest key is trend_name (falls back to filename)",
    )
    args = parser.parse_args()

    brief_mode = bool(args.brief_dir)

    if args.brief_dir:
        brief_root = Path(args.brief_dir)
        if not brief_root.is_dir():
            print(f"Error: --brief-dir is not a directory: {brief_root}", file=sys.stderr)
            sys.exit(1)
        md_files = sorted(brief_root.glob("*.md"))
        if not md_files:
            print(f"Error: no .md files found in directory: {brief_root}", file=sys.stderr)
            sys.exit(1)
        jobs = []
        for md_path in md_files:
            try:
                brief = load_trend_brief_md(md_path)
            except (OSError, ValueError) as e:
                print(f"Error: failed to parse brief {md_path}: {e}", file=sys.stderr)
                sys.exit(1)
            label = (brief.get("trend_name") or "").strip() or md_path.stem
            jobs.append({"label": label, "brief": brief, "source": str(md_path)})
        if args.only:
            only_set = set(args.only)
            jobs = [
                j for j in jobs
                if j["label"] in only_set
                or Path(j["source"]).stem in only_set
            ]
            if not jobs:
                print("Error: --only did not match any brief files", file=sys.stderr)
                sys.exit(1)
        themes = jobs
    else:
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
    print(f"  Brief mode: {brief_mode}")
    print(f"{'#'*70}\n")

    user_extra = (args.extra or "").strip()
    if args.extra_file:
        file_text = Path(args.extra_file).read_text(encoding="utf-8").strip()
        user_extra = f"{file_text}\n\n{user_extra}".strip() if user_extra else file_text

    for idx, item in enumerate(themes, 1):
        if brief_mode:
            theme = item["label"]
            trend_brief = item["brief"]
            source_md = item.get("source")
        else:
            theme = item
            trend_brief = None
            source_md = None

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

        run_kw = dict(
            theme=theme,
            user_style=args.style,
            user_color_mood=args.color_mood,
            user_extra=user_extra,
            skip_images=args.skip_images,
            on_progress=progress_cb,
        )
        if trend_brief is not None:
            run_kw["trend_brief"] = trend_brief
        result = pipeline.run(**run_kw)

        entry = {
            "status": result.status,
            "output_dir": str(pipeline.output_dir),
            "prompts": len(result.prompts_flat),
            "categories": len(result.prompts_grouped),
            "images": len(result.image_paths),
            "duration_seconds": result.duration_seconds,
            "error": result.error,
        }
        if source_md:
            entry["brief_source"] = source_md
        manifest["themes"][theme] = entry
        save_manifest(batch_dir, manifest)

        results_summary.append({
            "theme": theme,
            "status": result.status,
            "prompts": len(result.prompts_flat),
            "images": len(result.image_paths),
            "duration": result.duration_seconds,
        })

    batch_elapsed = time.time() - batch_start

    print(f"\n{'#'*70}")
    print(f"  BATCH COMPLETE — {total} themes in {batch_elapsed:.0f}s")
    print(f"{'#'*70}\n")
    print(f"{'Theme':<35} {'Status':<12} {'Prompts':>8} {'Images':>7} {'Time':>8}")
    print("-" * 75)
    for r in results_summary:
        d = r.get("duration")
        t_str = f"{d:.0f}s" if d else "-"
        print(f"{r['theme']:<35} {r['status']:<12} {r.get('prompts', '-'):>8} "
              f"{r.get('images', '-'):>7} {t_str:>8}")
    print()
    print(f"Manifest: {batch_dir / MANIFEST_FILE}")
    print(f"Output:   {batch_dir}")


if __name__ == "__main__":
    main()
