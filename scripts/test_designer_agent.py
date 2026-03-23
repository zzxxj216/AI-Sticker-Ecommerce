"""Phase 2 测试: DesignerAgent

测试流程：
  1. 读取 Planner 输出（txt 文件）
  2. 调用 DesignerAgent
  3. 直接打印模型返回内容

用法:
  python -m scripts.test_designer_agent
  python -m scripts.test_designer_agent --planner-output path/to/planner_output.txt
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import DESIGNER_SYSTEM, build_designer_prompt

DEFAULT_PLANNER_OUTPUT = "output/test/planner_test_output.txt"


def main():
    parser = argparse.ArgumentParser(description="Test DesignerAgent")
    parser.add_argument(
        "--planner-output",
        default=DEFAULT_PLANNER_OUTPUT,
        help="Path to planner output txt file",
    )
    args = parser.parse_args()

    planner_path = Path(args.planner_output)
    if not planner_path.exists():
        print(f"Error: Planner output file not found: {planner_path}")
        print("Please run test_planner_agent.py first.")
        sys.exit(1)

    planner_text = planner_path.read_text(encoding="utf-8").strip()
    if not planner_text:
        print(f"Error: Planner output file is empty: {planner_path}")
        sys.exit(1)

    print("=" * 60)
    print("DesignerAgent Test")
    print("=" * 60)
    print(f"Planner output: {planner_path} ({len(planner_text)} chars)")
    print()

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}")
    print()

    user_prompt = build_designer_prompt(planner_text)

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=DESIGNER_SYSTEM,
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
    out_path = output_dir / "designer_test_output.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Output saved to: {out_path}")
    print()
    print("=== DesignerAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
