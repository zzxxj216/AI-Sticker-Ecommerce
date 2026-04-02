"""
NewsAPI 数据获取模块
免费账户: 100次/天，获取结构化新闻热点
注册: https://newsapi.org/register
"""
import requests
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config


class NewsApiFetcher:
    SOURCE_NAME = "NewsAPI"
    BASE_URL = "https://newsapi.org/v2"
    MAX_ITEMS = 50

    CATEGORIES = ["entertainment", "technology", "sports"]

    STICKER_KEYWORDS = [
        "viral trend",
        "meme",
        "cute animal",
        "pop culture",
        "new album music",
    ]

    def fetch(self) -> list[dict]:
        """获取 NewsAPI 热点新闻，总量上限 MAX_ITEMS 条"""
        if not config.NEWS_API_KEY:
            print(f"  [NewsAPI] 未配置 API Key，跳过")
            return []

        print(f"  [NewsAPI] 开始获取（上限 {self.MAX_ITEMS} 条）...")
        all_items = []

        # 1. 获取今日头条
        top_items = self._fetch_top_headlines(page_size=15)
        all_items.extend(top_items)
        print(f"  [NewsAPI] 头条: {len(top_items)} 条")

        # 2. 按娱乐/科技/体育类别获取
        for category in self.CATEGORIES:
            if len(all_items) >= self.MAX_ITEMS:
                break
            cat_items = self._fetch_by_category(category, page_size=5)
            all_items.extend(cat_items)
        cat_count = len(all_items) - len(top_items)
        print(f"  [NewsAPI] 类别新闻: {cat_count} 条")

        # 3. 贴纸专项关键词搜索
        kw_items = self._fetch_sticker_keywords()
        all_items.extend(kw_items)
        print(f"  [NewsAPI] 贴纸关键词: {len(kw_items)} 条")

        all_items = all_items[:self.MAX_ITEMS]
        print(f"  [NewsAPI] 共获取 {len(all_items)} 条")
        return all_items

    def _fetch_sticker_keywords(self) -> list[dict]:
        """针对贴纸业务的关键词定向搜索"""
        items = []
        for keyword in self.STICKER_KEYWORDS:
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/everything",
                    params={
                        "q": keyword,
                        "language": "en",
                        "sortBy": "popularity",
                        "pageSize": 3,
                        "apiKey": config.NEWS_API_KEY,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "ok":
                    for a in data.get("articles", []):
                        item = self._parse_article(a, f"keyword:{keyword}")
                        if item["keyword"]:
                            items.append(item)
            except Exception as e:
                print(f"  [NewsAPI] 关键词'{keyword}'搜索失败: {e}")
        return items

    def _fetch_top_headlines(self, page_size: int = 15) -> list[dict]:
        """获取美国今日头条"""
        try:
            resp = requests.get(
                f"{self.BASE_URL}/top-headlines",
                params={
                    "country": "us",
                    "pageSize": page_size,
                    "apiKey": config.NEWS_API_KEY,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                print(f"  [NewsAPI] API 返回错误: {data.get('message')}")
                return []

            return [self._parse_article(a, "top-headlines") for a in data.get("articles", [])]

        except Exception as e:
            print(f"  [NewsAPI] 头条获取失败: {e}")
            return []

    def _fetch_by_category(self, category: str, page_size: int = 5) -> list[dict]:
        """按新闻类别获取"""
        try:
            resp = requests.get(
                f"{self.BASE_URL}/top-headlines",
                params={
                    "country": "us",
                    "category": category,
                    "pageSize": page_size,
                    "apiKey": config.NEWS_API_KEY,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                return []

            return [self._parse_article(a, category) for a in data.get("articles", [])]

        except Exception as e:
            print(f"  [NewsAPI] {category} 类别获取失败: {e}")
            return []

    def _parse_article(self, article: dict, category: str) -> dict:
        return {
            "source": self.SOURCE_NAME,
            "category": category,
            "keyword": article.get("title", "").strip(),
            "summary": (article.get("description") or "")[:200].strip(),
            "url": article.get("url", ""),
            "image_url": article.get("urlToImage", ""),
            "news_source": article.get("source", {}).get("name", ""),
            "published_at": article.get("publishedAt", ""),
            "fetched_at": datetime.now().isoformat(),
            "method": "newsapi",
        }

if __name__ == "__main__":
    news = NewsApiFetcher()
    news.fetch()



