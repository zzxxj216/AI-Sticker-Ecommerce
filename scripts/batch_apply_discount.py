#!/usr/bin/env python3
"""Apply a standing DIRECT_DISCOUNT to all published listings on given shops."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

LIST_PRICE = float(os.getenv("TKSHOP_DEFAULT_SALE_PRICE", "13.98"))
DEFAULT_TARGET = 6.99
DEFAULT_PERCENT = int(os.getenv("TKSHOP_AUTO_DISCOUNT_PERCENT", "50") or 50)


def discount_for_target_price(
    target: float, *, list_price: float = LIST_PRICE,
) -> tuple[int, float]:
    """Return (integer_percent, rounded_final_price) for TikTok DIRECT_DISCOUNT."""
    best_pct, best_final, best_diff = 1, list_price, abs(list_price - target)
    for pct in range(1, 99):
        final = round(list_price * (1 - pct / 100), 2)
        diff = abs(final - target)
        if diff < best_diff:
            best_pct, best_final, best_diff = pct, final, diff
    return best_pct, best_final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-price", type=float, default=DEFAULT_TARGET,
        help=f"Desired buyer price (list price ${LIST_PRICE:.2f})",
    )
    parser.add_argument(
        "--percent", type=int, default=DEFAULT_PERCENT,
        help="Discount %% (default: TKSHOP_AUTO_DISCOUNT_PERCENT, 50 = $13.98→$6.99)",
    )
    parser.add_argument(
        "--shops", nargs="*", default=["inkelligentsticker", "inkelligentstudio"],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.5, help="Seconds between API calls")
    args = parser.parse_args()

    if args.percent > 0:
        pct = args.percent
        final = round(LIST_PRICE * (1 - pct / 100), 2)
    elif abs(LIST_PRICE * 0.5 - args.target_price) < 0.001:
        pct, final = 50, round(LIST_PRICE * 0.5, 2)
    else:
        pct, final = discount_for_target_price(args.target_price)

    print(json.dumps({
        "list_price": LIST_PRICE,
        "target_price": args.target_price,
        "discount_percent": pct,
        "expected_final_price": final,
        "shops": args.shops,
        "dry_run": args.dry_run,
    }, indent=2))

    if args.dry_run:
        import sqlite3
        conn = sqlite3.connect(ROOT / "data" / "ops_workbench.db")
        rows = conn.execute(
            """
            SELECT id, shop, title, tiktok_product_id, discount_percent
              FROM tkshop_products
             WHERE publish_status = 'published'
               AND COALESCE(tiktok_product_id, '') != ''
               AND shop IN ({})
             ORDER BY shop, id
            """.format(",".join("?" * len(args.shops))),
            tuple(args.shops),
        ).fetchall()
        print(f"would update {len(rows)} listings")
        for r in rows[:5]:
            print(" ", r)
        return 0

    from src.services.tkshop.service import get_tkshop_service
    import sqlite3

    conn = sqlite3.connect(ROOT / "data" / "ops_workbench.db")
    rows = conn.execute(
        """
        SELECT id, shop, title
          FROM tkshop_products
         WHERE publish_status = 'published'
           AND COALESCE(tiktok_product_id, '') != ''
           AND shop IN ({})
         ORDER BY shop, id
        """.format(",".join("?" * len(args.shops))),
        tuple(args.shops),
    ).fetchall()
    conn.close()

    svc = get_tkshop_service()
    ok, fail, skipped = [], [], []
    t0 = time.time()
    for i, (pid, shop, title) in enumerate(rows):
        res = svc.apply_discount(int(pid), percent=float(pct), force=True)
        rec = {
            "product_id": int(pid),
            "shop": shop,
            "title": (title or "")[:60],
            "ok": bool(res.get("applied")),
            "error": res.get("error") or res.get("reason") or "",
            "activity_id": res.get("activity_id") or "",
        }
        (ok if rec["ok"] else fail).append(rec)
        if args.sleep and i + 1 < len(rows):
            time.sleep(args.sleep)

    out = {
        "discount_percent": pct,
        "expected_final_price": final,
        "total": len(rows),
        "applied": len(ok),
        "failed": len(fail),
        "elapsed_s": round(time.time() - t0, 1),
        "failures": fail[:20],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
