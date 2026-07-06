"""Amazon 能力探针 (阶段 0).

打通"推送 listing 到 Amazon"前的第一步:不能凭空猜 SP-API 的 attributes 结构,
必须先从中间层 (multi-channel-api :8000) 拉到 STICKER_DECAL 的真实字段定义,
看清:必填项 / 图片字段名 / 合法 variation_theme / 枚举值。

依赖:中间层 :8000 必须在跑 (sibling repo:
    python -m uvicorn app.main:app --port 8000)

用法:
    python scripts/amazon_probe.py                      # 默认 STICKER_DECAL
    python scripts/amazon_probe.py PRODUCT_TYPE [store] # 指定类型/店铺

输出:
    - 控制台:ASCII 摘要 (本机 GBK 控制台,避免打印非 ASCII 崩溃)
    - 文件:  scratchpad 下落盘完整 JSON,便于细看
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000").rstrip("/")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "_amazon_probe_out")


def _get(path: str, params: dict | None = None) -> tuple[int, dict]:
    """GET {BASE}{path}?params -> (status_code, parsed_json_or_raw)."""
    url = BASE + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"_raw": body[:2000]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body[:2000]}
    except urllib.error.URLError as e:
        return 0, {"_error": str(e.reason)}


def _save(name: str, data: dict) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(OUT_DIR, name))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _envelope_data(payload: dict) -> dict:
    """中间层用 {success, message, data} 信封;取 data,失败时原样返回。"""
    if isinstance(payload, dict) and "data" in payload:
        d = payload.get("data")
        return d if isinstance(d, dict) else {"_data": d}
    return payload


def _walk_required(schema: dict, prefix: str = "") -> list[str]:
    """递归收集 JSON Schema 里的 required 字段路径 (尽力而为)。"""
    out: list[str] = []
    if not isinstance(schema, dict):
        return out
    req = schema.get("required")
    if isinstance(req, list):
        out.extend(prefix + r for r in req)
    props = schema.get("properties")
    if isinstance(props, dict):
        for k, v in props.items():
            out.extend(_walk_required(v, prefix + k + "."))
    # 数组项
    items = schema.get("items")
    if isinstance(items, dict):
        out.extend(_walk_required(items, prefix + "[]."))
    return out


def main() -> int:
    product_type = sys.argv[1] if len(sys.argv) > 1 else "STICKER_DECAL"
    store = sys.argv[2] if len(sys.argv) > 2 else None

    print("=" * 60)
    print("Amazon probe  base=%s" % BASE)
    print("product_type=%s  store=%s" % (product_type, store or "(default)"))
    print("=" * 60)

    # 1) 连通性 + 店铺
    code, stores = _get("/api/v1/amazon/stores")
    if code == 0:
        print("[FAIL] middle layer unreachable: %s" % stores.get("_error"))
        print("       start it: python -m uvicorn app.main:app --port 8000")
        return 2
    print("[ok] /stores  http=%s" % code)
    sdata = _envelope_data(stores)
    sp = _save("stores.json", stores)
    print("     saved: %s" % sp)

    # 2) 搜索 sticker 相关 product types (确认 STICKER_DECAL 是否正确)
    code, search = _get("/api/v1/amazon/product-types", {"keywords": "sticker"})
    print("[ok] /product-types?keywords=sticker  http=%s" % code)
    _save("product_types_search.json", search)

    # 3) 核心:STICKER_DECAL 字段定义
    code, ptype = _get("/api/v1/amazon/product-types/%s" % product_type, {"store": store})
    print("[%s] /product-types/%s  http=%s" % ("ok" if 200 <= code < 300 else "FAIL", product_type, code))
    pp = _save("product_type_%s.json" % product_type, ptype)
    print("     saved: %s" % pp)

    if not (200 <= code < 300):
        msg = ptype.get("message") or ptype.get("_raw") or ptype.get("_error")
        print("     message: %s" % str(msg)[:300])
        print("     -> STICKER_DECAL 可能不是正确类型;看 product_types_search.json")
        return 1

    data = _envelope_data(ptype)

    # SP-API ProductTypeDefinitions 通常返回 schema.link.resource (指向 S3 JSON Schema)
    schema = data.get("schema") if isinstance(data, dict) else None
    link = None
    if isinstance(schema, dict):
        link = (schema.get("link") or {}).get("resource")
    if link:
        print("     schema is a link (S3 JSON Schema): %s" % link[:120])
        code2, real = _get_url(link)
        if code2 and 200 <= code2 < 300 and isinstance(real, dict):
            rp = _save("schema_%s.json" % product_type, real)
            print("     fetched real schema, saved: %s" % rp)
            _summarize_schema(real)
        else:
            print("     could not fetch schema link (http=%s)" % code2)
    else:
        # 有的中间层直接内联 schema
        _summarize_schema(data if isinstance(data, dict) else {})

    print("=" * 60)
    print("DONE. 细看落盘文件: %s" % os.path.abspath(OUT_DIR))
    return 0


def _get_url(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 - probe is best-effort
        return 0, {"_error": str(e)}


def _summarize_schema(schema: dict) -> None:
    """打印必填字段 / 图片字段 / variation_theme 摘要 (全 ASCII)。"""
    props = schema.get("properties")
    if not isinstance(props, dict):
        # 可能 schema 在更深一层
        for key in ("schema", "definitions"):
            inner = schema.get(key)
            if isinstance(inner, dict) and isinstance(inner.get("properties"), dict):
                props = inner["properties"]
                break
    print("-" * 60)
    if isinstance(props, dict):
        names = sorted(props.keys())
        print("attributes count: %d" % len(names))
        # 必填
        req = schema.get("required")
        if isinstance(req, list):
            print("top-level required: %s" % ", ".join(sorted(req)))
        # 图片相关
        img = [n for n in names if "image" in n.lower() or "photo" in n.lower()]
        print("image fields: %s" % (", ".join(img) if img else "(none found at top level)"))
        # 变体
        var = [n for n in names if "variation" in n.lower() or "parentage" in n.lower() or "child" in n.lower()]
        print("variation fields: %s" % (", ".join(var) if var else "(none found)"))
        # variation_theme 枚举
        vt = props.get("variation_theme") or props.get("variationTheme")
        if isinstance(vt, dict):
            enum = _find_enum(vt)
            if enum:
                print("variation_theme enum: %s" % ", ".join(map(str, enum))[:400])
    else:
        print("no 'properties' found; inspect saved JSON manually")
    print("-" * 60)


def _find_enum(node: dict) -> list:
    if not isinstance(node, dict):
        return []
    if isinstance(node.get("enum"), list):
        return node["enum"]
    for v in node.values():
        if isinstance(v, dict):
            r = _find_enum(v)
            if r:
                return r
        elif isinstance(v, list):
            for it in v:
                r = _find_enum(it)
                if r:
                    return r
    return []


if __name__ == "__main__":
    raise SystemExit(main())
