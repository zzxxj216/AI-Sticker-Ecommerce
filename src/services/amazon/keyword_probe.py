"""Amazon 关键词挖掘(autocomplete 真实搜索词)。

打 Amazon 站内的 completion suggestions 接口(下拉建议),用「前缀修饰词 +
a–z 后缀扩展」刨出 100–200 个真实买家长尾搜索词。免 key、免登录,12 站点通用。

来源思路改写自 nexscope-ai/Amazon-Skills 的 research.sh,但:
- 用 httpx + 本仓 http_proxy 路由(completion.amazon.* 走代理),不依赖 bash;
- 并发受限抓取 + 去重;返回结构化 dict,供 copy_generator 等下游消费。

纯数据采集,不调 LLM。LLM 的分级(主词/次词/长尾/后端)留给上层。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from src.core.http_proxy import httpx_request_kwargs

# 站点 → (域名后缀, marketplace id)。autocomplete 用 mid 决定站点。
_MARKETPLACES: dict[str, tuple[str, str]] = {
    "us": ("amazon.com", "ATVPDKIKX0DER"),
    "uk": ("amazon.co.uk", "A1F83G8C2ARO7P"),
    "de": ("amazon.de", "A1PA6795UKMFR9"),
    "fr": ("amazon.fr", "A13V1IB3VIYZZH"),
    "it": ("amazon.it", "APJ6JRA9NG5V4"),
    "es": ("amazon.es", "A1RKKUPIHCS9HS"),
    "jp": ("amazon.co.jp", "A1VC38T7YXB528"),
    "ca": ("amazon.ca", "A2EUQ1WTGCTBG2"),
    "au": ("amazon.com.au", "A39IBJ37TRP1C6"),
    "in": ("amazon.in", "A21TJRUUN4KGV"),
    "mx": ("amazon.com.mx", "A1AM78C64UM0Y8"),
    "br": ("amazon.com.br", "A2Q3Y263D1WV6Z"),
}

# 常见购买意图前缀修饰词(英文站)。
_DEFAULT_MODIFIERS: tuple[str, ...] = (
    "best", "cheap", "cute", "funny", "aesthetic", "kawaii",
    "waterproof", "vinyl", "small", "large",
)

_LETTERS: tuple[str, ...] = tuple("abcdefghijklmnopqrstuvwxyz")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _suggest(prefix: str, domain: str, mid: str, *, timeout: float) -> list[str]:
    """单次 autocomplete 调用,返回该前缀下的建议词(原始大小写)。"""
    prefix = prefix.strip()
    if not prefix:
        return []
    url = f"https://completion.{domain}/api/2017/suggestions"
    params = {
        "alias": "aps",            # All Product Search
        "mid": mid,
        "client-info": "amazon-search-ui",
        "suggestion-type": "KEYWORD",
        "prefix": prefix,
    }
    try:
        resp = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            **httpx_request_kwargs(url, timeout=timeout),
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    out: list[str] = []
    for s in data.get("suggestions") or []:
        val = (s.get("value") or "").strip()
        if val:
            out.append(val)
    return out


def probe_keywords(
    seed: str,
    *,
    marketplace: str = "us",
    use_letters: bool = True,
    modifiers: tuple[str, ...] | None = _DEFAULT_MODIFIERS,
    max_terms: int = 200,
    max_workers: int = 6,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """挖掘 seed 的长尾搜索词。

    扩展前缀 = seed 本身 + 「seed + 空格 + a..z」(若 use_letters) + 「modifier + 空格 + seed」。
    去重(按小写),保序,截到 max_terms。返回:
        {seed, marketplace, count, prefixes_queried, keywords: [..]}
    """
    seed = (seed or "").strip()
    if not seed:
        return {"seed": "", "marketplace": marketplace, "count": 0,
                "prefixes_queried": 0, "keywords": []}

    mk = marketplace.lower().strip()
    if mk not in _MARKETPLACES:
        raise ValueError(f"unsupported marketplace '{marketplace}'; "
                         f"choose from {sorted(_MARKETPLACES)}")
    domain, mid = _MARKETPLACES[mk]

    prefixes: list[str] = [seed]
    if use_letters:
        prefixes += [f"{seed} {c}" for c in _LETTERS]
    if modifiers:
        prefixes += [f"{m} {seed}" for m in modifiers]

    seen: set[str] = set()
    ordered: list[str] = []

    def _add(values: list[str]) -> None:
        for v in values:
            key = v.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(v)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(_suggest, p, domain, mid, timeout=timeout): p
            for p in prefixes
        }
        for fut in as_completed(futures):
            _add(fut.result())
            if len(ordered) >= max_terms:
                break

    return {
        "seed": seed,
        "marketplace": mk,
        "count": min(len(ordered), max_terms),
        "prefixes_queried": len(prefixes),
        "keywords": ordered[:max_terms],
    }


def supported_marketplaces() -> list[str]:
    return sorted(_MARKETPLACES)


if __name__ == "__main__":  # 快速自测: python -m src.services.amazon.keyword_probe cat
    import sys

    kw = sys.argv[1] if len(sys.argv) > 1 else "cat stickers"
    mkt = sys.argv[2] if len(sys.argv) > 2 else "us"
    t0 = time.time()
    res = probe_keywords(kw, marketplace=mkt)
    # GBK 控制台: 只打 ASCII 安全的摘要
    print(f"seed={res['seed']!r} marketplace={res['marketplace']} "
          f"prefixes={res['prefixes_queried']} found={res['count']} "
          f"in {time.time() - t0:.1f}s")
    for i, k in enumerate(res["keywords"][:40], 1):
        try:
            print(f"{i:>3}. {k}")
        except UnicodeEncodeError:
            print(f"{i:>3}. <non-ascii>")
