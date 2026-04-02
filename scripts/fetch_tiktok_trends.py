"""TikTok 热点话题全流程 CLI

子命令:
  fetch     抓取热点话题，自动去重入库
  review    审核未审核话题（AI → Topic Review）
  brief     为 Approved 话题生成 Brief
  pipeline  全流程: fetch → review → brief
  status    查看数据库状态
  history   查看爬取历史

用法:
  # 首次全量抓取 + 入库
  python -m scripts.fetch_tiktok_trends fetch

  # 审核未处理话题
  python -m scripts.fetch_tiktok_trends review

  # 为通过的话题生成 Brief
  python -m scripts.fetch_tiktok_trends brief

  # 全流程（抓取 → 审核 → Brief）
  python -m scripts.fetch_tiktok_trends pipeline

  # 查看数据库状态
  python -m scripts.fetch_tiktok_trends status

  # 查看爬取历史
  python -m scripts.fetch_tiktok_trends history
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trend_fetcher.fetchers.tiktok import TikTokFetcher
from trend_fetcher.trend_db import TrendDB
from trend_fetcher.topic_pipeline import TopicPipeline

DEFAULT_DB = "data/tiktok_trends.db"


def _fmt(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


# ── fetch ────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)
    existing = db.total()

    print(f"{'='*60}")
    print(f"  TikTok 热点抓取  |  {args.country}  |  {args.period}d")
    print(f"  数据库: {args.db}  (现有 {existing} 条)")
    print(f"{'='*60}")

    fetcher = TikTokFetcher(
        country=args.country,
        period=args.period,
        headed=args.headed,
        timeout_ms=args.timeout,
    )
    crawl_result = fetcher.fetch(
        sort_by=args.sort_by,
        page_size=args.page_size,
        max_pages=args.max_pages,
        fetch_details=not args.list_only,
    )

    stats = db.upsert_crawl(crawl_result)

    print(f"\n{'='*60}")
    print(f"  抓取完成")
    print(f"{'='*60}")
    print(f"  本次抓取:    {crawl_result['meta']['total']} 个 hashtag")
    print(f"  新增入库:    {stats['new']}")
    print(f"  重复跳过:    {stats['duplicate']}")
    print(f"  数据库总量:  {stats['total']}")
    print(f"{'='*60}")

    db.close()


# ── review ───────────────────────────────────────────────

def cmd_review(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)
    pipeline = TopicPipeline(
        db,
        batch_size=args.batch_size,
        model=args.model,
        temperature=args.temperature,
    )
    pipeline.review_new_topics()
    db.close()


# ── brief ────────────────────────────────────────────────

def cmd_brief(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)
    pipeline = TopicPipeline(
        db,
        batch_size=args.batch_size,
        model=args.model,
        temperature=args.temperature,
    )
    pipeline.generate_briefs()
    db.close()


# ── pipeline ─────────────────────────────────────────────

def cmd_pipeline(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)

    # Step 1: fetch
    print(f"\n{'='*60}")
    print(f"  Step 1: 抓取热点  |  {args.country}  |  {args.period}d")
    print(f"{'='*60}")

    fetcher = TikTokFetcher(
        country=args.country,
        period=args.period,
        headed=args.headed,
        timeout_ms=args.timeout,
    )
    crawl_result = fetcher.fetch(
        sort_by=args.sort_by,
        page_size=args.page_size,
        max_pages=args.max_pages,
        fetch_details=not args.list_only,
    )
    stats = db.upsert_crawl(crawl_result)
    print(f"  新增 {stats['new']}  重复 {stats['duplicate']}  总计 {stats['total']}")

    # Step 2: review + brief
    pipeline = TopicPipeline(
        db,
        batch_size=args.batch_size,
        model=args.model,
        temperature=args.temperature,
    )
    pipeline.run_full()

    # 最终报告
    summary = db.status_summary()
    print(f"\n{'='*60}")
    print(f"  全流程完成")
    print(f"{'='*60}")
    print(f"  数据库总量:     {summary['total_hashtags']}")
    print(f"  已审核:         {summary['reviewed']}")
    print(f"  Approve:        {summary['approve']}")
    print(f"  Watchlist:      {summary['watchlist']}")
    print(f"  Reject:         {summary['reject']}")
    print(f"  Brief 已生成:   {summary['briefs_generated']}")
    print(f"  Brief 待生成:   {summary['briefs_pending']}")
    print(f"{'='*60}")

    db.close()


# ── status ───────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)
    s = db.status_summary()

    if s["total_hashtags"] == 0:
        print(f"数据库为空: {args.db}")
        print("运行 python -m scripts.fetch_tiktok_trends fetch 开始首次抓取")
        db.close()
        return

    print(f"{'='*60}")
    print(f"  TikTok 热点数据库  |  {args.db}")
    print(f"{'='*60}")
    print(f"  Hashtag 总量:     {s['total_hashtags']}")
    print(f"  有详情数据:       {s['with_detail']}")
    print(f"  待审核:           {s['pending_review']}")
    print(f"  已审核:           {s['reviewed']}")
    print(f"    ├ Approve:      {s['approve']}")
    print(f"    ├ Watchlist:    {s['watchlist']}")
    print(f"    └ Reject:       {s['reject']}")
    print(f"  Brief 已生成:     {s['briefs_generated']}")
    print(f"  Brief 待生成:     {s['briefs_pending']}")
    print(f"  历史爬取次数:     {s['total_crawls']}")
    print(f"{'='*60}")

    # Top hashtags
    items = db.list_hashtags(limit=15)
    if items:
        print(f"\n  Top 15 Hashtag:")
        for i, h in enumerate(items, 1):
            review = "·"
            if h["review_status"] == "reviewed":
                review = "R"
            brief = "·"
            if h["brief_status"] == "generated":
                brief = "B"
            print(
                f"  {i:3d}. [{review}{brief}] #{h['hashtag_name']:<28s}"
                f"  播放 {_fmt(h['video_views']):>8s}"
            )
        print(f"\n  标记: R=已审核  B=有Brief  ·=待处理")

    db.close()


# ── history ──────────────────────────────────────────────

def cmd_history(args: argparse.Namespace) -> None:
    db = TrendDB(args.db)
    logs = db.crawl_history()

    if not logs:
        print("暂无爬取记录")
        db.close()
        return

    print(f"{'='*60}")
    print(f"  爬取历史  |  共 {len(logs)} 次")
    print(f"{'='*60}")
    for log in logs[-15:]:
        ts = log["crawled_at"][:19].replace("T", " ")
        country = log.get("country", "?")
        new = log.get("new_count", 0)
        dup = log.get("dup_count", 0)
        total = log.get("total_after", 0)
        print(f"  {ts}  {country}  新增 {new:>3d}  重复 {dup:>3d}  总计 {total:>4d}")

    db.close()


# ── CLI 主入口 ────────────────────────────────────────────

def main() -> None:
    root = argparse.ArgumentParser(
        description="TikTok 热点话题全流程（抓取 → 审核 → Brief）"
    )
    sub = root.add_subparsers(dest="command")

    # 公共参数: --db
    db_kw = dict(default=DEFAULT_DB, help=f"数据库路径 (默认: {DEFAULT_DB})")

    # ── fetch
    p_fetch = sub.add_parser("fetch", help="抓取热点话题入库")
    p_fetch.add_argument("--db", **db_kw)
    p_fetch.add_argument("--country", default="US")
    p_fetch.add_argument("--period", type=int, default=7)
    p_fetch.add_argument("--sort-by", default="popular")
    p_fetch.add_argument("--page-size", type=int, default=50)
    p_fetch.add_argument("--max-pages", type=int, default=10)
    p_fetch.add_argument("--list-only", action="store_true",
                         help="仅抓列表，跳过详情页")
    p_fetch.add_argument("--headed", action="store_true")
    p_fetch.add_argument("--timeout", type=int, default=45000)

    # ── review
    p_review = sub.add_parser("review", help="审核未处理话题")
    p_review.add_argument("--db", **db_kw)
    p_review.add_argument("--batch-size", type=int, default=10)
    p_review.add_argument("--model", default=None)
    p_review.add_argument("--temperature", type=float, default=0.5)

    # ── brief
    p_brief = sub.add_parser("brief", help="生成 Approved 话题的 Brief")
    p_brief.add_argument("--db", **db_kw)
    p_brief.add_argument("--batch-size", type=int, default=10)
    p_brief.add_argument("--model", default=None)
    p_brief.add_argument("--temperature", type=float, default=0.5)

    # ── pipeline
    p_pipe = sub.add_parser("pipeline", help="全流程: fetch → review → brief")
    p_pipe.add_argument("--db", **db_kw)
    p_pipe.add_argument("--country", default="US")
    p_pipe.add_argument("--period", type=int, default=7)
    p_pipe.add_argument("--sort-by", default="popular")
    p_pipe.add_argument("--page-size", type=int, default=50)
    p_pipe.add_argument("--max-pages", type=int, default=10)
    p_pipe.add_argument("--list-only", action="store_true")
    p_pipe.add_argument("--headed", action="store_true")
    p_pipe.add_argument("--timeout", type=int, default=45000)
    p_pipe.add_argument("--batch-size", type=int, default=10)
    p_pipe.add_argument("--model", default=None)
    p_pipe.add_argument("--temperature", type=float, default=0.5)

    # ── status / history
    p_status = sub.add_parser("status", help="查看数据库状态")
    p_status.add_argument("--db", **db_kw)
    p_history = sub.add_parser("history", help="查看爬取历史")
    p_history.add_argument("--db", **db_kw)

    args = root.parse_args()

    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "brief":
        cmd_brief(args)
    elif args.command == "pipeline":
        cmd_pipeline(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "history":
        cmd_history(args)
    else:
        root.print_help()


if __name__ == "__main__":
    main()
