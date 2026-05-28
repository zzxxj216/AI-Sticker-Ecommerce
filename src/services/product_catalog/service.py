"""Product catalog — hierarchical progressive-loading index of products.

Scenario ② needs to find, for a new product, the *existing* products it could
be merged with (same merchandisable theme, possibly across topics). Throwing
every product at the model each time does not scale: context grows with the
catalog and accuracy drops. Instead we keep a small, skill-style tree on disk
and let the model descend it one small index at a time:

    data/product_catalog/
      index.json              L0  major categories      (always loaded, tiny)
      <major>/_index.json     L1  sub categories         (loaded after major pick)
      <major>/<sub>.json      L2  product records        (loaded after sub pick)

This module owns the *data layer*: gathering source products, building a theme
fingerprint per product, and reading/writing the tree (incl. the progressive
``load_majors`` / ``load_subs`` / ``load_products`` helpers). The LLM
classification that fills the tree lives in :mod:`classifier`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.logger import get_logger

logger = get_logger("service.product_catalog")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
DEFAULT_ROOT = Path("data/product_catalog")

# Same wrapper the lab/single-SKU flows use (multi-channel-api).
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")

INDEX_FILE = "index.json"
SUB_INDEX_FILE = "_index.json"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())


def slugify(s: str, *, fallback: str = "misc", max_len: int = 40) -> str:
    """ASCII slug for filesystem-safe category dirs/files. Non-Latin names
    (e.g. Chinese category names) collapse to ``fallback`` so callers should
    keep a human ``name`` field alongside the slug."""
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (out or fallback)[:max_len]


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _parse_keywords(raw: Any) -> list[str]:
    """tkshop/lab keywords are stored as a JSON array string."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class ProductCatalog:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH, root: Path = DEFAULT_ROOT) -> None:
        self.db_path = db_path
        self.root = root

    # ------------------------------------------------------------------
    # Progressive load (the read path used during matching)
    # ------------------------------------------------------------------

    def _shop_dir(self, shop: str) -> Path:
        """One subtree per shop — products never merge across shops, so the shop
        is the top partition ('分清店铺')."""
        return self.root / (slugify(shop, fallback="_default") if shop else "_default")

    def list_shops(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(d.name for d in self.root.iterdir()
                      if d.is_dir() and (d / INDEX_FILE).exists())

    def load_majors(self, shop: str) -> list[dict]:
        """L0: every major category in a shop with keywords + counts. Small
        enough to hand to the classifier whole."""
        return _read_json(self._shop_dir(shop) / INDEX_FILE, {"majors": []}).get("majors", [])

    def load_subs(self, shop: str, major_slug: str) -> list[dict]:
        """L1: sub categories within one major. Loaded only after a major is
        picked."""
        return _read_json(self._shop_dir(shop) / major_slug / SUB_INDEX_FILE,
                          {"subs": []}).get("subs", [])

    def load_products(self, shop: str, major_slug: str, sub_slug: str) -> list[dict]:
        """L2: the product records of one sub category. Loaded only after a sub
        is picked — this is where the candidate ranking happens."""
        return _read_json(self._shop_dir(shop) / major_slug / f"{sub_slug}.json",
                          {"products": []}).get("products", [])

    # ------------------------------------------------------------------
    # Write path (used by rebuild / incremental maintenance)
    # ------------------------------------------------------------------

    def clear_shop(self, shop: str) -> None:
        """Remove a shop's subtree before a rebuild (stale categories shouldn't
        linger)."""
        import shutil
        d = self._shop_dir(shop)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def write_major_index(self, shop: str, majors: list[dict]) -> None:
        _write_json(self._shop_dir(shop) / INDEX_FILE,
                    {"shop": shop, "majors": majors, "updated_at": _now()})

    def write_sub_index(self, shop: str, major_slug: str, major_name: str, subs: list[dict]) -> None:
        _write_json(
            self._shop_dir(shop) / major_slug / SUB_INDEX_FILE,
            {"shop": shop, "major": major_slug, "major_name": major_name,
             "subs": subs, "updated_at": _now()},
        )

    def write_products(
        self, shop: str, major_slug: str, sub_slug: str, *, major_name: str,
        sub_name: str, products: list[dict],
    ) -> None:
        _write_json(
            self._shop_dir(shop) / major_slug / f"{sub_slug}.json",
            {
                "shop": shop, "major": major_slug, "major_name": major_name,
                "sub": sub_slug, "sub_name": sub_name,
                "products": products, "updated_at": _now(),
            },
        )

    # ------------------------------------------------------------------
    # Source products + fingerprint
    # ------------------------------------------------------------------

    def _pack_topic_name(self, conn: sqlite3.Connection, pack_id: int) -> str:
        row = conn.execute(
            """SELECT ht.topic_name AS topic_name
                 FROM packs pk
                 JOIN pack_series ps ON ps.id = pk.series_id
                 JOIN topic_plans  tp ON tp.id = ps.plan_id
            LEFT JOIN hot_topics   ht ON ht.id = tp.topic_id
                WHERE pk.id = ?""",
            (pack_id,),
        ).fetchone()
        return (row["topic_name"] if row and row["topic_name"] else "") or ""

    def gather_local_products(self) -> list[dict]:
        """Normalised records for every local product that could be a merge
        target — multi-SKU (``lab_msku_products``) and single-SKU
        (``tkshop_products``). Each carries the material the classifier and
        ranker need (title, packs, topic, keywords, shop, status)."""
        out: list[dict] = []
        with _open_db(self.db_path) as conn:
            for r in conn.execute(
                """SELECT id, title, topic_id, shop, publish_status,
                          tiktok_product_id, keywords
                     FROM lab_msku_products"""
            ).fetchall():
                packs = [
                    {"pack_id": p["pack_id"], "name": p["display_name"] or ""}
                    for p in conn.execute(
                        """SELECT s.pack_id, pk.display_name
                             FROM lab_msku_product_skus s
                        LEFT JOIN packs pk ON pk.id = s.pack_id
                            WHERE s.product_id = ? AND s.pack_id IS NOT NULL
                            ORDER BY s.sort_order, s.id""",
                        (r["id"],),
                    ).fetchall()
                ]
                topic_name = ""
                if r["topic_id"]:
                    t = conn.execute(
                        "SELECT topic_name FROM hot_topics WHERE id=?", (r["topic_id"],)
                    ).fetchone()
                    topic_name = (t["topic_name"] if t else "") or ""
                out.append(self._norm_record(
                    kind="lab_msku", product_id=r["id"], title=r["title"],
                    shop=r["shop"], status=r["publish_status"],
                    tiktok_product_id=r["tiktok_product_id"], packs=packs,
                    topic_name=topic_name, keywords=_parse_keywords(r["keywords"]),
                ))

            for r in conn.execute(
                """SELECT id, pack_id, title, shop, publish_status,
                          tiktok_product_id, keywords
                     FROM tkshop_products"""
            ).fetchall():
                packs, topic_name = [], ""
                if r["pack_id"]:
                    pk = conn.execute(
                        "SELECT display_name FROM packs WHERE id=?", (r["pack_id"],)
                    ).fetchone()
                    packs = [{"pack_id": r["pack_id"], "name": (pk["display_name"] if pk else "") or ""}]
                    topic_name = self._pack_topic_name(conn, r["pack_id"])
                out.append(self._norm_record(
                    kind="tkshop", product_id=r["id"], title=r["title"],
                    shop=r["shop"], status=r["publish_status"],
                    tiktok_product_id=r["tiktok_product_id"], packs=packs,
                    topic_name=topic_name, keywords=_parse_keywords(r["keywords"]),
                ))
        return out

    @staticmethod
    def _norm_record(
        *, kind: str, product_id: int, title: Optional[str], shop: Optional[str],
        status: Optional[str], tiktok_product_id: Optional[str],
        packs: list[dict], topic_name: str, keywords: list[str],
    ) -> dict:
        return {
            "ref": f"{kind}:{product_id}",     # stable cross-table id
            "kind": kind,                       # lab_msku | tkshop (| platform later)
            "product_id": product_id,
            "tiktok_product_id": (tiktok_product_id or "").strip(),
            "shop": (shop or "").strip(),
            "title": (title or "").strip(),
            "status": (status or "").strip(),
            "packs": packs,
            "topic_name": topic_name,
            "keywords": keywords,
        }

    def gather_platform_products(self, shop: str, *, max_pages: int = 6,
                                 page_size: int = 50) -> list[dict]:
        """Live products on the platform for a shop, via multi-channel-api's
        ``/products/search`` (paginated). These are merge targets that may not
        exist locally. Packs are unknown from the platform, so the theme comes
        from the title alone. Degrades to [] if the wrapper is unreachable."""
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/search"
        params = {"shop": shop} if shop else None
        out: list[dict] = []
        token = ""
        for _ in range(max_pages):
            body: dict[str, Any] = {"page_size": page_size}
            if token:
                body["page_token"] = token
            try:
                resp = requests.post(endpoint, params=params, json=body, timeout=30)
                data = resp.json() if resp.text else {}
            except requests.RequestException as e:
                logger.warning("platform search failed (shop=%s): %s", shop, e)
                break
            inner = data.get("data") if isinstance(data, dict) else None
            if not isinstance(inner, dict):
                inner = data if isinstance(data, dict) else {}
            for p in (inner.get("products") or inner.get("product_list") or []):
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("id") or p.get("product_id") or "").strip()
                if not pid:
                    continue
                out.append({
                    "ref": f"platform:{pid}", "kind": "platform", "product_id": None,
                    "tiktok_product_id": pid, "shop": shop,
                    "title": (p.get("title") or "").strip(),
                    "status": (p.get("status") or p.get("product_status") or "").strip(),
                    "packs": [], "topic_name": "", "keywords": [],
                })
            token = (inner.get("next_page_token") or inner.get("page_token") or "").strip()
            if not token:
                break
        return out

    def gather_for_shop(self, shop: str, *, include_platform: bool = True) -> list[dict]:
        """The merge-target library for one shop: local products assigned to
        this shop + the shop's platform live products. Deduped by
        ``tiktok_product_id`` (a local record wins over its platform twin —
        it knows the packs)."""
        local = [p for p in self.gather_local_products() if (p.get("shop") or "") == shop]
        records = list(local)
        if include_platform:
            tracked = {p["tiktok_product_id"] for p in local if p["tiktok_product_id"]}
            for pp in self.gather_platform_products(shop):
                if pp["tiktok_product_id"] and pp["tiktok_product_id"] in tracked:
                    continue
                records.append(pp)
        return records

    def find_record(self, ref: str) -> tuple[Optional[dict], str]:
        """Locate a product record by ref → (record, shop). Checks local
        products first, then the catalog files (platform-only records live
        only there). Returns (None, '') if not found."""
        for p in self.gather_local_products():
            if p["ref"] == ref:
                return p, (p.get("shop") or "")
        for shop in self.list_shops():
            for m in self.load_majors(shop):
                for s in self.load_subs(shop, m["slug"]):
                    for p in self.load_products(shop, m["slug"], s["slug"]):
                        if p.get("ref") == ref:
                            return p, shop
        return None, ""

    def prune_product(self, tiktok_id: str) -> int:
        """Self-heal: drop any catalog record with this tiktok_product_id (e.g.
        a product deleted on the platform). Returns how many were removed."""
        if not tiktok_id:
            return 0
        removed = 0
        for shop in self.list_shops():
            for m in self.load_majors(shop):
                for s in self.load_subs(shop, m["slug"]):
                    prods = self.load_products(shop, m["slug"], s["slug"])
                    kept = [p for p in prods if p.get("tiktok_product_id") != tiktok_id]
                    if len(kept) != len(prods):
                        removed += len(prods) - len(kept)
                        self.write_products(shop, m["slug"], s["slug"],
                                            major_name=m["name"], sub_name=s["name"], products=kept)
        if removed:
            logger.info("pruned %d catalog record(s) for deleted product %s", removed, tiktok_id)
        return removed

    @staticmethod
    def fingerprint(p: dict) -> str:
        """One-line theme summary fed to the classifier/ranker. Kept compact so
        many products fit in a single index without bloating context."""
        parts: list[str] = [p.get("title") or ""]
        packs = ", ".join(x["name"] for x in (p.get("packs") or []) if x.get("name"))
        if packs:
            parts.append(f"包: {packs}")
        if p.get("topic_name"):
            parts.append(f"主题: {p['topic_name']}")
        kw = ", ".join(p.get("keywords") or [])
        if kw:
            parts.append(f"关键词: {kw}")
        return " | ".join(x for x in parts if x.strip())


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_catalog: Optional[ProductCatalog] = None


def get_product_catalog() -> ProductCatalog:
    global _catalog
    if _catalog is None:
        _catalog = ProductCatalog()
    return _catalog
