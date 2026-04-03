"""
AI Sticker Trend Fetcher - 主程序入口
每日自动获取海外热点，经过贴纸机会管线筛选生成 brief

用法:
    python main.py                        # 热点抓取 + 贴纸机会管线（默认）
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from config import config
    from fetchers import GoogleTrendsFetcher, RedditFetcher, RssFetcher, NewsApiFetcher
except ImportError:
    from trend_fetcher.config import config
    from trend_fetcher.fetchers import GoogleTrendsFetcher, RedditFetcher, RssFetcher, NewsApiFetcher


def fetch_raw_data() -> dict[str, list]:
    """并发抓取所有数据源，返回 {source_name: [items]}"""
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


def save_raw_results(raw_data: dict) -> Path:
    """保存原始抓取结果"""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = config.OUTPUT_DIR / f"trends_{timestamp}.json"

    result = {
        "fetch_time": datetime.now().isoformat(),
        "sources": {k: len(v) for k, v in raw_data.items()},
        "total_items": sum(len(v) for v in raw_data.values()),
        "raw_data": raw_data,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    latest_file = config.OUTPUT_DIR / "latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return output_file


def run_sticker_pipeline(fetch_first: bool = True) -> dict:
    """
    贴纸机会管线：
      fetch_first=True  → 先抓取, 再跑 sticker pipeline
      fetch_first=False → 直接读 latest.json 跑 sticker pipeline
    """
    from sticker_pipeline import StickerOpportunityPipeline

    if fetch_first:
        print("=" * 60)
        print(f"  AI Sticker 热点雷达 + 贴纸管线  |  "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        raw_data = fetch_raw_data()
        save_raw_results(raw_data)

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


def main():
    parser = argparse.ArgumentParser(
        description="AI Sticker 热点雷达 - 每日获取海外热点并生成贴纸选题"
    )
    parser.add_argument(
        "--sticker-only", action="store_true",
        help="仅跑贴纸机会管线（读取已有的 latest.json）"
    )
    args = parser.parse_args()

    if args.sticker_only:
        run_sticker_pipeline(fetch_first=False)
    else:
        run_sticker_pipeline(fetch_first=True)


if __name__ == "__main__":
    main()
