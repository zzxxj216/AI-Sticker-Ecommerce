"""
Google Trends 数据获取模块
- 方案A: RSS Feed（无需 API Key，最稳定）
- 方案B: pytrends（可获取更详细的趋势数据）
"""
import time
import requests
import feedparser
from datetime import datetime
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config


class GoogleTrendsFetcher:
    SOURCE_NAME = "Google Trends"

    def fetch(self) -> list[dict]:
        """
        获取 Google Trends 热点，优先用 RSS（稳定），
        再尝试 pytrends 补充更多数据
        """
        print(f"  [Google Trends] 开始获取...")
        results = []

        # 方案A: RSS（无需任何 Key，最可靠）
        rss_items = self._fetch_via_rss()
        results.extend(rss_items)
        print(f"  [Google Trends RSS] 获取到 {len(rss_items)} 条热搜")

        # 方案B: pytrends（可选，获取更多细节）
        try:
            pt_items = self._fetch_via_pytrends()
            # 合并去重
            existing_keywords = {r["keyword"].lower() for r in results}
            added = 0
            for item in pt_items:
                if item["keyword"].lower() not in existing_keywords:
                    results.append(item)
                    existing_keywords.add(item["keyword"].lower())
                    added += 1
            print(f"  [Google Trends pytrends] 额外补充 {added} 条")
        except Exception as e:
            print(f"  [Google Trends pytrends] 跳过: {e}")

        return results

    def _fetch_via_rss(self) -> list[dict]:
        """通过 RSS Feed 获取 Google Trends 实时热搜"""
        # 尝试多个 URL（Google 曾多次更改接口路径）
        candidate_urls = [
            f"https://trends.google.com/trending/rss?geo={config.TREND_GEO_CODE}",
            f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={config.TREND_GEO_CODE}",
        ]
        url = candidate_urls[0]
        # 检查哪个 URL 可用
        for candidate in candidate_urls:
            try:
                test = requests.head(candidate, headers=config.HTTP_HEADERS, timeout=8)
                if test.status_code == 200:
                    url = candidate
                    break
            except Exception:
                continue
        items = []
        try:
            # feedparser 有时无法正确处理 Google Trends RSS，改用 requests 直接下载
            resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=15)
            resp.raise_for_status()

            # 手动解析 XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            ns = {"ht": "https://trends.google.com/trends/trendingsearches/daily"}

            channel = root.find("channel")
            if channel is None:
                # 尝试 feedparser 作为后备
                feed = feedparser.parse(resp.text)
                entries = feed.entries
            else:
                entries = channel.findall("item")

            rank = 0
            for entry in entries[:25]:
                # 处理 ElementTree 解析的 item
                if hasattr(entry, "find"):
                    title_el = entry.find("title")
                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    link_el = entry.find("link")
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    # 获取搜索量
                    approx_el = entry.find("ht:approx_traffic", ns)
                    traffic = approx_el.text if approx_el is not None else None
                else:
                    # feedparser 对象
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "")
                    traffic = self._extract_traffic(entry.get("summary", ""))

                if not title:
                    continue

                rank += 1
                items.append({
                    "source": self.SOURCE_NAME,
                    "keyword": title,
                    "traffic": traffic,
                    "rank": rank,
                    "url": link,
                    "fetched_at": datetime.now().isoformat(),
                    "method": "rss",
                })

        except Exception as e:
            print(f"  [Google Trends RSS] 请求失败: {e}")
        return items

    def _fetch_via_pytrends(self) -> list[dict]:
        """通过 pytrends 获取趋势数据"""
        from pytrends.request import TrendReq
        import urllib3

        # urllib3 2.0+ 将 method_whitelist 改名为 allowed_methods
        # 通过 monkey-patch 解决兼容性问题
        urllib3_version = tuple(int(x) for x in urllib3.__version__.split(".")[:2])
        if urllib3_version >= (2, 0):
            from urllib3.util.retry import Retry
            _orig_init = Retry.__init__
            def _patched_init(self, *args, **kwargs):
                if "method_whitelist" in kwargs:
                    kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
                _orig_init(self, *args, **kwargs)
            Retry.__init__ = _patched_init

        pytrends = TrendReq(
            hl="en-US",
            tz=360,
            timeout=(10, config.PYTRENDS_TIMEOUT),
            retries=config.PYTRENDS_RETRIES,
            backoff_factor=config.PYTRENDS_BACKOFF,
        )

        time.sleep(1)  # 避免频率限制
        df = pytrends.trending_searches(pn=config.TREND_GEO)

        items = []
        for i, row in df.iterrows():
            keyword = str(row[0]).strip()
            if keyword:
                items.append({
                    "source": self.SOURCE_NAME,
                    "keyword": keyword,
                    "traffic": None,
                    "rank": i + 1,
                    "url": f"https://www.google.com/search?q={keyword.replace(' ', '+')}",
                    "fetched_at": datetime.now().isoformat(),
                    "method": "pytrends",
                })
        return items

    def _extract_traffic(self, html_text: str) -> Optional[str]:
        """从 HTML 描述中提取搜索量"""
        import re
        match = re.search(r"(\d[\d,]+\+?)\s*searches", html_text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
