"""Etsy 一键同步服务(卡贴包线)。

pack → local_product(master)→ Etsy listing 草稿。编排:
  1. get_or_create_local_product(pack_id) 拿 master
  2. 收集主副图本地路径(master.images 优先, 兜底 pack previews / cover)
  3. 选一个"合规"视频(tk_videos, ffprobe 选 5-15s; ffprobe 不可用则交中间层兜底)
  4. generate_etsy_content(master) 出 Etsy SEO 文案
  5. 价格 = ETSY_FIXED_PRICE 一口价(配置了就用它); 否则 TK 价 × 倍率
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
import threading
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
# 统一定价: 配置后所有 Etsy listing 一口价(显式传 price_multiplier 时仍可覆盖)。
ETSY_FIXED_PRICE = float(os.getenv("ETSY_FIXED_PRICE", "0") or 0)

MAX_IMAGES = 10

# 同一产品的同步互斥(进程内): 并发点两次「上架」会各建一条 listing(实测发生过,
# pack_21 15 秒内建了两条草稿), 用内存锁挡掉后到的那次。
_SYNCING: set[int] = set()
_SYNC_LOCK = threading.Lock()

# Etsy 平台侧 listing 已被删除(如后台手动删)的错误特征 —— 命中则本地映射已失效。
_LISTING_GONE_MARKERS = (
    "must be active or expired but is removed",
    "Could not find a Listing",
)


def _listing_gone(err_text: Any) -> bool:
    t = str(err_text or "")
    return any(m in t for m in _LISTING_GONE_MARKERS)


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

    def _clear_stale_listing(self, local_product_id: int, note: str) -> None:
        """平台侧 listing 已被删除(后台手动删等): 清掉本地映射,
        回到「未上架」状态, 之后「上架」会新建 listing。"""
        with self._conn() as conn:
            conn.execute(
                """UPDATE etsy_products
                      SET etsy_listing_id='', status='removed', sync_status='draft',
                          last_error=?, synced_at=NULL
                    WHERE local_product_id=?""",
                (str(note)[:500], local_product_id),
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
                  dry_run: bool = False, _allow_recreate: bool = True) -> dict[str, Any]:
        mult = price_multiplier or ETSY_PRICE_MULTIPLIER
        fixed = ETSY_FIXED_PRICE if (ETSY_FIXED_PRICE > 0 and not price_multiplier) else None
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
        price = fixed if fixed else round(self._base_price(master) * mult, 2)
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
            "sku": seller_sku or None,     # 与 TK master seller_sku 一致(跨平台对账)

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

        # 同一产品互斥: 并发重复点「上架」会各建一条 listing, 后到的直接拒。
        with _SYNC_LOCK:
            if lp_id in _SYNCING:
                return {"ok": False, "error": "该产品正在同步 Etsy 中, 请稍候(防止重复建 listing)"}
            _SYNCING.add(lp_id)

        self._set_status(lp_id, "syncing")
        recreate = False
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
            # 更新目标 listing 已在平台被删(如 Etsy 后台手动删) → 清映射后重建。
            if (_allow_recreate and existing_listing
                    and _listing_gone(json.dumps(rj, ensure_ascii=False))):
                recreate = True
            else:
                self._set_status(lp_id, "failed", str(err))
                logger.warning("etsy sync-pack failed: pack=%s %s", pack_id, str(err)[:200])
                return {"ok": False, "error": str(err), "detail": data}
        except requests.RequestException as e:
            self._set_status(lp_id, "failed", f"NETWORK: {e}")
            return {"ok": False, "error": f"网络错误(中间层不可达?): {str(e)[:200]}"}
        except Exception as e:  # noqa: BLE001
            self._set_status(lp_id, "failed", f"INTERNAL: {e}")
            return {"ok": False, "error": f"内部错误: {str(e)[:200]}"}
        finally:
            with _SYNC_LOCK:
                _SYNCING.discard(lp_id)

        # 只有 recreate 分支会走到这(锁已释放, 重入安全): 清掉失效映射, 新建一条。
        logger.warning("etsy listing %s 已在平台被删, 清除本地映射并新建 (pack=%s lp=%s)",
                       existing_listing, pack_id, lp_id)
        self._clear_stale_listing(lp_id, f"platform removed listing {existing_listing}")
        return self.sync_pack(pack_id, price_multiplier=price_multiplier, _allow_recreate=False)

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

    def set_state(self, local_product_id: int, state: str) -> dict[str, Any]:
        """改 Etsy listing 平台状态(active/inactive/draft) —— 下架用 'inactive'。
        调中间层 PATCH /etsy/listings/{id}, 成功回写 etsy_products.status。"""
        if state not in ("active", "inactive", "draft"):
            return {"ok": False, "error": f"不支持的状态: {state}"}
        row = self.get_or_create(local_product_id)
        listing_id = (row.get("etsy_listing_id") or "").strip()
        if not listing_id:
            return {"ok": False, "error": "该产品还没上架到 Etsy(无 listing_id)"}
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/etsy/listings/{listing_id}"
        try:
            resp = requests.patch(endpoint, json={"fields": {"state": state}},
                                  timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                rj = resp.json()
            except Exception:
                rj = {"_raw_text": resp.text[:500]}
            data = rj["data"] if isinstance(rj.get("data"), dict) else rj
            if 200 <= resp.status_code < 300:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE etsy_products SET status=?, synced_at=? WHERE local_product_id=?",
                        (state, _now(), local_product_id),
                    )
                    conn.commit()
                logger.info("etsy set_state ok: lp=%s listing=%s state=%s",
                            local_product_id, listing_id, state)
                return {"ok": True, "listing_id": listing_id, "state": state}
            err = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            if _listing_gone(json.dumps(rj, ensure_ascii=False)):
                self._clear_stale_listing(local_product_id, f"platform removed listing {listing_id}")
                return {"ok": False, "listing_gone": True,
                        "error": "该 listing 已在 Etsy 平台被删除(可能在 Etsy 后台手动删的)。"
                                 "本地映射已清除 — 需要时点「上架」会重新建 listing。"}
            logger.warning("etsy set_state failed: lp=%s %s", local_product_id, str(err)[:200])
            return {"ok": False, "error": str(err)}
        except requests.RequestException as e:
            return {"ok": False, "error": f"网络错误(中间层不可达?): {str(e)[:200]}"}

    def delete_listing(self, local_product_id: int) -> dict[str, Any]:
        """删除 Etsy listing —— 只允许草稿/inactive(active 的先下架再删, 守不删正式
        listing 的规则)。成功(或平台侧本就已删)后清掉本地映射, 回到「未传」状态。"""
        row = self.get_or_create(local_product_id)
        listing_id = (row.get("etsy_listing_id") or "").strip()
        if not listing_id:
            return {"ok": False, "error": "该产品没有 Etsy listing"}
        if (row.get("status") or "") == "active":
            return {"ok": False, "error": "listing 是正式上架(active)状态 — 请先「下架」再删除"}
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/etsy/listings/{listing_id}"
        try:
            resp = requests.delete(endpoint, timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                rj = resp.json()
            except Exception:
                rj = {"_raw_text": resp.text[:300]}
            gone_already = _listing_gone(json.dumps(rj, ensure_ascii=False))
            if 200 <= resp.status_code < 300 or gone_already:
                self._clear_stale_listing(local_product_id, f"deleted listing {listing_id} via workbench")
                logger.info("etsy delete ok: lp=%s listing=%s", local_product_id, listing_id)
                return {"ok": True, "deleted": listing_id}
            data = rj["data"] if isinstance(rj.get("data"), dict) else rj
            err = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            logger.warning("etsy delete failed: lp=%s %s", local_product_id, str(err)[:200])
            return {"ok": False, "error": str(err)}
        except requests.RequestException as e:
            return {"ok": False, "error": f"网络错误(中间层不可达?): {str(e)[:200]}"}

    def set_price(self, local_product_id: int, new_price: float) -> dict[str, Any]:
        """直接改 Etsy 平台价(不重建 listing) —— 调中间层快捷改价端点(读-改-写 inventory)。
        成功回写 etsy_products.price。"""
        try:
            new_price = round(float(new_price), 2)
        except (TypeError, ValueError):
            return {"ok": False, "error": "价格无效"}
        if new_price <= 0:
            return {"ok": False, "error": "价格必须大于 0"}
        row = self.get_or_create(local_product_id)
        listing_id = (row.get("etsy_listing_id") or "").strip()
        if not listing_id:
            return {"ok": False, "error": "该产品还没上架到 Etsy(无 listing_id)"}
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/etsy/listings/{listing_id}/inventory/price"
        try:
            resp = requests.post(endpoint, params={"price": new_price}, timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                rj = resp.json()
            except Exception:
                rj = {"_raw_text": resp.text[:500]}
            data = rj["data"] if isinstance(rj.get("data"), dict) else rj
            if 200 <= resp.status_code < 300:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE etsy_products SET price=?, synced_at=? WHERE local_product_id=?",
                        (new_price, _now(), local_product_id),
                    )
                    conn.commit()
                logger.info("etsy set_price ok: lp=%s listing=%s price=%s",
                            local_product_id, listing_id, new_price)
                return {"ok": True, "listing_id": listing_id, "price": new_price}
            err = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            if _listing_gone(json.dumps(rj, ensure_ascii=False)):
                self._clear_stale_listing(local_product_id, f"platform removed listing {listing_id}")
                return {"ok": False, "listing_gone": True,
                        "error": "该 listing 已在 Etsy 平台被删除(可能在 Etsy 后台手动删的)。"
                                 "本地映射已清除 — 需要时点「上架」会重新建 listing。"}
            logger.warning("etsy set_price failed: lp=%s %s", local_product_id, str(err)[:200])
            return {"ok": False, "error": str(err)}
        except requests.RequestException as e:
            return {"ok": False, "error": f"网络错误(中间层不可达?): {str(e)[:200]}"}


_service: Optional[EtsyListingService] = None


def get_etsy_service() -> EtsyListingService:
    global _service
    if _service is None:
        _service = EtsyListingService()
    return _service
