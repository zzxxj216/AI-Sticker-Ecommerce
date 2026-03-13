"""Test topic generation (pipeline Step 1)"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.ai.claude_service import ClaudeService
from src.services.sticker.theme_generator import ThemeContentGenerator


def main():

    theme = "Golden Retriever"

    print(f"=== Topic Generation Test ===")
    print(f"Theme: {theme}\n")

    claude = ClaudeService()
    generator = ThemeContentGenerator(claude_service=claude)
    result = generator.generate_topics(theme, max_topics=4)

    print(result.summary())
    print("\n=== Full JSON Output ===")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
