"""Phase 3 Test: Prompt Builder

Usage:
  python -m scripts.test_prompt_builder \
      --planner-output output/test/planner_test_output.txt \
      --designer-output output/test/designer_test_output.txt

  python -m scripts.test_prompt_builder \
      --planner-output output/planner_eval/.../planning.md \
      --designer-output output/test/designer_test_output.txt \
      --brief-json output/planner_eval/.../brief.json
"""

import argparse
import json
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT  # noqa: F401 (triggers sys.path + dotenv)
from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import (
    PROMPT_BUILDER_SYSTEM,
    build_prompt_builder_prompt,
    parse_prompt_builder_output,
)


def main():
    parser = argparse.ArgumentParser(description="Test Prompt Builder")
    parser.add_argument(
        "--planner-output",
        required=True,
        help="Path to planner output (txt/md)",
    )
    parser.add_argument(
        "--designer-output",
        required=True,
        help="Path to designer/sticker-spec output (txt)",
    )
    parser.add_argument(
        "--brief-json",
        default=None,
        help="Optional JSON trend brief file",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="Display theme (fallback when no brief JSON)",
    )
    args = parser.parse_args()

    planner_path = Path(args.planner_output)
    designer_path = Path(args.designer_output)

    for label, p in [("Planner output", planner_path), ("Designer output", designer_path)]:
        if not p.exists():
            print(f"Error: {label} not found: {p}")
            sys.exit(1)

    planner_text = planner_path.read_text(encoding="utf-8").strip()
    designer_text = designer_path.read_text(encoding="utf-8").strip()

    trend_brief = None
    if args.brief_json:
        brief_path = Path(args.brief_json)
        if not brief_path.exists():
            print(f"Error: Brief file not found: {brief_path}")
            sys.exit(1)
        raw = json.loads(brief_path.read_text(encoding="utf-8"))
        trend_brief = raw.get("brief", raw) if isinstance(raw, dict) else raw

    print("=" * 60)
    print("Prompt Builder Test")
    print("=" * 60)
    print(f"Planner output : {planner_path} ({len(planner_text)} chars)")
    print(f"Designer output: {designer_path} ({len(designer_text)} chars)")
    if trend_brief:
        print(f"Trend brief    : {args.brief_json}")
    elif args.theme:
        print(f"Theme (no brief): {args.theme}")
    print()

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}")
    print()

    user_prompt = build_prompt_builder_prompt(
        designer_text,
        trend_brief=trend_brief,
        pack_plan_text=planner_text,
        theme=args.theme,
    )

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=PROMPT_BUILDER_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    usage = result.get("usage", {})
    print()
    print("=" * 60)
    print(f"MODEL OUTPUT  (tokens: in={usage.get('input_tokens','?')} out={usage.get('output_tokens','?')})")
    print("=" * 60)
    print(text if text else "(empty)")
    print()

    # ---- parse & summary ----
    parsed = parse_prompt_builder_output(text)
    stickers = parsed["stickers"]

    print("=" * 60)
    print("PARSE SUMMARY")
    print("=" * 60)
    print(f"Pack foundation : {len(parsed['pack_foundation'])} chars")
    print(f"Stickers parsed : {len(stickers)}")
    print(f"Quality control : {len(parsed['quality_control'])} chars")
    print()

    for s in stickers:
        neg_preview = (s["negative"][:40] + "...") if len(s["negative"]) > 40 else s["negative"]
        print(f"  #{s['index']:2d} [{s['role']:12s}] {s['name']}")
        print(f"       prompt   : {s['prompt'][:80]}...")
        print(f"       negative : {neg_preview}")
        print()

    # ---- save ----
    output_dir = Path("output/test")
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / "prompt_builder_output.txt"
    out_path.write_text(text, encoding="utf-8")
    print(f"Full output saved to: {out_path}")

    if parsed["pack_foundation"]:
        pf_path = output_dir / "pack_foundation.txt"
        pf_path.write_text(parsed["pack_foundation"], encoding="utf-8")
        print(f"Pack foundation saved to: {pf_path}")

    if parsed["quality_control"]:
        qc_path = output_dir / "quality_control.txt"
        qc_path.write_text(parsed["quality_control"], encoding="utf-8")
        print(f"Quality control saved to: {qc_path}")

    print()
    print("=== Prompt Builder Test COMPLETE ===")


if __name__ == "__main__":
    main()
