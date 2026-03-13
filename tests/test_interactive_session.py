"""CLI runner for testing the interactive session + image generation.

Usage:
    python tests/test_interactive_session.py                  # chat only, print configs
    python tests/test_interactive_session.py --generate        # chat + generate 10 individual images
    python tests/test_interactive_session.py --preview         # chat + generate 1 preview collection sheet
    python tests/test_interactive_session.py --preview --no-claude-prompt  # use template prompt (faster)

Chat naturally with the sticker design assistant. When confirmed,
the final GenerationConfig(s) are printed — and optionally fed
into PackGenerator to produce actual sticker images.

Special commands:
    /slots   — show current slot states
    /config  — show current GenerationConfig(s)
    /reset   — reset the session
    /quit    — exit
"""

import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

from src.services.sticker.interactive_session import InteractiveSession


WELCOME = """\
+==============================================================+
|     Sticker Pack Design Assistant — Multi-pack Chat v3       |
+==============================================================+
|  Chat freely with the AI to describe what stickers you want. |
|  The AI will guide you through the setup and automatically   |
|  extract key info in the background.                         |
|  Supports creating multiple independent packs at once!       |
|                                                              |
|  Commands:  /slots  /config  /reset  /quit                   |
+==============================================================+
"""


def run_chat() -> list:
    """Run the interactive chat loop. Returns list of GenerationConfigs."""
    print(WELCOME)

    session = InteractiveSession()

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nBye!")
            return []

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd == "/quit":
                print("Bye!")
                return []
            elif cmd == "/slots":
                print(f"\n--- Slot States ---\n{session.slots_summary()}\n")
                continue
            elif cmd == "/config":
                configs = session.get_configs()
                print(f"\n--- Current Config ({len(configs)} pack(s)) ---")
                for i, cfg in enumerate(configs):
                    print(f"\n[Pack {i + 1}]")
                    print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
                continue
            elif cmd == "/reset":
                session.reset()
                print("Session reset. Tell me what stickers you'd like to create!")
                continue
            else:
                print(f"Unknown command: {cmd}")
                continue

        resp = session.process_message(user_input)

        safe_msg = resp.message.encode("utf-8", errors="replace").decode("utf-8")
        print(f"\nAI: {safe_msg}")

        if resp.updated_slots:
            names = ", ".join(resp.updated_slots.keys())
            print(f"\n   [Extracted: {names}]")

        if resp.missing_slots:
            print(f"   [Still needed: {', '.join(resp.missing_slots)}]")

        if resp.ready_for_generation:
            print(f"\n{'=' * 60}")
            print(f"  All confirmed! {len(resp.configs)} sticker pack(s) ready:")
            print(f"{'=' * 60}")
            for i, cfg in enumerate(resp.configs):
                print(f"\n--- Pack {i + 1} ---")
                print(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False))
            print(f"{'=' * 60}")
            return resp.configs

    return []


def run_generation(configs: list, max_workers: int = 3) -> None:
    """Take confirmed configs and generate actual sticker images."""
    from src.services.sticker.pack_generator import PackGenerator

    print(f"\n{'=' * 60}")
    print(f"  Generating sticker images — {len(configs)} pack(s)")
    print(f"{'=' * 60}\n")

    generator = PackGenerator()

    def progress_cb(stage: str, current: int, total: int):
        bar = f"[{current}/{total}]" if total else ""
        print(f"  {bar} {stage}")

    packs = generator.generate_from_configs(
        configs=configs,
        max_workers=max_workers,
        progress_callback=progress_cb,
    )

    print(f"\n{'=' * 60}")
    print("  Generation Results")
    print(f"{'=' * 60}")
    for i, pack in enumerate(packs):
        success = pack.success_count
        total = pack.total_count
        duration = pack.duration_seconds
        print(f"\n  Pack {i + 1}: {pack.name}")
        print(f"    Success: {success}/{total}")
        print(f"    Duration: {duration:.1f}s")
        for sticker in pack.stickers:
            status = "OK" if sticker.status == "success" else "FAIL"
            path = sticker.image_path or "(no image)"
            print(f"    {status} #{sticker.id}: {path}")
    print(f"{'=' * 60}\n")


def run_preview(configs: list, use_claude_prompt: bool = True) -> None:
    """Take confirmed configs and generate preview collection sheet images."""
    from src.services.sticker.pack_generator import PackGenerator

    print(f"\n{'=' * 60}")
    print(f"  Generating preview sheets — {len(configs)} pack(s)")
    print(f"{'=' * 60}\n")

    generator = PackGenerator()

    def progress_cb(stage: str, current: int, total: int):
        bar = f"[{current}/{total}]" if total else ""
        print(f"  {bar} {stage}")

    results = generator.generate_preview_from_configs(
        configs=configs,
        use_claude_prompt=use_claude_prompt,
        progress_callback=progress_cb,
    )

    print(f"\n{'=' * 60}")
    print("  Preview Results")
    print(f"{'=' * 60}")
    for i, result in enumerate(results):
        print(f"\n  Pack {i + 1}: {result['pack_name']}")
        print(f"    Stickers: {len(result['sticker_ideas'])}")
        print(f"    Style: {result['style_guide'].get('art_style', 'N/A')}")

        img = result["preview_image"]
        if img.get("success"):
            print(f"    Preview: {img['image_path']}")
            print(f"    Size: {img.get('size_kb', '?')} KB")
            print(f"    Duration: {img.get('elapsed', 0):.1f}s")
        else:
            print(f"    Failed: {img.get('error', 'unknown')}")

        print(f"\n    Prompt ({len(result['preview_prompt'])} chars):")
        print(f"    {result['preview_prompt'][:200]}...")
    print(f"\n{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="Interactive sticker session + generation")
    parser.add_argument("--generate", action="store_true", help="Generate individual sticker images")
    parser.add_argument("--preview", action="store_true", help="Generate preview collection sheet (all stickers in 1 image)")
    parser.add_argument("--workers", type=int, default=3, help="Gemini concurrency (default: 3)")
    parser.add_argument("--no-claude-prompt", action="store_true", help="Use template prompt instead of Claude (faster, for --preview)")
    args = parser.parse_args()

    configs = run_chat()

    if not configs:
        print("\nDone (no configs to generate).")
        return

    if args.preview:
        run_preview(configs, use_claude_prompt=not args.no_claude_prompt)
    elif args.generate:
        run_generation(configs, max_workers=args.workers)
    else:
        print("\nHint:")
        print("  --preview    Generate a preview sheet (all stickers in 1 image)")
        print("  --generate   Generate individual sticker images")
        print("Example: python tests/test_interactive_session.py --preview")

    print("\nDone.")


if __name__ == "__main__":
    main()
