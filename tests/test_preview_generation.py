"""Test preview image prompt generation and image generation pipeline.

Usage:
    python tests/test_preview_generation.py                     # full pipeline
    python tests/test_preview_generation.py --theme "cats"      # custom theme
    python tests/test_preview_generation.py --count 6           # fewer stickers
    python tests/test_preview_generation.py --no-claude-prompt  # skip Claude for prompt, use template
    python tests/test_preview_generation.py --prompt-only       # only generate the prompt, skip image
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.claude_service import ClaudeService
from src.services.ai.gemini_service import GeminiService
from src.services.sticker.pack_generator import PackGenerator


def progress(stage: str, current: int, total: int):
    bar = "█" * current + "░" * (total - current)
    print(f"  [{bar}] {current}/{total} — {stage}")


def main():
    parser = argparse.ArgumentParser(description="Preview generation test")
    parser.add_argument("--theme", default="Artificial Intelligence", help="Theme to generate for")
    parser.add_argument("--count", type=int, default=10, help="Number of sticker ideas")
    parser.add_argument("--no-claude-prompt", action="store_true", help="Use template instead of Claude for preview prompt")
    parser.add_argument("--prompt-only", action="store_true", help="Only generate the prompt, skip image generation")
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"  Preview Generation Pipeline Test")
    print(f"  Theme: {args.theme}")
    print(f"  Sticker count: {args.count}")
    print(f"  Claude prompt: {not args.no_claude_prompt}")
    print(f"  Generate image: {not args.prompt_only}")
    print(f"{'=' * 60}\n")

    claude = ClaudeService()
    gemini = GeminiService()
    generator = PackGenerator(claude_service=claude, gemini_service=gemini)

    if args.prompt_only:
        _run_prompt_only(generator, args)
    else:
        _run_full_pipeline(generator, args)


def _run_prompt_only(generator: PackGenerator, args):
    """Generate topics → theme content → style guide → ideas → preview prompt (no image)."""
    from src.services.sticker.theme_generator import ThemeContentGenerator

    theme_gen = ThemeContentGenerator(claude_service=generator.claude)

    print("[1/4] Generating topics...")
    topic_result = theme_gen.generate_topics(args.theme, max_topics=4)
    print(f"  → {len(topic_result.topics)} topics generated\n")
    print(topic_result.summary())

    print(f"\n[2/4] Expanding theme content...")
    theme_content = theme_gen.generate(args.theme)
    print(f"  → {theme_content.summary()}\n")

    print("[3/4] Creating style guide + sticker ideas...")
    tc_dict = theme_content.to_dict()

    from src.services.ai import build_pack_style_guide_prompt
    style_guide = generator.claude.generate_json(
        prompt=build_pack_style_guide_prompt(tc_dict),
        max_tokens=2000,
        temperature=0.7,
    )
    print(f"  → Art style: {style_guide.get('art_style', 'N/A')}")
    print(f"  → Mood: {style_guide.get('mood', 'N/A')}\n")

    text_count = max(1, int(args.count * 0.3))
    element_count = max(1, int(args.count * 0.4))
    combined_count = args.count - text_count - element_count

    from src.services.ai import (
        build_text_sticker_prompt,
        build_element_sticker_prompt,
        build_combined_sticker_prompt,
    )

    ideas = []
    if text_count > 0:
        text_ideas = generator.claude.generate_json(
            prompt=build_text_sticker_prompt(style_guide, tc_dict, text_count),
            max_tokens=4000, temperature=0.9,
        )
        if isinstance(text_ideas, list):
            ideas.extend(text_ideas)

    if element_count > 0:
        elem_ideas = generator.claude.generate_json(
            prompt=build_element_sticker_prompt(style_guide, tc_dict, element_count),
            max_tokens=4000, temperature=0.9,
        )
        if isinstance(elem_ideas, list):
            ideas.extend(elem_ideas)

    if combined_count > 0:
        comb_ideas = generator.claude.generate_json(
            prompt=build_combined_sticker_prompt(style_guide, tc_dict, combined_count),
            max_tokens=4000, temperature=0.9,
        )
        if isinstance(comb_ideas, list):
            ideas.extend(comb_ideas)

    for i, idea in enumerate(ideas, 1):
        idea["index"] = i
    print(f"  → {len(ideas)} sticker ideas generated\n")

    print("[4/4] Generating preview prompt...")
    english_theme = tc_dict.get("theme_english", args.theme)
    pack_name = f"THE {english_theme.upper()} STICKER PACK"

    preview_prompt = generator.generate_preview_prompt(
        pack_name=pack_name,
        sticker_ideas=ideas,
        style_guide=style_guide,
        use_claude=not args.no_claude_prompt,
    )

    print(f"\n{'=' * 60}")
    print("  PREVIEW PROMPT")
    print(f"{'=' * 60}")
    print(preview_prompt)
    print(f"{'=' * 60}\n")

    print("Sticker ideas summary:")
    for idea in ideas:
        idx = idea.get("index", "?")
        title = idea.get("title", "Untitled")
        stype = idea.get("type", "?")
        print(f"  {idx}. [{stype}] {title}")

    print(f"\nDone! Use --prompt-only=false or remove it to also generate the image.")


def _run_full_pipeline(generator: PackGenerator, args):
    """Run the complete generate_preview pipeline."""
    print("Running full preview pipeline...\n")

    result = generator.generate_preview(
        theme=args.theme,
        count=args.count,
        use_claude_prompt=not args.no_claude_prompt,
        progress_callback=progress,
    )

    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    print(f"  Pack name: {result['pack_name']}")
    print(f"  Sticker ideas: {len(result['sticker_ideas'])}")
    print(f"  Style: {result['style_guide'].get('art_style', 'N/A')}")

    print(f"\n  Preview prompt ({len(result['preview_prompt'])} chars):")
    print(f"  {result['preview_prompt'][:200]}...")

    img = result["preview_image"]
    if img["success"]:
        print(f"\n  Preview image: {img['image_path']}")
        print(f"  Size: {img['size_kb']} KB")
        print(f"  Generation time: {img['elapsed']:.1f}s")
    else:
        print(f"\n  Image generation failed: {img.get('error')}")

    print(f"\n  Sticker ideas:")
    for idea in result["sticker_ideas"]:
        idx = idea.get("index", "?")
        title = idea.get("title", "Untitled")
        stype = idea.get("type", "?")
        print(f"    {idx}. [{stype}] {title}")

    output_file = Path("data/output") / "preview_result.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    serializable = {
        "pack_name": result["pack_name"],
        "preview_prompt": result["preview_prompt"],
        "preview_image_path": img.get("image_path"),
        "style_guide": result["style_guide"],
        "sticker_ideas": result["sticker_ideas"],
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    print(f"\n  Full result saved to: {output_file}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
