# -*- coding: utf-8 -*-
"""列出 TikTok Shop 的产品(找要同步到 Amazon 的那个)。只读。

用法:
  python tk_list.py                     # 默认店,前 20 个
  python tk_list.py --shop main --n 50
  python tk_list.py --status ACTIVATE   # 按状态过滤(真实枚举:DRAFT/PENDING/FAILED/
                                        #  ACTIVATE/SELLER_DEACTIVATED/PLATFORM_DEACTIVATED/FREEZE/DELETED)

注:状态过滤同时做**客户端兜底**(中间层旧版把 status 包错位置时服务端过滤会失效)。
"""
import sys
import _tk


def main(n: int, status: str | None):
    got, token, pages = [], None, 0
    while len(got) < n and pages < 50:
        pages += 1
        body = {"page_size": 100 if status else min(100, n - len(got))}
        if token:
            body["page_token"] = token
        if status:
            body["status"] = status
        o = _tk.api("POST", "/products/search", body=body)
        if not o.get("success"):
            raise SystemExit("搜索失败:" + str(o.get("message") or o.get("detail")))
        d = o.get("data") or {}
        page_items = d.get("products") or d.get("product_list") or []
        if status:                                   # 客户端兜底过滤
            page_items = [p for p in page_items if p.get("status") == status]
        got.extend(page_items)
        token = d.get("next_page_token")
        if not token:
            break
    got = got[:n]
    print(f"共取到 {len(got)} 个产品" + (f"(status={status})" if status else "") + "\n")
    print(f"{'product_id':<20} {'状态':<20} {'SKU数':<5} {'首SKU':<22} {'价格':<9} 标题")
    for p in got:
        skus = p.get("skus") or []
        s0 = skus[0] if skus else {}
        pr = s0.get("price") or {}
        amt = _tk.amount(pr.get("sale_price") or pr.get("tax_exclusive_price"))
        price = "?" if amt is None else f"{amt:.2f}"
        print(f"{p.get('id',''):<20} {p.get('status',''):<20} {len(skus):<5} "
              f"{s0.get('seller_sku','-'):<22} ${price:<8} {str(p.get('title',''))[:50]}")


if __name__ == "__main__":
    argv = _tk.consume_shop(sys.argv[1:])
    n = 20
    if "--n" in argv:
        n = int(argv[argv.index("--n") + 1])
    status = None
    if "--status" in argv:
        status = argv[argv.index("--status") + 1]
    main(n, status)
