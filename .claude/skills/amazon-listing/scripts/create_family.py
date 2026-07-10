"""一键创建变体族(父体 + N 个子体),一份 family.json 搞定。

family.json 结构:
  {
    "product_type": "STICKER_DECAL",
    "parent_sku": "INK-XXX-PARENT",
    "variation_theme": "COLOR",
    "common_attributes": { ...所有 SKU 共享的完整属性(含 offer/价格/库存,父体会自动剥掉)... },
    "other_images": ["file:C:/../sub1.jpg", ...],          # 全族共用副图(可选,≤8)
    "children": [
      {"sku": "INK-XXX-NAVY", "color": "Navy Floral", "main_image": "file:C:/../navy.jpg",
       "attributes": {"item_name": [ ...每变体覆盖字段,可选... ]},
       "other_images": [ ...该子体专属副图,覆盖族级,可选... ]}
    ]
  }

用法:
  python create_family.py validate family.json   # 全族 VALIDATION_PREVIEW 零写入校验
  python create_family.py create   family.json   # 真建:父先子后;父体 GET 防覆盖

规则(与手工建族一致,见 SKILL.md「变体族」):
  - 父体 = common 剥掉 offer 三件套(purchasable_offer/list_price/fulfillment_availability)
    和 color/图片,加 parentage_level=parent + variation_theme。
  - 子体 = common + parentage_level=child + child_parent_sku_relationship + color + 主图/副图。
  - 记得 common_attributes 带 title_differentiation(Item Highlight)——非必填但漏了只能事后补。
"""
import sys, json
import _amz

OFFER_KEYS = ("purchasable_offer", "list_price", "fulfillment_availability")


def _img(loc: str, sku: str) -> str:
    if isinstance(loc, str) and loc.startswith("file:"):
        url = _amz.upload_image_cos(loc[5:], key_prefix=f"amazon/{sku.lower()}")
        print(f"  COS: {loc[5:].split(chr(92))[-1].split('/')[-1]} -> {url.split('/')[-1]}")
        return url
    return loc


def parent_attrs(fam: dict) -> dict:
    a = {k: v for k, v in fam["common_attributes"].items()
         if k not in OFFER_KEYS and k != "color" and "image_locator" not in k}
    a["parentage_level"] = _amz.L("parent")
    a["variation_theme"] = [{"name": fam["variation_theme"], "marketplace_id": _amz.MP}]
    return a


def child_attrs(fam: dict, ch: dict) -> dict:
    a = dict(fam["common_attributes"])
    a["parentage_level"] = _amz.L("child")
    a["child_parent_sku_relationship"] = [{
        "child_relationship_type": "variation",
        "parent_sku": fam["parent_sku"], "marketplace_id": _amz.MP}]
    a["color"] = _amz.Lt(ch["color"])
    a["main_product_image_locator"] = [{"media_location": _img(ch["main_image"], ch["sku"]),
                                        "marketplace_id": _amz.MP}]
    subs = ch.get("other_images", fam.get("other_images", []))[:8]
    for i, loc in enumerate(subs, 1):
        a[f"other_product_image_locator_{i}"] = [{"media_location": _img(loc, fam["parent_sku"]),
                                                  "marketplace_id": _amz.MP}]
    a.update(ch.get("attributes", {}))
    return a


def submit(action: str, sku: str, ptype: str, attrs: dict, tag: str):
    if action == "validate":
        resp = _amz.put_listing(sku, ptype, attrs, mode="VALIDATION_PREVIEW")
    else:
        resp = _amz.put_listing(sku, ptype, attrs)
    status, issues = _amz.issues_of(resp)
    errs = [i for i in issues if i.get("severity") == "ERROR"]
    print(f"[{tag}] {sku} status={status} issues={len(issues)} errors={len(errs)}")
    for i in issues:
        print(f"   [{i.get('severity')}] {','.join(i.get('attributeNames') or [])} :: {i.get('message','')[:110]}")
    return status, errs


def run(action: str, path: str):
    fam = json.loads(open(path, encoding="utf-8").read())
    ptype = fam["product_type"]
    print(f"=== {action.upper()} 变体族 {fam['parent_sku']} ({len(fam['children'])} 子体) ===")

    if action == "create":
        # 红线防覆盖:父体和**每个子体**都 GET 确认不存在(SP-API PUT 是 upsert,
        # 撞名会静默覆盖已有 listing;子体也必须查)。
        for sku in [fam["parent_sku"]] + [c["sku"] for c in fam["children"]]:
            if _amz.sku_exists(sku):
                print(f"[ABORT] {sku} 已存在,按红线拒绝覆盖(只增不改)。")
                return
    st, errs = submit(action, fam["parent_sku"], ptype, parent_attrs(fam), "父")
    if action == "create" and (errs or st not in ("ACCEPTED", "VALID")):
        print("[STOP] 父体失败,不再建子体。")
        return
    for ch in fam["children"]:
        submit(action, ch["sku"], ptype, child_attrs(fam, ch), "子")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    if len(argv) < 2 or argv[0] not in ("validate", "create"):
        print(__doc__); sys.exit(1)
    run(argv[0], argv[1])
