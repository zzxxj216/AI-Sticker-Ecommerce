"""
Module G — Sticker Opportunity Card Builder
基于评分和母型，为每个候选生成最终决策卡。

decision 阈值：
  recommend  >=70  — 推荐，直接生成 brief 进入生产
  review     50-69 — 需要审核，人工确认后可进入生产
  reject     <50   — 丢弃
"""


RECOMMEND_THRESHOLD = 70
REVIEW_THRESHOLD = 50

# theme_type → 推荐销售平台
_TYPE_PLATFORMS = {
    "animal_cute":        ["TikTok Shop", "Amazon", "Etsy"],
    "evergreen_emotion":  ["Amazon", "Etsy", "Shopify"],
    "humor_relatable":    ["TikTok Shop", "Amazon"],
    "seasonal_event":     ["Amazon", "Etsy", "Shopify"],
    "lifestyle_identity": ["Etsy", "Amazon", "Shopify"],
    "aesthetic_visual":   ["TikTok Shop", "Etsy"],
    "food_drink":         ["Amazon", "Etsy"],
    "nature_outdoors":    ["Etsy", "Shopify"],
    "pop_culture_moment": ["TikTok Shop"],
    "fandom":             ["TikTok Shop", "Amazon"],
}


class OpportunityCardBuilder:
    """把 scored+mapped 候选转成最终 opportunity card。"""

    def build(self, candidates: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """
        返回 (recommend_cards, review_cards, dropped_count)
          recommend: >=70 推荐，直接生成 brief
          review:    50-69 需审核
          <50 丢弃
        """
        recommend = []
        review = []

        for c in candidates:
            score = c.get("sticker_opportunity_score", 0)
            if score < REVIEW_THRESHOLD:
                continue

            decision = "recommend" if score >= RECOMMEND_THRESHOLD else "review"

            card = {
                "theme_id": c.get("theme_id", ""),
                "normalized_theme": c.get("normalized_theme", ""),
                "theme_type": c.get("theme_type", ""),
                "sticker_opportunity_score": score,
                "trend_heat_score": c.get("trend_heat_score", 0),
                "score_breakdown": c.get("score_breakdown", {}),
                "sticker_fit_level": self._fit_level(score),
                "decision": decision,
                "best_platform": _TYPE_PLATFORMS.get(
                    c.get("theme_type", ""), ["Amazon"]),
                "recommended_pack_archetype": c.get(
                    "recommended_pack_archetype", ""),
                "core_emotional_hook": c.get(
                    "candidate_emotional_hooks", [])[:3],
                "suggested_visual_symbol_pool": c.get(
                    "candidate_visual_symbols", []),
                "candidate_keywords": c.get("candidate_keywords", []),
                "one_line_interpretation": c.get(
                    "one_line_interpretation", ""),
                "raw_titles": c.get("raw_titles", [])[:5],
                "risk_flags": self._assess_risks(c),
                "recommended_next_step": self._next_step(score),
                "source_count": len(set(
                    item.get("base_platform", "")
                    for item in c.get("source_items", [])
                    if not item.get("base_platform", "").endswith("_mirror")
                )),
            }

            if decision == "recommend":
                recommend.append(card)
            else:
                review.append(card)

        dropped = len(candidates) - len(recommend) - len(review)
        print(f"  [CardBuilder] recommend {len(recommend)} | "
              f"review {len(review)} | dropped {dropped}")
        return recommend, review

    @staticmethod
    def _fit_level(score: float) -> str:
        if score >= 85:
            return "very_high"
        if score >= 70:
            return "high"
        if score >= 50:
            return "medium"
        return "low"

    @staticmethod
    def _assess_risks(c: dict) -> list[str]:
        risks = []
        theme_type = c.get("theme_type", "")
        if theme_type == "fandom":
            risks.append("potential_ip_overlap")
        if theme_type == "pop_culture_moment":
            risks.append("short_lifecycle")
        if c.get("trend_heat_score", 0) < 20:
            risks.append("low_trend_signal")

        breakdown = c.get("score_breakdown", {})
        if breakdown.get("originality_safety", 15) < 10:
            risks.append("originality_concern")
        if breakdown.get("lifecycle_strength", 10) < 5:
            risks.append("may_expire_quickly")
        return risks

    @staticmethod
    def _next_step(score: float) -> str:
        if score >= 85:
            return "fast_track_to_production"
        if score >= 70:
            return "generate_brief_and_produce"
        if score >= 50:
            return "manual_review_required"
        return "drop"
