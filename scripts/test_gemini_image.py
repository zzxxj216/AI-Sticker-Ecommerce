"""简单的 Gemini 图片生成测试脚本

Usage:
    python scripts/test_gemini_image.py
    python scripts/test_gemini_image.py --prompt "A cute cat sticker"
    python scripts/test_gemini_image.py --prompt "Pixel art mushroom" --output my_image.png
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(".env", override=True)

from src.services.ai.gemini_service import GeminiService


def main():
    parser = argparse.ArgumentParser(description="Gemini 图片生成测试")
    parser.add_argument(
        "--prompt", "-p",
        default="A cute cartoon cat sticker with big eyes, flat vector style, white background, no text",
        help="图片生成提示词",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出文件路径（默认保存到 data/output/images/test/）",
    )
    args = parser.parse_args()

    print(f"Prompt: {args.prompt}")
    print("Generating image...")

    gemini = GeminiService()

    output_path = None
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("data/output/images/test")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "test_image.png"

    result = gemini.generate_image(
        prompt=args.prompt,
        output_path=output_path,
    )

    if result.get("success"):
        print(f"Done! Image saved to: {result['image_path']}")
        print(f"Size: {result.get('size_kb', '?')} KB")
        print(f"Time: {result.get('elapsed', '?'):.1f}s")
    else:
        print(f"Failed: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
