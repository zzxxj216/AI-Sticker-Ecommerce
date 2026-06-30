"""编辑(PATCH)已有 listing 的字段 —— 只改指定字段,不动图片/其它。product_type 自动从 GET 探测。

用法:
  # 改简单文本/枚举字段(可多个);text 类自动带 language_tag
  python edit_listing.py <SKU> set item_name="新标题" style=Cartoon pattern=Animals finish_type=Matte
  # 改价(purchasable_offer + list_price)
  python edit_listing.py <SKU> price 12.99 [--list 16.99]
  # 改库存(0=下架草稿;>0=可售)
  python edit_listing.py <SKU> stock 50
  # 任意 patch(完全控制):patches.json = [{"op":"replace","path":"/attributes/xxx","value":[...]}]
  python edit_listing.py <SKU> patches patches.json

  通用:--store <name>(默认 main)、--type <PRODUCT_TYPE>(探测失败时手动指定)、--dry(只打印不提交)。

🔴 这是**修改现有 listing**:只改自己上的产品;改前建议先 pull_product 看一眼。
"""
import sys, json
import _amz

# 需要 language_tag 的文本类属性(其余按纯 value 处理)
TEXT_ATTRS = {
    "item_name", "product_description", "title_differentiation", "style", "pattern",
    "finish_type", "color", "material", "generic_keyword", "model_name", "manufacturer",
    "theme", "subject_character", "special_feature", "surface_recommendation",
}


def _detect_type(sku: str, override: str | None) -> str:
    if override:
        return override
    o = _amz.get_listing(sku)
    summ = (o.get("data", {}) or {}).get("summaries") or []
    pt = summ[0].get("productType") if summ else None
    if not pt:
        raise SystemExit(f"探测不到 {sku} 的 product_type,请加 --type <PRODUCT_TYPE>")
    return pt


def _val(attr: str, value: str) -> list:
    if attr in TEXT_ATTRS:
        return _amz.Lt(value)
    return _amz.L(value)


def build_patches(action: str, args: list) -> list:
    ops = []
    if action == "set":
        for pair in args:
            if "=" not in pair:
                raise SystemExit(f"set 需要 attr=value,得到 {pair!r}")
            k, v = pair.split("=", 1)
            ops.append({"op": "replace", "path": f"/attributes/{k.strip()}", "value": _val(k.strip(), v)})
    elif action == "price":
        amount = float(args[0])
        list_price = amount
        if "--list" in args:
            list_price = float(args[args.index("--list") + 1])
        ops.append({"op": "replace", "path": "/attributes/purchasable_offer",
                    "value": [{"currency": "USD", "our_price": [{"schedule": [{"value_with_tax": round(amount, 2)}]}],
                               "marketplace_id": _amz.MP}]})
        ops.append({"op": "replace", "path": "/attributes/list_price",
                    "value": [{"value": round(list_price, 2), "currency": "USD", "marketplace_id": _amz.MP}]})
    elif action == "stock":
        qty = int(args[0])
        ops.append({"op": "replace", "path": "/attributes/fulfillment_availability",
                    "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": qty}]})
    elif action == "patches":
        ops = json.loads(open(args[0], encoding="utf-8").read())
    else:
        raise SystemExit(f"未知动作 {action}(set/price/stock/patches)")
    return ops


def run(sku: str, action: str, action_args: list, ptype: str | None, dry: bool):
    pt = _detect_type(sku, ptype)
    patches = build_patches(action, action_args)
    print(f"  product_type={pt}  patches={len(patches)}")
    for p in patches:
        print(f"    {p['op']} {p['path']}")
    if dry:
        print("  [dry] 未提交。"); return
    resp = _amz.patch_listing(sku, pt, patches)
    status, issues = _amz.issues_of(resp)
    errs = [i for i in issues if i.get("severity") == "ERROR"]
    print(f"  -> status={status} errors={len(errs)}")
    for i in errs:
        print(f"     ERR {','.join(i.get('attributeNames') or [])} :: {i.get('message','')[:120]}")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    # 抽 --type / --dry
    ptype = None
    if "--type" in argv:
        ptype = argv[argv.index("--type") + 1]
        argv = [a for j, a in enumerate(argv) if a != "--type" and (j == 0 or argv[j - 1] != "--type")]
    dry = "--dry" in argv
    argv = [a for a in argv if a != "--dry"]
    if len(argv) < 2:
        print(__doc__); sys.exit(1)
    run(argv[0], argv[1], argv[2:], ptype, dry)
