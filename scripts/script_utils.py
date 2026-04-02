"""Shared utilities for scripts/*.py runners.

Provides common boilerplate: project-root sys.path setup, env loading,
output saving, and header/footer printing.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)


def ensure_output_dir(subdir: str = "test") -> Path:
    """Create and return output/<subdir>/ directory."""
    out = PROJECT_ROOT / "output" / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def save_output(text: str, filename: str, subdir: str = "test") -> Path:
    """Save text to output/<subdir>/<filename> and print the path."""
    out_dir = ensure_output_dir(subdir)
    out_path = out_dir / filename
    out_path.write_text(text, encoding="utf-8")
    print(f"Output saved to: {out_path}")
    return out_path


def print_header(title: str, **kwargs) -> None:
    """Print a formatted header with optional key-value pairs."""
    print("=" * 60)
    print(title)
    print("=" * 60)
    for key, value in kwargs.items():
        if value is not None:
            print(f"{key}: {value}")
    print()


def print_result(text: str, usage: dict) -> None:
    """Print model output with token count."""
    print()
    print("=" * 60)
    print(f"MODEL OUTPUT  (tokens: {usage.get('output_tokens', '?')})")
    print("=" * 60)
    print(text if text else "(empty)")
    print()


def load_input_file(path: str, label: str = "input") -> str:
    """Load a text file, exit with error if missing or empty."""
    p = Path(path)
    if not p.exists():
        print(f"Error: {label} file not found: {p}")
        sys.exit(1)
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        print(f"Error: {label} file is empty: {p}")
        sys.exit(1)
    return content
