# -*- coding: utf-8 -*-
"""listing 上架体检:专抓"非必填但该有"的字段(Amazon 校验器不报、但影响转化/展示的)。

建完/改完跑一遍:python lint_listing.py <SKU> [<SKU> ...]  [--store xx]

检查项(FAIL=必须修 / WARN=建议修):
  - title_differentiation(Item Highlight)缺失 → FAIL(实证:漏了只能事后逐 SKU 补)
  - 标题 >75 字符 WARN(>200 FAIL);Item Highlight >125 FAIL
  - bullet_point ≠5 条 WARN;product_description 缺失 FAIL
  - generic_keyword 缺失 WARN,>249 字节 FAIL
  - 图:子体/单品无主图 FAIL,总图 <7 WARN(1主+8副=9 最佳)
  - brand ≠ Inkelligent WARN(丢 GTIN 豁免风险)
  - STICKER_DECAL 缺 batteries_required FAIL(必填,缺了 INVALID)
  - 子体缺 color FAIL;父体不检查图/offer
"""
import sys
import _amz


def g(a, key, sub="value"):
    v = a.get(key)
    return v[0].get(sub) if v else None


def lint(sku):
    o = _amz.get_listing(sku)
    if not o.get("success"):
        print(f"### {sku}: 拉不到({o.get('message')})"); return 1
    d = o.get("data", {}) or {}
    a = d.get("attributes", {}) or {}
    summ = (d.get("summaries") or [{}])[0]
    pt = summ.get("productType") or "?"
    parentage = g(a, "parentage_level") or "standalone"
    is_parent = parentage == "parent"
    probs = []

    def fail(m): probs.append(("FAIL", m))
    def warn(m): probs.append(("WARN", m))

    title = g(a, "item_name") or ""
    hl = g(a, "title_differentiation") or ""
    if not hl:
        fail("缺 title_differentiation(Item Highlight)——创建时就该带")
    elif len(hl) > 125:
        fail(f"Item Highlight {len(hl)} 字 >125")
    if len(title) > 200:
        fail(f"标题 {len(title)} 字 >200(2025 新规上限)")
    elif len(title) > 75:
        warn(f"标题 {len(title)} 字 >75(建议简洁)")

    bullets = a.get("bullet_point") or []
    if len(bullets) != 5:
        warn(f"五点 {len(bullets)} 条(建议 5)")
    if not g(a, "product_description"):
        fail("缺 product_description")

    gk = g(a, "generic_keyword") or ""
    if not gk:
        warn("缺 generic_keyword(后端词,白捡流量)")
    elif len(gk.encode("utf-8")) > 249:
        fail(f"后端词 {len(gk.encode('utf-8'))} 字节 >249(超了整条不索引)")

    if (g(a, "brand") or "") != _amz.BRAND:
        warn(f"brand={g(a,'brand')}(≠{_amz.BRAND},可能丢 GTIN 豁免)")
    if pt == "STICKER_DECAL" and "batteries_required" not in a:
        fail("STICKER_DECAL 缺 batteries_required(必填)")

    if not is_parent:
        if not a.get("main_product_image_locator"):
            fail("无主图")
        n_img = (1 if a.get("main_product_image_locator") else 0) + \
                len([k for k in a if k.startswith("other_product_image_locator")])
        if 0 < n_img < 7:
            warn(f"共 {n_img} 张图(<7,建议 1主+6副 起步)")
        if parentage == "child" and not g(a, "color"):
            fail("子体缺 color(变体区分字段)")

    tag = {"parent": "父", "child": "子"}.get(parentage, "单")
    n_fail = sum(1 for s, _ in probs if s == "FAIL")
    head = "PASS" if not probs else (f"{n_fail} FAIL" if n_fail else "WARN")
    print(f"### {sku} [{tag}|{pt}] {head}")
    for s, m in probs:
        print(f"   [{s}] {m}")
    return n_fail


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    if not argv:
        print(__doc__); sys.exit(1)
    total = sum(lint(sku) for sku in argv)
    sys.exit(1 if total else 0)
