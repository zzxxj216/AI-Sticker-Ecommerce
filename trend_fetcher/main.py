"""
AI Sticker Trend Fetcher - 主程序入口
每日自动获取海外热点，聚合分析，生成贴纸选题和图片

用法:
    python main.py                        # 完整流程（热点+分析+生成图片）
    python main.py --schedule             # 启动定时调度（每日7点执行）
    python main.py --no-ai                # 跳过 Claude 分析和图片生成
    python main.py --no-image             # 只分析选题，不生成图片
    python main.py --sticker-pipeline     # 热点抓取 + 贴纸机会管线（推荐）
    python main.py --sticker-only         # 仅跑贴纸机会管线（使用 latest.json）
"""
import argparse
import json
import sys
import os
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_tf_dir = os.path.dirname(os.path.abspath(__file__))
if _tf_dir not in sys.path:
    sys.path.insert(0, _tf_dir)

# 修复 Windows 终端 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from config import config
    from fetchers import GoogleTrendsFetcher, RedditFetcher, RssFetcher, NewsApiFetcher
    from aggregator import TrendAggregator
    from analyzer import StickerIdeaAnalyzer
    from image_generator import StickerImageGenerator
except ImportError:
    # 尝试作为模块运行时的绝对路径导入
    from trend_fetcher.config import config
    from trend_fetcher.fetchers import GoogleTrendsFetcher, RedditFetcher, RssFetcher, NewsApiFetcher
    from trend_fetcher.aggregator import TrendAggregator
    from trend_fetcher.analyzer import StickerIdeaAnalyzer
    from trend_fetcher.image_generator import StickerImageGenerator


def fetch_raw_data() -> dict[str, list]:
    """Step 1: 并发抓取所有数据源，返回 {source_name: [items]}"""
    print("\n[Step 1] 并发抓取热点数据...")

    fetchers = {
        "google_trends": GoogleTrendsFetcher(),
        "reddit": RedditFetcher(),
        "rss": RssFetcher(),
        "newsapi": NewsApiFetcher(),
    }

    raw_data = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(fetcher.fetch): name
            for name, fetcher in fetchers.items()
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                raw_data[name] = future.result()
            except Exception as e:
                print(f"  [{name}] 抓取异常: {e}")
                raw_data[name] = []

    total_raw = sum(len(v) for v in raw_data.values())
    print(f"\n[Step 1 完成] 共获取 {total_raw} 条原始数据")
    for name, items in raw_data.items():
        print(f"  - {name}: {len(items)} 条")
    return raw_data


