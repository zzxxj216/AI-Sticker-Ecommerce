"""Catalog classification — fills the hierarchical tree with the LLM.

Two entry points:

* :meth:`rebuild_from_local` — one-shot clustering of *all* products into a
  fresh major→sub→products taxonomy. Used to seed the catalog.
* :meth:`classify_one` — progressive classification of a *single* product into
  the *existing* tree (read L0 → pick/propose major, read L1 → pick/propose
  sub). Used for incremental maintenance and as the descent step in matching.

Both end on the LLM's judgement (so cat ≈ kitten, cross-topic), but only ever
feed it a small index at a time — never the whole catalog.
"""

from __future__ import annotations

from typing import Optional

from src.core.logger import get_logger
from src.services.product_catalog.service import (
    ProductCatalog,
    get_product_catalog,
    slugify,
)

logger = get_logger("service.product_catalog.classifier")


def _dedup_slug(slug: str, used: set[str]) -> str:
    if slug not in used:
        return slug
    i = 2
    while f"{slug}-{i}" in used:
        i += 1
    return f"{slug}-{i}"


class CatalogClassifier:
    def __init__(self, catalog: Optional[ProductCatalog] = None) -> None:
        self.cat = catalog or get_product_catalog()

    # ------------------------------------------------------------------
    # One-shot rebuild (seed the whole tree)
    # ------------------------------------------------------------------

    def rebuild_shop(self, shop: str, *, include_platform: bool = True) -> dict:
        """Cluster one shop's library (local + platform live) into a fresh tree."""
        products = self.cat.gather_for_shop(shop, include_platform=include_platform)
        if not products:
            return {"ok": False, "shop": shop, "error": "no products for shop"}
        by_ref = {p["ref"]: p for p in products}
        source = "\n".join(f"{p['ref']} :: {self.cat.fingerprint(p)}" for p in products)
        taxonomy = self._llm_cluster(source)
        if not taxonomy.get("majors"):
            return {"ok": False, "shop": shop, "error": "LLM returned no taxonomy"}
        self.cat.clear_shop(shop)
        res = self._write_tree(shop, taxonomy, by_ref)
        res["shop"] = shop
        return res

    def rebuild_all(self, shops: list[str], *, include_platform: bool = True) -> dict:
        """Rebuild every shop's tree (one LLM clustering call per shop)."""
        results = [self.rebuild_shop(s, include_platform=include_platform) for s in shops]
        return {"ok": all(r.get("ok") for r in results), "shops": results}

    def _llm_cluster(self, source: str) -> dict:
        from src.services.ai.router import get_router
        from src.services.ai.base import try_parse_json

        prompt = (
            "You organise a sticker shop's products into a 2-level taxonomy so "
            "that products in the SAME sub-category are mergeable variants of "
            "one merchandisable theme — even if they came from different source "
            "topics. Example: 'Minimal Cat Affirmations', 'Magical Dream Cats', "
            "'Cat Cafe' all belong to one sub-category (cats), regardless of "
            "their origin.\n\n"
            "Build:\n"
            "- MAJOR categories (大类): broad domains, e.g. animals, "
            "festivals-seasons, life-milestones, vehicles, memes.\n"
            "- SUB categories (小类): the specific theme within a major, e.g. "
            "cats / dogs under animals; fathers-day / graduation under "
            "festivals-seasons.\n\n"
            "Rules:\n"
            "1. Assign EVERY product to exactly one major→sub. Copy each "
            "product's id (e.g. 'tkshop:4') VERBATIM into that sub's products.\n"
            "2. Group by THEME/subject, not by writing style or material "
            "(ignore generic words like sticker, waterproof, vinyl, pack).\n"
            "3. For each major and sub give: a short english ascii `slug` "
            "(lowercase, hyphens), a human `name` (Chinese ok), and a few "
            "`keywords` that identify the theme (incl. synonyms).\n"
            "4. Prefer a handful of meaningful majors; don't make every product "
            "its own category.\n\n"
            "Return ONLY JSON of shape:\n"
            '{"majors":[{"slug":"animals","name":"动物","keywords":["cat","dog"],'
            '"subs":[{"slug":"cats","name":"猫咪","keywords":["cat","kitten",'
            '"feline"],"products":["tkshop:4","tkshop:5"]}]}]}\n\n'
            f"PRODUCTS (id :: theme):\n{source}"
        )
        text = get_router().text_complete(
            prompt,
            system="You are a precise product taxonomist. Output ONLY JSON.",
            temperature=0.3, max_tokens=8000, task="catalog:cluster",
            related_table="product_catalog",
        )
        return try_parse_json(text) or {}

    # ------------------------------------------------------------------
    # Progressive classification (descend the existing tree, one level at a
    # time — the read path used by matching and incremental maintenance)
    # ------------------------------------------------------------------

    def pick_node(self, options: list[dict], fingerprint: str, level: str) -> dict:
        """Ask the LLM which of ``options`` (a small index: slug/name/keywords)
        the product belongs to. Returns
        ``{slug, is_new, new_name, new_keywords, confidence}``. ``slug`` is ''
        (and ``is_new`` True) when nothing fits.

        Only the small index for ONE level is sent — this is what keeps context
        bounded as the catalog grows."""
        from src.services.ai.router import get_router
        from src.services.ai.base import try_parse_json

        if not options:
            return {"slug": "", "is_new": True, "new_name": "", "new_keywords": [], "confidence": 1.0}

        opt_lines = "\n".join(
            f"- slug={o['slug']} | name={o.get('name', '')} | keywords={', '.join(o.get('keywords') or [])}"
            for o in options
        )
        prompt = (
            f"A new sticker product needs to be placed into a {level} category by "
            "THEME/subject (a cat product joins the cats category even if its "
            "wording differs — cat≈kitten≈feline). Ignore generic words like "
            "sticker/waterproof/vinyl/pack.\n\n"
            f"NEW PRODUCT:\n{fingerprint}\n\n"
            f"EXISTING {level.upper()} CATEGORIES:\n{opt_lines}\n\n"
            "Pick the ONE category it belongs to. If none is the same theme, set "
            "is_new=true and propose a new category (english ascii slug, human "
            "name, keywords).\n"
            'Return ONLY JSON: {"slug":"<existing slug or empty>","is_new":false,'
            '"new_name":"","new_keywords":[],"confidence":0.0}'
        )
        text = get_router().text_complete(
            prompt,
            system="You classify products by theme. Output ONLY JSON.",
            temperature=0.1, max_tokens=400, task=f"catalog:pick_{level}",
            related_table="product_catalog",
        )
        data = try_parse_json(text) or {}
        slug = (data.get("slug") or "").strip()
        valid = {o["slug"] for o in options}
        is_new = bool(data.get("is_new")) or slug not in valid
        return {
            "slug": "" if is_new else slug,
            "is_new": is_new,
            "new_name": (data.get("new_name") or "").strip(),
            "new_keywords": data.get("new_keywords") or [],
            "confidence": float(data.get("confidence") or 0.0),
        }

    def classify_one(self, shop: str, fingerprint: str) -> dict:
        """Descend the existing tree for one product within a shop: pick major
        (L0), then pick sub (L1). Returns the resolved path with per-level
        confidence and ``is_new`` flags. When a level is new, no merge
        candidates exist there yet (the product would seed a new branch)."""
        majors = self.cat.load_majors(shop)
        mpick = self.pick_node(majors, fingerprint, "major")
        if mpick["is_new"]:
            return {
                "major": "", "major_new": True, "major_name": mpick["new_name"],
                "sub": "", "sub_new": True, "sub_name": mpick["new_name"],
                "confidence": mpick["confidence"],
            }
        subs = self.cat.load_subs(shop, mpick["slug"])
        spick = self.pick_node(subs, fingerprint, "sub")
        m_name = next((m["name"] for m in majors if m["slug"] == mpick["slug"]), mpick["slug"])
        return {
            "major": mpick["slug"], "major_new": False, "major_name": m_name,
            "sub": "" if spick["is_new"] else spick["slug"],
            "sub_new": spick["is_new"],
            "sub_name": spick["new_name"] if spick["is_new"]
                        else next((s["name"] for s in subs if s["slug"] == spick["slug"]), spick["slug"]),
            "confidence": round((mpick["confidence"] + spick["confidence"]) / 2, 2),
        }

    def _write_tree(self, shop: str, taxonomy: dict, by_ref: dict[str, dict]) -> dict:
        majors_out: list[dict] = []
        used_major_slugs: set[str] = set()
        assigned: set[str] = set()

        for mj in taxonomy.get("majors", []):
            m_slug = _dedup_slug(slugify(mj.get("slug") or mj.get("name") or "misc"), used_major_slugs)
            used_major_slugs.add(m_slug)
            m_name = (mj.get("name") or m_slug).strip()
            subs_out: list[dict] = []
            used_sub_slugs: set[str] = set()

            for sb in mj.get("subs", []):
                s_slug = _dedup_slug(slugify(sb.get("slug") or sb.get("name") or "misc"), used_sub_slugs)
                used_sub_slugs.add(s_slug)
                s_name = (sb.get("name") or s_slug).strip()
                recs = []
                for ref in (sb.get("products") or []):
                    if ref in by_ref and ref not in assigned:
                        assigned.add(ref)
                        recs.append(by_ref[ref])
                self.cat.write_products(
                    shop, m_slug, s_slug, major_name=m_name, sub_name=s_name, products=recs)
                subs_out.append({
                    "slug": s_slug, "name": s_name,
                    "keywords": sb.get("keywords") or [], "product_count": len(recs),
                })

            self.cat.write_sub_index(shop, m_slug, m_name, subs_out)
            majors_out.append({
                "slug": m_slug, "name": m_name,
                "keywords": mj.get("keywords") or [], "sub_count": len(subs_out),
            })

        # Anything the LLM dropped lands in a visible misc bucket (never lost).
        leftover = [r for r in by_ref if r not in assigned]
        if leftover:
            self.cat.write_products(
                shop, "misc", "uncategorized", major_name="未分类", sub_name="未分类",
                products=[by_ref[r] for r in leftover])
            self.cat.write_sub_index(shop, "misc", "未分类", [
                {"slug": "uncategorized", "name": "未分类", "keywords": [], "product_count": len(leftover)}])
            majors_out.append({
                "slug": "misc", "name": "未分类", "keywords": [], "sub_count": 1})

        self.cat.write_major_index(shop, majors_out)
        logger.info("catalog rebuilt [shop=%s]: %d majors, %d products (%d uncategorized)",
                    shop, len(majors_out), len(assigned) + len(leftover), len(leftover))
        return {
            "ok": True,
            "majors": len(majors_out),
            "products": len(assigned) + len(leftover),
            "uncategorized": len(leftover),
        }


_classifier: Optional[CatalogClassifier] = None


def get_catalog_classifier() -> CatalogClassifier:
    global _classifier
    if _classifier is None:
        _classifier = CatalogClassifier()
    return _classifier
