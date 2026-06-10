#!/usr/bin/env python3
"""Set TikTok inventory to a fixed quantity for all published listings."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.services.tkshop.service import TKSHOP_SERVER_TIMEOUT, TKSHOP_SERVER_URL

DEFAULT_STOCK = int(os.getenv("TKSHOP_DEFAULT_QUANTITY", "16") or 16)
DEFAULT_SHOPS = ["inkelligentsticker", "inkelligentstudio"]


def _inventory_skus(product_data: dict, qty: int) -> list[dict]:
    skus = product_data.get("skus") or []
    payload: list[dict] = []
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        sku_id = sku.get("id") or sku.get("sku_id")
        if not sku_id:
            continue
        inv = sku.get("inventory") or []
        stock_infos = []
        for row in inv:
            if not isinstance(row, dict):
                continue
            wid = row.get("warehouse_id")
            if not wid:
                continue
            stock_infos.append({"warehouse_id": str(wid), "available_stock": int(qty)})
        if stock_infos:
            payload.append({"id": str(sku_id), "stock_infos": stock_infos})
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=int, default=DEFAULT_STOCK)
    parser.add_argument("--shops", nargs="*", default=DEFAULT_SHOPS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args()

    conn = sqlite3.connect(ROOT / "data" / "ops_workbench.db")
    rows = conn.execute(
        """
        SELECT id, shop, title, tiktok_product_id
          FROM tkshop_products
         WHERE publish_status = 'published'
           AND COALESCE(tiktok_product_id, '') != ''
           AND shop IN ({})
         ORDER BY shop, id
        """.format(",".join("?" * len(args.shops))),
        tuple(args.shops),
    ).fetchall()
    conn.close()

    print(json.dumps({
        "stock": args.stock,
        "shops": args.shops,
        "total": len(rows),
        "dry_run": args.dry_run,
    }, indent=2))

    if args.dry_run:
        for r in rows[:5]:
            print(" ", r)
        return 0

    base = TKSHOP_SERVER_URL.rstrip("/")
    ok, fail = [], []
    t0 = time.time()
    for i, (pid, shop, title, tt_id) in enumerate(rows):
        rec = {
            "product_id": int(pid),
            "shop": shop,
            "tiktok_product_id": tt_id,
            "title": (title or "")[:60],
        }
        try:
            get_url = f"{base}/api/v1/tiktok/products/{tt_id}"
            get_resp = requests.get(get_url, params={"shop": shop}, timeout=TKSHOP_SERVER_TIMEOUT)
            get_data = get_resp.json() if get_resp.text else {}
            product_data = get_data.get("data") if isinstance(get_data, dict) else {}
            if not get_resp.ok or not isinstance(product_data, dict):
                rec["ok"] = False
                rec["error"] = get_data.get("message") or f"GET HTTP {get_resp.status_code}"
                fail.append(rec)
                continue

            skus_payload = _inventory_skus(product_data, args.stock)
            if not skus_payload:
                rec["ok"] = False
                rec["error"] = "no SKU inventory rows found"
                fail.append(rec)
                continue

            put_url = f"{base}/api/v1/tiktok/products/{tt_id}/inventory"
            put_resp = requests.put(
                put_url,
                params={"shop": shop},
                json={"product_id": str(tt_id), "skus": skus_payload},
                timeout=TKSHOP_SERVER_TIMEOUT,
            )
            put_data = put_resp.json() if put_resp.text else {}
            if put_resp.ok and put_data.get("success", True):
                rec["ok"] = True
                rec["sku_count"] = len(skus_payload)
                ok.append(rec)
            else:
                rec["ok"] = False
                rec["error"] = put_data.get("message") or put_data.get("detail") or f"PUT HTTP {put_resp.status_code}"
                fail.append(rec)
        except requests.RequestException as exc:
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"
            fail.append(rec)

        if args.sleep and i + 1 < len(rows):
            time.sleep(args.sleep)

    out = {
        "stock": args.stock,
        "total": len(rows),
        "updated": len(ok),
        "failed": len(fail),
        "elapsed_s": round(time.time() - t0, 1),
        "failures": fail[:20],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
