"""
Module E — Sticker Opportunity Scorer
按"贴纸商品可做性"打分（满分 100），替代原有的"热度"评分。

7 个维度：
  sticker_visualizability  /20  — 能画出来吗？
  emotional_buyability     /15  — 有人愿意买来表达情感吗？
  visual_richness          /15  — 视觉元素够丰富做成贴纸包吗？
  platform_fit             /15  — 适合哪些平台卖？
  lifecycle_strength       /10  — 这个题材能卖多久？
  originality_safety       /15  — 版权/原创安全？
  trend_strength           /10  — 热度信号强度
"""
import re


# 高可视化主题类型
_HIGH_VIZ_TYPES = {"animal_cute", "food_drink", "nature_outdoors",
                   "aesthetic_visual", "seasonal_event"}
_MEDIUM_VIZ_TYPES = {"lifestyle_identity", "humor_relatable", "fandom"}

# 高情感购买力类型
_HIGH_EMO_TYPES = {"evergreen_emotion", "animal_cute", "humor_relatable"}

# 长生命周期类型
_EVERGREEN_TYPES = {"evergreen_emotion", "animal_cute", "lifestyle_identity",
                    "nature_outdoors", "food_drink", "aesthetic_visual"}
_SEASONAL_TYPES = {"seasonal_event"}

# 平台适配关键词
_TIKTOK_KEYWORDS = {"trend", "viral", "aesthetic", "vibe", "y2k",
                    "cottagecore", "dark academia", "slay", "bestie"}
_AMAZON_KEYWORDS = {"cute", "funny", "pet", "cat", "dog", "mom", "dad",
                    "gift", "birthday", "love", "heart"}
_ETSY_KEYWORDS = {"kawaii", "vintage", "retro", "handmade", "boho",
                  "minimalist", "botanical", "cottage", "fairy"}


