"""Phase 3 Test: PrompterAgent (legacy)

Usage:
  python -m scripts.test_prompter_agent
  python -m scripts.test_prompter_agent --planner-output path/to/planner.txt --designer-output path/to/designer.txt
"""

import argparse

from scripts.script_utils import (
    print_header, print_result, save_output, load_input_file,
)

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import PROMPTER_SYSTEM, build_prompter_prompt


def main():
    parser = argparse.ArgumentParser(description="Test PrompterAgent")
    parser.add_argument("--planner-output", default="output/test/planner_test_output.txt")
    parser.add_argument("--designer-output", default="output/test/designer_test_output.txt")
    args = parser.parse_args()

    planner_text = load_input_file(args.planner_output, "Planner output")
    designer_text = load_input_file(args.designer_output, "Designer output")

    print_header(
        "PrompterAgent Test",
        **{"Planner output": f"{args.planner_output} ({len(planner_text)} chars)"},
        **{"Designer output": f"{args.designer_output} ({len(designer_text)} chars)"},
    )

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}\n")

    user_prompt = build_prompter_prompt(planner_text, designer_text)

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=PROMPTER_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    print_result(text, result["usage"])
    save_output(text, "prompter_test_output.txt")
    print("=== PrompterAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
