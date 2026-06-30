"""推送 listing 到 Amazon(经中间层 multi-channel-api)。

安全:推送前先 GET 确认 SKU 不存在(不覆盖已有产品);默认 dry_run=校验不写入。
真实创建走 store=AMAZON_STORE(默认 main=inkelligent)。详见记忆 amazon-test-safety。
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

from src.core.logger import get_logger
from . import payload
from .listings import get_amazon_service

logger = get_logger("services.amazon.sync")

TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000").rstrip("/")
AMAZON_STORE = os.getenv("AMAZON_STORE", "main")
TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "90"))


def _endpoint(path: str) -> str:
    return f"{TKSHOP_SERVER_URL}/api/v1/amazon{path}"


def sku_exists(seller_sku: str) -> bool:
    """GET 中间层确认 SKU 是否已存在(防覆盖)。NOT_FOUND → False。"""
    try:
        r = requests.get(
            _endpoint(f"/listings/{seller_sku}"),
            params={"store": AMAZON_STORE}, timeout=30,
        )
    except requests.RequestException:
        # 网络异常时保守返回 True(宁可不推,不冒覆盖风险)
        return True
    if 200 <= r.status_code < 300:
        return True
    try:
        body = r.json()
    except Exception:
        body = {}
    detail = str(body.get("detail") or body.get("message") or r.text)
    return "NOT_FOUND" not in detail and "not found" not in detail.lower()


def push_one(
    local_product_id: int,
    *,
    dry_run: bool = True,
    quantity: int = 0,
) -> dict:
    """推送一条 listing。

    dry_run=True → mode=VALIDATION_PREVIEW(零写入校验)。
    dry_run=False → 真实创建草稿(先 GET 防覆盖);quantity 默认 0=不可售。
    """
    svc = get_amazon_service()
    pv = svc.build_preview(local_product_id)
    if not pv.get("ok"):
        return {"ok": False, "error": pv.get("error", "preview 失败")}

    problems = pv.get("problems") or []
    if problems:
        return {"ok": False, "error": "未就绪", "problems": problems}

    sku = (pv.get("seller_sku") or "").strip()
    if not sku:
        return {"ok": False, "error": "缺 SKU"}
    price = pv.get("price")

    attrs = payload.build_sticker_attributes(
        pv["copy"], pv["image_urls"], seller_sku=sku, price=price,
        quantity=int(quantity),
    )
    body: dict[str, Any] = {
        "sku": sku, "product_type": "STICKER_DECAL",
        "attributes": attrs, "requirements": "LISTING",
    }
    if dry_run:
        body["mode"] = "VALIDATION_PREVIEW"
    elif sku_exists(sku):
        # 真实写入前防覆盖:已存在则拒绝(红线:只增不改)
        return {"ok": False, "error": f"SKU {sku} 已存在,按安全策略拒绝覆盖"}

    try:
        r = requests.post(
            _endpoint("/listings"), params={"store": AMAZON_STORE},
            json=body, timeout=TIMEOUT,
        )
        out = r.json()
    except requests.RequestException as e:
        if not dry_run:
            svc.record_push(local_product_id, status="error", sync_status="failed",
                            last_error=f"NETWORK: {str(e)[:200]}")
        return {"ok": False, "error": f"NETWORK: {str(e)[:200]}"}
    except ValueError:
        return {"ok": False, "error": f"HTTP {r.status_code}: 非 JSON 响应"}

    if not out.get("success"):
        msg = str(out.get("message") or out.get("detail") or "")[:300]
        if not dry_run:
            svc.record_push(local_product_id, status="error", sync_status="failed",
                            last_error=msg)
        return {"ok": False, "error": msg, "raw": out}

    data = out.get("data") or {}
    status = data.get("status", "")
    issues = data.get("issues") or []
    submission_id = data.get("submissionId", "")
    asin = data.get("asin", "")
    errors = [i for i in issues if (i.get("severity") == "ERROR")]

    result = {
        "ok": (status in ("ACCEPTED", "VALID")) and not errors,
        "dry_run": dry_run,
        "status": status,
        "issues": issues,
        "submission_id": submission_id,
    }
    if not dry_run:
        svc.record_push(
            local_product_id,
            status=("accepted" if status == "ACCEPTED" and not errors else "error"),
            sync_status=("pushed" if status == "ACCEPTED" and not errors else "failed"),
            asin=asin, submission_id=submission_id,
            last_error="; ".join(i.get("message", "") for i in errors)[:300],
        )
    return result


_NONE = None


def get_sync():
    return push_one
