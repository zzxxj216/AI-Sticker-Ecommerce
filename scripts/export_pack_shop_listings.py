#!/usr/bin/env python3
"""Export per-shop title, SKU ids, and cover for selected packs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ops_workbench.db"
# Default shops for export script (internal registry keys).
DEFAULT_SHOPS = ("inkelligentsticker", "inkelligentstudio")
DEFAULT_SALE_PRICE = "16.99"
DEFAULT_DISCOUNT_PRICE = ""
DEFAULT_STOCK = "100"


def _parse_keywords(raw: str) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _search_keywords(keywords: list[str]) -> str:
    parts = [k.strip().lstrip("#") for k in keywords if k.strip()]
    return ", ".join(parts)[:500]


def _path_to_v2_url(disk_path: str) -> str:
    p = (disk_path or "").replace("\\", "/")
    marker = "output/packs/"
    idx = p.find(marker)
    if idx < 0:
        return ""
    return "/v2-outputs/" + p[idx + len(marker) :]


def _resolve_cover(conn: sqlite3.Connection, *, pack_id: int, listing_id: int | None) -> str:
    if listing_id:
        row = conn.execute(
            """
            SELECT local_path FROM tkshop_product_images
             WHERE product_id = ? AND role = 'main'
             ORDER BY sort_order ASC, id ASC LIMIT 1
            """,
            (listing_id,),
        ).fetchone()
        if row and row[0]:
            return str(row[0]).replace("\\", "/")
    row = conn.execute(
        """
        SELECT li.local_path
          FROM local_product_images li
          JOIN local_products lp ON lp.id = li.local_product_id
         WHERE lp.pack_id = ? AND li.role = 'main'
         ORDER BY li.sort_order ASC, li.id ASC
         LIMIT 1
        """,
        (pack_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0]).replace("\\", "/")
    row = conn.execute(
        "SELECT cover_image_path FROM packs WHERE id = ?", (pack_id,),
    ).fetchone()
    return (row[0] or "").replace("\\", "/") if row else ""


def _tunnel_base() -> str:
    log = ROOT / "logs" / "tunnel.log"
    if not log.is_file():
        return ""
    text = log.read_text(errors="ignore")
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
    return m.group(0).rstrip("/") if m else ""


def export_rows(pack_ids: list[int], shops: tuple[str, ...]) -> list[dict]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    base = _tunnel_base()
    out: list[dict] = []
    for pack_id in pack_ids:
        pack = conn.execute(
            "SELECT id, pack_uid, display_name FROM packs WHERE id = ?",
            (pack_id,),
        ).fetchone()
        if not pack:
            out.append({
                "pack_id": pack_id,
                "pack_name": "",
                "shop": "",
                "title": "",
                "seller_sku": "",
                "tiktok_product_id": "",
                "tiktok_sku_id": "",
                "cover_path": "",
                "cover_url": "",
                "cover_public_url": "",
                "publish_status": "pack_not_found",
                "keywords": "",
                "search_keywords": "",
                "sale_price": "",
                "discount_price": "",
                "stock": "",
            })
            continue
        listings = conn.execute(
            """
            SELECT id, shop, title, seller_sku, tiktok_product_id,
                   tiktok_sku_id, publish_status, keywords
              FROM tkshop_products
             WHERE pack_id = ?
             ORDER BY shop, id
            """,
            (pack_id,),
        ).fetchall()
        by_shop = {r["shop"]: r for r in listings}
        for shop in shops:
            row = by_shop.get(shop)
            if not row:
                out.append({
                    "pack_id": pack_id,
                    "pack_uid": pack["pack_uid"],
                    "pack_name": pack["display_name"],
                    "shop": shop,
                    "title": "",
                    "seller_sku": "",
                    "tiktok_product_id": "",
                    "tiktok_sku_id": "",
                    "cover_path": _resolve_cover(conn, pack_id=pack_id, listing_id=None),
                    "cover_url": "",
                    "cover_public_url": "",
                    "publish_status": "no_listing",
                    "keywords": "",
                    "search_keywords": "",
                    "sale_price": "",
                    "discount_price": "",
                    "stock": "",
                })
                continue
            kws = _parse_keywords(row["keywords"] or "[]")
            cover = _resolve_cover(conn, pack_id=pack_id, listing_id=row["id"])
            rel = _path_to_v2_url(cover)
            out.append({
                "pack_id": pack_id,
                "pack_uid": pack["pack_uid"],
                "pack_name": pack["display_name"],
                "shop": shop,
                "title": row["title"] or "",
                "seller_sku": row["seller_sku"] or "",
                "tiktok_product_id": row["tiktok_product_id"] or "",
                "tiktok_sku_id": row["tiktok_sku_id"] or "",
                "cover_path": cover,
                "cover_url": rel,
                "cover_public_url": (base + rel) if base and rel else "",
                "publish_status": row["publish_status"] or "",
                "keywords": ", ".join(kws),
                "search_keywords": _search_keywords(kws),
                "sale_price": DEFAULT_SALE_PRICE,
                "discount_price": DEFAULT_DISCOUNT_PRICE,
                "stock": DEFAULT_STOCK,
            })
    conn.close()
    return out


def pivot_wide(rows: list[dict], shops: tuple[str, ...]) -> list[dict]:
    """One row per pack; each shop's fields become prefixed columns."""
    by_pack: dict[int, dict] = {}
    for r in rows:
        pid = int(r["pack_id"])
        base = by_pack.setdefault(pid, {
            "pack_id": pid,
            "pack_uid": r.get("pack_uid") or "",
            "pack_name": r.get("pack_name") or "",
        })
        shop = (r.get("shop") or "").strip()
        if shop not in shops:
            continue
        prefix = shop
        for key in (
            "title", "seller_sku", "tiktok_product_id", "tiktok_sku_id",
            "cover_path", "cover_url", "cover_public_url", "publish_status",
            "keywords", "search_keywords", "sale_price", "discount_price", "stock",
        ):
            base[f"{prefix}_{key}"] = r.get(key) or ""
    wide: list[dict] = []
    for pid in sorted(by_pack):
        row = by_pack[pid]
        for shop in shops:
            for key in (
                "title", "seller_sku", "tiktok_product_id", "tiktok_sku_id",
                "cover_path", "cover_url", "cover_public_url", "publish_status",
            ):
                row.setdefault(f"{shop}_{key}", "")
        wide.append(row)
    return wide


