"""Scenario ② matcher.

Find the existing products a new product could be merged with. The work is done
by descending the catalog tree (``classify_one``: major → sub) and then taking
the products in the matched sub-category as candidates — they are, by
construction, the same merchandisable theme. Only a small index is loaded at
each level, so this stays cheap as the catalog grows.
"""

from __future__ import annotations

from typing import Optional

from src.core.logger import get_logger
from src.services.product_catalog.service import ProductCatalog, get_product_catalog
from src.services.product_catalog.classifier import (
    CatalogClassifier,
    get_catalog_classifier,
)

logger = get_logger("service.product_catalog.matcher")


class CatalogMatcher:
    def __init__(
        self,
        catalog: Optional[ProductCatalog] = None,
        classifier: Optional[CatalogClassifier] = None,
    ) -> None:
        self.cat = catalog or get_product_catalog()
        self.clf = classifier or get_catalog_classifier()

    def find_candidates(
        self,
        shop: str,
        fingerprint: str,
        *,
        exclude_ref: str = "",
        exclude_tiktok_id: str = "",
    ) -> dict:
        """Classify the product down THIS shop's tree, then return the merge
        candidates in its sub-category. The tree is already per-shop, so no
        cross-shop candidate can appear. ``exclude_*`` drop the product itself."""
        path = self.clf.classify_one(shop, fingerprint)
        out: dict = {"shop": shop, "path": path, "candidates": []}

        if path["sub_new"] or not path["sub"]:
            out["note"] = "该店铺下没有同主题产品 — 这是新主题"
            return out

        candidates = []
        for p in self.cat.load_products(shop, path["major"], path["sub"]):
            if exclude_ref and p["ref"] == exclude_ref:
                continue
            if exclude_tiktok_id and p.get("tiktok_product_id") == exclude_tiktok_id:
                continue
            candidates.append(p)
        # Rank the same-theme set cheaply: live products (have a tiktok id)
        # first — merging into a real selling listing is the point — then
        # multi-SKU bundles (already variant-shaped) before single-SKU.
        def _rank(p: dict) -> tuple:
            return (1 if p.get("tiktok_product_id") else 0,
                    1 if p.get("kind") == "lab_msku" else 0)
        candidates.sort(key=_rank, reverse=True)
        out["candidates"] = candidates
        return out

    def find_candidates_for_ref(self, ref: str, *, shop: str = "") -> dict:
        """Match an existing product (local or platform) against its shop's tree
        (excludes itself)."""
        record, rec_shop = self.cat.find_record(ref)
        if not record:
            raise ValueError(f"product {ref} not found")
        return self.find_candidates(shop or rec_shop, self.cat.fingerprint(record), exclude_ref=ref)

    def find_candidates_for_pack(self, pack_id: int, shop: str) -> dict:
        """Match a PACK (a not-yet-product source) against a shop's tree — used
        by the pack-flow 'add to existing' chooser."""
        with __import__("sqlite3").connect(str(self.cat.db_path)) as conn:
            conn.row_factory = __import__("sqlite3").Row
            pk = conn.execute(
                "SELECT id, display_name, pack_uid FROM packs WHERE id=?", (pack_id,)
            ).fetchone()
            if not pk:
                raise ValueError(f"pack #{pack_id} not found")
            topic = self.cat._pack_topic_name(conn, pack_id)
        fp = self.cat.fingerprint({
            "title": pk["display_name"] or pk["pack_uid"] or f"Pack {pack_id}",
            "packs": [{"name": pk["display_name"] or ""}],
            "topic_name": topic, "keywords": [],
        })
        return self.find_candidates(shop, fp)


_matcher: Optional[CatalogMatcher] = None


def get_catalog_matcher() -> CatalogMatcher:
    global _matcher
    if _matcher is None:
        _matcher = CatalogMatcher()
    return _matcher
