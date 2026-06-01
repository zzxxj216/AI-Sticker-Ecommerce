#!/usr/bin/env python3
"""Deactivate inkelligentsticker listings outside the approved pack allowlist."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ops_workbench.db"

KEEP_PACK_IDS = frozenset({
    78, 77, 76, 75, 74, 71, 66, 65, 64,
    13, 17, 29, 30, 31, 33,
})
SHOP = "inkelligentsticker"


def _load_service():
    spec = importlib.util.spec_from_file_location(
        "tkshop_service", ROOT / "src/services/tkshop/service.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TKShopService(db_path=DB)


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, pack_id, title, publish_status, tiktok_product_id
          FROM tkshop_products
         WHERE shop IN ('inkelligentsticker', 'main')
           AND COALESCE(tiktok_product_id, '') != ''
           AND publish_status NOT IN ('seller_deactivated', 'deleted', 'draft')
         ORDER BY id
        """,
    ).fetchall()
    conn.close()

    to_deactivate = [dict(r) for r in rows if r["pack_id"] not in KEEP_PACK_IDS]
    keep = [dict(r) for r in rows if r["pack_id"] in KEEP_PACK_IDS]

    print(f"Shop: {SHOP}")
    print(f"Keeping {len(keep)} listing(s) on platform")
    print(f"Deactivating {len(to_deactivate)} listing(s)")

    if not to_deactivate:
        print("Nothing to deactivate.")
        return 0

    svc = _load_service()
    ok, fail = 0, 0
    for r in to_deactivate:
        pid = r["id"]
        print(f"\n  #{pid} pack={r['pack_id']} status={r['publish_status']}")
        print(f"    { (r['title'] or '')[:70] }")
        res = svc.deactivate_on_platform(pid)
        if res.get("ok"):
            ok += 1
            print("    ✓ deactivated")
        else:
            fail += 1
            print(f"    ✗ {res.get('error_message') or res}")

    print(f"\nDone: {ok} ok, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
