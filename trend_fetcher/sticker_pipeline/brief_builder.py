"""
Module H — Trend Brief Builder
把 approve 的 opportunity card 转成现有贴纸生产链可直接消费的 trend brief。
输出格式严格对齐 src/services/batch/trend_brief_schema.py 的 REQUIRED_FIELDS。
"""
import json
from datetime import datetime
from pathlib import Path


# archetype → 默认贴纸包规模
_ARCHETYPE_PACK_SIZE = {
    "aesthetic_pack":          {"tier": "medium", "sticker_count_range": "8-12"},
    "emotion_humor_pack":      {"tier": "medium", "sticker_count_range": "8-12"},
    "seasonal_festival_pack":  {"tier": "large",  "sticker_count_range": "10-15"},
    "lifestyle_identity_pack": {"tier": "medium", "sticker_count_range": "8-12"},
    "object_icon_pack":        {"tier": "large",  "sticker_count_range": "10-15"},
    "label_badge_pack":        {"tier": "small",  "sticker_count_range": "6-8"},
}

# archetype → 默认 must_avoid
_ARCHETYPE_AVOID = {
    "aesthetic_pack":          ["text-heavy designs", "realistic photo style",
                                "brand logos", "copyrighted characters"],
    "emotion_humor_pack":      ["offensive humor", "political statements",
                                "brand references", "copyrighted characters"],
    "seasonal_festival_pack":  ["religious symbols unless clearly secular",
                                "brand logos", "copyrighted characters"],
    "lifestyle_identity_pack": ["stereotypes", "brand logos",
                                "copyrighted characters"],
    "object_icon_pack":        ["brand logos", "copyrighted characters",
                                "text-heavy designs"],
    "label_badge_pack":        ["slurs", "hate speech", "brand names",
                                "copyrighted fonts"],
}

# theme_type → trend_type 映射
_THEME_TO_TREND_TYPE = {
    "pop_culture_moment":   "viral_moment",
    "evergreen_emotion":    "evergreen",
    "seasonal_event":       "seasonal",
    "lifestyle_identity":   "lifestyle",
    "animal_cute":          "evergreen",
    "aesthetic_visual":     "aesthetic",
    "humor_relatable":      "evergreen",
    "fandom":               "fandom",
    "nature_outdoors":      "evergreen",
    "food_drink":           "evergreen",
}


