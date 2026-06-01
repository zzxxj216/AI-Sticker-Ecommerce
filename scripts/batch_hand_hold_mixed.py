#!/usr/bin/env python3
"""Hand-hold 5th gallery image: replace (delete+gen) vs append (gen only when short)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# lp 16,17,18,2,4,7,8,9,10,13 — delete 5th then regenerate hand-hold
REPLACE_FIFTH = [2, 4, 7, 8, 9, 10, 13, 16, 17, 18]
# lp 5,6 — append hand-hold as 5th when gallery is short
APPEND_FIFTH = [5, 6]

os.environ.setdefault("TKSHOP_SECONDARY_IMAGE_QUALITY", "medium")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", type=int, nargs="*", default=REPLACE_FIFTH)
    parser.add_argument("--append", type=int, nargs="*", default=APPEND_FIFTH)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fill-only", action="store_true", help="Generate only, no push")
    args = parser.parse_args()

    from src.services.tkshop.service import get_tkshop_service

    result = get_tkshop_service().batch_hand_hold_gallery_mixed(
        args.replace,
        args.append,
        workers=args.workers,
        push=not args.fill_only,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
