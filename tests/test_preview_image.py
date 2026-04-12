"""Test preview prompt generation + image generation in isolation.

Uses hardcoded sticker ideas and style guide — no full pipeline needed.

Usage:
    python tests/test_preview_image.py                      # Claude prompt + image
    python tests/test_preview_image.py --template            # template prompt + image
    python tests/test_preview_image.py --prompt-only          # only show prompt, skip image
    python tests/test_preview_image.py --template --prompt-only
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------- Hardcoded test data ----------

PACK_NAME = "THE NEURAL SORCERER & ARCHITECT PACK"

STYLE_GUIDE = {
    "art_style": "Minimalist neon vector illustration with rounded shapes and soft glowing edges",
    "color_palette": {
        "primary": "#4F46E5",
        "secondary": "#7C3AED",
        "accent": "#FACC15",
        "background": "#FFFFFF",
        "text_color": "#1E1B4B",
    },
    "line_style": "2px rounded stroke with neon glow effect, no hard edges",
    "mood": "playful, tech-forward, futuristic, witty",
    "typography_style": "Bold rounded sans-serif, slightly condensed, all-caps for emphasis",
    "visual_consistency_rules": [
        "Max 3 colors per sticker from the palette",
        "All icons share 8px corner radius",
        "Text stickers use pill-shaped background",
        "Shadows always bottom-right, 10% opacity",
    ],
}

STICKER_IDEAS = [
    {
        "index": 1,
        "type": "element",
        "title": "Prompt Spell Book",
        "concept": "An ancient spell book with Python code and {JSON} floating out of its pages, representing prompt engineering as modern sorcery",
    },
    {
        "index": 2,
        "type": "element",
        "title": "Neural Tree of Life",
        "concept": "A glowing tree where each fruit is a neural network node, trunk made of flowing data streams",
    },
    {
        "index": 3,
        "type": "element",
        "title": "Multi-Agent Gears",
        "concept": "Three interlocking gears labeled Planner, Coder, Executor representing multi-agent system collaboration",
    },
    {
        "index": 4,
        "type": "element",
        "title": "Perfect Loss Curve",
        "concept": "A coordinate graph showing a smooth loss curve dropping to zero, labeled Pure Serotonin",
    },
    {
        "index": 5,
        "type": "text",
        "title": "Prompt Negotiator",
        "sticker_text": "I don't debug, I REPHRASE my prompts",
        "concept": "Bold text sticker with the O in 'don't' replaced by a chat bubble icon",
    },
    {
        "index": 6,
        "type": "text",
        "title": "Black Box Theory",
        "sticker_text": "It's not Magic, it's Math",
        "concept": "Stacked text with a small black cube icon replacing the period",
    },
    {
        "index": 7,
        "type": "combined",
        "title": "Hallucination Warning",
        "sticker_text": "WARNING: Hallucinating",
        "concept": "A yellow warning triangle icon with glitch effect, paired with bold warning text",
    },
    {
        "index": 8,
        "type": "combined",
        "title": "Reasoning Loading Bar",
        "sticker_text": "Thinking... 80%",
        "concept": "A neon progress bar at 80% with the text Thinking above it, representing reasoning models",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Test preview prompt + image generation")
    parser.add_argument("--template", action="store_true", help="Use template mode instead of Claude")
    parser.add_argument("--prompt-only", action="store_true", help="Only generate prompt, skip image")
    args = parser.parse_args()

    mode = "template" if args.template else "Claude"
    print(f"{'=' * 60}")
    print(f"  Preview Image Test")
    print(f"  Pack: {PACK_NAME}")
    print(f"  Stickers: {len(STICKER_IDEAS)}")
    print(f"  Prompt mode: {mode}")
    print(f"  Generate image: {not args.prompt_only}")
    print(f"{'=' * 60}\n")

    from src.services.sticker.pack_generator import PackGenerator

    if args.template:
        # Template mode — no API calls for prompt, only needs Gemini for image
        from src.services.ai.prompt_builder import build_preview_prompt_direct

        print("[1/2] Building preview prompt (template)...")
        preview_prompt = build_preview_prompt_direct(PACK_NAME, STICKER_IDEAS, STYLE_GUIDE)
    else:
        # Claude mode — needs Claude for prompt
        from src.services.ai.claude_service import ClaudeService
        from src.services.ai.prompt_builder import build_preview_prompt_via_claude

        print("[1/2] Generating preview prompt (Claude)...")
        claude = ClaudeService()
        meta_prompt = build_preview_prompt_via_claude(PACK_NAME, STICKER_IDEAS, STYLE_GUIDE)
        result = claude.generate(prompt=meta_prompt, temperature=0.7)
        preview_prompt = result["text"].strip()
        print(f"  Claude tokens: in={result['usage']['input_tokens']}, out={result['usage']['output_tokens']}")
        print(f"  Cost: ${result['cost']:.4f}")

    print(f"\n{'─' * 60}")
    print("  GENERATED PREVIEW PROMPT")
    print(f"{'─' * 60}")
    print(preview_prompt)
    print(f"{'─' * 60}")
    print(f"  Length: {len(preview_prompt)} chars\n")

    if args.prompt_only:
        print("Done (--prompt-only). Run without it to generate the image.")
        return

    # Generate image
    from src.services.ai.gemini_service import GeminiService
    from datetime import datetime

    print("[2/2] Generating preview image via Gemini...")
    gemini = GeminiService()

    output_dir = Path("data/output/images") / datetime.now().strftime("%Y%m%d") / "previews"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"preview_test_{datetime.now().strftime('%H%M%S')}.png"

    img_result = gemini.generate_image(prompt=preview_prompt, output_path=output_path)

    print(f"\n{'─' * 60}")
    print("  IMAGE RESULT")
    print(f"{'─' * 60}")
    if img_result["success"]:
        print(f"  Status:  SUCCESS")
        print(f"  Path:    {img_result['image_path']}")
        print(f"  Size:    {img_result['size_kb']} KB")
        print(f"  Time:    {img_result['elapsed']:.1f}s")
    else:
        print(f"  Status:  FAILED")
        print(f"  Error:   {img_result.get('error')}")
    print(f"{'─' * 60}\n")


if __name__ == "__main__":
    main()
