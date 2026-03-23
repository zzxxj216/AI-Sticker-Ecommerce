"""Phase 1 测试: PlannerAgent

测试流程：
  1. 初始化 OpenAIService
  2. 调用 PlannerAgent（自然语言输出）
  3. 直接打印模型返回内容

用法:
  python -m scripts.test_planner_agent
  python -m scripts.test_planner_agent --theme "Route 66 Roadtrip"
  python -m scripts.test_planner_agent --theme "猫咪日常" --style "kawaii cartoon"
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_prompts import PLANNER_SYSTEM, build_planner_prompt


def main():
    parser = argparse.ArgumentParser(description="Test PlannerAgent")
    parser.add_argument("--theme", default="Route 66 Roadtrip sticker pack", help="Sticker pack theme")
    parser.add_argument("--style", default=None, help="User style preference")
    parser.add_argument("--color-mood", default=None, help="User color mood preference")
    parser.add_argument("--extra", default="", help="Extra instructions")
    args = parser.parse_args()

    print("=" * 60)
    print("PlannerAgent Test")
    print("=" * 60)
    print(f"Theme: {args.theme}")
    if args.style:
        print(f"Style: {args.style}")
    if args.color_mood:
        print(f"Color mood: {args.color_mood}")
    print()

    openai_svc = OpenAIService()
    print(f"Model: {openai_svc.model}")
    print()

    user_prompt = build_planner_prompt(
        theme='Hot Dog Cartoon',
    )

    print("Calling OpenAI API...")
    result = openai_svc.generate(
        prompt=user_prompt,
        system=PLANNER_SYSTEM,
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
    out_path = output_dir / "planner_test_output2.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Output saved to: {out_path}")
    print()
    print("=== PlannerAgent Test COMPLETE ===")


if __name__ == "__main__":
    main()
