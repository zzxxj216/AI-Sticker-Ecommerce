"""Phase 1 Test: PlannerAgent

Usage:
  python -m scripts.test_planner_agent
  python -m scripts.test_planner_agent --theme "Route 66 Roadtrip"
  python -m scripts.test_planner_agent --theme "Cat Daily Life" --style "kawaii cartoon"
"""

import argparse
from scripts.script_utils import (
    print_header, print_result, save_output,
)

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import PLANNER_SYSTEM, build_planner_prompt


def main():
    parser = argparse.ArgumentParser(description="Test PlannerAgent")
    parser.add_argument("--theme", default="Route 66 Roadtrip sticker pack", help="Sticker pack theme")
    parser.add_argument("--style", default=None, help="User style preference")
    parser.add_argument("--color-mood", default=None, help="User color mood preference")
    parser.add_argument("--extra", default="", help="Extra instructions")
    args = parser.parse_args()

    print_header(
        "PlannerAgent Test",
        Theme=args.theme,
        Style=args.style,
        **{"Color mood": args.color_mood},
    )

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}\n")

    user_prompt = build_planner_prompt(
        theme=args.theme,
        user_style=args.style,
        user_color_mood=args.color_mood,
        user_extra=args.extra or None,
    )

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=PLANNER_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    print_result(text, result["usage"])
    save_output(text, "planner_test_output.txt")
    print("=== PlannerAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
