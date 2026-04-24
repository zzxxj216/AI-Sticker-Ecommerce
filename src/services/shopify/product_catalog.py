"""Shopify Admin REST: list products and export full payload + CSV tables."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import requests

from src.core.logger import get_logger

logger = get_logger("shopify_product_catalog")


def _next_url_from_link_header(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' not in part and "rel='next'" not in part:
            continue
        m = re.search(r"<([^>]+)>", part.strip())
        if m:
            return m.group(1)
    return None


def fetch_all_products(
    rest_base: str,
    headers: dict[str, str],
    *,
    published_status: str = "any",
    fields: str | None = None,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """Paginate ``GET /products.json``. ``fields=None`` returns full product objects."""
    products: list[dict[str, Any]] = []
    q = f"{rest_base}/products.json?limit=250&published_status={published_status}"
    if fields:
        q += f"&fields={fields}"
    url = q
    while url:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        batch = r.json().get("products") or []
        products.extend(batch)
        url = _next_url_from_link_header(r.headers.get("Link"))
        logger.info("products total=%s last_page=%s", len(products), len(batch))
    return products


def storefront_product_url(
    shop_domain: str, handle: str, base_url: str | None = None
) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base:
        return f"{base}/products/{handle}"
    return f"https://{shop_domain}/products/{handle}"


def admin_product_url(shop_domain: str, product_id: int | str) -> str:
    return f"https://{shop_domain}/admin/products/{product_id}"


def export_products_csv(
    rows: list[dict[str, Any]],
    shop_domain: str,
    outfile: Path,
    storefront_base: str | None = None,
) -> Path:
    """简易单列：标题、链接等（兼容旧用法）。"""
    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "title",
        "handle",
        "status",
        "product_type",
        "vendor",
        "storefront_url",
        "admin_url",
    ]
    with outfile.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for p in rows:
            pid = p.get("id")
            handle = p.get("handle") or ""
            w.writerow(
                {
                    "id": pid,
                    "title": p.get("title") or "",
                    "handle": handle,
                    "status": p.get("status") or "",
                    "product_type": p.get("product_type") or "",
                    "vendor": p.get("vendor") or "",
                    "storefront_url": storefront_product_url(
                        shop_domain, handle, storefront_base
                    ),
                    "admin_url": admin_product_url(shop_domain, pid),
                }
            )
    return outfile


def _cell_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def export_products_full_json(
    products: list[dict[str, Any]], outfile: Path, *, indent: int = 2
) -> Path:
    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    with outfile.open("w", encoding="utf-8") as f:
        json.dump({"products": products}, f, ensure_ascii=False, indent=indent)
    return outfile


def export_products_flat_csv(
    products: list[dict[str, Any]],
    shop_domain: str,
    outfile: Path,
    storefront_base: str | None = None,
) -> Path:
    """每个产品一行；嵌套字段序列化为 JSON 字符串单元格。"""
    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    keys: set[str] = set()
    for p in products:
        flat: dict[str, str] = {}
        for k, v in p.items():
            flat[k] = _cell_value(v)
            keys.add(k)
        pid = p.get("id")
        handle = p.get("handle") or ""
        flat["storefront_url"] = storefront_product_url(
            shop_domain, handle, storefront_base
        )
        flat["admin_url"] = admin_product_url(shop_domain, pid)
        keys.update(("storefront_url", "admin_url"))
        rows.append(flat)
    priority = [
        "id",
        "title",
        "handle",
        "status",
        "vendor",
        "product_type",
        "tags",
        "body_html",
        "storefront_url",
        "admin_url",
    ]
    rest = sorted(k for k in keys if k not in priority)
    fieldnames = [k for k in priority if k in keys] + rest
    with outfile.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return outfile


def export_variants_csv(
    products: list[dict[str, Any]],
    shop_domain: str,
    outfile: Path,
    storefront_base: str | None = None,
) -> Path:
    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    keys: set[str] = {
        "product_id",
        "product_title",
        "product_handle",
        "storefront_url",
    }
    for p in products:
        pid = p.get("id")
        title = p.get("title") or ""
        handle = p.get("handle") or ""
        sf = storefront_product_url(shop_domain, handle, storefront_base)
        for v in p.get("variants") or []:
            if not isinstance(v, dict):
                continue
            row: dict[str, str] = {
                "product_id": str(pid) if pid is not None else "",
                "product_title": title,
                "product_handle": handle,
                "storefront_url": sf,
            }
            for k, val in v.items():
                row[k] = _cell_value(val)
                keys.add(k)
            rows.append(row)
    priority = [
        "product_id",
        "product_title",
        "product_handle",
        "storefront_url",
        "id",
        "sku",
        "title",
        "price",
        "compare_at_price",
        "inventory_quantity",
    ]
    rest = sorted(k for k in keys if k not in priority)
    fieldnames = [k for k in priority if k in keys] + rest
    with outfile.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
    return outfile


def export_images_csv(
    products: list[dict[str, Any]],
    shop_domain: str,
    outfile: Path,
    storefront_base: str | None = None,
) -> Path:
    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    keys: set[str] = {
        "product_id",
        "product_title",
        "product_handle",
        "storefront_url",
    }
    for p in products:
        pid = p.get("id")
        title = p.get("title") or ""
        handle = p.get("handle") or ""
        sf = storefront_product_url(shop_domain, handle, storefront_base)
        for img in p.get("images") or []:
            if not isinstance(img, dict):
                continue
            row: dict[str, str] = {
                "product_id": str(pid) if pid is not None else "",
                "product_title": title,
                "product_handle": handle,
                "storefront_url": sf,
            }
            for k, val in img.items():
                row[k] = _cell_value(val)
                keys.add(k)
            rows.append(row)
    priority = [
        "product_id",
        "product_title",
        "product_handle",
        "storefront_url",
        "id",
        "position",
        "src",
        "alt",
        "width",
        "height",
    ]
    rest = sorted(k for k in keys if k not in priority)
    fieldnames = [k for k in priority if k in keys] + rest
    with outfile.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
    return outfile


def resolve_export_stem(output: str | Path) -> Path:
    """``foo.csv`` / ``foo.json`` -> stem ``foo``；其余路径原样作前缀。"""
    p = Path(output)
    if p.suffix.lower() in (".csv", ".json"):
        return p.with_suffix("")
    return p


def export_full_bundle(
    products: list[dict[str, Any]],
    shop_domain: str,
    stem: Path,
    storefront_base: str | None = None,
    *,
    write_json: bool = True,
    write_tables: bool = True,
    write_summary: bool = False,
) -> dict[str, Path]:
    """写入 ``{stem}_full.json``、``{stem}_products.csv`` 等。"""
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    if write_json:
        out["json"] = export_products_full_json(
            products, stem.parent / f"{stem.name}_full.json"
        )
    if write_tables:
        out["products"] = export_products_flat_csv(
            products,
            shop_domain,
            stem.parent / f"{stem.name}_products.csv",
            storefront_base,
        )
        out["variants"] = export_variants_csv(
            products,
            shop_domain,
            stem.parent / f"{stem.name}_variants.csv",
            storefront_base,
        )
        out["images"] = export_images_csv(
            products,
            shop_domain,
            stem.parent / f"{stem.name}_images.csv",
            storefront_base,
        )
    if write_summary:
        out["summary"] = export_products_csv(
            products,
            shop_domain,
            stem.parent / f"{stem.name}_summary.csv",
            storefront_base,
        )
    return out
