#!/usr/bin/env python3
"""复现并诊断 TKShop 主图 image_edit（JieKou gpt-image-2-edit）失败原因。

与 auto_design_images 主图路径一致：合并网格参考图 + MAIN_BUNDLE_PROMPT。

示例::

    # 商品 #23（默认读 draft_23 的 split_stickers_merged_grid.png）
    python scripts/debug_tkshop_main_image_edit.py --product-id 23

    # 只打 1 次请求，快速看错误（不重试）
    python scripts/debug_tkshop_main_image_edit.py --product-id 23 --attempts 1

    # 缩小参考图后再试（验证是否因请求体过大）
    python scripts/debug_tkshop_main_image_edit.py --product-id 23 --shrink-max-side 1024

    # 指定参考图路径
    python scripts/debug_tkshop_main_image_edit.py --ref output/packs/.../split_stickers_merged_grid.png

成功时会把结果写到 output/debug_tkshop_main_edit/。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from src.services.tkshop.prompts import MAIN_BUNDLE_PROMPT  # noqa: E402

DB_PATH = ROOT / "data" / "ops_workbench.db"
OUT_DIR = ROOT / "output" / "debug_tkshop_main_edit"


def _mask_key(key: str) -> str:
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4]} (len={len(key)})"


def _image_endpoints() -> tuple[str, str]:
    api_key = (
        os.getenv("JIEKOU_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("AIHUBMIX_API_KEY", "")
    )
    i2i = os.getenv(
        "JIEKOU_IMAGE_EDIT_URL",
        "https://api.jiekou.ai/v3/gpt-image-2-edit",
    )
    return api_key, i2i


def _resolve_ref_path(product_id: int | None, ref: Path | None) -> Path:
    if ref is not None:
        if not ref.is_file():
            raise FileNotFoundError(ref)
        return ref

    if product_id is None:
        raise ValueError("需要 --product-id 或 --ref")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT p.pack_uid
          FROM tkshop_products pr
          JOIN packs p ON p.id = pr.pack_id
         WHERE pr.id = ?
        """,
        (product_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"tkshop_products 中无 id={product_id}")

    pack_uid = row["pack_uid"]
    candidate = (
        ROOT
        / "output"
        / "packs"
        / pack_uid
        / "products"
        / f"draft_{product_id}"
        / "references"
        / "split_stickers_merged_grid.png"
    )
    if not candidate.is_file():
        raise FileNotFoundError(
            f"未找到合并参考图: {candidate}\n"
            "请先跑 auto_design_images 或传入 --ref"
        )
    return candidate


def _load_ref_bytes(path: Path, shrink_max_side: int | None) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    meta: dict = {
        "path": str(path),
        "file_bytes": len(raw),
    }
    try:
        im = Image.open(path)
        meta["pixels"] = f"{im.width}x{im.height}"
        meta["mode"] = im.mode
    except Exception as e:
        meta["pil_error"] = repr(e)
        return raw, meta

    if shrink_max_side and max(im.size) > shrink_max_side:
        im = im.convert("RGB")
        im.thumbnail((shrink_max_side, shrink_max_side), Image.Resampling.LANCZOS)
        from io import BytesIO

        buf = BytesIO()
        im.save(buf, format="PNG", optimize=True)
        out = buf.getvalue()
        meta["shrunk_to"] = f"{im.width}x{im.height}"
        meta["shrunk_bytes"] = len(out)
        return out, meta

    return raw, meta


def _last_ai_log(product_id: int) -> dict | None:
    if not DB_PATH.is_file():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT id, status, error, latency_ms, created_at, prompt_summary
          FROM ai_call_logs
         WHERE related_id = ?
           AND task = 'tkshop_image_design:edit'
           AND prompt_summary LIKE 'Studio e-commerce main%'
         ORDER BY id DESC
         LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _build_body(
    ref_bytes: bytes,
    *,
    size: str,
    quality: str,
) -> dict:
    b64 = base64.b64encode(ref_bytes).decode("ascii")
    return {
        "image": f"data:image/png;base64,{b64}",
        "prompt": MAIN_BUNDLE_PROMPT,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": os.getenv("V2_IMAGE_OUTPUT_FORMAT", "png"),
    }


