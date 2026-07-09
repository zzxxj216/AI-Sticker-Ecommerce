# -*- coding: utf-8 -*-
"""拉取某店铺(某站点)的**所有在线产品**(数据源:赛狐 listing 列表,经中间层)。

用法:
  python list_products.py --store byane            # byane 美国站全部在线产品
  python list_products.py --store byane@UK         # 英国站
  python list_products.py --store byane --all      # 含下架/删除(默认只看 active)
  python list_products.py --store byane --csv out.csv   # 导出 CSV

店铺×站点 → 赛狐 shopId 的映射来自中间层 /stores/detail(赛狐注册表)。
纯只读。注:数据来自赛狐同步,和 Amazon 实时值可能有分钟级延迟。
"""
import csv as _csv
import json
import sys
import urllib.request
from urllib.parse import urlencode

import _amz


def _sellfox_listings(params: dict) -> dict:
    """中间层 /api/v1/sellfox/listings(_amz.api 固定 /amazon 前缀,这里单独请求)。"""
    url = f"{_amz.BASE}/api/v1/sellfox/listings?" + urlencode(params)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(url), timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "message": f"NETWORK: {e.reason}"}


def _shop_id(store: str) -> str:
    base, _, site = store.partition("@")
    site = (site or "US").upper()
    o = _amz.api("GET", "/stores/detail")
    if not o.get("success"):
        raise SystemExit("取不到 /stores/detail:" + str(o.get("message")))
    rows = (o.get("data") or {}).get("rows") or []
    hit = next((r for r in rows if r["store"] == base.lower() and r["site"] == site), None)
    if not hit or not hit.get("shop_id"):
        avail = sorted({f"{r['store']}@{r['site']}" for r in rows if r.get("shop_id")})
        raise SystemExit(f"'{base}@{site}' 不在赛狐注册表(可选:{', '.join(avail)})")
    return hit["shop_id"]


def main(store: str, show_all: bool, csv_path: str | None):
    shop = _shop_id(store)
    status = None if show_all else "active"
    out, page = [], 1
    while True:
        params = {"shop_ids": shop, "page_no": page, "page_size": 100}
        if status:
            params["online_status"] = status
        o = _sellfox_listings(params)
        if not o.get("success"):
            raise SystemExit("拉取失败:" + str(o.get("message") or o.get("detail")))
        d = o.get("data") or {}
        rows = d.get("rows") or []
        out.extend(rows)
        if page >= int(d.get("totalPage") or 1):
            break
        page += 1
    print(f"共 {len(out)} 个产品(store={store} shopId={shop} {'全部状态' if show_all else '仅active'})\n")
    print(f"{'SKU':<28} {'ASIN':<12} {'父ASIN':<12} {'价格':<10} {'库存':<6} {'BB':<3} 标题")
    for x in out:
        sku = x.get("sku") or x.get("msku") or x.get("commoditySku") or "?"
        price = f"{x.get('currency','')}{x.get('listingPrice','?')}"
        bb = "Y" if x.get("buyBoxWinner") else "-"
        print(f"{sku:<28} {x.get('asin','?'):<12} {x.get('parentAsin') or '-':<12} {price:<10} "
              f"{x.get('quantity','?'):<6} {bb:<3} {str(x.get('title',''))[:50]}")
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["sku", "asin", "parent_asin", "price", "currency", "quantity",
                        "buybox", "status", "open_date", "title"])
            for x in out:
                w.writerow([x.get("sku") or x.get("msku") or x.get("commoditySku"), x.get("asin"), x.get("parentAsin"),
                            x.get("listingPrice"), x.get("currency"), x.get("quantity"),
                            x.get("buyBoxWinner"), x.get("onlineStatus"), x.get("openDate"), x.get("title")])
        print(f"\n已导出 {csv_path}")


if __name__ == "__main__":
    argv = _amz.consume_store(sys.argv[1:])
    show_all = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    csv_path = None
    if "--csv" in argv:
        csv_path = argv[argv.index("--csv") + 1]
    main(_amz.STORE, show_all, csv_path)
