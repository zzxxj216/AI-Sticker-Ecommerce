"""TikTok Creative Center — 热门 Hashtag 全量抓取（Playwright 版）

Phase 1: 抓取所有筛选类型（全部 / new_on_board）的 hashtag 列表
Phase 2: 逐个打开详情页，从 __NEXT_DATA__ 提取：
         - 兴趣随时间变化趋势 (trend)
         - 受众洞察：年龄 / 兴趣 / 地区 (audience*)
         - 相关标签 (relatedHashtags)
         - 相关创作者 (creators, via API)

用法:
  python -m scripts.tiktok_creative_radar_hashtags --country US --period 7
  python -m scripts.tiktok_creative_radar_hashtags --list-only
  python -m scripts.tiktok_creative_radar_hashtags --headed --out output/tiktok_full.json

依赖:
  pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import (
    sync_playwright, Response, Route, BrowserContext, Page,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

ALL_FILTERS = ["", "new_on_board"]
FILTER_LABELS = {
    "": "全部 (Top 100)",
    "new_on_board": "首次进入 Top 100",
}

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


# ── 工具 ─────────────────────────────────────────────────

def _ts_to_date(ts: int | float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _fmt_views(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


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


def _parse_list_item(item: dict) -> dict:
    trend = [
        {"date": _ts_to_date(p["time"]), "value": p["value"]}
        for p in item.get("trend", [])
    ]
    return {
        "hashtag_id": item.get("hashtag_id"),
        "hashtag_name": item.get("hashtag_name", ""),
        "rank": item.get("rank"),
        "publish_cnt": item.get("publish_cnt", 0),
        "video_views": item.get("video_views", 0),
        "is_promoted": item.get("is_promoted", False),
        "industry": (item.get("industry_info") or {}).get("value", ""),
        "trend": trend,
    }


def _parse_detail(data: dict) -> dict:
    """从 __NEXT_DATA__.props.pageProps.data 提取详情。"""

    # 趋势
    trend = [
        {"date": _ts_to_date(p["time"]), "value": p["value"]}
        for p in data.get("trend", [])
    ]

    # 受众 - 年龄
    audience_ages = []
    for a in data.get("audienceAges", []):
        audience_ages.append({
            "age_level": a.get("ageLevel", ""),
            "score": a.get("score", 0),
        })

    # 受众 - 兴趣
    audience_interests = []
    for a in data.get("audienceInterests", []):
        info = a.get("interestInfo", {})
        audience_interests.append({
            "interest": info.get("value", ""),
            "interest_id": info.get("id", ""),
            "score": a.get("score", 0),
        })

    # 受众 - 地区
    audience_countries = []
    for a in data.get("audienceCountries", []):
        info = a.get("countryInfo", {})
        audience_countries.append({
            "country": info.get("value", ""),
            "country_code": info.get("id", ""),
            "score": a.get("score", 0),
        })

    # 相关标签
    related_hashtags = []
    for h in data.get("relatedHashtags", []):
        related_hashtags.append({
            "hashtag_name": h.get("hashtagName", ""),
            "hashtag_id": h.get("hashtagId", ""),
            "video_url": h.get("videoUrl", ""),
        })

    # 推荐标签
    rec_list = []
    for h in data.get("recList", []):
        rec_list.append({
            "hashtag_name": h.get("hashtagName", ""),
            "hashtag_id": h.get("hashtagId", ""),
        })

    longevity = data.get("longevity", {})

    return {
        "description": data.get("description", ""),
        "trend": trend,
        "longevity": {
            "popular_days": longevity.get("popularDays", 0),
            "current_popularity": longevity.get("currentPopularity", 0),
        },
        "publish_cnt_all": data.get("publishCntAll", 0),
        "video_views_all": data.get("videoViewsAll", 0),
        "video_url": data.get("videoUrl", ""),
        "audience_ages": audience_ages,
        "audience_interests": audience_interests,
        "audience_countries": audience_countries,
        "related_hashtags": related_hashtags,
        "recommended": rec_list,
    }


def _parse_creator(c: dict) -> dict:
    return {
        "nick_name": c.get("nick_name", ""),
        "avatar_url": c.get("avatar_url", ""),
        "follower_cnt": c.get("follower_cnt", 0),
        "liked_cnt": c.get("liked_cnt", 0),
        "user_url": c.get("tt_link", ""),
    }


# ── Phase 1: 列表抓取 ────────────────────────────────────

def fetch_all_hashtags(
    ctx: BrowserContext,
    *,
    country: str,
    period: int,
    filters: list[str],
    sort_by: str,
    page_size: int,
    max_pages: int,
    timeout_ms: int,
) -> dict[str, list[dict]]:
    """对每个 filter_by 抓取全部分页。返回 {filter: [items]}。"""

    results: dict[str, list[dict]] = {}

    for filt in filters:
        label = FILTER_LABELS.get(filt, filt) or "全部"
        print(f"\n  ── {label} ──")
        items: list[dict] = []
        target_page = 1
        captured: list[dict] = []

        def route_handler(route: Route) -> None:
            new_url = _rewrite_list_url(
                route.request.url,
                page=target_page, limit=page_size,
                period=period, country_code=country,
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
                f"{LIST_PAGE_URL}?countryCode={country}"
                f"&period={period}&_t={int(time.time())}"
            )
            page.goto(nav_url, wait_until="networkidle", timeout=timeout_ms)

            deadline = time.time() + 10
            while not captured and time.time() < deadline:
                time.sleep(0.5)

            if captured:
                data = captured[0]["data"]
                page_items = data.get("list", [])
                pagination = data.get("pagination", {})
                items.extend(page_items)
                total = pagination.get("total", "?")
                has_more = pagination.get("has_more", False)
                print(f"    页 {pg}: {len(page_items)} 条  (总 {total})")
                if not has_more:
                    break
            else:
                print(f"    页 {pg}: 无数据")
                break
            time.sleep(1.5)

        page.close()
        results[filt] = items
        print(f"  ── {label} 完成: {len(items)} 条 ──")

    return results


# ── Phase 2: 详情页 (__NEXT_DATA__ + creator API) ─────────

def fetch_hashtag_details(
    ctx: BrowserContext,
    hashtag_names: list[str],
    *,
    country: str,
    period: int,
    timeout_ms: int,
) -> dict[str, dict]:
    """逐个打开详情页，从 __NEXT_DATA__ 提取全部分析数据。"""

    all_details: dict[str, dict] = {}
    total = len(hashtag_names)

    page = ctx.new_page()

    # 先访问列表页建立 session
    page.goto(
        f"{LIST_PAGE_URL}?countryCode={country}&period={period}",
        wait_until="networkidle", timeout=timeout_ms,
    )
    time.sleep(1)

    for idx, name in enumerate(hashtag_names, 1):
        print(f"  [{idx:3d}/{total}] #{name:<30s}", end="", flush=True)

        # 捕获 creator API
        creators_data: list[dict] = []

        def _capture_creator(resp: Response) -> None:
            if API_CREATOR_PATTERN not in resp.url:
                return
            try:
                body = resp.json()
                if body.get("code") == 0:
                    creators_data.extend(body["data"].get("creators", []))
            except Exception:
                pass

        page.on("response", _capture_creator)

        detail_url = (
            DETAIL_PAGE_URL.format(hashtag=name)
            + f"?countryCode={country}&period={period}"
            + f"&_t={int(time.time())}"
        )

        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=timeout_ms)
            time.sleep(4)
        except Exception as e:
            print(f"  ERR: {e}")
            page.remove_listener("response", _capture_creator)
            continue

        page.remove_listener("response", _capture_creator)

        # 提取 __NEXT_DATA__
        detail_result: dict = {}
        try:
            next_data_raw = page.evaluate("""
                () => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }
            """)
            if next_data_raw:
                nd = json.loads(next_data_raw)
                page_data = nd.get("props", {}).get("pageProps", {}).get("data", {})
                if page_data and page_data.get("hashtagName"):
                    detail_result = _parse_detail(page_data)
        except Exception:
            pass

        # 合并创作者
        detail_result["creators"] = [_parse_creator(c) for c in creators_data]

        all_details[name] = detail_result

        # 进度显示
        n_ages = len(detail_result.get("audience_ages", []))
        n_interests = len(detail_result.get("audience_interests", []))
        n_countries = len(detail_result.get("audience_countries", []))
        n_related = len(detail_result.get("related_hashtags", []))
        n_creators = len(detail_result.get("creators", []))
        has_trend = bool(detail_result.get("trend"))

        parts = []
        if has_trend:
            parts.append("trend")
        if n_ages:
            parts.append(f"{n_ages} ages")
        if n_interests:
            parts.append(f"{n_interests} interests")
        if n_countries:
            parts.append(f"{n_countries} regions")
        if n_related:
            parts.append(f"{n_related} related")
        if n_creators:
            parts.append(f"{n_creators} creators")

        status = ", ".join(parts) if parts else "no data"
        print(f"  [{status}]")

        time.sleep(1)

    page.close()
    return all_details


# ── 主流程 ────────────────────────────────────────────────

def run(
    *,
    country: str = "US",
    period: int = 7,
    filters: list[str] | None = None,
    sort_by: str = "popular",
    page_size: int = 50,
    max_pages: int = 10,
    list_only: bool = False,
    headed: bool = False,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    if filters is None:
        filters = list(ALL_FILTERS)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            user_agent=BROWSER_UA,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
        )

        # ── Phase 1 ──
        print(f"{'='*60}")
        print(f"  Phase 1: 列表抓取  |  {country}  |  {period}d")
        print(f"{'='*60}")

        raw_by_filter = fetch_all_hashtags(
            ctx,
            country=country, period=period, filters=filters,
            sort_by=sort_by, page_size=page_size, max_pages=max_pages,
            timeout_ms=timeout_ms,
        )

        # 合并去重
        seen_ids: set[str] = set()
        all_items: list[dict] = []
        item_filter_map: dict[str, list[str]] = {}

        for filt, items in raw_by_filter.items():
            for item in items:
                hid = item.get("hashtag_id", "")
                name = item.get("hashtag_name", "")
                if hid not in seen_ids:
                    seen_ids.add(hid)
                    all_items.append(item)
                    item_filter_map[name] = [FILTER_LABELS.get(filt, filt)]
                else:
                    if name in item_filter_map:
                        item_filter_map[name].append(FILTER_LABELS.get(filt, filt))

        parsed_items = [_parse_list_item(it) for it in all_items]
        for pi in parsed_items:
            pi["found_in_filters"] = item_filter_map.get(pi["hashtag_name"], [])

        print(f"\n  Phase 1 汇总: {len(parsed_items)} 个独立 hashtag")

        # ── Phase 2 ──
        details: dict[str, dict] = {}
        if not list_only:
            names = [it["hashtag_name"] for it in parsed_items if it["hashtag_name"]]
            print(f"\n{'='*60}")
            print(f"  Phase 2: 详情分析  |  {len(names)} 个 hashtag")
            print(f"{'='*60}")

            details = fetch_hashtag_details(
                ctx, names,
                country=country, period=period,
                timeout_ms=timeout_ms,
            )

            ok = sum(1 for d in details.values() if d.get("trend"))
            print(f"\n  Phase 2 完成: {ok}/{len(details)} 个有完整数据")

        browser.close()

    # 合并
    for pi in parsed_items:
        name = pi["hashtag_name"]
        if name in details:
            pi["detail"] = details[name]

    return {
        "platform": "tiktok",
        "data_type": "trending_hashtags_full",
        "country": country,
        "period_days": period,
        "filters": [FILTER_LABELS.get(f, f) for f in filters],
        "sort_by": sort_by,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "total": len(parsed_items),
        "by_filter": {
            FILTER_LABELS.get(f, f): len(v) for f, v in raw_by_filter.items()
        },
        "hashtags": parsed_items,
    }


# ── CLI ───────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="TikTok Creative Center 热门 hashtag 全量抓取 + 详情分析"
    )
    p.add_argument("--country", default="US")
    p.add_argument("--period", type=int, default=7)
    p.add_argument("--filters", nargs="+", default=None,
                   help="筛选类型: '' (全部), 'new_on_board' (新进榜)")
    p.add_argument("--sort-by", default="popular")
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--max-pages", type=int, default=10)
    p.add_argument("--list-only", action="store_true", help="仅抓列表，跳过详情")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--out", type=str, default=None)
    p.add_argument("--timeout", type=int, default=45000)
    args = p.parse_args()

    result = run(
        country=args.country,
        period=args.period,
        filters=args.filters,
        sort_by=args.sort_by,
        page_size=args.page_size,
        max_pages=args.max_pages,
        list_only=args.list_only,
        headed=args.headed,
        timeout_ms=args.timeout,
    )

    # 保存
    json_str = json.dumps(result, ensure_ascii=False, indent=2)
    out_path = Path(args.out) if args.out else Path(
        f"output/tiktok_hashtags_{args.country}_{args.period}d.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json_str, encoding="utf-8")

    # 摘要
    print(f"\n{'='*75}")
    print(f"  TikTok 热门 Hashtag 完整报告  |  {args.country}  |  近 {args.period} 天")
    print(f"  总计: {result['total']} 条  |  保存: {out_path}")
    print(f"{'='*75}")

    for i, h in enumerate(result["hashtags"][:20], 1):
        industry = f" [{h['industry']}]" if h.get("industry") else ""
        detail = h.get("detail", {})

        extra_parts = []
        if detail.get("audience_interests"):
            top_interest = detail["audience_interests"][0]["interest"]
            extra_parts.append(f"兴趣:{top_interest}")
        if detail.get("audience_ages"):
            top_age = detail["audience_ages"][0]
            extra_parts.append(f"年龄:{top_age['age_level']}")
        if detail.get("audience_countries"):
            top_country = detail["audience_countries"][0]["country"]
            extra_parts.append(f"地区:{top_country}")
        if detail.get("related_hashtags"):
            rh = [r["hashtag_name"] for r in detail["related_hashtags"][:3]]
            extra_parts.append(f"相关:{'|'.join(rh)}")

        extra = f"  ({', '.join(extra_parts)})" if extra_parts else ""

        print(
            f"  {i:3d}. #{h['hashtag_name']:<28s}"
            f"  {_fmt_views(h['video_views']):>8s}"
            f"  {h['publish_cnt']:>7,}帖{industry}{extra}"
        )


if __name__ == "__main__":
    main()
