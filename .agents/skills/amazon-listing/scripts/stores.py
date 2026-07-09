# -*- coding: utf-8 -*-
"""店铺 × 站点 矩阵一览:哪些店、哪些站点、seller_id、是否已授权可用。

用法:python stores.py [--refresh]
数据来自中间层 GET /amazon/stores/detail(赛狐注册表 + 本地 SP-API 授权情况)。
寻址写法:--store 店名[@站点],如 byane(=北美)/ byane@UK / inkelligent@CA。
"""
import sys
import _amz


def main(refresh: bool):
    o = _amz.api("GET", "/stores/detail", params={"refresh": "true"} if refresh else None)
    if not o.get("success"):
        print("FAIL:", o.get("message") or o.get("detail")); return
    d = o.get("data", {}) or {}
    rows = d.get("rows", [])
    cur = None
    print(f"{'店铺':<14} {'站点':<4} {'区域':<4} {'seller_id':<16} {'可用':<4} 寻址写法")
    print("-" * 66)
    for r in rows:
        if r["store"] != cur:
            if cur is not None:
                print()
            cur = r["store"]
        ok = "✅" if r.get("authorized") else "❌未授权"
        print(f"{r['store']:<14} {r['site']:<4} {r['region']:<4} {r.get('seller_id') or '-':<16} {ok:<5} --store {r['usage']}")
    ali = d.get("aliases") or {}
    if ali:
        print("\n别名:", ", ".join(f"{k}→{v}" for k, v in ali.items()))
    print(d.get("hint", ""))
    print("❌未授权 = 该区域还没配 SP-API token(去中间层 .env 的 AMAZON_STORES_JSON 加对应条目)")


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--refresh"]
    _amz.consume_store(argv)  # 只为打横幅/加载 env;本命令不区分店
    main("--refresh" in sys.argv)
