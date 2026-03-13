"""
RSS Feed 聚合模块 - 完全免费，无需任何 API Key
覆盖主流英文媒体 + 特定贴纸相关兴趣社区
"""
import feedparser
from datetime import datetime
from email.utils import parsedate_to_datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config


class RssFetcher:
    SOURCE_NAME = "RSS"

    # 扩展的贴纸业务相关 RSS 源
    EXTENDED_FEEDS: dict = {
        # 主流新闻（了解时事热点）
        "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
        "CNN Top Stories": "http://rss.cnn.com/rss/edition.rss",
        "The Guardian US": "https://www.theguardian.com/us-news/rss",
        "NPR News": "https://feeds.npr.org/1001/rss.xml",

        # 流行文化（贴纸核心选题）
        "BuzzFeed": "https://www.buzzfeed.com/index.xml",
        "Mashable": "https://mashable.com/feeds/rss/all",

        # 设计与艺术（贴纸设计灵感）
        "Design Milk": "https://design-milk.com/feed/",
        "It's Nice That": "https://www.itsnicethat.com/rss",
        "Creative Boom": "https://www.creativeboom.com/feed/",

        # 电商与产品趋势
        "Product Hunt": "https://www.producthunt.com/feed",
        "TechCrunch": "https://techcrunch.com/feed/",

        # Reddit（作为 RSS 补充）
        "Reddit r/popular": "https://www.reddit.com/r/popular/.rss",
        "Reddit r/memes": "https://www.reddit.com/r/memes/.rss",
        "Reddit r/aww": "https://www.reddit.com/r/aww/.rss",

        # Google Trends
        "Google Trends US": f"https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
    }

    def fetch(self) -> list[dict]:
        """获取所有 RSS 源的热点内容"""
        print(f"  [RSS] 开始获取 {len(self.EXTENDED_FEEDS)} 个来源...")
        all_items = []

        for feed_name, feed_url in self.EXTENDED_FEEDS.items():
            items = self._parse_feed(feed_name, feed_url)
            all_items.extend(items)
            if items:
                print(f"  [RSS] {feed_name}: {len(items)} 条")

        print(f"  [RSS] 共获取 {len(all_items)} 条")
        return all_items

    def _parse_feed(self, feed_name: str, feed_url: str, limit: int = 8) -> list[dict]:
        """解析单个 RSS Feed"""
        items = []
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                return items

            for entry in feed.entries[:limit]:
                title = entry.get("title", "").strip()
                if not title or len(title) < 5:
                    continue

                # 尝试解析发布时间
                pub_date = None
                if hasattr(entry, "published"):
                    try:
                        pub_date = parsedate_to_datetime(entry.published).isoformat()
                    except Exception:
                        pub_date = entry.published

                # 提取摘要
                summary = ""
                if hasattr(entry, "summary"):
                    import re
                    summary = re.sub(r"<[^>]+>", "", entry.summary)[:200].strip()

                items.append({
                    "source": self.SOURCE_NAME,
                    "feed_name": feed_name,
                    "keyword": title,
                    "summary": summary,
                    "url": entry.get("link", ""),
                    "published_at": pub_date,
                    "fetched_at": datetime.now().isoformat(),
                    "method": "rss",
                })

        except Exception as e:
            print(f"  [RSS] {feed_name} 解析失败: {e}")

        return items
