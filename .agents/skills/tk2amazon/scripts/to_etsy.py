# -*- coding: utf-8 -*-
"""TK 产品同步到 Etsy(走中间层现成端点,**只建草稿**,上架永远人工)。

用法(默认只打印计划,不发请求;人点头后加 --go 才真建):
  python to_etsy.py tk_drafts/<id>/draft.json --tags "cat stickers,rescue cat,..." [选项]
  python to_etsy.py tk_drafts/<id>/draft.json --tags "..." --go        # 真建(Etsy 草稿)

选项:
  --tags "a,b,c"      Etsy 标签,≤13 个(TK 没有对应字段,必须人给或 agent 拟好经人确认)
  --title "..."       标题覆盖(≤140,缺省用 TK 标题截断)
  --price 12.99       统一价覆盖 | --price-mult 1.3  价格倍率(默认 1.0=沿用 TK 价)
  --qty 100           数量覆盖(1-999;缺省 TK 库存)
  --taxonomy-id 1317  类目(默认 1317=Stickers;查询:profiles 子命令)
  --listing-id <id>   更新已有 Etsy listing 的文案/tags(不动图和变体)
  --shop main         TK 店铺
辅助查询(只读):
  python to_etsy.py profiles        # 运费模板/备货时效/退货政策/类目 一览
  python to_etsy.py list [draft]    # 列 Etsy listing(默认看草稿)

安全:端点建的 listing 一律停在 **draft**(不售卖不计费);上架要人工 PATCH state=active
(开始计上架费)。重复同步同一 TK 品会建**新草稿**(不覆盖)——别重复 --go。
"""
import json
import sys
import urllib.request
from urllib.parse import urlencode

import _tk


def _api(method: str, path: str, *, params=None, body=None, timeout=300) -> dict:
    """中间层 /api/v1/etsy{path}(绕代理;本脚本唯一的写调用是 sync-from-tiktok=建 Etsy 草稿)。"""
    url = f"{_tk.BASE}/api/v1/etsy{path}"
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


def cmd_profiles():
    for name, path in [("运费模板 shipping-profiles", "/shipping-profiles"),
                       ("备货时效 readiness-states", "/readiness-states"),
                       ("退货政策 return-policies", "/return-policies")]:
        o = _api("GET", path)
        rows = (o.get("data") or {}).get("results") or o.get("data") or []
        print(f"--- {name} ---")
        for r in (rows if isinstance(rows, list) else [rows])[:5]:
            rid = r.get("shipping_profile_id") or r.get("readiness_state_id") or r.get("return_policy_id")
            print(f"  {rid}  {r.get('title') or r.get('processing_min','')}")
    o = _api("GET", "/taxonomy", params={"query": "sticker"})
    print("--- 类目 taxonomy(sticker)---")
    for r in (o.get("data") or [])[:5]:
        print(f"  {r.get('id')}  {r.get('name')}  ({r.get('full_path') or ''})")


def cmd_list(state: str):
    o = _api("GET", "/listings", params={"state": state, "limit": 20})
    rows = (o.get("data") or {}).get("results") or []
    print(f"Etsy listings(state={state}):{len(rows)} 个")
    for r in rows:
        p = r.get("price") or {}
        amt = p.get("amount")
        price = f"{amt / (p.get('divisor') or 100):.2f}" if isinstance(amt, (int, float)) else "?"
        print(f"  {r.get('listing_id')}  [{r.get('state')}]  ${price}  {str(r.get('title',''))[:56]}")


def build_body(draft: dict, opt: dict) -> dict:
    src = draft.get("source") or {}
    body = {"tiktok_product_id": src.get("product_id")}
    if src.get("shop") and src["shop"] != "(默认)":
        body["tiktok_shop"] = src["shop"]
    tags = [t.strip() for t in (opt.get("tags") or "").split(",") if t.strip()]
    if not tags:
        raise SystemExit("[拒绝] --tags 必给(Etsy 标签 TK 没有,人给或 agent 拟好经人确认;≤13 个,逗号分隔)")
    if len(tags) > 13:
        raise SystemExit(f"[拒绝] tags {len(tags)} 个 >13(Etsy 上限)")
    body["tags"] = tags
    title = opt.get("title") or draft.get("title_tk") or ""
    if len(title) > 140:
        print(f"  [warn] 标题 {len(title)} 字 >140,Etsy 会截断")
    body["title_override"] = title
    if opt.get("price") is not None:
        body["price_override"] = float(opt["price"])
    elif opt.get("price_mult") is not None:
        body["price_multiplier"] = float(opt["price_mult"])
    if opt.get("qty") is not None:
        q = int(opt["qty"])
        if not 1 <= q <= 999:
            raise SystemExit("[拒绝] qty 需在 1-999(Etsy 单 offering 上限)")
        body["quantity_override"] = q
    if opt.get("taxonomy_id") is not None:
        body["taxonomy_id"] = int(opt["taxonomy_id"])
    if opt.get("listing_id") is not None:
        body["etsy_listing_id"] = int(opt["listing_id"])
    return body


def main(argv: list):
    if argv and argv[0] == "profiles":
        cmd_profiles(); return
    if argv and argv[0] == "list":
        cmd_list(argv[1] if len(argv) > 1 else "draft"); return
    if not argv:
        print(__doc__); sys.exit(1)
    path = argv[0]
    opt, go, i = {}, False, 1
    keymap = {"--tags": "tags", "--title": "title", "--price": "price", "--price-mult": "price_mult",
              "--qty": "qty", "--taxonomy-id": "taxonomy_id", "--listing-id": "listing_id"}
    while i < len(argv):
        if argv[i] == "--go":
            go = True; i += 1; continue
        if argv[i] in keymap and i + 1 < len(argv):
            opt[keymap[argv[i]]] = argv[i + 1]; i += 2; continue
        i += 1
    draft = json.load(open(path, encoding="utf-8"))
    body = build_body(draft, opt)
    print("=== TK → Etsy 同步计划(端点只建草稿)===")
    print(json.dumps(body, ensure_ascii=False, indent=1))
    print(f"TK 价参考:{[(s.get('price'), s.get('price_raw')) for s in draft.get('skus') or []]}")
    if not go:
        print("\n[dry] 未发请求。把上面计划贴给用户确认;点头后加 --go 真建(Etsy 草稿)。")
        return
    o = _api("POST", "/sync-from-tiktok", body=body)
    d = o.get("data") or {}
    if not o.get("success"):
        raise SystemExit("失败:" + str(o.get("message") or o.get("detail")))
    print(f"\n✅ Etsy 草稿已建:listing_id={d.get('listing_id')} state={d.get('state')}")
    print(f"   url: {d.get('url')}")
    print(f"   images_ok={d.get('images_ok')} variants={len(d.get('variants') or [])}")
    print("   ⚠️ 现在是草稿:人工审核后要上架 → PATCH /api/v1/etsy/listings/<id> {\"fields\":{\"state\":\"active\"}}(开始计上架费)")


if __name__ == "__main__":
    main(_tk.consume_shop(sys.argv[1:]))