class OpportunityScorer:
    """给每个 theme_candidate 计算贴纸机会分。"""

    def score(self, candidates: list[dict]) -> list[dict]:
        scored = []
        for c in candidates:
            breakdown = {
                "sticker_visualizability": self._score_visualizability(c),
                "emotional_buyability": self._score_emotional(c),
                "visual_richness": self._score_visual_richness(c),
                "platform_fit": self._score_platform_fit(c),
                "lifecycle_strength": self._score_lifecycle(c),
                "originality_safety": self._score_originality(c),
                "trend_strength": self._score_trend_strength(c),
            }
            total = sum(breakdown.values())
            c["sticker_opportunity_score"] = round(total, 1)
            c["score_breakdown"] = breakdown
            c["trend_heat_score"] = self._calc_trend_heat(c)
            scored.append(c)

        scored.sort(key=lambda x: x["sticker_opportunity_score"], reverse=True)

        top3 = scored[:3]
        print(f"  [Scorer] {len(scored)} 个候选已评分 | "
              f"Top3: " + ", ".join(
                  f"{t['normalized_theme'][:25]}({t['sticker_opportunity_score']})"
                  for t in top3))
        return scored

    # ------------------------------------------------------------------
    # 维度 1: sticker_visualizability /20
    # ------------------------------------------------------------------
    def _score_visualizability(self, c: dict) -> float:
        theme_type = c.get("theme_type", "")
        symbols = c.get("candidate_visual_symbols", [])

        score = 0.0
        if theme_type in _HIGH_VIZ_TYPES:
            score += 12
        elif theme_type in _MEDIUM_VIZ_TYPES:
            score += 8
        else:
            score += 4

        score += min(len(symbols) * 2, 8)
        return min(score, 20)

    # ------------------------------------------------------------------
    # 维度 2: emotional_buyability /15
    # ------------------------------------------------------------------
    def _score_emotional(self, c: dict) -> float:
        theme_type = c.get("theme_type", "")
        hooks = c.get("candidate_emotional_hooks", [])

        score = 0.0
        if theme_type in _HIGH_EMO_TYPES:
            score += 8
        else:
            score += 4

        strong_emotions = {"love", "joy", "comfort", "nostalgia", "pride",
                           "warmth", "happiness", "motivation", "humor",
                           "cute", "cozy", "sassy", "empowerment"}
        matched = sum(1 for h in hooks
                      if any(e in h.lower() for e in strong_emotions))
        score += min(matched * 3, 7)
        return min(score, 15)

    # ------------------------------------------------------------------
    # 维度 3: visual_richness /15
    # ------------------------------------------------------------------
    def _score_visual_richness(self, c: dict) -> float:
        symbols = c.get("candidate_visual_symbols", [])
        n = len(symbols)
        if n >= 8:
            return 15
        if n >= 5:
            return 12
        if n >= 3:
            return 9
        if n >= 2:
            return 6
        return 3

    # ------------------------------------------------------------------
    # 维度 4: platform_fit /15
    # ------------------------------------------------------------------
    def _score_platform_fit(self, c: dict) -> float:
        keywords = set()
        for kw in c.get("candidate_keywords", []):
            keywords.update(kw.lower().split())
        theme = c.get("normalized_theme", "").lower()
        keywords.update(theme.split())

        platforms_matched = 0
        if keywords & _TIKTOK_KEYWORDS:
            platforms_matched += 1
        if keywords & _AMAZON_KEYWORDS:
            platforms_matched += 1
        if keywords & _ETSY_KEYWORDS:
            platforms_matched += 1

        if platforms_matched >= 3:
            return 15
        if platforms_matched == 2:
            return 12
        if platforms_matched == 1:
            return 8
        # 兜底：animal_cute / evergreen_emotion 默认适配多平台
        if c.get("theme_type") in ("animal_cute", "evergreen_emotion"):
            return 10
        return 5

    # ------------------------------------------------------------------
    # 维度 5: lifecycle_strength /10
    # ------------------------------------------------------------------
    def _score_lifecycle(self, c: dict) -> float:
        theme_type = c.get("theme_type", "")
        if theme_type in _EVERGREEN_TYPES:
            return 10
        if theme_type in _SEASONAL_TYPES:
            return 7
        if theme_type == "humor_relatable":
            return 6
        # pop_culture_moment / fandom 生命周期短
        return 4

    # ------------------------------------------------------------------
    # 维度 6: originality_safety /15
    # ------------------------------------------------------------------
    def _score_originality(self, c: dict) -> float:
        theme_type = c.get("theme_type", "")
        theme = c.get("normalized_theme", "").lower()

        if theme_type == "fandom":
            return 5

        risky_words = {"celebrity", "movie", "tv show", "game",
                       "album", "tour", "concert", "film"}
        if any(w in theme for w in risky_words):
            return 7

        if theme_type in ("evergreen_emotion", "animal_cute",
                          "nature_outdoors", "food_drink"):
            return 15

        return 12

    # ------------------------------------------------------------------
    # 维度 7: trend_strength /10
    # ------------------------------------------------------------------
    def _score_trend_strength(self, c: dict) -> float:
        items = c.get("source_items", [])
        if not items:
            return 3

        sources = set()
        max_reddit = 0
        has_traffic = False

        for item in items:
            sources.add(item.get("base_platform", item.get("source", "")))
            if item.get("score", 0) > max_reddit:
                max_reddit = item["score"]
            if item.get("traffic"):
                has_traffic = True

        score = 0.0
        # 多源加分
        real_sources = {s for s in sources
                        if not s.endswith("_mirror")}
        score += min(len(real_sources) * 2, 4)

        if max_reddit > 10000:
            score += 3
        elif max_reddit > 1000:
            score += 2
        elif max_reddit > 100:
            score += 1

        if has_traffic:
            score += 2

        return min(score, 10)

    # ------------------------------------------------------------------
    # 趋势热度分（独立于机会分，用于参考）
    # ------------------------------------------------------------------
    def _calc_trend_heat(self, c: dict) -> float:
        items = c.get("source_items", [])
        sources = set()
        max_reddit = 0
        traffic_num = 0

        for item in items:
            sources.add(item.get("base_platform", item.get("source", "")))
            if item.get("score", 0) > max_reddit:
                max_reddit = item["score"]
            t = item.get("traffic") or ""
            cleaned = re.sub(r'[,+\s]', '', str(t))
            try:
                traffic_num = max(traffic_num, int(cleaned))
            except ValueError:
                pass

        heat = 0.0
        heat += len(sources) * 15
        heat += min(len(items) * 3, 20)
        if max_reddit > 10000:
            heat += 25
        elif max_reddit > 1000:
            heat += 15
        if traffic_num > 100000:
            heat += 20
        elif traffic_num > 10000:
            heat += 10
        return round(heat, 1)
