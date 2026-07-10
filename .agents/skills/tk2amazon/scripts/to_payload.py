# -*- coding: utf-8 -*-
"""把补完的 draft.json 转成 amazon-listing 可用的 payload(单品)或 family.json(变体族)。

用法:
  python to_payload.py tk_drafts/<id>/draft.json            # 单品 → 同目录 payload.json
  python to_payload.py tk_drafts/<id>/draft.json --family   # 变体族 → 同目录 family.json

前提:draft 的 `amazon` 块已按 gap_check 补完(缺关键项会拒绝并指出)。
图片一律用**绝对路径** file:(先跑过 tk_pull --download),避免换目录跑 create 时断链。
产出后走 amazon-listing:create_listing.py validate/create 或 create_family.py。
"""
import json
import os
import sys

MP = "ATVPDKIKX0DER"


def Lt(v):
    return [{"value": v, "language_tag": "en_US", "marketplace_id": MP}]


def L(v):
    return [{"value": v, "marketplace_id": MP}]


def require(a: dict, keys: list[str]):
    missing = [k for k in keys if a.get(k) in (None, "", [])]
    if missing:
        raise SystemExit(f"[拒绝] amazon 块还缺:{', '.join(missing)} —— 先跑 gap_check.py 按清单补齐(事实类要问人)。")


def img_locators(d: dict) -> dict:
    """draft.images[].local(相对 tk_pull 的运行目录)→ file:<绝对路径>。
    main() 已 chdir 到该目录,直接 abspath 并检查存在。"""
    imgs = d.get("images") or []
    locs = []
    for im in imgs:
        loc = im.get("local")
        if not loc:
            continue
        p = os.path.abspath(loc)
        if not os.path.isfile(p):
            raise SystemExit(f"[拒绝] 图片文件不存在:{p} —— 重跑 tk_pull.py <id> --download")
        locs.append("file:" + p)
    if not locs:
        raise SystemExit("[拒绝] 图片未下载(payload 要用本地 file: 绝对路径)——重跑 tk_pull.py <id> --download")
    out = {"main_product_image_locator": [{"media_location": locs[0], "marketplace_id": MP}]}
    for i, u in enumerate(locs[1:9], 1):
        out[f"other_product_image_locator_{i}"] = [{"media_location": u, "marketplace_id": MP}]
    return out


def build_attributes(d: dict) -> dict:
    a = d["amazon"]
    require(a, ["store", "product_type", "sku", "title", "title_differentiation",
                "bullets", "description", "backend_keywords", "number_of_items",
                "unit_count", "price_usd"])
    if len(a["bullets"]) != 5:
        raise SystemExit(f"[拒绝] 五点要 5 条,现 {len(a['bullets'])} 条")
    if len(a["title"]) > 75:
        raise SystemExit(f"[拒绝] 标题 {len(a['title'])} 字 >75(带 Item Highlight 是硬规则)")
    if len(a["title_differentiation"]) > 125:
        raise SystemExit(f"[拒绝] Item Highlight {len(a['title_differentiation'])} 字 >125")
    if len(a["backend_keywords"].encode("utf-8")) > 249:
        raise SystemExit("[拒绝] 后端词 >249 字节(超了整条不索引)")

    pt = a["product_type"].upper()
    brand = a.get("brand") or "Inkelligent"
    attrs = {
        "condition_type": L("new_new"),
        "brand": L(brand),
        "manufacturer": Lt(brand),
        "item_name": Lt(a["title"]),
        "title_differentiation": Lt(a["title_differentiation"]),
        "bullet_point": [{"value": b, "language_tag": "en_US", "marketplace_id": MP} for b in a["bullets"]],
        "product_description": Lt(a["description"]),
        "country_of_origin": L(a.get("country_of_origin") or "CN"),
        "item_type_keyword": L(a.get("item_type_keyword") or "decorative-stickers"),
        "supplier_declared_dg_hz_regulation": L("not_applicable"),
        "supplier_declared_has_product_identifier_exemption": L(True),
        "number_of_items": L(int(a["number_of_items"])),
        "unit_count": [{"value": int(a["unit_count"]),
                        "type": {"value": "Count", "language_tag": "en_US"}, "marketplace_id": MP}],
        "generic_keyword": Lt(a["backend_keywords"]),
        "model_number": L(a["sku"]),
        "part_number": L(a["sku"]),
        "batteries_required": [{"value": False, "marketplace_id": MP}],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 0}],  # 红线:草稿
        "purchasable_offer": [{"currency": "USD",
                               "our_price": [{"schedule": [{"value_with_tax": float(a["price_usd"])}]}],
                               "marketplace_id": MP}],
        "list_price": [{"value": float(a.get("list_price") or a["price_usd"]), "currency": "USD",
                        "marketplace_id": MP}],
    }
    if a.get("color"):
        attrs["color"] = Lt(a["color"])
    if pt == "STICKER_DECAL":
        attrs.update({
            "theme": Lt(a.get("theme") or "Decorative"),
            "subject_character": Lt(a.get("subject_character") or "Assorted"),
            "special_feature": Lt(a.get("special_feature") or "Waterproof"),
            "surface_recommendation": Lt(a.get("surface_recommendation") or "Hard Surface"),
            "model_name": Lt(a.get("model_name") or a["title"][:40]),
            "required_product_compliance_certificate": L("Does Not Apply"),
        })
    elif pt == "LABEL":
        attrs["number_of_labels"] = L(int(a.get("number_of_labels") or a["unit_count"]))
        # LABEL 不要 model_name / required_product_compliance_certificate(实证)
    else:
        print(f"[warn] product_type={pt} 没有内置必填集,先用 amazon-listing 探针核对必填字段再 validate")
    return attrs


def main(path: str, family: bool):
    d = json.load(open(path, encoding="utf-8"))
    base_dir = os.path.dirname(os.path.abspath(path))
    os.chdir(os.path.join(base_dir, "..", ".."))   # 让 draft 里的相对 local 路径可解析
    a = d["amazon"]
    attrs = build_attributes(d)
    attrs.update(img_locators(d))
    outdir = base_dir
    if not family:
        payload = {"sku": a["sku"], "product_type": a["product_type"].upper(),
                   "requirements": "LISTING", "attributes": attrs}
        out = os.path.join(outdir, "payload.json")
        json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"payload 已生成:{out}")
        print(f"下一步(amazon-listing):\n  python create_listing.py validate \"{out}\" --store {a['store']}")
    else:
        variants = a.get("variants") or []
        if not variants:
            raise SystemExit('[拒绝] --family 需要 amazon.variants:[{"sku":..,"color":..,"main_image":"file:绝对路径"}...]'
                             "(变体映射是'必须问人'项)")
        common = {k: v for k, v in attrs.items()
                  if "image_locator" not in k and k != "color"}
        fam = {"product_type": a["product_type"].upper(), "parent_sku": a["sku"],
               "variation_theme": "COLOR", "common_attributes": common,
               "other_images": [attrs[k][0]["media_location"] for k in sorted(attrs)
                                if k.startswith("other_product_image_locator")],
               "children": variants}
        out = os.path.join(outdir, "family.json")
        json.dump(fam, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"family 已生成:{out}")
        print(f"下一步(amazon-listing):\n  python create_family.py validate \"{out}\" --store {a['store']}")


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--family"]
    if not args:
        print(__doc__); sys.exit(1)
    main(args[0], "--family" in sys.argv)
