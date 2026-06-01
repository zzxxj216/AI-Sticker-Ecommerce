#!/usr/bin/env python3
"""Fix tkshop_products.shop when 2店/3店 labels were swapped vs TikTok.

Probes each published listing against multi-channel-api with both shop
credentials and updates the local ``shop`` column to match the store that
actually owns the ``tiktok_product_id``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.services.tkshop.service import TKSHOP_SERVER_URL, TKSHOP_SERVER_TIMEOUT

DB = ROOT / "data" / "ops_workbench.db"
SHOPS = ("inkelligentsticker", "inkelligentstudio")


def _probe(tt_id: str, shop: str) -> bool:
    url = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tt_id}"
    try:
        resp = requests.get(url, params={"shop": shop}, timeout=TKSHOP_SERVER_TIMEOUT)
        data = resp.json() if resp.text else {}
        inner = data.get("data") if isinstance(data, dict) else None
        return bool(data.get("success")) and isinstance(inner, dict) and bool(inner)
    except requests.RequestException:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Write fixes to DB")
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        parser.error("pass --dry-run or --apply")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, shop, tiktok_product_id, local_product_id, title
          FROM tkshop_products
         WHERE COALESCE(tiktok_product_id, '') != ''
         ORDER BY id
        """
    ).fetchall()

    fixes, ok, ambiguous, missing = [], [], [], []
    for row in rows:
        tt = (row["tiktok_product_id"] or "").strip()
        db_shop = (row["shop"] or "").strip()
        hits = [s for s in SHOPS if _probe(tt, s)]
        time.sleep(0.15)
        if len(hits) == 1:
            actual = hits[0]
            if actual == db_shop:
                ok.append(int(row["id"]))
            else:
                fixes.append({
                    "listing_id": int(row["id"]),
                    "local_product_id": row["local_product_id"],
                    "db_shop": db_shop,
                    "actual_shop": actual,
                    "tiktok_product_id": tt,
                    "title": (row["title"] or "")[:60],
                })
        elif not hits:
            missing.append(int(row["id"]))
        else:
            ambiguous.append({"listing_id": int(row["id"]), "hits": hits, "tt_id": tt})

    report = {
        "total": len(rows),
        "already_ok": len(ok),
        "to_fix": len(fixes),
        "missing": len(missing),
        "ambiguous": len(ambiguous),
        "fixes": fixes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.apply and fixes:
        now = int(time.time())
        for item in fixes:
            conn.execute(
                "UPDATE tkshop_products SET shop = ? WHERE id = ?",
                (item["actual_shop"], item["listing_id"]),
            )
        conn.commit()
        print(f"updated {len(fixes)} listing shop row(s)")

    return 0 if not ambiguous else 1


if __name__ == "__main__":
    raise SystemExit(main())
