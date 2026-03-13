"""
Reddit 热点数据获取模块
- 方案A: 直接 JSON API（无需任何 Key，立即可用）
- 方案B: praw（需注册 Reddit App，数据更完整）
"""
import requests
import time
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config


class RedditFetcher:
    SOURCE_NAME = "Reddit"
    BASE_URL = "https://www.reddit.com"

    def fetch(self) -> list[dict]:
        """获取 Reddit 多个子版块的热门帖子"""
        print(f"  [Reddit] 开始获取...")

        # 优先尝试带认证的 praw
        if config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
            try:
                results = self._fetch_via_praw()
                print(f"  [Reddit praw] 获取到 {len(results)} 条")
                return results
            except Exception as e:
                print(f"  [Reddit praw] 失败，切换到无认证模式: {e}")

        # 无需 API Key 的直接请求
        results = self._fetch_via_json_api()
        print(f"  [Reddit JSON API] 获取到 {len(results)} 条")
        return results

    def _fetch_via_json_api(self) -> list[dict]:
        """
        通过 Reddit OAuth2 Token 调用 JSON API（无账号登录模式）
        Reddit 自 2023 年起要求 OAuth，需注册 App 获取 client_id
        如果未配置，回退到 RSS 方式
        """
        items = []

        # 尝试获取 OAuth Token（无需用户登录，只用 client_credentials）
        if not config.REDDIT_CLIENT_ID or not config.REDDIT_CLIENT_SECRET:
            print(f"  [Reddit] 未配置 API Key，使用 RSS 方式获取")
            return self._fetch_via_rss_fallback()

        try:
            # 获取 App-only OAuth Token
            auth = requests.auth.HTTPBasicAuth(
                config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET
            )
            token_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=auth,
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": "AIStickerTrendBot/1.0"},
                timeout=10,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")

            if not access_token:
                print(f"  [Reddit] 获取 Token 失败，回退到 RSS")
                return self._fetch_via_rss_fallback()

        except Exception as e:
            print(f"  [Reddit] OAuth 认证失败: {e}，回退到 RSS")
            return self._fetch_via_rss_fallback()

        # 使用 Token 调用 API
        headers = {
            "Authorization": f"bearer {access_token}",
            "User-Agent": "AIStickerTrendBot/1.0",
        }
        session = requests.Session()
        session.headers.update(headers)

        for subreddit in config.REDDIT_SUBREDDITS:
            url = f"https://oauth.reddit.com/r/{subreddit}/hot"
            try:
                resp = session.get(url, params={"limit": config.REDDIT_POST_LIMIT}, timeout=15)
                if resp.status_code == 429:
                    print(f"  [Reddit] r/{subreddit} 频率限制，等待5秒...")
                    time.sleep(5)
                    continue

                resp.raise_for_status()
                data = resp.json()

                for post in data.get("data", {}).get("children", []):
                    p = post.get("data", {})
                    title = p.get("title", "").strip()
                    if not title or p.get("stickied"):
                        continue

                    items.append({
                        "source": self.SOURCE_NAME,
                        "subreddit": subreddit,
                        "keyword": title,
                        "score": p.get("score", 0),
                        "comments": p.get("num_comments", 0),
                        "upvote_ratio": p.get("upvote_ratio", 0),
                        "flair": p.get("link_flair_text", "") or "",
                        "url": f"https://www.reddit.com{p.get('permalink', '')}",
                        "thumbnail": p.get("thumbnail", "") or "",
                        "fetched_at": datetime.now().isoformat(),
                        "method": "oauth_api",
                    })

                time.sleep(0.5)

            except Exception as e:
                print(f"  [Reddit] r/{subreddit} 请求失败: {e}")

        return items

    def _fetch_via_rss_fallback(self) -> list[dict]:
        """无需认证的 RSS 回退方案"""
        import feedparser
        items = []
        rss_subs = ["popular", "memes", "aww", "funny", "wholesomememes"]

        for subreddit in rss_subs:
            url = f"https://www.reddit.com/r/{subreddit}/.rss"
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:config.REDDIT_POST_LIMIT]:
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    items.append({
                        "source": self.SOURCE_NAME,
                        "subreddit": subreddit,
                        "keyword": title,
                        "score": 0,
                        "comments": 0,
                        "upvote_ratio": 0,
                        "flair": "",
                        "url": entry.get("link", ""),
                        "thumbnail": "",
                        "fetched_at": datetime.now().isoformat(),
                        "method": "rss_fallback",
                    })
                time.sleep(0.5)
            except Exception as e:
                print(f"  [Reddit RSS] r/{subreddit} 失败: {e}")

        if items:
            print(f"  [Reddit RSS] 回退方案获取 {len(items)} 条")
        return items

    def _fetch_via_praw(self) -> list[dict]:
        """使用 praw 库（需要 Reddit App 认证）"""
        import praw

        reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            user_agent="AIStickerTrendBot/1.0 (by /u/your_username)",
        )

        items = []
        for subreddit_name in config.REDDIT_SUBREDDITS:
            subreddit = reddit.subreddit(subreddit_name)
            for post in subreddit.hot(limit=config.REDDIT_POST_LIMIT):
                if post.stickied:
                    continue
                items.append({
                    "source": self.SOURCE_NAME,
                    "subreddit": subreddit_name,
                    "keyword": post.title,
                    "score": post.score,
                    "comments": post.num_comments,
                    "upvote_ratio": post.upvote_ratio,
                    "flair": post.link_flair_text or "",
                    "url": f"https://www.reddit.com{post.permalink}",
                    "thumbnail": post.thumbnail or "",
                    "fetched_at": datetime.now().isoformat(),
                    "method": "praw",
                })

        return items
