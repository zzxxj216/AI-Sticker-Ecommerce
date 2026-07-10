# -*- coding: utf-8 -*-
"""tk2amazon 共享工具:中间层 TikTok HTTP(绕系统代理、**只读白名单**)+ 店铺选择。

TK 侧只读是红线:api() 里有机械闸门 —— 只放行 GET 和 POST /products/search,
其余组合直接拒绝(中间层存在 TK 的创建/修改/删除端点,本 skill 永远不碰)。
"""
from __future__ import annotations
import json, os, sys, urllib.request, urllib.error
from urllib.parse import urlencode

# .env:本 skill 目录 → cwd → 仓根(skill 目录再上 3 级,主仓布局)依次尝试
_SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv
    for p in (os.path.join(_SKILL_DIR, ".env"), os.path.join(os.getcwd(), ".env"),
              os.path.abspath(os.path.join(_SKILL_DIR, "..", "..", "..", ".env"))):
        if os.path.isfile(p):
            load_dotenv(p, override=False)
except Exception:
    pass

BASE = os.getenv("AMAZON_MCA_URL", os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")).rstrip("/")
SHOP = os.getenv("TIKTOK_SHOP", "")          # 空 = 中间层默认店

# 内网服务绕过系统代理(挂梯子的机器否则连不上)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 只读白名单:GET 任意 + 仅此一个 POST。新脚本要加端点先想清楚是不是读操作。
_READONLY_POST = {"/products/search"}


def consume_shop(argv: list) -> list:
    """取出 `--shop <name>`,轻校验(对照 /shops/configured,连不上放行),打横幅。"""
    global SHOP
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--shop" and i + 1 < len(argv):
            SHOP = argv[i + 1].strip(); i += 2; continue
        if argv[i].startswith("--shop="):
            SHOP = argv[i].split("=", 1)[1].strip(); i += 1; continue
        out.append(argv[i]); i += 1
    if SHOP:
        try:
            o = api("GET", "/shops/configured")
            names = (o.get("data") or {}).get("shops") or []
            if o.get("success") and names and SHOP not in names:
                raise SystemExit(f"[拒绝] TK 店铺 '{SHOP}' 不在已配置列表 {names}")
        except SystemExit:
            raise
        except Exception:
            pass   # 校验接口不可达时放行(只读场景,选错店最坏也就是拉错数据)
    print(f"[tk shop = {SHOP or '(默认)'}]")
    return out


def api(method: str, path: str, *, params: dict | None = None, body: dict | None = None,
        timeout: int = 60) -> dict:
    """调中间层 /api/v1/tiktok{path}。**只读闸门**:非白名单的写方法直接拒。"""
    method = method.upper()
    if method != "GET" and not (method == "POST" and path in _READONLY_POST):
        raise SystemExit(f"[拒绝] tk2amazon 对 TK 只读:不允许 {method} {path}"
                         "(白名单:GET *、POST /products/search)")
    q = dict(params or {})
    if SHOP:
        q["shop"] = SHOP
    url = f"{BASE}/api/v1/tiktok{path}"
    if q:
        url += "?" + urlencode({k: v for k, v in q.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "message": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "message": f"NETWORK: {e.reason}; 中间层 {BASE} 连不上"
                                             "(查:服务在跑?同网可达?)"}


def amount(v):
    """TK 价格归一:有小数点透传('13.98'→13.98),纯整数按'分'除 100('1699'→16.99)。
    ⚠️ '17' 这类无点整值有 100 倍歧义 —— 展示时务必同时给 raw 原始值让人裁决。"""
    txt = "" if v is None else str(v).strip()
    if not txt:
        return None
    if "." in txt:
        try:
            return float(txt)
        except ValueError:
            return None
    try:
        return int(txt) / 100.0
    except ValueError:
        return None


_CT_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def download(url: str, dest_noext: str) -> str:
    """下载 TK CDN 图(带 UA)。扩展名按响应 Content-Type 定(写死 .jpg 会坑 PNG/WebP)。
    dest_noext 不带扩展名;返回实际写入路径。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _OPENER.open(req, timeout=60) as r:
        data = r.read()
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    ext = _CT_EXT.get(ct) or (os.path.splitext(url.split("?")[0])[1][:5] if "." in url.split("/")[-1] else "") or ".jpg"
    dest = dest_noext + ext
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def strip_html(html_text: str) -> str:
    """TK 描述 HTML → 纯文本。剥标签后用 html.unescape 一步解全部实体
    (顺序很重要:先剥标签再解实体,防 &lt;tag&gt; 解出的尖括号被误删)。"""
    import html as _html
    import re
    t = re.sub(r"<br\s*/?>|</p>|</li>", "\n", html_text or "", flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    return "\n".join(line.strip() for line in t.splitlines() if line.strip())
