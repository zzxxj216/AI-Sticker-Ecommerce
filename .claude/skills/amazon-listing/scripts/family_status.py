# -*- coding: utf-8 -*-
"""变体族状态一览 + ASIN 变更检测(防 AC-1022)。

用法:python family_status.py <SKU> <SKU> ...  [--store xx]

输出每个 SKU:ASIN / 状态 / 价 / 库存 / 图数;并与上次记录对比:
**Amazon 会悄悄重新分配子体 ASIN**(实证:5 子体变了 4 个),A+ 绑旧 ASIN 报 AC-1022。
本脚本把 ASIN 存到 skill 目录 .asin_cache.json,变了会 ⚠️ 高亮——绑 A+ 前先跑一遍。
"""
import sys, json, os
import _amz

CACHE = os.path.join(os.path.dirname(__file__), ".asin_cache.json")


def main(skus):
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    changed = []
    for sku in skus:
        o = _amz.get_listing(sku)
        if not o.get("success"):                    # SP-API 偶发超时,重试一次
            import time; time.sleep(2)
            o = _amz.get_listing(sku)
        if not o.get("success"):
            print(f"{sku:<26} 拉取失败: {o.get('message')}"); continue
        d = o.get("data", {}) or {}
        a = d.get("attributes", {}) or {}
        s = (d.get("summaries") or [{}])[0]
        asin = s.get("asin") or ""
        st = s.get("status") or []
        sts = ",".join(st) if isinstance(st, list) else st
        fa = a.get("fulfillment_availability") or [{}]
        qty = fa[0].get("quantity", "-")
        po = a.get("purchasable_offer") or [{}]
        try:
            price = po[0]["our_price"][0]["schedule"][0]["value_with_tax"]
        except (KeyError, IndexError):
            price = "-"
        n_img = (1 if a.get("main_product_image_locator") else 0) + \
                len([k for k in a if k.startswith("other_product_image_locator")])
        old = cache.get(sku)
        drift = ""
        if asin and old and old != asin:
            drift = f"  ⚠️ ASIN 变了 {old} -> {asin}(A+ 若绑旧 ASIN 会 AC-1022,需重绑!)"
            changed.append((sku, old, asin))
        if asin:
            cache[sku] = asin
        print(f"{sku:<26} {asin or '(待分配)':<12} {sts or '(处理中)':<26} ${price!s:<7} qty={qty!s:<6} 图={n_img}{drift}")
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
    if changed:
        print(f"\n⚠️ {len(changed)} 个 SKU 的 ASIN 被 Amazon 重新分配,涉及 A+ 的请用新 ASIN 重绑。")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    if not argv:
        print(__doc__); sys.exit(1)
    main(argv)
