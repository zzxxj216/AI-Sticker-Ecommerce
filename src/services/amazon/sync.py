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


# ======================================================================
# 一键:贴纸包 → Amazon listing + A+(固定横幅样式)生成 + 绑定 + 提审
# ======================================================================

APLUS_ASIN_POLL_TRIES = int(os.getenv("AMAZON_ASIN_POLL_TRIES", "10"))
APLUS_ASIN_POLL_INTERVAL = int(os.getenv("AMAZON_ASIN_POLL_INTERVAL", "20"))


def get_asin(seller_sku: str) -> str:
    """GET 中间层 listing 详情,取 summaries[0].asin;拿不到返回 ''。"""
    try:
        r = requests.get(
            _endpoint(f"/listings/{seller_sku}"),
            params={"store": AMAZON_STORE}, timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return ""
        data = (r.json() or {}).get("data") or {}
        summaries = data.get("summaries") or [{}]
        return str(summaries[0].get("asin") or "")
    except (requests.RequestException, ValueError):
        return ""


def _poll_asin(seller_sku: str, *, tries: int, interval: int) -> str:
    """新建 listing 后 ASIN 生成需要时间 — 轮询直到拿到或超时。"""
    import time as _time
    for i in range(max(1, tries)):
        asin = get_asin(seller_sku)
        if asin:
            return asin
        logger.info("one_click: ASIN not ready for %s (try %d/%d), wait %ds",
                    seller_sku, i + 1, tries, interval)
        _time.sleep(interval)
    return ""


def one_click_amazon(
    local_product_id: int,
    *,
    publish: bool = False,
    submit_aplus: bool = True,
) -> dict:
    """一键:文案/图就绪 → 建 listing(防覆盖)→ 轮询 ASIN → 固定样式 A+ 横幅
    (AI 生成 + COS)→ 建 A+ 文档 + 绑 ASIN +(可选)提审。

    幂等:listing 已 pushed 则跳过创建直奔 A+;A+ 已有 content_ref_key 则整体跳过。
    返回 {ok, steps:{...}, error} — 每步结果都记录,失败在哪一步一目了然。
    """
    svc = get_amazon_service()
    steps: dict[str, Any] = {}

    def _fail(step: str, err: str) -> dict:
        steps[step] = {"ok": False, "error": err}
        logger.warning("one_click #%s failed at %s: %s", local_product_id, step, err[:200])
        return {"ok": False, "steps": steps, "error": f"{step}: {err}"}

    # 0) 就绪检查(文案缺失会自动生成;图未传 COS 则自动从 master 传)。
    pv = svc.build_preview(local_product_id)
    if not pv.get("ok"):
        return _fail("preview", pv.get("error") or "preview 失败")
    if pv.get("problems"):
        # 常见问题=图片未传 COS,尝试自动补一次。
        up = svc.upload_images_from_master(local_product_id)
        steps["auto_upload_images"] = up
        pv = svc.build_preview(local_product_id)
        if pv.get("problems"):
            return _fail("ready", "未就绪:" + "、".join(pv["problems"]))
    steps["preview"] = {"ok": True}
    master = pv.get("master") or {}
    sku = (pv.get("seller_sku") or "").strip()

    # 1) listing:已推过则跳过(红线只增不改);否则真实创建草稿。
    listing = svc.get_or_create(local_product_id)
    if (listing.get("sync_status") == "pushed") or (listing.get("asin") or "").strip():
        steps["listing"] = {"ok": True, "skipped": "已推送过"}
    else:
        res = push_one(local_product_id, dry_run=False,
                       quantity=10 if publish else 0)
        steps["listing"] = res
        if not res.get("ok"):
            return {"ok": False, "steps": steps,
                    "error": "listing: " + str(res.get("error") or res.get("status") or "")[:200]}

    # 2) ASIN(创建后可能要等平台处理)。
    asin = (svc.get_or_create(local_product_id).get("asin") or "").strip()
    if not asin:
        asin = _poll_asin(sku, tries=APLUS_ASIN_POLL_TRIES,
                          interval=APLUS_ASIN_POLL_INTERVAL)
        if asin:
            svc.record_push(local_product_id,
                            status="accepted", sync_status="pushed", asin=asin)
    if not asin:
        return _fail("asin", f"轮询超时未拿到 ASIN(SKU={sku});稍后重跑一键即可续传 A+")
    steps["asin"] = {"ok": True, "asin": asin}

    # 3) A+:已有 content_ref_key 则不重建文档;若还没提审成功(新 ASIN 进目录
    #    要 15-60 分钟,首次提审常报 "ASIN does not exist in the catalog"),
    #    重跑一键时自动补一次提审。
    aplus_row = svc.get_aplus(local_product_id)
    existing_key = (aplus_row.get("content_ref_key") or "").strip()
    if existing_key:
        if (aplus_row.get("status") or "") == "submitted" or not submit_aplus:
            steps["aplus"] = {"ok": True, "skipped": "已有 A+ 文档",
                              "content_ref_key": existing_key}
            return {"ok": True, "steps": steps, "asin": asin,
                    "content_ref_key": existing_key}
        # 补提审(绑定端点带 submit=true;绑定幂等)。
        try:
            r = requests.post(
                _endpoint(f"/aplus/documents/{existing_key}/asins"),
                params={"store": AMAZON_STORE},
                json={"asins": [asin], "submit": True}, timeout=120,
            )
            data = (r.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError) as e:
            data = {"submit_error": str(e)[:200]}
        submitted = bool(data.get("submitted"))
        svc.record_aplus_submit(
            local_product_id, content_ref_key=existing_key,
            status=("submitted" if submitted else "draft"),
            last_error=("" if submitted else str(data.get("submit_error") or "")[:300]),
        )
        steps["aplus"] = {"ok": True, "content_ref_key": existing_key,
                          "submitted": submitted,
                          "resubmit_error": data.get("submit_error") or ""}
        return {"ok": True, "steps": steps, "asin": asin,
                "content_ref_key": existing_key}

    # 3a) 固定样式横幅生成 + COS。参考图=已传 COS 的主图对应本地图,退主版本首图。
    from .aplus_style import generate_aplus_banners
    imgs = svc.get_images(local_product_id)
    ref = ""
    for i in imgs:
        if i.get("local_path"):
            ref = i["local_path"]
            break
    pack_uid = (master.get("pack_uid") or "").strip()
    out_dir = (f"output/packs/{pack_uid}/products/amazon/aplus"
               if pack_uid else f"output/amazon_aplus/{local_product_id}")
    gen = generate_aplus_banners(master, output_dir=out_dir,
                                 reference_image=ref or None, upload=True)
    steps["aplus_banners"] = {
        "ok": gen.get("ok"),
        "banners": [{k: b.get(k) for k in ("key", "cos_url", "error")}
                    for b in gen.get("banners", [])],
    }
    urls = [b["cos_url"] for b in gen.get("banners", []) if b.get("cos_url")]
    if not urls:
        svc.record_aplus_submit(local_product_id, status="error",
                                last_error="横幅生成/上传全部失败")
        return _fail("aplus_banners", gen.get("error") or "横幅生成/上传全部失败")

    # 3b) 建 A+ 文档 + 绑 ASIN + 提审(中间层一步式)。
    doc_name = f"INK {sku} A+"[:100]
    try:
        r = requests.post(
            _endpoint("/aplus/documents"), params={"store": AMAZON_STORE},
            json={"name": doc_name, "asin": asin,
                  "image_urls": urls, "submit": bool(submit_aplus)},
            timeout=300,
        )
        out = r.json()
    except requests.RequestException as e:
        svc.record_aplus_submit(local_product_id, status="error",
                                last_error=f"NETWORK: {str(e)[:200]}")
        return _fail("aplus_create", f"NETWORK: {str(e)[:200]}")
    except ValueError:
        svc.record_aplus_submit(local_product_id, status="error",
                                last_error=f"HTTP {r.status_code}: 非 JSON")
        return _fail("aplus_create", f"HTTP {r.status_code}: 非 JSON 响应")

    if not out.get("success"):
        msg = str(out.get("message") or out.get("detail") or "")[:300]
        svc.record_aplus_submit(local_product_id, status="error", last_error=msg)
        return _fail("aplus_create", msg or "middle layer success=false")

    data = out.get("data") or {}
    key = str(data.get("contentReferenceKey") or "")
    submitted = bool(data.get("submitted"))
    svc.record_aplus_submit(
        local_product_id, content_ref_key=key,
        status=("submitted" if submitted else "draft"),
    )
    steps["aplus"] = {"ok": True, "content_ref_key": key, "submitted": submitted,
                      "modules": data.get("modules")}
    logger.info("one_click #%s done: asin=%s aplus=%s submitted=%s",
                local_product_id, asin, key or "?", submitted)
    return {"ok": True, "steps": steps, "asin": asin, "content_ref_key": key}


def resubmit_pending_aplus() -> dict:
    """扫描所有「已建档未提审」的 A+,自动补提审(定时任务用)。

    新 ASIN 进目录要 15-60 分钟,首次提审常报 "ASIN does not exist in the
    catalog" — 本函数由 web 应用的 APScheduler 周期执行,目录就绪后自动
    完成提审,无需人工。每行每轮最多试一次;成功即标 submitted。
    返回 {checked, submitted, still_pending, errors}。
    """
    svc = get_amazon_service()
    import sqlite3
    conn = sqlite3.connect("data/ops_workbench.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ap.local_product_id, ap.content_ref_key, al.asin
                 FROM amazon_aplus ap
                 JOIN amazon_listings al ON al.local_product_id = ap.local_product_id
                WHERE ap.content_ref_key != '' AND ap.status != 'submitted'
                  AND al.asin != ''""",
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"checked": 0, "submitted": 0, "still_pending": 0, "errors": []}

    submitted, pending, errors = 0, 0, []
    for r in rows:
        lp_id, key, asin = r["local_product_id"], r["content_ref_key"], r["asin"]
        try:
            resp = requests.post(
                _endpoint(f"/aplus/documents/{key}/asins"),
                params={"store": AMAZON_STORE},
                json={"asins": [asin], "submit": True}, timeout=120,
            )
            data = (resp.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError) as e:
            errors.append({"local_product_id": lp_id, "error": str(e)[:150]})
            continue
        if data.get("submitted"):
            svc.record_aplus_submit(lp_id, content_ref_key=key, status="submitted")
            submitted += 1
            logger.info("aplus auto-resubmit ok: lp #%s asin=%s", lp_id, asin)
        else:
            pending += 1
            err = str(data.get("submit_error") or "")[:300]
            svc.record_aplus_submit(lp_id, content_ref_key=key,
                                    status="draft", last_error=err)
    result = {"checked": len(rows), "submitted": submitted,
              "still_pending": pending, "errors": errors}
    logger.info("aplus auto-resubmit: %s", result)
    return result


def get_sync():
    return push_one
