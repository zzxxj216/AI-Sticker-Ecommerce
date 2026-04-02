"""TikTok Creative Center — 热门 Hashtag 抓取器

纯抓取层：返回原始数据，不做任何业务过滤或格式转换。
所有 Playwright 逻辑封装在此，外部只需调用 fetch()。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from playwright.sync_api import (
    sync_playwright, Response, Route, BrowserContext,
)

# ── 常量 ──────────────────────────────────────────────────

LIST_PAGE_URL = (
    "https://ads.tiktok.com/business/creativecenter"
    "/inspiration/popular/hashtag/pc/en"
)
DETAIL_PAGE_URL = (
    "https://ads.tiktok.com/business/creativecenter"
    "/hashtag/{hashtag}/pc/en"
)
API_LIST_PATTERN = "popular_trend/hashtag/list"
API_CREATOR_PATTERN = "popular_trend/hashtag/creator"
API_RADAR = "creative_radar_api"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# filter_by → 人类标签
FILTER_LABELS = {
    "": "all",
    "new_on_board": "new_on_board",
}


def _rewrite_list_url(
    original_url: str, *, page: int, limit: int,
    period: int, country_code: str, filter_by: str, sort_by: str,
) -> str:
    parsed = urlparse(original_url)
    params: dict[str, Any] = {
        "page": page, "limit": limit, "period": period,
        "country_code": country_code, "sort_by": sort_by,
    }
    if filter_by:
        params["filter_by"] = filter_by
    return urlunparse(parsed._replace(query=urlencode(params)))


# ── 主类 ─────────────────────────────────────────────────

class TikTokFetcher:
    """抓取 TikTok Creative Center 热门 Hashtag（列表 + 详情）。

    所有方法返回 **原始 dict**，不做字段重命名或裁剪。
    """

    def __init__(
        self,
        country: str = "US",
        period: int = 7,
        headed: bool = False,
        timeout_ms: int = 45_000,
    ):
        self.country = country
        self.period = period
        self.headed = headed
        self.timeout_ms = timeout_ms

    # ── 对外入口 ──────────────────────────────────────────

    def fetch(
        self,
        *,
        filters: list[str] | None = None,
        sort_by: str = "popular",
        page_size: int = 50,
        max_pages: int = 10,
        fetch_details: bool = True,
    ) -> dict[str, Any]:
        """一次性抓取列表 + 详情，返回完整原始数据。

        Returns:
            {
                "meta": { ... 抓取参数与时间 },
                "hashtags": {
                    "<hashtag_id>": {
                        "list_data": { ... API 原始 },
                        "detail_data": { ... __NEXT_DATA__ 原始 | None },
                        "creators_raw": [ ... creator API 原始 | [] ],
                        "found_in_filters": ["all", "new_on_board"],
                        "crawled_at": "ISO timestamp",
                    }
                }
            }
        """
        if filters is None:
            filters = ["", "new_on_board"]

        crawl_ts = datetime.now(tz=timezone.utc).isoformat()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=not self.headed,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                locale="en-US",
                viewport={"width": 1440, "height": 900},
                user_agent=BROWSER_UA,
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{ get: () => false });"
            )

            # Phase 1 — 列表
            print(f"[TikTokFetcher] 列表抓取  {self.country} / {self.period}d")
            raw_by_filter = self._fetch_lists(
                ctx, filters=filters, sort_by=sort_by,
                page_size=page_size, max_pages=max_pages,
            )

            # 合并、按 hashtag_id 去重，记录出现在哪些 filter
            hashtags: dict[str, dict] = {}
            for filt, items in raw_by_filter.items():
                label = FILTER_LABELS.get(filt, filt)
                for item in items:
                    hid = str(item.get("hashtag_id", ""))
                    if not hid:
                        continue
                    if hid in hashtags:
                        hashtags[hid]["found_in_filters"].append(label)
                    else:
                        hashtags[hid] = {
                            "list_data": item,
                            "detail_data": None,
                            "creators_raw": [],
                            "found_in_filters": [label],
                            "crawled_at": crawl_ts,
                        }

            print(f"[TikTokFetcher] 去重后 {len(hashtags)} 个独立 hashtag")

            # Phase 2 — 详情
            if fetch_details and hashtags:
                names = [
                    h["list_data"].get("hashtag_name", "")
                    for h in hashtags.values()
                    if h["list_data"].get("hashtag_name")
                ]
                print(f"[TikTokFetcher] 详情抓取  {len(names)} 个")
                detail_map = self._fetch_details(ctx, names)

                # 按 hashtag_name 回写
                name_to_id = {
                    h["list_data"]["hashtag_name"]: hid
                    for hid, h in hashtags.items()
                }
                for name, (page_data, creators) in detail_map.items():
                    hid = name_to_id.get(name)
                    if hid and hid in hashtags:
                        hashtags[hid]["detail_data"] = page_data
                        hashtags[hid]["creators_raw"] = creators

            browser.close()

        meta = {
            "platform": "tiktok",
            "country": self.country,
            "period": self.period,
            "filters": filters,
            "sort_by": sort_by,
            "crawled_at": crawl_ts,
            "total": len(hashtags),
        }
        return {"meta": meta, "hashtags": hashtags}

    # ── Phase 1: 列表 ────────────────────────────────────

    def _fetch_lists(
        self, ctx: BrowserContext, *,
        filters: list[str], sort_by: str,
        page_size: int, max_pages: int,
    ) -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {}

        for filt in filters:
            items: list[dict] = []
            target_page = 1
            captured: list[dict] = []

            def route_handler(route: Route) -> None:
                new_url = _rewrite_list_url(
                    route.request.url,
                    page=target_page, limit=page_size,
                    period=self.period, country_code=self.country,
                    filter_by=filt, sort_by=sort_by,
                )
                route.continue_(url=new_url)

            def on_resp(resp: Response) -> None:
                if API_LIST_PATTERN not in resp.url:
                    return
                try:
                    body = resp.json()
                    if body.get("code") == 0 and body.get("data"):
                        captured.append(body)
                except Exception:
                    pass

            page = ctx.new_page()
            page.on("response", on_resp)
            page.route(f"**/{API_LIST_PATTERN}*", route_handler)

            for pg in range(1, max_pages + 1):
                target_page = pg
                captured.clear()

                nav_url = (
                    f"{LIST_PAGE_URL}?countryCode={self.country}"
                    f"&period={self.period}&_t={int(time.time())}"
                )
                try:
                    page.goto(nav_url, wait_until="domcontentloaded",
                              timeout=self.timeout_ms)
                    page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  [{filt or 'all'}] 页 {pg}: 导航失败 {e}")
                    break

                deadline = time.time() + 15
                while not captured and time.time() < deadline:
                    time.sleep(0.5)

                if captured:
                    data = captured[0]["data"]
                    page_items = data.get("list", [])
                    pagination = data.get("pagination", {})
                    items.extend(page_items)
                    total = pagination.get("total", "?")
                    has_more = pagination.get("has_more", False)
                    label = FILTER_LABELS.get(filt, filt) or "all"
                    print(f"  [{label}] 页 {pg}: {len(page_items)} 条 (总 {total})")
                    if not has_more:
                        break
                else:
                    print(f"  [{filt or 'all'}] 页 {pg}: 无数据")
                    break
                time.sleep(1.5)

            page.close()
            results[filt] = items

        return results

    # ── Phase 2: 详情 ────────────────────────────────────

    def _fetch_details(
        self, ctx: BrowserContext, names: list[str],
    ) -> dict[str, tuple[dict | None, list[dict]]]:
        """返回 {name: (page_data_raw, creators_raw)}"""
        result: dict[str, tuple[dict | None, list[dict]]] = {}
        total = len(names)

        page = ctx.new_page()
        try:
            page.goto(
                f"{LIST_PAGE_URL}?countryCode={self.country}&period={self.period}",
                wait_until="domcontentloaded", timeout=self.timeout_ms,
            )
            page.wait_for_timeout(3000)
        except Exception:
            pass

        for idx, name in enumerate(names, 1):
            print(f"  [{idx:3d}/{total}] #{name}", end="", flush=True)

            creators_data: list[dict] = []

            def _cap(resp: Response) -> None:
                if API_CREATOR_PATTERN not in resp.url:
                    return
                try:
                    body = resp.json()
                    if body.get("code") == 0:
                        creators_data.extend(
                            body.get("data", {}).get("creators", [])
                        )
                except Exception:
                    pass

            page.on("response", _cap)

            url = (
                DETAIL_PAGE_URL.format(hashtag=name)
                + f"?countryCode={self.country}&period={self.period}"
                + f"&_t={int(time.time())}"
            )
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=self.timeout_ms)
                time.sleep(4)
            except Exception as e:
                print(f"  ERR timeout, skipping")
                page.remove_listener("response", _cap)
                result[name] = (None, [])
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                time.sleep(2)
                continue

            page.remove_listener("response", _cap)

            page_data: dict | None = None
            try:
                raw = page.evaluate("""
                    () => {
                        const el = document.getElementById('__NEXT_DATA__');
                        return el ? el.textContent : null;
                    }
                """)
                if raw:
                    nd = json.loads(raw)
                    pd = nd.get("props", {}).get("pageProps", {}).get("data", {})
                    if pd and pd.get("hashtagName"):
                        page_data = pd
            except Exception:
                pass

            result[name] = (page_data, creators_data)
            ok = "OK" if page_data else "no data"
            print(f"  {ok}")
            time.sleep(1)

        page.close()
        return result
