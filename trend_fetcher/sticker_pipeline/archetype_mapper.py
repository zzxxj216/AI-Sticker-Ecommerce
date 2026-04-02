"""
Module F — Pack Archetype Mapper
把通过项归类成贴纸包母型，为 planner 提供更明确的设计方向。

6 种母型：
  aesthetic_pack           — 视觉风格驱动（Y2K、复古、极简）
  emotion_humor_pack       — 情绪/幽默驱动（mood、梗、日常吐槽）
  seasonal_festival_pack   — 节日/季节驱动（圣诞、万圣节、春天）
  lifestyle_identity_pack  — 身份/生活方式驱动（猫奴、咖啡控、户外）
  object_icon_pack         — 物件/图标驱动（食物、植物、动物头像集）
  label_badge_pack         — 标签/徽章驱动（口号贴、身份标签）
"""


# theme_type → 默认母型映射
_TYPE_TO_ARCHETYPE = {
    "aesthetic_visual":     "aesthetic_pack",
    "evergreen_emotion":    "emotion_humor_pack",
    "humor_relatable":      "emotion_humor_pack",
    "seasonal_event":       "seasonal_festival_pack",
    "lifestyle_identity":   "lifestyle_identity_pack",
    "animal_cute":          "object_icon_pack",
    "food_drink":           "object_icon_pack",
    "nature_outdoors":      "object_icon_pack",
    "pop_culture_moment":   "label_badge_pack",
    "fandom":               "label_badge_pack",
}

# 关键词加权覆盖
_KEYWORD_OVERRIDES = {
    "aesthetic_pack":         {"aesthetic", "vibe", "y2k", "retro", "vintage",
                               "minimalist", "cottagecore", "dark academia", "pastel"},
    "emotion_humor_pack":     {"mood", "meme", "relatable", "funny", "anxiety",
                               "burnout", "introvert", "sarcasm", "chaos"},
    "seasonal_festival_pack": {"christmas", "halloween", "valentine", "easter",
                               "thanksgiving", "spring", "summer", "fall", "winter",
                               "new year", "holiday", "birthday"},
    "lifestyle_identity_pack":{"coffee", "plant", "bookworm", "gamer", "yoga",
                               "hiking", "gym", "nurse", "teacher", "mom", "dad",
                               "cat mom", "dog mom", "rescue"},
    "object_icon_pack":       {"cat", "dog", "frog", "bunny", "food", "sushi",
                               "pizza", "flower", "mushroom", "cactus", "star"},
    "label_badge_pack":       {"slay", "bestie", "no cap", "iconic", "queen",
                               "based", "mood", "sus", "bruh", "yolo"},
}

VALID_ARCHETYPES = list(_TYPE_TO_ARCHETYPE.values())


class ArchetypeMapper:
    """给每个 scored candidate 分配 recommended_pack_archetype。"""

    def map(self, scored_candidates: list[dict]) -> list[dict]:
        counts: dict[str, int] = {}
        for c in scored_candidates:
            archetype = self._determine_archetype(c)
            c["recommended_pack_archetype"] = archetype
            counts[archetype] = counts.get(archetype, 0) + 1

        dist = ", ".join(f"{k}({v})" for k, v in
                         sorted(counts.items(), key=lambda x: -x[1]))
        print(f"  [ArchetypeMapper] 分布: {dist}")
        return scored_candidates

    def _determine_archetype(self, c: dict) -> str:
        theme_type = c.get("theme_type", "")
        default = _TYPE_TO_ARCHETYPE.get(theme_type, "label_badge_pack")

        all_text = set()
        for kw in c.get("candidate_keywords", []):
            all_text.update(kw.lower().split())
        all_text.update(c.get("normalized_theme", "").lower().split())
        for sym in c.get("candidate_visual_symbols", []):
            all_text.update(sym.lower().split())

        best_match = default
        best_score = 0
        for archetype, keywords in _KEYWORD_OVERRIDES.items():
            overlap = len(all_text & keywords)
            if overlap > best_score:
                best_score = overlap
                best_match = archetype

        return best_match if best_score >= 2 else default
