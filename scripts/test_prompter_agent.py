"""Phase 3 测试: PrompterAgent

测试流程：
  1. 读取 Planner + Designer 输出
  2. 调用 PrompterAgent
  3. 直接打印模型返回内容

用法:
  python -m scripts.test_prompter_agent
  python -m scripts.test_prompter_agent --planner-output path/to/planner.txt --designer-output path/to/designer.txt
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import PROMPTER_SYSTEM, build_prompter_prompt

DEFAULT_PLANNER_OUTPUT = "output/test/planner_test_output.txt"
DEFAULT_DESIGNER_OUTPUT = "output/test/designer_test_output.txt"


def main():
    parser = argparse.ArgumentParser(description="Test PrompterAgent")
    parser.add_argument("--planner-output", default=DEFAULT_PLANNER_OUTPUT)
    parser.add_argument("--designer-output", default=DEFAULT_DESIGNER_OUTPUT)
    args = parser.parse_args()

    planner_path = Path(args.planner_output)
    designer_path = Path(args.designer_output)

    for label, path in [("Planner", planner_path), ("Designer", designer_path)]:
        if not path.exists():
            print(f"Error: {label} output file not found: {path}")
            print(f"Please run test_{label.lower()}_agent.py first.")
            sys.exit(1)

    planner_text = planner_path.read_text(encoding="utf-8").strip()
    designer_text = designer_path.read_text(encoding="utf-8").strip()

    if not planner_text or not designer_text:
        print("Error: Planner or Designer output file is empty.")
        sys.exit(1)

    print("=" * 60)
    print("PrompterAgent Test")
    print("=" * 60)
    print(f"Planner output: {planner_path} ({len(planner_text)} chars)")
    print(f"Designer output: {designer_path} ({len(designer_text)} chars)")
    print()

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}")
    print()

    user_prompt = build_prompter_prompt(planner_text, designer_text)

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=PROMPTER_SYSTEM,
        temperature=0.7,
    )

    text = result["text"]
    print()
    print("=" * 60)
    print(f"MODEL OUTPUT  (tokens: {result['usage']['output_tokens']})")
    print("=" * 60)
    print(text if text else "(empty)")
    print()

    output_dir = Path("output/test")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "prompter_test_output.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Output saved to: {out_path}")
    print()
    print("=== PrompterAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