def _wide_fieldnames(shops: tuple[str, ...]) -> list[str]:
    fields = ["pack_id", "pack_uid", "pack_name"]
    for shop in shops:
        for key in (
            "title", "seller_sku", "tiktok_product_id", "tiktok_sku_id",
            "cover_public_url", "cover_url", "cover_path", "publish_status",
            "keywords", "search_keywords", "sale_price", "discount_price", "stock",
        ):
            fields.append(f"{shop}_{key}")
    return fields


def write_xlsx(
    path: Path,
    *,
    wide_rows: list[dict],
    long_rows: list[dict],
    shops: tuple[str, ...],
) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws_wide = wb.active
    ws_wide.title = "汇总"
    wide_fields = _wide_fieldnames(shops)
    ws_wide.append(wide_fields)
    for cell in ws_wide[1]:
        cell.font = Font(bold=True)
    for r in wide_rows:
        ws_wide.append([r.get(f, "") for f in wide_fields])

    ws_long = wb.create_sheet("分店铺明细")
    long_fields = [
        "pack_id", "pack_uid", "pack_name", "shop", "title",
        "seller_sku", "tiktok_product_id", "tiktok_sku_id",
        "cover_path", "cover_url", "cover_public_url", "publish_status",
        "keywords", "search_keywords", "sale_price", "discount_price", "stock",
    ]
    ws_long.append(long_fields)
    for cell in ws_long[1]:
        cell.font = Font(bold=True)
    for r in long_rows:
        if r.get("publish_status") == "no_listing":
            continue
        ws_long.append([r.get(f, "") for f in long_fields])

    wb.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_ids", type=int, nargs="+")
    parser.add_argument(
        "--shops", default=",".join(DEFAULT_SHOPS),
        help=f"comma-separated shop keys (default: {','.join(DEFAULT_SHOPS)})",
    )
    parser.add_argument(
        "-o", "--output",
        default=str(ROOT / "output" / "pack_shop_export.csv"),
    )
    args = parser.parse_args()
    shops = tuple(s.strip() for s in args.shops.split(",") if s.strip())
    rows = export_rows(args.pack_ids, shops)
    wide = pivot_wide(rows, shops)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "pack_id", "pack_uid", "pack_name", "shop", "title",
        "seller_sku", "tiktok_product_id", "tiktok_sku_id",
        "cover_path", "cover_url", "cover_public_url", "publish_status",
        "keywords", "search_keywords", "sale_price", "discount_price", "stock",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_wide_fieldnames(shops))
        w.writeheader()
        w.writerows(wide)
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(wide, ensure_ascii=False, indent=2), encoding="utf-8")
    xlsx_path = out_path.with_suffix(".xlsx")
    write_xlsx(xlsx_path, wide_rows=wide, long_rows=rows, shops=shops)
    long_csv = out_path.with_name(out_path.stem + "_by_shop.csv")
    with long_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(r for r in rows if r.get("publish_status") != "no_listing")
    print(f"Wrote {len(wide)} pack row(s) -> {out_path} (wide CSV)")
    print(f"Excel -> {xlsx_path}")
    print(f"Long CSV (existing listings only) -> {long_csv}")
    print(f"JSON -> {json_path}")
    missing = [r for r in rows if r["publish_status"] in ("no_listing", "pack_not_found")]
    if missing:
        print(f"Note: {len(missing)} shop slot(s) have no listing (left blank in 汇总)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
