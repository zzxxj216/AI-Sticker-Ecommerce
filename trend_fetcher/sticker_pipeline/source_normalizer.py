"""
Module B — Source Normalizer
给每条原始 item 增加来源质量、镜像源标记、参与度、时效性等元信息，
为后续跨源去重和可信度判断提供基础。
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


# RSS feed 名中属于 Reddit / Google Trends 镜像的标记词
_REDDIT_MIRROR_FEEDS = {"reddit r/popular", "reddit r/memes", "reddit r/aww",
                        "reddit popular"}
_GOOGLE_MIRROR_FEEDS = {"google trends us", "google trends"}

# 用于判断时效性的窗口（小时）
_RECENCY_WINDOW_HOURS = 48


class SourceNormalizer:
    """把 raw_trend_items 标准化，补充来源质量字段。"""

    def normalize(self, raw_items: list[dict]) -> list[dict]:
        out = []
        for item in raw_items:
            enriched = {**item}
            enriched["base_platform"] = self._detect_base_platform(item)
            enriched["source_quality"] = self._assess_quality(item, enriched["base_platform"])
            enriched["is_mirrored_source"] = enriched["base_platform"].endswith("_mirror")
            enriched["is_fallback"] = self._is_fallback(item)
            enriched["has_real_engagement"] = self._has_engagement(item)
            enriched["is_recent_enough"] = self._is_recent(item)
            out.append(enriched)

        mirrors = sum(1 for i in out if i["is_mirrored_source"])
        fallbacks = sum(1 for i in out if i["is_fallback"])
        stale = sum(1 for i in out if not i["is_recent_enough"])
        print(f"  [Normalizer] {len(out)} 条已标准化 | "
              f"镜像源 {mirrors} | 降级源 {fallbacks} | 过期 {stale}")
        return out

    # ------------------------------------------------------------------

    def _detect_base_platform(self, item: dict) -> str:
        source = (item.get("source") or "").lower()
        feed = (item.get("feed_name") or "").lower()

        if source == "google trends":
            return "google"

        if source == "reddit":
            return "reddit"

        if source == "newsapi":
            return "newsapi"

        if source == "rss":
            if any(tag in feed for tag in _REDDIT_MIRROR_FEEDS):
                return "reddit_mirror"
            if any(tag in feed for tag in _GOOGLE_MIRROR_FEEDS):
                return "google_mirror"
            return "rss_native"

        return "unknown"

    def _assess_quality(self, item: dict, platform: str) -> str:
        """high = 原生 API 带参与数据, medium = RSS/原生无参与, low = 降级/镜像"""
        if platform.endswith("_mirror"):
            return "low"

        method = (item.get("method") or "").lower()
        if method in ("rss_fallback",):
            return "low"

        if platform == "reddit":
            if method in ("praw", "oauth_api") and item.get("score", 0) > 0:
                return "high"
            return "medium"

        if platform == "google":
            if item.get("traffic"):
                return "high"
            return "medium"

        if platform == "newsapi":
            return "high"

        return "medium"

    def _is_fallback(self, item: dict) -> bool:
        return (item.get("method") or "") == "rss_fallback"

    def _has_engagement(self, item: dict) -> bool:
        if item.get("score", 0) > 0:
            return True
        if item.get("comments", 0) > 0:
            return True
        if item.get("traffic"):
            return True
        return False

    def _is_recent(self, item: dict, window_hours: int = _RECENCY_WINDOW_HOURS) -> bool:
        """判断是否在时效窗口内；无时间戳的默认为 recent。"""
        pub = item.get("published_at")
        if not pub:
            return True

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)

        try:
            if isinstance(pub, str):
                for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        dt = datetime.strptime(pub, fmt)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt >= cutoff
                    except ValueError:
                        continue
                dt = parsedate_to_datetime(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt >= cutoff
        except Exception:
            pass
        return True