def _probe_once(
    *,
    url: str,
    api_key: str,
    body: dict,
    timeout: float,
    attempt: int,
    max_attempts: int,
) -> dict:
    """单次 POST，返回结构化诊断结果（不抛异常）。"""
    t0 = time.perf_counter()
    result: dict = {
        "attempt": f"{attempt}/{max_attempts}",
        "ok": False,
        "elapsed_s": None,
        "exception_type": None,
        "exception": None,
        "http_status": None,
        "response_headers": None,
        "response_body_preview": None,
        "response_json_keys": None,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(http2=False) as client:
            resp = client.post(url, headers=headers, json=body, timeout=timeout)
        result["elapsed_s"] = round(time.perf_counter() - t0, 2)
        result["http_status"] = resp.status_code
        result["response_headers"] = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() in (
                "content-type",
                "content-length",
                "server",
                "date",
                "x-request-id",
                "request-id",
            )
        }
        text = resp.text
        result["response_body_preview"] = text[:2000]
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                data = resp.json()
                result["response_json_keys"] = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            except Exception:
                pass
        if resp.is_success:
            result["ok"] = True
        else:
            result["exception_type"] = "HTTPStatusError"
            result["exception"] = f"HTTP {resp.status_code}"
    except httpx.HTTPStatusError as e:
        result["elapsed_s"] = round(time.perf_counter() - t0, 2)
        result["exception_type"] = type(e).__name__
        result["exception"] = str(e)
        if e.response is not None:
            result["http_status"] = e.response.status_code
            result["response_body_preview"] = e.response.text[:2000]
    except Exception as e:
        result["elapsed_s"] = round(time.perf_counter() - t0, 2)
        result["exception_type"] = type(e).__name__
        result["exception"] = str(e)
        result["traceback"] = traceback.format_exc()
    return result


