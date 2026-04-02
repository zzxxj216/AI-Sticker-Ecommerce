"""Phase 4 Test: Generate images from Prompt Builder output

Generate in role order: hero -> support -> text -> badge-label
Hero stickers first to lock the pack style, then fill remaining roles.

Usage:
  python -m scripts.test_image_generation \
      --prompt-builder-output output/test/prompt_builder_output.txt

  Generate hero only:
  python -m scripts.test_image_generation \
      --prompt-builder-output output/test/prompt_builder_output.txt \
      --roles hero

  Specific sticker numbers:
  python -m scripts.test_image_generation \
      --prompt-builder-output output/test/prompt_builder_output.txt \
      --only 1,2,3
"""

import sys
import argparse
import time
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT

from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_prompts import parse_prompt_builder_output

ROLE_ORDER = ["hero", "support", "text", "badge-label", "filler"]


def main():
    parser = argparse.ArgumentParser(description="Test image generation from Prompt Builder output")
    parser.add_argument(
        "--prompt-builder-output",
        default="output/test/prompt_builder_output.txt",
        help="Path to prompt_builder_output.txt",
    )
    parser.add_argument(
        "--roles",
        default=None,
        help="Comma-separated roles to generate (e.g. hero,support). Default: all",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated sticker numbers to generate (e.g. 1,2,3)",
    )
    parser.add_argument(
        "--output-dir",
        default="output/test/images",
        help="Output directory for generated images",
    )
    args = parser.parse_args()

    pb_path = Path(args.prompt_builder_output)
    if not pb_path.exists():
        print(f"Error: Prompt Builder output not found: {pb_path}")
        print("Please run test_prompt_builder.py first.")
        sys.exit(1)

    pb_text = pb_path.read_text(encoding="utf-8")
    parsed = parse_prompt_builder_output(pb_text)
    stickers = parsed["stickers"]

    if not stickers:
        print("Error: No stickers parsed from Prompt Builder output.")
        sys.exit(1)

    # ---- filter ----
    if args.only:
        only_ids = {int(x.strip()) for x in args.only.split(",")}
        stickers = [s for s in stickers if s["index"] in only_ids]

    if args.roles:
        allowed_roles = {r.strip().lower() for r in args.roles.split(",")}
        stickers = [s for s in stickers if s["role"].lower() in allowed_roles]

    if not stickers:
        print("No stickers matched the filter criteria.")
        sys.exit(0)

    # ---- sort by role order ----
    def role_key(s):
        r = s["role"].lower()
        return ROLE_ORDER.index(r) if r in ROLE_ORDER else len(ROLE_ORDER)

    stickers.sort(key=role_key)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Sticker Image Generation Test")
    print("=" * 60)
    print(f"Source       : {pb_path}")
    print(f"Total parsed : {len(parsed['stickers'])} stickers")
    print(f"To generate  : {len(stickers)} stickers")
    print(f"Output dir   : {output_dir}")
    print()

    for s in stickers:
        print(f"  #{s['index']:2d} [{s['role']:12s}] {s['name']}")
    print()

    gemini = GeminiService()
    print(f"Model: {gemini.model}")
    print()

    results = []
    current_role = None
    success_count = 0
    total_time = 0.0

    for s in stickers:
        role = s["role"]
        if role != current_role:
            current_role = role
            print(f"--- Generating {role.upper()} stickers ---")

        idx = s["index"]
        name = s["name"]
        prompt = s["prompt"]

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
        safe_name = safe_name.replace(" ", "_").strip("_")[:40]
        out_path = output_dir / f"sticker_{idx:02d}_{safe_name}.png"

        print(f"  #{idx:2d} {name} ... ", end="", flush=True)
        t0 = time.time()

        result = gemini.generate_image(
            prompt=prompt,
            output_path=out_path,
        )

        elapsed = time.time() - t0
        total_time += elapsed

        if result.get("success"):
            size_kb = result.get("size_kb", "?")
            print(f"OK  ({elapsed:.1f}s, {size_kb} KB) → {out_path.name}")
            success_count += 1
            results.append({
                "index": idx,
                "name": name,
                "role": role,
                "path": str(out_path),
                "size_kb": size_kb,
                "elapsed": elapsed,
            })
        else:
            err = result.get("error", "unknown")
            print(f"FAIL ({elapsed:.1f}s) — {err}")
            results.append({
                "index": idx,
                "name": name,
                "role": role,
                "path": None,
                "error": err,
                "elapsed": elapsed,
            })

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Success: {success_count}/{len(stickers)}")
    print(f"Total time: {total_time:.1f}s")
    print(f"Output dir: {output_dir}")
    print()

    for r in results:
        status = "OK" if r.get("path") else "FAIL"
        print(f"  #{r['index']:2d} [{r['role']:12s}] {r['name']:30s} {status}")

    print()
    print("=== Image Generation Test COMPLETE ===")


if __name__ == "__main__":
    main()
