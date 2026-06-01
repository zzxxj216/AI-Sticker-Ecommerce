#!/usr/bin/env python3
"""Generate one preview secondary: hand holding one sticker, pile background."""

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

os.environ.setdefault("TKSHOP_IMAGE_GEN_CONCURRENCY", "1")
os.environ.setdefault("TKSHOP_SECONDARY_IMAGE_QUALITY", "medium")

from src.services.tkshop.prompts import SECONDARY_HAND_HOLD_BUNDLE_BELOW_PROMPT
from src.services.tkshop.service import TKShopService, _open_db, _resolve_disk_path
from src.utils.image_utils import compose_reference_grid, compress_image_bytes_for_api


def _merged_ref_bytes(ctx: dict, asset_root: Path) -> tuple[bytes, str]:
    sheet_paths = [
        _resolve_disk_path(p, root=asset_root)
        for p in ctx.get("preview_image_paths", [])
    ]
    sticker_paths = [
        _resolve_disk_path(p, root=asset_root)
        for p in ctx.get("sticker_image_paths", [])
    ]
    sheet_paths = [p for p in sheet_paths if p.is_file()]
    sticker_paths = [p for p in sticker_paths if p.is_file()]
    preview_threshold = max(1, int(os.getenv("TKSHOP_MAIN_PREVIEW_MIN_COUNT", "3")))
    if len(sheet_paths) >= preview_threshold:
        bundle_paths, label = sheet_paths, "preview_sheets_merged_grid"
    elif sticker_paths:
        bundle_paths, label = sticker_paths, "split_stickers_merged_grid"
    else:
        bundle_paths, label = sheet_paths, "preview_sheets_merged_grid"
    if not bundle_paths:
        raise SystemExit("no preview or sticker assets for merged reference")
    raw = compose_reference_grid(
        [p.as_posix() for p in bundle_paths],
        cell=384 if len(bundle_paths) >= 12 else 512,
        max_side=int(os.getenv("TKSHOP_MAIN_MERGE_MAX_SIDE", "1536")),
    )
    compressed = compress_image_bytes_for_api(
        raw,
        max_side=int(os.getenv("TKSHOP_MAIN_REF_UPLOAD_MAX_SIDE", "1536")),
        max_bytes=int(os.getenv("TKSHOP_MAIN_REF_MAX_BYTES", "1800000")),
    )
    return compressed, label


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-product-id", type=int, default=1,
        help="Master local product id (default: 1)",
    )
    parser.add_argument(
        "--product-id", type=int, default=None,
        help="Optional tkshop_products listing id (overrides local lookup)",
    )
    parser.add_argument(
        "--replace-last-secondary", action="store_true",
        help="Replace the newest secondary in master + listing gallery",
    )
    args = parser.parse_args()

    svc = TKShopService()
    asset_root = svc.db_path.resolve().parent.parent

    with _open_db(svc.db_path) as conn:
        if args.product_id:
            product_id = int(args.product_id)
            lp_id = conn.execute(
                "SELECT local_product_id FROM tkshop_products WHERE id = ?",
                (product_id,),
            ).fetchone()
            local_product_id = int(lp_id["local_product_id"]) if lp_id else args.local_product_id
        else:
            local_product_id = args.local_product_id
            row = conn.execute(
                """
                SELECT id FROM tkshop_products
                 WHERE local_product_id = ?
                 ORDER BY id LIMIT 1
                """,
                (local_product_id,),
            ).fetchone()
            if not row:
                raise SystemExit(f"no listing for local_product #{local_product_id}")
            product_id = int(row["id"])

    ctx = svc.collect_pack_design_context(product_id)
    ref_bytes, ref_label = _merged_ref_bytes(ctx, asset_root)
    out_dir = ROOT / "output" / "preview_hand_hold"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"lp{local_product_id}_hand_hold_pile_{stamp}.png"

    print(f"product_id={product_id} local_product_id={local_product_id}")
    print(f"reference={ref_label} ({len(ref_bytes)} bytes)")
    print(f"prompt={SECONDARY_HAND_HOLD_BUNDLE_BELOW_PROMPT[:120]}...")
    print("generating...")

    out_bytes = svc.router.image_edit(
        ref_bytes,
        SECONDARY_HAND_HOLD_BUNDLE_BELOW_PROMPT,
        size="1024x1024",
        quality=os.getenv("TKSHOP_SECONDARY_IMAGE_QUALITY", "medium"),
        task="tkshop_preview:hand_hold_below",
        related_table="tkshop_products",
        related_id=product_id,
    )
    out_path.write_bytes(out_bytes)
    print(f"saved: {out_path}")

    if args.replace_last_secondary:
        with _open_db(svc.db_path) as conn:
            row = conn.execute(
                """
                SELECT id FROM local_product_images
                 WHERE local_product_id = ? AND role = 'secondary'
                 ORDER BY sort_order DESC, id DESC LIMIT 1
                """,
                (local_product_id,),
            ).fetchone()
        if row:
            svc.delete_local_product_image(int(row["id"]))
        img_id = svc.add_local_product_image(
            local_product_id,
            src_path=out_path,
            role="secondary",
            source="ai",
            ai_prompt=SECONDARY_HAND_HOLD_BUNDLE_BELOW_PROMPT,
        )
        svc.sync_master_images_to_listing(product_id, replace=True)
        print(f"replaced last secondary -> local image #{img_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
