#!/usr/bin/env python3
"""Resume interrupted local-product AI image generation.

Runs one or more local products. Use ``--workers`` to process multiple
products in parallel (each worker gets its own DB connection / service).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

os.environ.setdefault("TKSHOP_IMAGE_GEN_CONCURRENCY", "1")
os.environ.setdefault("TKSHOP_MAIN_IMAGE_QUALITY", "medium")

_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _run_one(lp_id: int, *, secondary_count: int) -> tuple[int, bool, str]:
    from src.services.tkshop.service import TKShopService

    _log(f"\n=== local_product #{lp_id} (thread {threading.current_thread().name}) ===")
    t0 = time.time()
    try:
        svc = TKShopService()
        out = svc.auto_design_images_for_local_product(
            lp_id,
            secondary_count=secondary_count,
            replace_existing_ai=True,
        )
        elapsed = time.time() - t0
        generated = int(out.get("generated") or 0)
        failed = int(out.get("failed") or 0)
        msg = (
            f"  #{lp_id} done in {elapsed:.1f}s "
            f"generated={generated} failed={failed}"
        )
        _log(msg)
        return lp_id, generated >= 1, msg
    except Exception as e:
        msg = f"  #{lp_id} FAILED: {e}"
        _log(msg)
        return lp_id, False, msg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "local_product_ids", type=int, nargs="+",
        help="local_product ids to run",
    )
    parser.add_argument("--secondary-count", type=int, default=3)
    parser.add_argument(
        "--workers", type=int, default=1,
        help="parallel products (default 1 = sequential)",
    )
    args = parser.parse_args()
    workers = max(1, min(args.workers, len(args.local_product_ids)))

    _log(
        f"Starting {len(args.local_product_ids)} product(s) "
        f"with workers={workers}, secondary_count={args.secondary_count}"
    )

    failed: list[int] = []
    if workers == 1:
        for lp_id in args.local_product_ids:
            _, ok, _ = _run_one(lp_id, secondary_count=args.secondary_count)
            if not ok:
                failed.append(lp_id)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _run_one, lp_id, secondary_count=args.secondary_count,
                ): lp_id
                for lp_id in args.local_product_ids
            }
            for fut in as_completed(futures):
                lp_id, ok, _ = fut.result()
                if not ok:
                    failed.append(lp_id)

    if failed:
        _log(f"\nFailed ids: {sorted(set(failed))}")
        return 1
    _log("\nAll resumed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
