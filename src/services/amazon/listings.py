"""Amazon listing 服务(卡贴包线)。raw sqlite,路由层薄,AI 走 AIRouter,图床走 cdn。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Optional

from src.core.logger import get_logger
from . import copy_generator, payload, aplus, image_studio
from .cdn import get_cdn

logger = get_logger("services.amazon")

DB_PATH = os.getenv("OPS_DB_PATH", "data/ops_workbench.db")


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> int:
    return int(time.time())


class AmazonListingService:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- master 读取(复用 tkshop 的 get_local_product) ----
    def _master(self, local_product_id: int) -> Optional[dict]:
        try:
            from src.services.tkshop.service import get_tkshop_service
            return get_tkshop_service().get_local_product(local_product_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("amazon _master(%s) failed: %s", local_product_id, str(e)[:200])
            return None

    # ---- listing 行 ----
    def get_or_create(self, local_product_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM amazon_listings WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
            if row:
                return dict(row)
            master = self._master(local_product_id) or {}
            sku = (master.get("seller_sku") or "").strip()
            price = master.get("default_price")
            now = _now()
            conn.execute(
                """INSERT INTO amazon_listings
                   (local_product_id, seller_sku, price, created_at, updated_at)
                   VALUES (?,?,?,?,?)""",
                (local_product_id, sku, price, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM amazon_listings WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
            return dict(row)

    def _save_copy(self, local_product_id: int, copy: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE amazon_listings SET copy_json=?, updated_at=? WHERE local_product_id=?",
                (json.dumps(copy, ensure_ascii=False), _now(), local_product_id),
            )
            conn.commit()

    def set_price(self, local_product_id: int, price: float) -> bool:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return False
        if price < 0:
            return False
        self.get_or_create(local_product_id)
        with self._conn() as conn:
            conn.execute(
                "UPDATE amazon_listings SET price=?, updated_at=? WHERE local_product_id=?",
                (price, _now(), local_product_id),
            )
            conn.commit()
        return True

    # ---- 文案 ----
    def regenerate_content(
        self, local_product_id: int,
        *, keyword_tiers: Optional[dict] = None,
    ) -> dict:
        master = self._master(local_product_id)
        if not master:
            return {}
        n = int(master.get("total_stickers") or master.get("default_quantity") or 50) or 50
        copy = copy_generator.generate_copy(
            master, number_of_items=n, keyword_tiers=keyword_tiers,
        )
        self.get_or_create(local_product_id)
        self._save_copy(local_product_id, copy)
        return copy

    def _load_copy(self, local_product_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT copy_json FROM amazon_listings WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
        if not row or not (row["copy_json"] or "").strip() or row["copy_json"] == "{}":
            return {}
        try:
            d = json.loads(row["copy_json"])
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    # ---- 图片(本地图 → COS) ----
    def get_images(self, local_product_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM amazon_images WHERE local_product_id=? ORDER BY sort_order, id",
                (local_product_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upload_images_from_master(self, local_product_id: int) -> dict:
        """把 master 的图片传到 COS,记录到 amazon_images。返回 {ok, uploaded, errors}."""
        master = self._master(local_product_id)
        if not master:
            return {"ok": False, "error": "本地产品不存在"}
        cdn = get_cdn()
        if not cdn.is_configured():
            return {"ok": False, "error": "图床未配置(.env 的 COS_*)"}
        imgs = master.get("images") or []
        # main 先,其余按 sort
        def _role(i):
            r = (i.get("role") or "").lower()
            return "main" if r in ("main", "") else "other"
        imgs_sorted = sorted(imgs, key=lambda i: (0 if _role(i) == "main" else 1,
                                                  int(i.get("sort_order") or 0)))
        uploaded, errors = [], []
        with self._conn() as conn:
            conn.execute("DELETE FROM amazon_images WHERE local_product_id=?", (local_product_id,))
            for idx, im in enumerate(imgs_sorted):
                path = im.get("local_path") or im.get("disk_path") or ""
                if not path or not os.path.isfile(path):
                    errors.append({"path": path, "error": "文件不存在"})
                    continue
                try:
                    url = cdn.upload_file(path)
                except Exception as e:  # noqa: BLE001
                    errors.append({"path": path, "error": str(e)[:200]})
                    continue
                conn.execute(
                    """INSERT INTO amazon_images
                       (local_product_id, role, local_path, cos_url, sort_order, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (local_product_id, "main" if idx == 0 else "other", path, url, idx, _now()),
                )
                uploaded.append(url)
            conn.commit()
        return {"ok": len(uploaded) > 0, "uploaded": uploaded, "errors": errors}

    def image_urls(self, local_product_id: int) -> list[str]:
        return [r["cos_url"] for r in self.get_images(local_product_id) if r.get("cos_url")]

    def _master_reference_image(self, master: dict) -> str:
        """取 master 首图本地路径,作为 AI 出图的风格参考(保持品牌一致)。"""
        for im in master.get("images") or []:
            p = im.get("local_path") or im.get("disk_path") or ""
            if p and os.path.isfile(p):
                return p
        return ""

    def regenerate_images_ai(
        self, local_product_id: int,
        *, keywords: Optional[list] = None, n_secondary: int = 4,
        use_reference: bool = True,
    ) -> dict:
        """用 Gemini 生成主副图 → COS,覆盖写入 amazon_images。

        keywords: 真实搜索高频词,作为副图主题取向。
        返回 image_studio.studio_generate 的结果(含 plan + 每张 record)。
        """
        master = self._master(local_product_id)
        if not master:
            return {"ok": False, "error": "本地产品不存在"}
        out_dir = os.path.join("output", "amazon", str(local_product_id))
        ref = self._master_reference_image(master) if use_reference else ""
        result = image_studio.studio_generate(
            master, output_dir=out_dir, keywords=keywords,
            n_secondary=n_secondary, reference_image=ref or None,
        )
        # 落库:有 local_path 的图按顺序写入(main 在前)。
        rows = [r for r in result.get("images") or [] if r.get("local_path")]
        if rows:
            self.get_or_create(local_product_id)
            now = _now()
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM amazon_images WHERE local_product_id=?", (local_product_id,)
                )
                for idx, r in enumerate(rows):
                    conn.execute(
                        """INSERT INTO amazon_images
                           (local_product_id, role, local_path, cos_url, sort_order, created_at)
                           VALUES (?,?,?,?,?,?)""",
                        (local_product_id,
                         "main" if r.get("role") == "main" or idx == 0 else "other",
                         r.get("local_path") or "", r.get("cos_url") or "", idx, now),
                    )
                conn.commit()
        return result

    def regenerate_images_fixed(self, local_product_id: int) -> dict:
        """固定样式版主副图:main 白底合规 + lifestyle/size_chart/material/full_set。

        与 regenerate_images_ai(GPT 临时规划)不同,提示词固定(image_style.py),
        每个产品风格一致。生成 → COS → 覆盖写入 amazon_images(main 在前)。
        """
        from .image_style import generate_amazon_images
        master = self._master(local_product_id)
        if not master:
            return {"ok": False, "error": "本地产品不存在"}
        out_dir = os.path.join("output", "amazon", str(local_product_id), "fixed")
        ref = self._master_reference_image(master)
        result = generate_amazon_images(
            master, output_dir=out_dir, reference_image=ref or None, upload=True,
        )
        rows = [r for r in result.get("images") or [] if r.get("local_path")]
        if rows:
            self.get_or_create(local_product_id)
            now = _now()
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM amazon_images WHERE local_product_id=?", (local_product_id,)
                )
                for idx, r in enumerate(rows):
                    conn.execute(
                        """INSERT INTO amazon_images
                           (local_product_id, role, local_path, cos_url, sort_order, created_at)
                           VALUES (?,?,?,?,?,?)""",
                        (local_product_id,
                         "main" if r.get("role") == "main" or idx == 0 else "other",
                         r.get("local_path") or "", r.get("cos_url") or "", idx, now),
                    )
                conn.commit()
        return result

    # ---- 预览(详情页数据) ----
    def build_preview(self, local_product_id: int, *, force_regen: bool = False) -> dict:
        master = self._master(local_product_id)
        if not master:
            return {"ok": False, "error": "本地产品不存在", "local_product_id": local_product_id}
        listing = self.get_or_create(local_product_id)
        copy = {} if force_regen else self._load_copy(local_product_id)
        if force_regen or not copy:
            copy = self.regenerate_content(local_product_id) or copy
        images = self.get_images(local_product_id)
        urls = [i["cos_url"] for i in images if i.get("cos_url")]
        price = listing.get("price")
        problems = payload.validate_ready(copy, urls, price)
        return {
            "ok": True,
            "local_product_id": local_product_id,
            "master": master,
            "listing": listing,
            "copy": copy,
            "images": images,
            "image_urls": urls,
            "price": price,
            "seller_sku": listing.get("seller_sku") or master.get("seller_sku") or "",
            "problems": problems,
            "ready": not problems,
        }

    # ---- 列表页 ----
    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT lp.id AS local_product_id, lp.title, lp.seller_sku AS master_sku,
                          lp.default_price,
                          al.status, al.sync_status, al.asin, al.price AS amazon_price,
                          al.copy_json, al.updated_at,
                          (SELECT COUNT(*) FROM amazon_images ai
                            WHERE ai.local_product_id=lp.id) AS image_count
                     FROM local_products lp
                     LEFT JOIN amazon_listings al ON al.local_product_id = lp.id
                    ORDER BY lp.id DESC"""
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["has_copy"] = bool((d.get("copy_json") or "").strip() and d.get("copy_json") != "{}")
            d.pop("copy_json", None)
            out.append(d)
        return out

    # ---- A+ 内容 ----
    def get_aplus(self, local_product_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM amazon_aplus WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
        if not row:
            return {}
        d = dict(row)
        try:
            d["modules"] = (json.loads(d.get("modules_json") or "{}") or {}).get("modules", [])
        except (ValueError, TypeError):
            d["modules"] = []
        return d

    def _save_aplus(self, local_product_id: int, modules: dict) -> None:
        now = _now()
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM amazon_aplus WHERE local_product_id=?", (local_product_id,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE amazon_aplus SET modules_json=?, updated_at=? WHERE local_product_id=?",
                    (json.dumps(modules, ensure_ascii=False), now, local_product_id),
                )
            else:
                conn.execute(
                    """INSERT INTO amazon_aplus (local_product_id, modules_json, created_at, updated_at)
                       VALUES (?,?,?,?)""",
                    (local_product_id, json.dumps(modules, ensure_ascii=False), now, now),
                )
            conn.commit()

    def regenerate_aplus(
        self, local_product_id: int,
        *, keywords: Optional[list] = None,
    ) -> dict:
        master = self._master(local_product_id)
        if not master:
            return {}
        modules = aplus.generate_aplus(master, keywords=keywords)
        self._save_aplus(local_product_id, modules)
        return modules

    def build_aplus_preview(self, local_product_id: int, *, force_regen: bool = False) -> dict:
        master = self._master(local_product_id)
        if not master:
            return {"ok": False, "error": "本地产品不存在", "local_product_id": local_product_id}
        row = self.get_aplus(local_product_id)
        modules = row.get("modules") or []
        if force_regen or not modules:
            modules = (self.regenerate_aplus(local_product_id) or {}).get("modules", [])
        urls = self.image_urls(local_product_id)
        return {
            "ok": True,
            "local_product_id": local_product_id,
            "master": master,
            "modules": modules,
            "image_urls": urls,
            "status": row.get("status") or "draft",
        }

    def record_aplus_submit(
        self, local_product_id: int, *,
        content_ref_key: str = "", status: str = "submitted", last_error: str = "",
    ) -> None:
        """回写 A+ 提交结果(contentReferenceKey / 状态)。行不存在则先建空行。"""
        now = _now()
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM amazon_aplus WHERE local_product_id=?", (local_product_id,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO amazon_aplus (local_product_id, modules_json, created_at, updated_at)
                       VALUES (?, '{}', ?, ?)""",
                    (local_product_id, now, now),
                )
            conn.execute(
                """UPDATE amazon_aplus
                      SET content_ref_key=COALESCE(NULLIF(?, ''), content_ref_key),
                          status=?, last_error=?, updated_at=?
                    WHERE local_product_id=?""",
                (content_ref_key, status, last_error, now, local_product_id),
            )
            conn.commit()

    # ---- 推送结果回写 ----
    def record_push(
        self, local_product_id: int, *,
        status: str, sync_status: str,
        asin: str = "", submission_id: str = "", last_error: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE amazon_listings
                      SET status=?, sync_status=?, asin=COALESCE(NULLIF(?, ''), asin),
                          submission_id=?, last_error=?, updated_at=?
                    WHERE local_product_id=?""",
                (status, sync_status, asin, submission_id, last_error, _now(), local_product_id),
            )
            conn.commit()


_svc: Optional[AmazonListingService] = None


def get_amazon_service() -> AmazonListingService:
    global _svc
    if _svc is None:
        _svc = AmazonListingService()
    return _svc
