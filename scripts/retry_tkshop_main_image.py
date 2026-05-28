#!/usr/bin/env python3
"""仅重试 TKShop 商品主图（不碰副图）。

与贴纸拆分相同：走 AIRouter.image_edit，但单独跑、并发=1，
默认 quality=medium（与 preview_gen 拆分一致）。

用法::

    python scripts/retry_tkshop_main_image.py --product-id 23
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# 在 import service 之前固定环境，避免与副图并发抢 edit 接口
os.environ.setdefault("TKSHOP_IMAGE_GEN_CONCURRENCY", "1")
os.environ.setdefault("TKSHOP_MAIN_IMAGE_QUALITY", "medium")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", type=int, default=23)
    parser.add_argument(
        "--quality",
        default=os.getenv("TKSHOP_MAIN_IMAGE_QUALITY", "medium"),
        help="主图 quality（默认 medium，与贴纸拆分一致）",
    )
    args = parser.parse_args()

    from src.services.tkshop.service import TKShopService, _open_db

    svc = TKShopService()
    pid = args.product_id

    # 删掉旧的 AI 主图行，避免堆多条 main
    with _open_db(svc.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id FROM tkshop_product_images
             WHERE product_id = ? AND role = 'main' AND source = 'ai'
            """,
            (pid,),
        ).fetchall()
    for r in rows:
        svc.delete_image(r["id"])
        print(f"  已删除旧 AI 主图 image_id={r['id']}")

    # 若仍有非 AI 的 main（如旧 main.png），降为 secondary，避免新图落成 main_2.png
    with _open_db(svc.db_path) as conn:
        others = conn.execute(
            """
            SELECT id FROM tkshop_product_images
             WHERE product_id = ? AND role = 'main'
            """,
            (pid,),
        ).fetchall()
    for r in others:
        svc.update_image(r["id"], role="secondary")
        print(f"  已将 image_id={r['id']} 降为 secondary")

    print(f"\n重试主图 product #{pid} (workers=1, quality={args.quality}) …")
    t0 = time.time()
    out = svc.auto_design_images(
        pid,
        secondary_count=0,
        replace_existing_ai=False,
        main_quality=args.quality,
    )
    elapsed = time.time() - t0
    print(f"\n耗时 {elapsed:.1f}s")
    print(f"  generated: {out.get('generated')}")
    print(f"  failed:    {out.get('failed')}")
    for r in out.get("results") or []:
        print(f"  - {r}")
    if out.get("generated", 0) < 1:
        return 1
    print("\n成功。请刷新商品详情页查看 main.png。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
