"""Etsy 一键同步服务(卡贴包线)。

pack → local_product(master)→ Etsy listing 草稿。编排:
  1. get_or_create_local_product(pack_id) 拿 master
  2. 收集主副图本地路径(master.images 优先, 兜底 pack previews / cover)
  3. 选一个"合规"视频(tk_videos, ffprobe 选 5-15s; ffprobe 不可用则交中间层兜底)
  4. generate_etsy_content(master) 出 Etsy SEO 文案
  5. 价格 = TK 价(local_products.default_price / 模板价 / 6.99)× 倍率
  6. POST 中间层 /api/v1/etsy/sync-pack(建草稿+传图+传视频)
  7. 回填 etsy_products(etsy_listing_id / price / copy_json / sync_status)

raw sqlite, 路由层薄, AI 走 AIRouter, 平台调用走 multi-channel-api 中间层。
ASCII-only 日志(本机控制台 GBK)。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from typing import Any, Optional

import requests

from src.core.logger import get_logger
from src.services.etsy.copy_generator import generate_etsy_content

logger = get_logger("services.etsy")

DB_PATH = os.getenv("OPS_DB_PATH", "data/ops_workbench.db")
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
TKSHOP_SERVER_TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "300"))
ETSY_PRICE_MULTIPLIER = float(os.getenv("ETSY_PRICE_MULTIPLIER", "1.3"))
ETSY_PRICE_FALLBACK = float(os.getenv("ETSY_PRICE_FALLBACK", "6.99"))

MAX_IMAGES = 10


def _now() -> int:
    return int(time.time())


def _video_duration(path: str) -> Optional[float]:
    """ffprobe 读时长(秒); ffprobe 不可用/失败返回 None。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=20,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


