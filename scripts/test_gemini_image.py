"""Simple Gemini image generation test script.

Usage:
    python scripts/test_gemini_image.py
    python scripts/test_gemini_image.py --prompt "A cute cat sticker"
    python scripts/test_gemini_image.py --prompt "Pixel art mushroom" --output my_image.png
"""

import argparse
import sys
from pathlib import Path

from scripts.script_utils import ensure_output_dir
from src.services.ai.gemini_service import GeminiService


def main():
    parser = argparse.ArgumentParser(description="Gemini image generation test")
    parser.add_argument(
        "--prompt", "-p",
        default="A cute cartoon cat sticker with big eyes, flat vector style, white background, no text",
        help="Image generation prompt",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path (defaults to output/test/)",
    )
    args = parser.parse_args()

    print(f"Prompt: {args.prompt}")
    print("Generating image...")

    gemini = GeminiService()

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = ensure_output_dir("test") / "test_image.png"

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
