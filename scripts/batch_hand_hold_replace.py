#!/usr/bin/env python3
"""Replace packaging/envelope 5th secondaries with hand-hold pile shots + push."""

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

# Default: today's envelope/packaging 5th-image batch.
DEFAULT_ENVELOPE_LP_IDS = [
    1, 12, 15, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28,
]

os.environ.setdefault("TKSHOP_SECONDARY_IMAGE_QUALITY", "medium")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "local_product_ids", type=int, nargs="*",
        help="local_product ids (default: envelope batch list)",
    )
    parser.add_argument("--workers", type=int, default=3, help="Parallel image gen")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fill-only", action="store_true", help="Generate only, no push")
    args = parser.parse_args()

    ids = args.local_product_ids or DEFAULT_ENVELOPE_LP_IDS
    from src.services.tkshop.service import get_tkshop_service

    result = get_tkshop_service().batch_replace_hand_hold_and_push(
        ids,
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