def run_fetch(use_ai: bool = True, use_image: bool = True) -> dict:
    """执行完整的热点获取 + 聚合 + 分析流程（原有模式）"""
    start_time = time.time()

    print("=" * 60)
    print(f"  AI Sticker 热点雷达  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ---- Step 1: 并发抓取 ----
    raw_data = fetch_raw_data()

    # ---- Step 2: 聚合 ----
    print("\n[Step 2] 聚合去重分析...")
    aggregator = TrendAggregator()
    result = aggregator.aggregate(raw_data)

    # ---- Step 3: Claude AI 分析（可选）----
    if use_ai:
        print("\n[Step 3] Claude AI 分析贴纸选题...")
        analyzer = StickerIdeaAnalyzer()
        ai_result = analyzer.analyze(result)
        result["ai_analysis"] = ai_result
    else:
        print("\n[Step 3] 跳过 AI 分析（--no-ai 模式）")
        result["ai_analysis"] = None

    # ---- Step 4: Nano Banana 生成贴纸图片（可选）----
    result["image_results"] = []
    if use_ai and use_image and result.get("ai_analysis"):
        sticker_ideas = result["ai_analysis"].get("sticker_ideas", [])
        if sticker_ideas:
            print(f"\n[Step 4] Nano Banana 生成贴纸图片（{len(sticker_ideas)} 张）...")
            try:
                generator = StickerImageGenerator()
                image_results = generator.generate_batch(sticker_ideas)
                result["image_results"] = image_results
            except Exception as e:
                print(f"  [Step 4] 图片生成失败: {e}")
        else:
            print("\n[Step 4] 无选题数据，跳过图片生成")
    elif not use_image:
        print("\n[Step 4] 跳过图片生成（--no-image 模式）")
    elif not use_ai:
        print("\n[Step 4] 跳过图片生成（AI 分析已关闭）")

    # ---- Step 5: 保存结果 ----
    elapsed = time.time() - start_time
    result["elapsed_seconds"] = round(elapsed, 2)

    output_path = save_results(result)

    # ---- 打印摘要 ----
    print_summary(result, output_path)

    return result


def run_sticker_pipeline(fetch_first: bool = True) -> dict:
    """
    贴纸机会管线模式：
      fetch_first=True  → 先抓取+聚合, 再跑 sticker pipeline
      fetch_first=False → 直接读 latest.json 跑 sticker pipeline
    """
    from sticker_pipeline import StickerOpportunityPipeline

    if fetch_first:
        print("=" * 60)
        print(f"  AI Sticker 热点雷达 + 贴纸管线  |  "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        raw_data = fetch_raw_data()

        print("\n[Step 2] 聚合去重分析...")
        aggregator = TrendAggregator()
        result = aggregator.aggregate(raw_data)

        elapsed = time.time()
        result["elapsed_seconds"] = 0
        save_results(result)
        print_summary(result, config.OUTPUT_DIR / "latest.json")

        all_raw_items = []
        for items in raw_data.values():
            all_raw_items.extend(items)

        pipeline = StickerOpportunityPipeline()
        return pipeline.run(all_raw_items)
    else:
        print("=" * 60)
        print(f"  Sticker Opportunity Pipeline (from latest.json)  |  "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        pipeline = StickerOpportunityPipeline()
        return pipeline.run_from_latest()


def save_results(result: dict) -> Path:
    """保存结果到 JSON 文件"""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = config.OUTPUT_DIR / f"trends_{timestamp}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 同时保存一份 latest.json（方便后续程序读取）
    latest_file = config.OUTPUT_DIR / "latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return output_file


def print_summary(result: dict, output_path: Path):
    """在终端打印美观的摘要报告"""
    summary = result.get("summary", {})
    top_trends = result.get("top_trends", [])
    cross_source = result.get("cross_source_trends", [])
    ai_analysis = result.get("ai_analysis")

    print("\n" + "=" * 60)
    print("  热点摘要报告")
    print("=" * 60)

    print(f"\n[统计] 数据统计:")
    print(f"   原始条目: {summary.get('total_raw_items', 0)}")
    print(f"   过滤后:   {summary.get('after_filter', 0)}")
    print(f"   独立热点: {summary.get('unique_trends', 0)}")
    print(f"   跨源共现: {summary.get('cross_source_trends', 0)}")

    print(f"\n[TOP] 综合热点 Top 10:")
    for i, trend in enumerate(top_trends[:10], 1):
        sources = ", ".join(trend.get("sources", []))
        traffic = trend.get("google_traffic", "")
        reddit = trend.get("top_reddit_score", 0)

        info_parts = [f"评分:{trend['score']}"]
        if traffic:
            info_parts.append(f"搜索量:{traffic}")
        if reddit > 0:
            info_parts.append(f"Reddit:{reddit:,}赞")

        print(f"   {i:2d}. {trend['keyword'][:50]}")
        print(f"       [{sources}] {' | '.join(info_parts)}")

    if cross_source:
        print(f"\n[CROSS] 多平台共现热点（最可信信号）:")
        for trend in cross_source[:8]:
            sources = " + ".join(trend.get("sources", []))
            print(f"   >> {trend['keyword'][:50]} [{sources}]")

    if ai_analysis:
        print(f"\n[AI] Claude 贴纸选题分析:")
        print("-" * 40)
        ideas = ai_analysis.get("sticker_ideas", [])
        for idea in ideas:
            title = idea.get("title", "未知选题")
            prompt_preview = idea.get("image_prompt", "")[:60]
            print(f"  [OK] {title}")
            if prompt_preview:
                print(f"       提示词: {prompt_preview}...")

    image_results = result.get("image_results", [])
    if image_results:
        success = [r for r in image_results if r.get("success")]
        fail = [r for r in image_results if not r.get("success")]
        print(f"\n[图片] 生成结果: {len(success)} 成功 / {len(fail)} 失败")
        for r in image_results:
            status = "[OK]" if r.get("success") else "[X]"
            name = r.get("filename") or r.get("error", "未知错误")
            elapsed_s = r.get("elapsed", 0)
            print(f"  {status} {r.get('title','')[:30]} -> {name} ({elapsed_s}s)")

    elapsed = result.get("elapsed_seconds", 0)
    print(f"\n[完成] 总耗时 {elapsed:.1f}s | 结果已保存: {output_path}")
    if image_results:
        today = datetime.now().strftime("%Y%m%d")
        print(f"[图片] 保存目录: {config.IMAGE_OUTPUT_DIR / today}")
    print("=" * 60)


def run_scheduler():
    """定时任务调度器 - 每日7点执行"""
    try:
        import schedule
    except ImportError:
        print("请先安装 schedule: pip install schedule")
        sys.exit(1)

    print(f"[调度器] 已启动，每日 07:00 自动执行")
    print(f"[调度器] 按 Ctrl+C 停止\n")

    schedule.every().day.at("07:00").do(run_fetch)

    # 启动后立即执行一次
    run_fetch(use_ai=True, use_image=True)

    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(
        description="AI Sticker 热点雷达 - 每日获取海外热点并分析贴纸选题"
    )
    parser.add_argument(
        "--schedule", action="store_true",
        help="启动定时调度模式（每日07:00自动执行）"
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="跳过 Claude AI 分析和图片生成"
    )
    parser.add_argument(
        "--no-image", action="store_true",
        help="只做 Claude 选题分析，不调用 Nano Banana 生成图片"
    )
    parser.add_argument(
        "--sticker-pipeline", action="store_true",
        help="热点抓取 + 贴纸机会管线（抓取→聚合→主题抽象→评分→brief）"
    )
    parser.add_argument(
        "--sticker-only", action="store_true",
        help="仅跑贴纸机会管线（读取已有的 latest.json）"
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduler()
    elif args.sticker_pipeline:
        run_sticker_pipeline(fetch_first=True)
    elif args.sticker_only:
        run_sticker_pipeline(fetch_first=False)
    else:
        run_fetch(
            use_ai=not args.no_ai,
            use_image=not args.no_image and not args.no_ai,
        )


if __name__ == "__main__":
    main()
