"""Test: IMAGE_GENERATION_SYSTEM - Generation execution plan

Input: Planner output + Designer output + Prompt Builder output + optional Brief JSON
Output: 4-part image generation execution plan

Usage:
  python -m scripts.test_generation_plan \
      --planner-output  output/planner_eval/.../planning.md \
      --designer-output output/test/designer_test_output.txt \
      --prompt-builder-output output/test/prompt_builder_output.txt \
      --brief-json output/planner_eval/.../brief.json
"""

import sys
import argparse
import json
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT

from src.services.ai.openai_service import OpenAIService
from src.services.batch.image_generation import (
    IMAGE_GENERATION_SYSTEM,
    build_image_generation_prompt,
    parse_image_generation_output,
)


def main():
    parser = argparse.ArgumentParser(description="Test IMAGE_GENERATION_SYSTEM")
    parser.add_argument("--planner-output", required=True)
    parser.add_argument("--designer-output", required=True)
    parser.add_argument("--prompt-builder-output", required=True)
    parser.add_argument("--brief-json", default=None)
    parser.add_argument("--theme", default=None)
    args = parser.parse_args()

    paths = {
        "Planner": Path(args.planner_output),
        "Designer": Path(args.designer_output),
        "Prompt Builder": Path(args.prompt_builder_output),
    }
    for label, p in paths.items():
        if not p.exists():
            print(f"Error: {label} file not found: {p}")
            sys.exit(1)

    planner_text = paths["Planner"].read_text(encoding="utf-8").strip()
    designer_text = paths["Designer"].read_text(encoding="utf-8").strip()
    pb_text = paths["Prompt Builder"].read_text(encoding="utf-8").strip()

    trend_brief = None
    if args.brief_json:
        bp = Path(args.brief_json)
        if not bp.exists():
            print(f"Error: Brief not found: {bp}")
            sys.exit(1)
        raw = json.loads(bp.read_text(encoding="utf-8"))
        trend_brief = raw.get("brief", raw) if isinstance(raw, dict) else raw

    print("=" * 60)
    print("IMAGE_GENERATION_SYSTEM Test")
    print("=" * 60)
    print(f"Planner        : {paths['Planner']} ({len(planner_text)} chars)")
    print(f"Designer       : {paths['Designer']} ({len(designer_text)} chars)")
    print(f"Prompt Builder : {paths['Prompt Builder']} ({len(pb_text)} chars)")
    if trend_brief:
        print(f"Brief          : {args.brief_json}")
    print()

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}")
    print()

    user_prompt = build_image_generation_prompt(
        pb_text,
        trend_brief=trend_brief,
        pack_plan_text=planner_text,
        sticker_spec_text=designer_text,
        theme=args.theme,
    )

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=IMAGE_GENERATION_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    usage = result.get("usage", {})
    print()
    print("=" * 60)
    print(f"MODEL OUTPUT  (in={usage.get('input_tokens','?')} out={usage.get('output_tokens','?')})")
    print("=" * 60)
    print(text if text else "(empty)")
    print()

    # ---- parse & summary ----
    parsed = parse_image_generation_output(text)

    print("=" * 60)
    print("PARSE SUMMARY")
    print("=" * 60)
    print(f"Generation setup : {len(parsed['generation_setup'])} chars")
    print(f"Batch plan       : {len(parsed['batch_plan'])} chars")
    print(f"Tasks parsed     : {len(parsed['tasks'])}")
    print(f"Assembly notes   : {len(parsed['assembly_notes'])} chars")
    print()

    for t in parsed["tasks"]:
        print(f"  #{t['index']:2d} [{t['role']:12s}] {t['name']}")
        gen_preview = t["generation_task"][:70] + "..." if len(t["generation_task"]) > 70 else t["generation_task"]
        print(f"       task: {gen_preview}")
        print()

    # ---- save ----
    output_dir = Path("output/test")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "generation_plan_output.txt"
    out_path.write_text(text, encoding="utf-8")
    print(f"Full output saved to: {out_path}")
    print()
    print("=== IMAGE_GENERATION_SYSTEM Test COMPLETE ===")


if __name__ == "__main__":
    main()