def _decode_and_save(resp_json: dict, out_path: Path) -> bool:
    """尽量按 router._decode_image_response 逻辑取图。"""
    import requests as req

    urls: list[str] = []
    if isinstance(resp_json.get("images"), list):
        urls = [u for u in resp_json["images"] if isinstance(u, str)]
    for item in resp_json.get("data") or []:
        if isinstance(item, dict):
            if item.get("url"):
                urls.append(item["url"])
            elif item.get("b64_json"):
                out_path.write_bytes(base64.b64decode(item["b64_json"]))
                return True
    for url in urls:
        r = req.get(url, timeout=180)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--product-id", type=int, default=23, help="tkshop_products.id（默认 23）")
    parser.add_argument("--ref", type=Path, default=None, help="参考图路径（覆盖 product 默认路径）")
    parser.add_argument("--attempts", type=int, default=3, help="重试次数（默认 3，与线上一致）")
    parser.add_argument("--timeout", type=float, default=300, help="单次请求超时秒数")
    parser.add_argument("--size", default=None, help="覆盖 TKSHOP_MAIN_IMAGE_SIZE / 默认 1024x1024")
    parser.add_argument("--quality", default=None, help="覆盖 TKSHOP_MAIN_IMAGE_QUALITY / 默认 high")
    parser.add_argument(
        "--shrink-max-side",
        type=int,
        default=0,
        help="将参考图最长边缩到此值再请求（0=不缩）",
    )
    parser.add_argument(
        "--via-router",
        action="store_true",
        help="改用 AIRouter.image_edit（与生产代码完全一致）",
    )
    args = parser.parse_args()

    api_key, i2i_url = _image_endpoints()
    size = args.size or os.getenv("TKSHOP_MAIN_IMAGE_SIZE") or "1024x1024"
    quality = args.quality or os.getenv("TKSHOP_MAIN_IMAGE_QUALITY", "high")

    print("=" * 60)
    print("TKShop 主图 image_edit 诊断")
    print("=" * 60)

    ref_path = _resolve_ref_path(args.product_id if args.ref is None else None, args.ref)
    shrink = args.shrink_max_side or None
    ref_bytes, ref_meta = _load_ref_bytes(ref_path, shrink)
    body = _build_body(ref_bytes, size=size, quality=quality)
    json_bytes = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    print("\n[配置]")
    print(f"  JIEKOU_IMAGE_EDIT_URL = {i2i_url}")
    print(f"  API key               = {_mask_key(api_key)}")
    print(f"  size / quality        = {size} / {quality}")
    print(f"  attempts / timeout    = {args.attempts} / {args.timeout}s")
    print(f"  via-router            = {args.via_router}")

    print("\n[参考图]")
    for k, v in ref_meta.items():
        print(f"  {k}: {v}")
    print(f"  request_json_bytes    = {json_bytes:,} (~{json_bytes / 1024 / 1024:.2f} MiB)")

    if args.product_id:
        log = _last_ai_log(args.product_id)
        if log:
            print("\n[数据库最近一条主图 edit 记录]")
            print(f"  ai_call_logs.id      = {log['id']}")
            print(f"  status              = {log['status']}")
            print(f"  latency_ms          = {log['latency_ms']}")
            print(f"  error               = {(log['error'] or '')[:300]}")

    if not api_key:
        print("\n[错误] 未配置 JIEKOU_API_KEY / OPENAI_API_KEY / AIHUBMIX_API_KEY")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"product_{args.product_id or 'custom'}_{int(time.time())}.json"
    report: dict = {
        "ref_meta": ref_meta,
        "config": {
            "url": i2i_url,
            "size": size,
            "quality": quality,
            "request_json_bytes": json_bytes,
        },
        "attempts": [],
    }

    if args.via_router:
        print("\n[模式] AIRouter.image_edit")
        from src.core.exceptions import APIError
        from src.services.ai.router import get_router

        t0 = time.perf_counter()
        try:
            out = get_router().image_edit(
                ref_bytes,
                MAIN_BUNDLE_PROMPT,
                size=size,
                quality=quality,
                task="tkshop_image_design:edit",
                related_table="tkshop_products",
                related_id=args.product_id,
            )
            elapsed = round(time.perf_counter() - t0, 2)
            out_path = OUT_DIR / f"main_ok_{int(time.time())}.png"
            out_path.write_bytes(out)
            print(f"\n[成功] {elapsed}s → {out_path} ({len(out):,} bytes)")
            report["via_router"] = {"ok": True, "elapsed_s": elapsed, "out": str(out_path)}
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n报告已写入: {report_path}")
            return 0
        except APIError as e:
            elapsed = round(time.perf_counter() - t0, 2)
            print(f"\n[失败] {elapsed}s — APIError: {e}")
            report["via_router"] = {"ok": False, "elapsed_s": elapsed, "error": str(e)}
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n报告已写入: {report_path}")
            return 1

    print("\n[模式] 原始 httpx（逐次打印详情）")
    backoff = 2.0
    success = False
    for attempt in range(1, args.attempts + 1):
        print(f"\n--- 尝试 {attempt}/{args.attempts} ---")
        one = _probe_once(
            url=i2i_url,
            api_key=api_key,
            body=body,
            timeout=args.timeout,
            attempt=attempt,
            max_attempts=args.attempts,
        )
        report["attempts"].append(one)
        print(f"  耗时: {one.get('elapsed_s')}s")
        if one.get("http_status") is not None:
            print(f"  HTTP: {one['http_status']}")
        if one.get("response_headers"):
            print(f"  响应头: {json.dumps(one['response_headers'], ensure_ascii=False)}")
        if one.get("exception_type"):
            print(f"  异常: {one['exception_type']}: {one.get('exception')}")
        if one.get("response_body_preview") and not one.get("ok"):
            preview = one["response_body_preview"]
            if preview.strip():
                print(f"  响应体(前 500 字):\n    {preview[:500]}")

        if one.get("ok"):
            success = True
            try:
                data = json.loads(one["response_body_preview"] or "{}")
            except json.JSONDecodeError:
                data = {}
            out_path = OUT_DIR / f"main_ok_{int(time.time())}.png"
            if isinstance(data, dict) and _decode_and_save(data, out_path):
                print(f"\n[成功] 已保存: {out_path}")
            else:
                print("\n[成功] HTTP 200，但未能从 JSON 解析出图片（请查看报告中的 response_body_preview）")
            break

        if attempt < args.attempts:
            wait = backoff * (2 ** (attempt - 1))
            print(f"  → {wait:.1f}s 后重试…")
            time.sleep(wait)

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n完整 JSON 报告: {report_path}")

    if success:
        print("\n结论: 本次请求成功。若线上仍失败，可能是并发/瞬时网络问题。")
        return 0

    last = report["attempts"][-1] if report["attempts"] else {}
    print("\n结论:")
    if last.get("http_status"):
        print(f"  上游返回了 HTTP {last['http_status']}（业务/鉴权/参数错误），见响应体。")
    elif last.get("exception_type") in (
        "RemoteProtocolError",
        "ConnectError",
        "ReadTimeout",
        "ReadError",
        "WriteError",
    ):
        print(
            f"  传输层失败: {last['exception_type']} — 服务端在返回 HTTP 响应前断开连接。\n"
            f"  常见原因: 请求体过大(当前 JSON ~{json_bytes / 1024 / 1024:.1f}MB)、上游超时、\n"
            f"  网关限流、网络不稳定。可试: --shrink-max-side 1024 --attempts 1"
        )
    else:
        print(f"  未分类错误: {last.get('exception_type')} — {last.get('exception')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
