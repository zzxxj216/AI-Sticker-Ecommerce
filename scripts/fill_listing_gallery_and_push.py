#!/usr/bin/env python3
"""Fill local product galleries to 5 images (1 main + 4 secondary) and push updates.

Workflow per local product with published listings:
  1. AI-generate missing master images (secondary-only when main exists)
  2. Copy master gallery onto each published shop listing
  3. POST update to TikTok via multi-channel-api
"""

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

os.environ.setdefault("TKSHOP_IMAGE_GEN_CONCURRENCY", "1")
os.environ.setdefault("TKSHOP_MAIN_IMAGE_QUALITY", "medium")
os.environ.setdefault("TKSHOP_SECONDARY_IMAGE_QUALITY", "medium")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "local_product_ids", type=int, nargs="*",
        help="Optional subset of local_product ids (default: all published)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only, no AI/push")
    parser.add_argument("--fill-only", action="store_true", help="Generate images only")
    parser.add_argument("--push-only", action="store_true", help="Sync + push only")
    parser.add_argument("--target-total", type=int, default=5)
    parser.add_argument("--target-secondary", type=int, default=4)
    parser.add_argument(
        "--duplicate", action="store_true",
        help="Pad to 5 images by duplicating existing secondaries (no AI)",
    )
    parser.add_argument(
        "--ai-then-duplicate", action="store_true",
        help="Try AI first; duplicate-pad anything still short",
    )
    parser.add_argument(
        "--regenerate", action="store_true",
        help="Remove duplicate rows and AI-generate replacements (not copy)",
    )
    args = parser.parse_args()

    fill_mode = "ai"
    if args.regenerate:
        fill_mode = "regenerate"
    elif args.duplicate:
        fill_mode = "duplicate"
    elif args.ai_then_duplicate:
        fill_mode = "ai_then_duplicate"

    from src.services.tkshop.service import get_tkshop_service

    svc = get_tkshop_service()
    result = svc.batch_fill_gallery_and_push(
        args.local_product_ids or None,
        target_total=args.target_total,
        target_secondary=args.target_secondary,
        dry_run=args.dry_run,
        fill=not args.push_only,
        push=not args.fill_only,
        fill_mode=fill_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
