"""Scenario ② merge orchestration.

Sub-case 1 (``merge_local``): fold related local products into ONE multi-SKU
product / one link. Per the product decision, the resulting listing's title +
description come from the *new* product. Source products are left intact (the
caller decides whether to archive/deactivate them) so a merge is never
destructive on its own.

Sub-case 2 (``merge_into_platform``) lives below and touches a live listing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.logger import get_logger
from src.services.lab_multi_sku import get_lab_multi_sku_service
from src.services.lab_multi_sku.service import attr_value_en, pack_seller_sku
from src.services.product_catalog.service import DEFAULT_DB_PATH, TKSHOP_SERVER_URL

logger = get_logger("service.product_catalog.merge")


def _cents_to_amount(v: Any) -> str:
    """TikTok GET returns prices as integer-cent strings ('1699' = $16.99).
    Convert to a major-unit string for re-submission; pass through if it
    already has a decimal."""
    s = str(v or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return f"{int(s) / 100:.2f}"
    return s


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ref(ref: str) -> tuple[str, int]:
    kind, _, pid = (ref or "").partition(":")
    return kind, int(pid)


def _fetch_meta(conn: sqlite3.Connection, ref: str) -> Optional[dict]:
    kind, pid = _parse_ref(ref)
    table = "lab_msku_products" if kind == "lab_msku" else "tkshop_products"
    r = conn.execute(
        f"SELECT title, description_html, shop, tiktok_product_id FROM {table} WHERE id=?",
        (pid,),
    ).fetchone()
    if not r:
        return None
    if kind == "lab_msku":
        pack_ids = [
            row["pack_id"] for row in conn.execute(
                "SELECT pack_id FROM lab_msku_product_skus WHERE product_id=? "
                "AND pack_id IS NOT NULL ORDER BY sort_order, id",
                (pid,),
            ).fetchall()
        ]
    else:
        pr = conn.execute("SELECT pack_id FROM tkshop_products WHERE id=?", (pid,)).fetchone()
        pack_ids = [pr["pack_id"]] if pr and pr["pack_id"] else []
    return {
        "ref": ref, "kind": kind, "id": pid,
        "title": (r["title"] or "").strip(),
        "description_html": r["description_html"] or "",
        "shop": (r["shop"] or "").strip(),
        "tiktok_product_id": (r["tiktok_product_id"] or "").strip(),
        "pack_ids": pack_ids,
    }


class MergeService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.lab = get_lab_multi_sku_service()

    def merge_local(self, new_ref: str, target_refs: list[str]) -> dict:
        """Combine ``new_ref`` + one or more ``target_refs`` into a single
        multi-SKU product. Title/description follow the NEW product.

        Container choice (least churn): reuse an existing multi-SKU product if
        one is involved; otherwise create a fresh multi-SKU product seeded from
        the new product's pack. Every other product's packs become new SKUs.
        """
        if not target_refs:
            raise ValueError("no target selected")
        with _open_db(self.db_path) as conn:
            new = _fetch_meta(conn, new_ref)
            targets = [_fetch_meta(conn, t) for t in target_refs]
        if not new:
            raise ValueError(f"new product {new_ref} not found")
        targets = [t for t in targets if t]
        if not targets:
            raise ValueError("target product(s) not found")

        shop = new["shop"] or next((t["shop"] for t in targets if t["shop"]), "")
        all_parts = [new] + targets

        # Pick the container: prefer an existing multi-SKU (new first, then a
        # target); else create a fresh one seeded from the new product's pack.
        if new["kind"] == "lab_msku":
            container = new["id"]
            self.lab.update_product_meta(container, title=new["title"],
                                         description_html=new["description_html"])
            donors = [p for p in targets]
        else:
            lab_target = next((t for t in targets if t["kind"] == "lab_msku"), None)
            if lab_target:
                container = lab_target["id"]
                self.lab.update_product_meta(container, title=new["title"],
                                             description_html=new["description_html"])
                donors = [new] + [t for t in targets if t is not lab_target]
            else:
                if not new["pack_ids"]:
                    raise ValueError("new product has no pack to seed the bundle")
                container = self.lab.create_from_pack(
                    new["pack_ids"][0], shop=shop,
                    title=new["title"], description_html=new["description_html"])
                # new's first pack already seeded; remaining new packs + targets
                donors = [{"pack_ids": new["pack_ids"][1:]}] + targets

        added = []
        for d in donors:
            for pid in d.get("pack_ids", []):
                sku_id = self.lab.add_sku_from_pack(container, pid)
                if sku_id:
                    added.append({"pack_id": pid, "sku_id": sku_id})

        logger.info("local merge → product #%s, +%d SKUs from %s",
                    container, len(added), [p["ref"] for p in all_parts])
        return {
            "ok": True,
            "canonical_product_id": container,
            "added_skus": added,
            "merged_refs": [p["ref"] for p in all_parts],
            "detail_url": f"/v2/lab/multi-sku/{container}",
            "note": "源产品未删除;如需避免重复上架,请单独归档/下架它们",
        }


    # ------------------------------------------------------------------
    # Sub-case 2: add a pack as a new SKU to a LIVE platform product
    # ------------------------------------------------------------------

    def _fetch_live(self, tiktok_id: str, shop: str) -> dict:
        """Read a live product's current state so a full-PUT update doesn't
        clobber what's already there (existing SKUs, title, description,
        images, category)."""
        url = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tiktok_id}"
        resp = requests.get(url, params={"shop": shop} if shop else None, timeout=30)
        data = resp.json() if resp.text else {}
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        if not inner.get("id"):
            raise ValueError(f"live product {tiktok_id} not found (shop={shop})")
        chains = inner.get("category_chains") or []
        leaf = next((c.get("id") for c in chains if c.get("is_leaf")), "") or "928016"
        image_urls = []
        for im in (inner.get("main_images") or []):
            urls = im.get("urls") or []
            if urls:
                image_urls.append(urls[0])
        title = (inner.get("title") or "").strip()
        skus = []
        for s in (inner.get("skus") or []):
            attrs = s.get("sales_attributes") or []
            attr_val = (attrs[0].get("value_name") if attrs else "") or ""
            if not attr_val:  # single-SKU product → no dimension yet; derive one
                attr_val = attr_value_en(title.split("|")[0], s.get("seller_sku") or "Original")
            img = (attrs[0].get("sku_img") if attrs else None) or {}
            sku_img_url = (img.get("urls") or [None])[0] or ""
            inv = s.get("inventory") or []
            skus.append({
                "seller_sku": s.get("seller_sku") or "",
                "attribute_value": attr_val,
                "price_amount": _cents_to_amount((s.get("price") or {}).get("sale_price")),
                "stock": (inv[0].get("quantity") if inv else None),
                "image_path": "", "image_url": sku_img_url,
            })
        return {
            "title": title,
            "description_html": inner.get("description") or "",
            "category_id": leaf,
            "image_urls": image_urls,
            "skus": skus,
        }

    def _ai_refresh_desc(self, title: str, variant_names: list[str]) -> str:
        """Regenerate a short HTML description covering all variants. Used only
        when the operator opts into 'AI 刷新描述' in the preview."""
        from src.services.ai.router import get_router
        prompt = (
            f"Write a short English (en-US) HTML product description for a "
            f"multi-variant sticker product titled '{title}'. The buyer picks "
            f"one of these variants: {', '.join(v for v in variant_names if v)}. "
            "Use one <p> plus a <ul> of 2-4 short selling points. "
            "Return ONLY the HTML, no code fences."
        )
        return get_router().text_complete(
            prompt, system="You write concise e-commerce HTML. Output ONLY HTML.",
            temperature=0.6, max_tokens=600, task="msku:refresh_desc",
            related_table="lab_msku_products",
        ).strip()

    def preview_platform_merge(self, pack_id: int, tiktok_id: str, shop: str) -> dict:
        """Read-only: fetch the live product + project the post-merge result so
        the operator can review (and adjust) BEFORE the irreversible write.
        Returns ``stale=True`` if the product no longer exists on the platform."""
        try:
            live = self._fetch_live(tiktok_id, shop)
        except ValueError:
            return {"ok": False, "stale": True, "tiktok_product_id": tiktok_id,
                    "error": "该产品在平台已不存在(可能已删除)"}
        with _open_db(self.db_path) as conn:
            pk = conn.execute(
                "SELECT id, display_name, pack_uid FROM packs WHERE id=?", (pack_id,)
            ).fetchone()
        if not pk:
            raise ValueError(f"pack #{pack_id} not found")
        new_seller_sku = pack_seller_sku(pk["pack_uid"] or pk["display_name"] or "", pack_id)
        already = any(s["seller_sku"] == new_seller_sku for s in live["skus"])
        existing_vals = {s["attribute_value"] for s in live["skus"]}
        new_val = attr_value_en(pk["display_name"] or "", f"Pack {pack_id}")
        if new_val in existing_vals:
            new_val = f"{new_val} ({pack_id})"[:100]
        return {
            "ok": True, "stale": False, "already_present": already,
            "tiktok_product_id": tiktok_id, "shop": shop,
            "target": {
                "title": live["title"],
                "description_html": live["description_html"],
                "skus": [{"seller_sku": s["seller_sku"],
                          "attribute_value": s["attribute_value"],
                          "image_url": s.get("image_url") or ""} for s in live["skus"]],
            },
            "new_variant": {
                "seller_sku": new_seller_sku,
                "attribute_value": new_val,
                "pack_name": pk["display_name"] or "",
                "image_path": get_lab_multi_sku_service()._pack_image_abs(pack_id),
            },
        }

    def merge_into_platform(self, pack_id: int, tiktok_id: str, shop: str,
                            *, new_variant_value: str = "", refresh_desc: bool = False) -> dict:
        """情况2: add a pack as a NEW SKU to a live product. Fetches the live
        state, appends the pack as a variant (keeping all existing SKUs + title
        + images), and full-PUTs it back. Single-SKU products are promoted to
        multi (the existing SKU gets a derived variant value). The new variant's
        name can be overridden; the description is kept unless ``refresh_desc``."""
        live = self._fetch_live(tiktok_id, shop)

        with _open_db(self.db_path) as conn:
            pk = conn.execute(
                "SELECT id, display_name, pack_uid FROM packs WHERE id=?", (pack_id,)
            ).fetchone()
        if not pk:
            raise ValueError(f"pack #{pack_id} not found")
        new_seller_sku = pack_seller_sku(pk["pack_uid"] or pk["display_name"] or "", pack_id)
        if any(s["seller_sku"] == new_seller_sku for s in live["skus"]):
            return {"ok": False, "error": "该卡包已是这个产品的 SKU", "tiktok_product_id": tiktok_id}

        existing_vals = {s["attribute_value"] for s in live["skus"]}
        new_val = attr_value_en(new_variant_value or pk["display_name"] or "", f"Pack {pack_id}")
        if new_val in existing_vals:
            new_val = f"{new_val} ({pack_id})"[:100]

        description_html = live["description_html"]
        if refresh_desc:
            try:
                refreshed = self._ai_refresh_desc(
                    live["title"], [s["attribute_value"] for s in live["skus"]] + [new_val])
                if refreshed:
                    description_html = refreshed
            except Exception as e:
                logger.warning("desc refresh failed, keeping original: %s", e)
        # TikTok requires EVERY variant of a multi-SKU product to carry an image
        # on update. Variants lacking one fall back to the product main image;
        # the new variant uses its pack cover (or the same fallback).
        fallback_img = live["image_urls"][0] if live["image_urls"] else ""
        for s in live["skus"]:
            if not s.get("image_url") and not s.get("image_path"):
                s["image_url"] = fallback_img
        new_pack_img = get_lab_multi_sku_service()._pack_image_abs(pack_id)
        new_sku = {
            "seller_sku": new_seller_sku, "attribute_value": new_val,
            "price_amount": "", "stock": None,
            "image_path": new_pack_img,
            "image_url": "" if new_pack_img else fallback_img,
        }

        payload = {
            "title": live["title"],
            "description_html": description_html,
            "category_id": live["category_id"],
            "sales_attribute_name": "Theme",
            "image_urls": live["image_urls"],
            "image_paths": [],
            "skus": live["skus"] + [new_sku],
        }
        url = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tiktok_id}/sticker_multi_sku_update"
        try:
            resp = requests.post(url, params={"shop": shop} if shop else None,
                                 json=payload, timeout=180)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        if not (resp.ok and inner.get("ok")):
            return {"ok": False, "tiktok_product_id": tiktok_id,
                    "error_code": inner.get("error_code"),
                    "error": inner.get("error_message") or "update failed"}
        logger.info("情况2 merge: pack #%s → live %s (shop=%s), now %d SKUs",
                    pack_id, tiktok_id, shop, len(payload["skus"]))
        return {
            "ok": True, "tiktok_product_id": tiktok_id, "shop": shop,
            "sku_count": len(payload["skus"]),
            "added_seller_sku": new_seller_sku,
            "skus": inner.get("skus") or [],
        }


_merge: Optional[MergeService] = None


def get_merge_service() -> MergeService:
    global _merge
    if _merge is None:
        _merge = MergeService()
    return _merge
