# -*- coding: utf-8 -*-
"""拉取 TK 产品详情 → 规范化成"同步草稿" draft JSON(TK 侧只读)。

用法(在你的**工作目录**跑,tk_drafts/ 会建在当前目录;全流程都在这个目录):
  python tk_pull.py <product_id> --download          # 推荐:一开始就带 --download
  python tk_pull.py <product_id> --shop main

安全设计:
- draft.json 已存在时,**保留旧的 amazon 块和已下载图片的 local**(只刷新 TK 事实)——
  重跑不会丢人工答案;想彻底重来加 --force。
- 图片 local 记**绝对路径**,后续 to_payload/create 从任何目录跑都不断。
- 单张图下载失败只警告跳过,draft.json 照常落盘(先落盘再下载)。
"""
import json
import os
import sys
import time

import _tk


def normalize(d: dict) -> dict:
    imgs = []
    for im in (d.get("main_images") or d.get("images") or []):     # 新旧字段 fallback
        urls = im.get("urls") or im.get("url_list") or []
        if urls:
            imgs.append({"url": urls[0], "w": im.get("width"), "h": im.get("height")})
    skus = []
    for s in d.get("skus") or []:
        price = s.get("price") or {}
        raw = (price.get("sale_price") or price.get("tax_exclusive_price")
               or price.get("original_price") or price.get("unit_price"))
        inv_list = s.get("inventory") or s.get("stock_infos") or []
        inv = sum(int(x.get("quantity", x.get("available_stock", 0)) or 0) for x in inv_list)
        attrs = []
        for a in (s.get("sales_attributes") or []):
            item = {"name": a.get("name"), "value": (a.get("value_name") or a.get("value"))}
            sk_img = a.get("sku_img") or {}
            v_urls = sk_img.get("urls") or sk_img.get("url_list") or []
            if v_urls:
                item["image_url"] = v_urls[0]        # 变体图(--download 会一并下载)
            attrs.append(item)
        skus.append({
            "tk_sku_id": s.get("id") or s.get("sku_id"),
            "seller_sku": s.get("seller_sku"),
            "price": _tk.amount(raw),
            "price_raw": None if raw is None else str(raw),   # 保留原始值,防"整数分"百倍歧义
            "currency": price.get("currency"),
            "qty_tk": inv,
            "sales_attributes": attrs,
        })
    desc_html = d.get("description") or ""
    return {
        "source": {"platform": "tiktok", "shop": _tk.SHOP or "(默认)", "product_id": d.get("id"),
                   "status": d.get("product_status") or d.get("status"),
                   "pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")},
        "title_tk": d.get("title") or "",
        "description_html": desc_html,
        "description_text": _tk.strip_html(desc_html),
        "images": imgs,
        "skus": skus,
        "package": {"dimensions": d.get("package_dimensions") or {},
                    "weight": d.get("package_weight") or {}},
        "category_chain": [c.get("local_name") for c in (d.get("category_chains") or [])
                           if c.get("local_name")],
        "listing_quality_tier_tk": d.get("listing_quality_tier"),
        # ↓↓ Amazon 侧待补(人机协作填;gap_check.py 逐项检查;事实来源写进 notes)↓↓
        "amazon": {
            "store": None,              # 目标店铺[@站点],如 "main" / "byane@UK"(字符串)
            "product_type": None,       # "STICKER_DECAL" / "LABEL"(探针确认)
            "keyword_csv": None,        # 关键词表**绝对路径**(老板规矩:创建前必须有)
            "sku": None,                # Amazon 新 SKU(全新唯一)
            "title": None,              # ≤75 字符英文
            "title_differentiation": None,   # Item Highlight ≤125
            "bullets": [],              # 恰好 5 条英文字符串
            "description": None,        # 英文纯文本
            "backend_keywords": None,   # 字符串,≤249 字节
            "number_of_items": None, "unit_count": None,   # 整数
            "color": None, "theme": None, "subject_character": None,
            "price_usd": None,          # 数字;沿用 TK 价还是重定,人来定
            "variants": None,           # 变体族用:[{"sku","color","main_image":"file:绝对路径"}]
            "notes": [],                # 事实留痕:如 "number_of_items=50 ← 用户确认 2026-07-10"
        },
    }


def merge_keep_human_work(new: dict, old: dict) -> dict:
    """重跑保护:老 draft 的 amazon 块整体保留;老 images[].local 按 url 匹配回填。"""
    if old.get("amazon"):
        new["amazon"] = old["amazon"]
    old_local = {im.get("url"): im.get("local") for im in old.get("images") or [] if im.get("local")}
    for im in new["images"]:
        if im["url"] in old_local:
            im["local"] = old_local[im["url"]]
    return new


def main(pid: str, do_download: bool, force: bool):
    if not str(pid).isdigit():
        raise SystemExit(f"[拒绝] product_id 应为纯数字,得到 {pid!r}")
    o = _tk.api("GET", f"/products/{pid}", timeout=90)
    if not o.get("success"):
        raise SystemExit("拉取失败:" + str(o.get("message") or o.get("detail")))
    draft = normalize(o.get("data") or {})
    outdir = os.path.abspath(os.path.join("tk_drafts", str(pid)))
    path = os.path.join(outdir, "draft.json")
    os.makedirs(outdir, exist_ok=True)
    if os.path.exists(path) and not force:
        old = json.load(open(path, encoding="utf-8"))
        draft = merge_keep_human_work(draft, old)
        print("  (draft 已存在:amazon 块与已下载图已保留;要彻底重来加 --force)")

    def dump():
        json.dump(draft, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    dump()                                            # 先落盘,下载失败不丢事实
    if do_download:
        for i, im in enumerate(draft["images"], 1):
            if im.get("local") and os.path.isfile(im["local"]):
                continue                              # 已下过
            try:
                im["local"] = _tk.download(im["url"], os.path.join(outdir, "imgs", f"{i:02d}"))
                print(f"  图 {i} -> {im['local']}")
            except Exception as e:
                print(f"  [warn] 图 {i} 下载失败(跳过):{str(e)[:60]}")
        vi = 0
        for s in draft["skus"]:
            for a in s["sales_attributes"]:
                if a.get("image_url") and not (a.get("local") and os.path.isfile(a.get("local", ""))):
                    vi += 1
                    try:
                        a["local"] = _tk.download(a["image_url"], os.path.join(outdir, "imgs", f"var_{vi:02d}"))
                        print(f"  变体图 {vi} -> {a['local']}")
                    except Exception as e:
                        print(f"  [warn] 变体图 {vi} 下载失败(跳过):{str(e)[:60]}")
        dump()

    print(f"\n草稿已生成:{path}")
    print(f"  标题(TK): {draft['title_tk'][:70]}")
    print(f"  图 {len(draft['images'])} 张 | SKU {len(draft['skus'])} 个 | 类目 {' > '.join(draft['category_chain'][:3])}")
    has_var = any(s["sales_attributes"] for s in draft["skus"])
    print(f"  变体: {'有(sales_attributes,需人确认 COLOR 映射)' if has_var else '无(单品)'}")
    print(f"\n下一步:python gap_check.py \"{path}\"")


if __name__ == "__main__":
    argv = _tk.consume_shop(sys.argv[1:])
    do_download = "--download" in argv
    force = "--force" in argv
    argv = [a for a in argv if a not in ("--download", "--force")]
    if not argv:
        print(__doc__); sys.exit(1)
    main(argv[0], do_download, force)
