"""Phase 2 Test: DesignerAgent

Usage:
  python -m scripts.test_designer_agent
  python -m scripts.test_designer_agent --planner-output path/to/planner_output.txt
  python -m scripts.test_designer_agent --brief-json path/to/brief.json --theme "Theme Name"
"""

import argparse
import json
from pathlib import Path

from scripts.script_utils import (
    print_header, print_result, save_output, load_input_file,
)

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import DESIGNER_SYSTEM, build_designer_prompt


def main():
    parser = argparse.ArgumentParser(description="Test DesignerAgent")
    parser.add_argument(
        "--planner-output",
        default="output/test/planner_test_output.txt",
        help="Path to planner output txt file",
    )
    parser.add_argument("--brief-json", default=None, help="Optional JSON trend brief")
    parser.add_argument("--theme", default=None, help="Theme name (fallback when no brief)")
    args = parser.parse_args()

    planner_text = load_input_file(args.planner_output, "Planner output")

    trend_brief = None
    if args.brief_json:
        brief_path = Path(args.brief_json)
        if not brief_path.exists():
            print(f"Error: Brief file not found: {brief_path}")
            return
        raw = json.loads(brief_path.read_text(encoding="utf-8"))
        trend_brief = raw.get("brief", raw) if isinstance(raw, dict) else raw

    print_header(
        "DesignerAgent Test",
        **{"Planner output": f"{args.planner_output} ({len(planner_text)} chars)"},
        **{"Trend brief": args.brief_json} if trend_brief else {},
        Theme=args.theme,
    )

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}\n")

    user_prompt = build_designer_prompt(
        planner_text,
        trend_brief=trend_brief,
        theme=args.theme,
    )

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=DESIGNER_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    print_result(text, result["usage"])
    save_output(text, "designer_test_output.txt")
    print("=== DesignerAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
