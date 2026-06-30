"""拉取产品信息。
用法:
  python pull_product.py <SKU> [<SKU> ...]          # 摘要
  python pull_product.py <SKU> --json               # 完整 attributes JSON
  python pull_product.py --asin <ASIN>              # 按 ASIN 查 catalog
"""
import sys, json
import _amz


def summarize(sku: str):
    o = _amz.get_listing(sku)
    if not o.get("success"):
        print(f"{sku}: 拉取失败 {o.get('message') or o.get('detail')}")
        return
    d = o["data"]; a = d.get("attributes", {})
    def g(k):
        v = a.get(k, []); return v[0].get("value") if v else ""
    summ = d.get("summaries", [])
    st = summ[0].get("status") if summ else "processing"
    asin = summ[0].get("asin", "") if summ else ""
    imgs = sorted(k for k in a if "image_locator" in k)
    price = ""
    po = a.get("purchasable_offer", [{}])
    try:
        price = po[0]["our_price"][0]["schedule"][0]["value_with_tax"]
    except Exception:
        pass
    print(f"=== {sku} ===")
    print(f"  ASIN: {asin or '-'}   status: {st}")
    print(f"  title: {g('item_name')}")
    print(f"  item_highlight: {(g('title_differentiation') or '')[:80]}")
    print(f"  style={g('style')} pattern={g('pattern')} finish={g('finish_type')} | images={len(imgs)} price=${price}")
    errs = [i for i in d.get("issues", []) if i.get("severity") == "ERROR"]
    if errs:
        print(f"  issues({len(errs)}): " + " | ".join(i.get("message", "")[:70] for i in errs[:3]))


def full(sku: str):
    print(json.dumps(_amz.get_listing(sku), ensure_ascii=False, indent=2))


def by_asin(asin: str, raw: bool = False):
    o = _amz.api("GET", f"/catalog/{asin}", params={"store": _amz.STORE})
    if raw:
        print(json.dumps(o, ensure_ascii=False, indent=2)); return
    if not o.get("success"):
        print(f"ASIN {asin}: 拉取失败 {o.get('message') or o.get('detail')}"); return
    d = o["data"]
    s = (d.get("summaries") or [{}])[0]
    img_block = (d.get("images") or [{}])[0].get("images", [])
    mains = [i for i in img_block if i.get("variant") == "MAIN"]
    variants = sorted({i.get("variant") for i in img_block})
    bc = s.get("browseClassification") or {}
    print(f"=== ASIN {asin} ===")
    print(f"  title: {s.get('itemName')}")
    print(f"  brand={s.get('brand')} style={s.get('style')} color={s.get('color')} manufacturer={s.get('manufacturer')}")
    print(f"  model={s.get('modelNumber')} part={s.get('partNumber')} class={bc.get('displayName')}")
    print(f"  images: {len(img_block)} 个(variants={variants}) main={mains[0].get('link') if mains else '-'}")
    ranks = d.get("salesRanks") or []
    for blk in ranks[:1]:
        for r in (blk.get("classificationRanks") or [])[:1] + (blk.get("displayGroupRanks") or [])[:1]:
            print(f"  rank: #{r.get('rank')} in {r.get('title')}")


if __name__ == "__main__":
    args = _amz.consume_store(sys.argv[1:])
    if not args:
        print(__doc__); sys.exit(1)
    if args[0] == "--asin":
        ids = [a for a in args[1:] if not a.startswith("--")]
        for asin in ids:
            by_asin(asin, raw=("--json" in args))
        sys.exit(0)
    if "--json" in args:
        for s in [a for a in args if not a.startswith("--")]:
            full(s)
    else:
        for s in [a for a in args if not a.startswith("--")]:
            summarize(s)
