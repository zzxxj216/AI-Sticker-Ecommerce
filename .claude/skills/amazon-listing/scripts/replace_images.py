"""替换已有 listing 的主图/副图(PATCH,只动图片字段)。product_type 自动探测。

用法:
  python replace_images.py <SKU> show                          # 看当前主/副图(URL+是否已被Amazon抓取)
  python replace_images.py <SKU> main file:C:/x.png            # 只换主图
  python replace_images.py <SKU> gallery file:1.png file:2.png ...   # 换副图(按序填槽1..N,≤8)
  python replace_images.py <SKU> all  file:主.png file:副1.png ...    # 主图+副图一次换
  通用:--store/--type/--dry 同 edit_listing。

已知坑(实证):
  - Amazon **不允许清空多余图槽**(delete/空 replace 都报 InvalidInput)——新图比旧槽少时,
    多出的槽会显示成新图的重复;要删只能去 Seller Central 图片管理器手删。脚本会提醒。
  - 换图必须走内容哈希 key(upload_image_cos 已内置),固定 URL Amazon 不重抓。
"""
import sys
import _amz


def _detect_type(sku, override):
    if override:
        return override
    o = _amz.get_listing(sku)
    summ = (o.get("data", {}) or {}).get("summaries") or []
    pt = summ[0].get("productType") if summ else None
    if not pt:
        raise SystemExit(f"探测不到 {sku} 的 product_type,请加 --type")
    return pt


def _url(loc, sku):
    if loc.startswith("file:"):
        u = _amz.upload_image_cos(loc[5:], key_prefix=f"amazon/{sku.lower()}")
        print(f"  COS: {loc[5:].split('/')[-1]} -> {u.split('/')[-1]}")
        return u
    return loc


def cmd_show(sku):
    a = (_amz.get_listing(sku).get("data", {}) or {}).get("attributes", {}) or {}
    fields = ["main_product_image_locator"] + [f"other_product_image_locator_{i}" for i in range(1, 9)]
    for f in fields:
        v = a.get(f)
        if not v:
            continue
        u = v[0].get("media_location", "")
        state = "AMZ已抓" if "media-amazon" in u else "待抓取"
        print(f"  {f:<32} [{state}] {u}")


def build(sku, action, imgs):
    ops = []
    if action == "main":
        ops.append({"op": "replace", "path": "/attributes/main_product_image_locator",
                    "value": [{"media_location": _url(imgs[0], sku), "marketplace_id": _amz.MP}]})
    elif action == "gallery":
        for i, loc in enumerate(imgs[:8], 1):
            ops.append({"op": "replace", "path": f"/attributes/other_product_image_locator_{i}",
                        "value": [{"media_location": _url(loc, sku), "marketplace_id": _amz.MP}]})
    elif action == "all":
        ops = build(sku, "main", imgs[:1]) + build(sku, "gallery", imgs[1:])
    else:
        raise SystemExit("动作:show / main / gallery / all")
    return ops


def warn_leftover(sku, action, n_new):
    if action not in ("gallery", "all"):
        return
    a = (_amz.get_listing(sku).get("data", {}) or {}).get("attributes", {}) or {}
    old = len([k for k in a if k.startswith("other_product_image_locator")])
    if old > n_new:
        print(f"  ⚠️ 原有 {old} 个副图槽,新图只 {n_new} 张;多出的 {old - n_new} 槽 API 清不掉"
              f"(会显示重复),请到 Seller Central 图片管理器手删。")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    ptype = None
    if "--type" in argv:
        ptype = argv[argv.index("--type") + 1]
        argv = [a for j, a in enumerate(argv) if a != "--type" and (j == 0 or argv[j - 1] != "--type")]
    dry = "--dry" in argv
    argv = [a for a in argv if a != "--dry"]
    if len(argv) < 2:
        print(__doc__); sys.exit(1)
    sku, action, imgs = argv[0], argv[1], argv[2:]
    if action == "show":
        cmd_show(sku); sys.exit(0)
    n_sub = len(imgs[1:]) if action == "all" else len(imgs)
    warn_leftover(sku, action, n_sub)
    ops = build(sku, action, imgs)
    if dry:
        print(f"  [dry] {len(ops)} 个 patch,未提交。"); sys.exit(0)
    resp = _amz.patch_listing(sku, _detect_type(sku, ptype), ops)
    status, issues = _amz.issues_of(resp)
    errs = [i for i in issues if i.get("severity") == "ERROR"]
    print(f"  -> status={status} errors={len(errs)}")
    for i in errs:
        print(f"     ERR {i.get('message','')[:120]}")
