# -*- coding: utf-8 -*-
"""TK 产品同步到 Shopify(经中间层建品,**status=draft 写死**,上架永远人工)。

用法(默认只打印 payload 计划;人点头后加 --go 才真建):
  python to_shopify.py tk_drafts/<id>/draft.json [选项]
  python to_shopify.py tk_drafts/<id>/draft.json --go

选项:
  --title "..."     标题覆盖(≤255,缺省 TK 标题)
  --price 12.99     价格覆盖(缺省沿用 TK 价;TK 价取不到则必给)
  --qty 100         库存(缺省 TK 库存)
  --tags "a,b,c"    Shopify 标签(逗号串,可选)
  --vendor X        品牌(默认 Inkelligent) | --type X  产品类型(默认 Stickers)
  --max-images 8    图片上限(默认 8,base64 内嵌,需先 tk_pull --download)
辅助:
  python to_shopify.py list         # 列 Shopify 产品(最近 20)

安全:status **写死 draft**(不售卖);上架要人工在 Shopify 后台切 Active。
图片优先用 tk_pull 下载的本地图(base64 attachment,不依赖 TK CDN 存活);
没下载过则退回 TK URL src 模式并警告。
"""
import base64
import json
import os
import sys
import urllib.request
from urllib.parse import urlencode

import _tk


def _api(method: str, path: str, *, params=None, body=None, timeout=300) -> dict:
    url = f"{_tk.BASE}/api/v1/shopify{path}"
    if params:
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _tk._OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "message": f"NETWORK: {e.reason}"}


def cmd_list():
    o = _api("GET", "/products", params={"limit": 20})
    rows = (o.get("data") or {}).get("products") or o.get("data") or []
    print(f"Shopify 产品:{len(rows)} 个")
    for r in rows if isinstance(rows, list) else []:
        v0 = (r.get("variants") or [{}])[0]
        print(f"  {r.get('id')}  [{r.get('status')}]  ${v0.get('price','?')}  {str(r.get('title',''))[:56]}")


def _desc_html(text: str) -> str:
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(f"<p>{esc(p)}</p>" for p in paras)


def build_payload(draft: dict, opt: dict) -> dict:
    skus = draft.get("skus") or []
    s0 = skus[0] if skus else {}
    price = opt.get("price")
    if price is None:
        price = s0.get("price")
        if price is None:
            raise SystemExit(f"[拒绝] TK 价取不到(raw={s0.get('price_raw')!r}),必须 --price 指定")
        print(f"  价格沿用 TK:{price}(raw {s0.get('price_raw')!r} —— 无小数点的 raw 有百倍歧义,确认过再 --go)")
    qty = int(opt.get("qty") or s0.get("qty_tk") or 0)
    title = (opt.get("title") or draft.get("title_tk") or "")[:255]

    variants = []
    if any(s.get("sales_attributes") for s in skus):        # 多变体:每 TK SKU 一个变体
        for s in skus:
            name = " / ".join(a.get("value") or "" for a in s.get("sales_attributes") or []) or (s.get("seller_sku") or "Default")
            p = opt.get("price") or s.get("price")
            variants.append({"option1": name[:255], "price": f"{float(p):.2f}",
                             "sku": s.get("seller_sku") or "", "inventory_quantity": int(s.get("qty_tk") or qty)})
    else:
        variants.append({"price": f"{float(price):.2f}", "sku": s0.get("seller_sku") or "",
                         "inventory_quantity": qty})

    images, used_local = [], False
    for i, im in enumerate((draft.get("images") or [])[: int(opt.get("max_images") or 8)], 1):
        loc = im.get("local")
        if loc and os.path.isfile(loc):
            with open(loc, "rb") as f:
                images.append({"attachment": base64.b64encode(f.read()).decode(),
                               "alt": f"{title[:60]} - {i}", "position": i})
            used_local = True
        else:
            images.append({"src": im.get("url"), "alt": f"{title[:60]} - {i}", "position": i})
    if images and not used_local:
        print("  [warn] 用的是 TK CDN URL 直链(未 tk_pull --download);URL 失效图会丢,建议先下载")

    payload = {
        "title": title,
        "body_html": _desc_html(draft.get("description_text") or ""),
        "vendor": opt.get("vendor") or "Inkelligent",
        "product_type": opt.get("type") or "Stickers",
        "status": "draft",                     # 🔴 写死草稿,绝不 active
        "variants": variants,
        "images": images,
    }
    if opt.get("tags"):
        payload["tags"] = opt["tags"]
    if len(variants) > 1:
        payload["options"] = [{"name": "Style"}]
    return payload


def main(argv: list):
    if argv and argv[0] == "list":
        cmd_list(); return
    if not argv:
        print(__doc__); sys.exit(1)
    path = argv[0]
    opt, go, i = {}, False, 1
    keymap = {"--title": "title", "--price": "price", "--qty": "qty", "--tags": "tags",
              "--vendor": "vendor", "--type": "type", "--max-images": "max_images"}
    while i < len(argv):
        if argv[i] == "--go":
            go = True; i += 1; continue
        if argv[i] in keymap and i + 1 < len(argv):
            opt[keymap[argv[i]]] = argv[i + 1]; i += 2; continue
        i += 1
    draft = json.load(open(path, encoding="utf-8"))
    payload = build_payload(draft, opt)
    view = dict(payload)
    view["images"] = [(f"<base64 {len(im['attachment'])}B>" if "attachment" in im
                       else f"<src {str(im.get('src',''))[:60]}>") for im in payload["images"]]
    print("=== TK → Shopify 建品计划(status=draft)===")
    print(json.dumps(view, ensure_ascii=False, indent=1)[:2000])
    if not go:
        print("\n[dry] 未发请求。把计划贴给用户确认;点头后加 --go 真建(Shopify 草稿)。")
        return
    o = _api("POST", "/products", body=payload)
    d = (o.get("data") or {})
    if not o.get("success"):
        raise SystemExit("失败:" + str(o.get("message") or o.get("detail")))
    pid = d.get("id")
    print(f"\n✅ Shopify 草稿已建:product_id={pid} status={d.get('status')} handle={d.get('handle')}")
    print(f"   后台: https://admin.shopify.com/store/inkelligent/products/{pid}")
    print("   ⚠️ 现在是 draft:人工审核后在后台切 Active 才上架。")


if __name__ == "__main__":
    main(_tk.consume_shop(sys.argv[1:]))