class BriefBuilder:
    """把 approved opportunity cards 转成标准 trend briefs。"""

    def build(self, approved_cards: list[dict]) -> list[dict]:
        briefs = []
        for card in approved_cards:
            brief = self._card_to_brief(card)
            briefs.append(brief)

        print(f"  [BriefBuilder] 生成 {len(briefs)} 份 trend briefs")
        return briefs

    def _card_to_brief(self, card: dict) -> dict:
        theme = card.get("normalized_theme", "Untitled")
        theme_type = card.get("theme_type", "")
        archetype = card.get("recommended_pack_archetype", "object_icon_pack")
        hooks = card.get("core_emotional_hook", [])
        symbols = card.get("suggested_visual_symbol_pool", [])
        platforms = card.get("best_platform", [])
        keywords = card.get("candidate_keywords", [])

        brief = {
            "trend_name": self._format_trend_name(theme),
            "trend_type": _THEME_TO_TREND_TYPE.get(theme_type, "trending"),
            "one_line_explanation": (
                card.get("one_line_interpretation")
                or f"Trending theme: {theme}"
            ),
            "why_now": self._generate_why_now(card),
            "lifecycle": self._infer_lifecycle(card),
            "platform": platforms,
            "product_goal": self._infer_product_goal(archetype),
            "target_audience": self._infer_audience(card),
            "emotional_core": hooks if hooks else ["fun", "self-expression"],
            "visual_symbols": symbols if symbols else [theme],
            "must_avoid": _ARCHETYPE_AVOID.get(archetype, [
                "brand logos", "copyrighted characters"]),
            "risk_notes": card.get("risk_flags", []),
            "pack_size_goal": _ARCHETYPE_PACK_SIZE.get(
                archetype, {"tier": "medium", "sticker_count_range": "8-12"}),
            "_meta": {
                "generated_by": "trend_fetcher.sticker_pipeline",
                "generated_at": datetime.now().isoformat(),
                "theme_id": card.get("theme_id", ""),
                "sticker_opportunity_score": card.get(
                    "sticker_opportunity_score", 0),
                "trend_heat_score": card.get("trend_heat_score", 0),
                "pack_archetype": archetype,
                "seo_keywords": keywords[:8],
            },
        }
        return brief

    @staticmethod
    def _format_trend_name(theme: str) -> str:
        return " ".join(w.capitalize() for w in theme.split())

    def _generate_why_now(self, card: dict) -> str:
        parts = []
        sc = card.get("source_count", 0)
        if sc >= 2:
            parts.append(f"trending across {sc} platforms")

        heat = card.get("trend_heat_score", 0)
        if heat >= 50:
            parts.append("strong engagement signals")
        elif heat >= 30:
            parts.append("growing online discussion")

        score = card.get("sticker_opportunity_score", 0)
        if score >= 85:
            parts.append("high commercial potential for sticker products")
        elif score >= 75:
            parts.append("solid sticker market fit")

        if not parts:
            parts.append("currently gaining traction online")

        return "; ".join(parts).capitalize()

    @staticmethod
    def _infer_lifecycle(card: dict) -> str:
        breakdown = card.get("score_breakdown", {})
        lc = breakdown.get("lifecycle_strength", 5)
        if lc >= 9:
            return "evergreen"
        if lc >= 7:
            return "seasonal_recurring"
        if lc >= 5:
            return "medium_term"
        return "short_burst"

    @staticmethod
    def _infer_product_goal(archetype: str) -> list[str]:
        goals = {
            "aesthetic_pack":          ["laptop stickers", "journal decoration",
                                        "phone case stickers"],
            "emotion_humor_pack":      ["laptop stickers", "water bottle stickers",
                                        "gift inserts"],
            "seasonal_festival_pack":  ["gift wrapping", "card decoration",
                                        "seasonal merchandise"],
            "lifestyle_identity_pack": ["laptop stickers", "water bottle stickers",
                                        "car bumper stickers"],
            "object_icon_pack":        ["journal decoration", "scrapbooking",
                                        "phone case stickers"],
            "label_badge_pack":        ["laptop stickers", "water bottle stickers",
                                        "planner stickers"],
        }
        return goals.get(archetype, ["laptop stickers", "water bottle stickers"])

    @staticmethod
    def _infer_audience(card: dict) -> dict:
        theme_type = card.get("theme_type", "")
        archetype = card.get("recommended_pack_archetype", "")

        audience_defaults = {
            "animal_cute": {
                "age_range": "16-35",
                "gender_tilt": "female-leaning",
                "profile": "Pet lovers, animal enthusiasts, and cute aesthetic fans",
                "usage_scenarios": ["decorating laptops", "journaling",
                                    "gifting to pet-loving friends"],
            },
            "evergreen_emotion": {
                "age_range": "18-30",
                "gender_tilt": "neutral",
                "profile": "Young adults who express emotions through stickers and digital culture",
                "usage_scenarios": ["personalizing devices", "mood expression",
                                    "self-care journaling"],
            },
            "humor_relatable": {
                "age_range": "16-28",
                "gender_tilt": "neutral",
                "profile": "Gen Z and young millennials who love meme culture",
                "usage_scenarios": ["laptop decoration", "sharing laughs",
                                    "water bottle stickers"],
            },
            "seasonal_event": {
                "age_range": "18-45",
                "gender_tilt": "female-leaning",
                "profile": "Holiday enthusiasts and gift shoppers",
                "usage_scenarios": ["seasonal decoration", "gift wrapping",
                                    "party supplies"],
            },
        }

        default = {
            "age_range": "18-35",
            "gender_tilt": "neutral",
            "profile": "Young adults interested in self-expression through sticker culture",
            "usage_scenarios": ["laptop decoration", "journaling",
                                "personalizing belongings"],
        }
        return audience_defaults.get(theme_type, default)