class EtsyListingService:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- master(复用 tkshop) ----
    @staticmethod
    def _tk():
        from src.services.tkshop.service import get_tkshop_service
        return get_tkshop_service()

    # ---- etsy_products 行 ----
    def get_or_create(self, local_product_id: int, seller_sku: str = "") -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM etsy_products WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
            if row:
                return dict(row)
            conn.execute(
                """INSERT INTO etsy_products (local_product_id, seller_sku, created_at)
                   VALUES (?,?,?)""",
                (local_product_id, seller_sku, _now()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM etsy_products WHERE local_product_id=?",
                (local_product_id,),
            ).fetchone()
            return dict(row)

    def _set_status(self, local_product_id: int, sync_status: str, err: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE etsy_products SET sync_status=?, last_error=? WHERE local_product_id=?",
                (sync_status, err[:500], local_product_id),
            )
            conn.commit()

    def _save_success(self, local_product_id: int, listing_id: str, price: float,
                      copy: dict, status: str = "draft") -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE etsy_products
                      SET etsy_listing_id=?, price=?, copy_json=?, status=?,
                          sync_status='synced', last_error='', synced_at=?
                    WHERE local_product_id=?""",
                (listing_id, price, json.dumps(copy, ensure_ascii=False), status,
                 _now(), local_product_id),
            )
            conn.commit()

    # ---- 图片 / 视频收集 ----
    def _collect_image_paths(self, conn: sqlite3.Connection, master: dict) -> list[str]:
        # 1) master 的有序主副图(main 优先, list_local_product_images 已排好)
        imgs = [
            i["local_path"] for i in (master.get("images") or [])
            if i.get("local_path") and os.path.exists(i["local_path"])
        ]
        if imgs:
            return [os.path.abspath(p) for p in imgs[:MAX_IMAGES]]
        # 2) 兜底: pack 的 ok previews
        pack_id = master.get("pack_id")
        prow = conn.execute(
            "SELECT series_id, cover_image_path FROM packs WHERE id=?", (pack_id,),
        ).fetchone()
        if prow and prow["series_id"]:
            prevs = conn.execute(
                """SELECT image_path FROM pack_previews
                    WHERE series_id=? AND generation_status='ok' AND image_path!=''
                    ORDER BY preview_idx""",
                (prow["series_id"],),
            ).fetchall()
            imgs = [p["image_path"] for p in prevs if os.path.exists(p["image_path"])]
            if imgs:
                return [os.path.abspath(p) for p in imgs[:MAX_IMAGES]]
        # 3) 再兜底: 封面一张
        if prow and prow["cover_image_path"] and os.path.exists(prow["cover_image_path"]):
            return [os.path.abspath(prow["cover_image_path"])]
        return []

    def _pick_video(self, conn: sqlite3.Connection, pack_id: int) -> Optional[str]:
        rows = conn.execute(
            "SELECT local_video_path FROM tk_videos WHERE pack_id=? AND local_video_path!='' ORDER BY id DESC",
            (pack_id,),
        ).fetchall()
        existing = [
            r["local_video_path"] for r in rows
            if r["local_video_path"] and os.path.exists(r["local_video_path"])
        ]
        if not existing:
            return None
        ffprobe_worked = False
        for p in existing:
            d = _video_duration(p)
            if d is not None:
                ffprobe_worked = True
                if 5 <= d <= 15:
                    return os.path.abspath(p)
        # ffprobe 可用但无合规视频 → 不传(避免必被 Etsy 拒时长)
        # ffprobe 不可用 → 传最新的, 让中间层规格兜底
        return None if ffprobe_worked else os.path.abspath(existing[0])

    def _base_price(self, master: dict) -> float:
        base = master.get("default_price")
        if base:
            try:
                return float(base)
            except (TypeError, ValueError):
                pass
        try:
            from src.services.tkshop.service import _price_from_default_template
            sale, _disc = _price_from_default_template(master.get("default_template_json") or "{}")
            if sale:
                return float(sale)
        except Exception:
            pass
        return ETSY_PRICE_FALLBACK

    # ---- 主流程 ----
    def sync_pack(self, pack_id: int, *, price_multiplier: Optional[float] = None,
                  dry_run: bool = False) -> dict[str, Any]:
        mult = price_multiplier or ETSY_PRICE_MULTIPLIER
        tk = self._tk()
        lp_id = tk.get_or_create_local_product(pack_id)
        master = tk.get_local_product(lp_id) or {}

        with self._conn() as conn:
            image_paths = self._collect_image_paths(conn, master)
            video_path = self._pick_video(conn, pack_id)
        if not image_paths:
            return {"ok": False, "error": "no_images",
                    "message": "该卡包没有可用的主副图, 请先生成/上传图片再同步 Etsy"}

        copy = generate_etsy_content(master)
        price = round(self._base_price(master) * mult, 2)
        seller_sku = (master.get("seller_sku") or "").strip()
        row = self.get_or_create(lp_id, seller_sku)
        existing_listing = (row.get("etsy_listing_id") or "").strip()

        payload = {
            "title": copy["title"],
            "description": copy["description"],
            "tags": copy["tags"],
            "materials": copy["materials"],
            "price": price,
            "quantity": 100,
            "image_paths": image_paths,
            "video_path": video_path,
            "external_ref": f"pack_{pack_id}",
            # 已同步过则走更新(刷新文案), 避免累积新草稿
            "listing_id": int(existing_listing) if existing_listing.isdigit() else None,
        }

        if dry_run:
            return {"ok": True, "dry_run": True, "local_product_id": lp_id,
                    "price": price, "image_count": len(image_paths),
                    "has_video": bool(video_path),
                    "mode": "update" if existing_listing else "create",
                    "existing_listing_id": existing_listing or None,
                    "copy": {"title": copy["title"], "tags": copy["tags"],
                             "description_preview": copy["description"][:200]}}

        self._set_status(lp_id, "syncing")
        try:
            endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/etsy/sync-pack"
            resp = requests.post(endpoint, json=payload, timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                rj = resp.json()
            except Exception:
                rj = {"_raw_text": resp.text[:500]}
            # 解信封: mca 包成 {success, message, data:{ok,...}}
            data = rj["data"] if isinstance(rj.get("data"), dict) else rj
            listing_id = str(data.get("listing_id") or "")
            ok = 200 <= resp.status_code < 300 and bool(data.get("ok")) and bool(listing_id)
            if ok:
                self._save_success(lp_id, listing_id, price, copy, data.get("state") or "draft")
                logger.info("etsy sync-pack ok: pack=%s listing=%s", pack_id, listing_id)
                return {"ok": True, "pack_id": pack_id, "local_product_id": lp_id,
                        "listing_id": listing_id, "url": data.get("url"),
                        "images_ok": data.get("images_ok"), "video": data.get("video"),
                        "price": price}
            err = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            self._set_status(lp_id, "failed", str(err))
            logger.warning("etsy sync-pack failed: pack=%s %s", pack_id, str(err)[:200])
            return {"ok": False, "error": str(err), "detail": data}
        except requests.RequestException as e:
            self._set_status(lp_id, "failed", f"NETWORK: {e}")
            return {"ok": False, "error": f"网络错误(中间层不可达?): {str(e)[:200]}"}
        except Exception as e:  # noqa: BLE001
            self._set_status(lp_id, "failed", f"INTERNAL: {e}")
            return {"ok": False, "error": f"内部错误: {str(e)[:200]}"}

    def get_for_pack(self, pack_id: int) -> Optional[dict]:
        """pack 详情页展示用: 返回该 pack 的 Etsy 同步行(若有)。"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT ep.* FROM etsy_products ep
                     JOIN local_products lp ON lp.id = ep.local_product_id
                    WHERE lp.pack_id = ?""",
                (pack_id,),
            ).fetchone()
            return dict(row) if row else None


_service: Optional[EtsyListingService] = None


def get_etsy_service() -> EtsyListingService:
    global _service
    if _service is None:
        _service = EtsyListingService()
    return _service
