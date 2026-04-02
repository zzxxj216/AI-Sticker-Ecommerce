"""
Module D — Hard Filter
砍掉不适合做贴纸的题材：人物依赖、品牌依赖、IP 依赖、
纯新闻事件、caption-only、视觉符号不足、生命周期太短。
"""
import re


class HardFilter:

    # ---- 人物名（持续扩展，只需覆盖高频出现的） ----
    PERSON_MARKERS = {
        "taylor swift", "drake", "beyonce", "rihanna", "kanye", "ye",
        "elon musk", "jeff bezos", "mark zuckerberg", "bill gates",
        "brie larson", "tom holland", "zendaya", "timothee chalamet",
        "dua lipa", "billie eilish", "olivia rodrigo", "bad bunny",
        "lebron james", "messi", "ronaldo", "neymar",
        "kim kardashian", "kylie jenner", "selena gomez",
        "joe rogan", "mr beast", "mrbeast", "pewdiepie",
        "oprah", "ellen",
    }

    # ---- 品牌 ----
    BRAND_MARKERS = {
        "apple", "iphone", "ipad", "macbook", "samsung", "galaxy",
        "google pixel", "tesla", "nvidia", "amd", "intel",
        "amazon prime", "netflix", "spotify", "tiktok", "instagram",
        "coca cola", "pepsi", "mcdonalds", "starbucks",
        "nike", "adidas", "gucci", "louis vuitton",
        "xbox", "playstation", "ps5", "ps6",
    }

    # ---- 受版权保护 IP ----
    IP_MARKERS = {
        "disney", "pixar", "marvel", "dc comics", "batman", "superman",
        "spider-man", "spiderman", "avengers", "frozen",
        "nintendo", "mario", "zelda", "pokemon", "pikachu",
        "harry potter", "hogwarts", "star wars", "mandalorian",
        "lord of the rings", "game of thrones",
        "fortnite", "minecraft", "roblox", "genshin impact",
        "one piece", "naruto", "dragon ball", "demon slayer",
        "mickey mouse", "winnie the pooh",
        "barbie", "transformers", "power rangers",
    }

    # ---- 纯新闻事件信号词 ----
    NEWS_EVENT_SIGNALS = {
        "breaking:", "just in:", "update:", "developing:",
        "arrested", "indicted", "sentenced", "fired",
        "stock price", "shares", "ipo", "earnings",
        "crash", "accident", "earthquake", "hurricane",
        "recall", "lawsuit", "investigation",
    }

    # ---- Caption-only 检测：过短 + 无名词实体 ----
    MIN_THEME_WORD_COUNT = 2

    def filter(self, theme_candidates: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        返回 (approved, rejected)
        rejected 每条带 reject_reasons 列表
        """
        approved = []
        rejected = []

        for theme in theme_candidates:
            reasons = self._check(theme)
            if reasons:
                theme["reject_reasons"] = reasons
                rejected.append(theme)
            else:
                approved.append(theme)

        print(f"  [HardFilter] 通过 {len(approved)} / 拒绝 {len(rejected)}")
        if rejected:
            reason_counts: dict[str, int] = {}
            for r in rejected:
                for reason in r["reject_reasons"]:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
            top_reasons = sorted(reason_counts.items(), key=lambda x: -x[1])[:5]
            print(f"  [HardFilter] 拒绝原因 TOP: "
                  + ", ".join(f"{r}({c})" for r, c in top_reasons))

        return approved, rejected

    # ------------------------------------------------------------------

    def _check(self, theme: dict) -> list[str]:
        reasons = []
        name = (theme.get("normalized_theme") or "").lower()
        titles = " ".join(theme.get("raw_titles", [])).lower()
        combined = f"{name} {titles}"

        if self._match_any(combined, self.PERSON_MARKERS):
            reasons.append("person_dependent")

        if self._match_any(combined, self.BRAND_MARKERS):
            reasons.append("brand_dependent")

        if self._match_any(combined, self.IP_MARKERS):
            reasons.append("ip_dependent")

        if self._is_news_event(combined):
            reasons.append("pure_news_event")

        if self._is_caption_only(theme):
            reasons.append("caption_only_no_theme")

        symbols = theme.get("candidate_visual_symbols", [])
        if len(symbols) < 2 and theme.get("theme_type") != "evergreen_emotion":
            reasons.append("too_few_visual_symbols")

        return reasons

    @staticmethod
    def _match_any(text: str, markers: set[str]) -> bool:
        for marker in markers:
            if marker in text:
                return True
        return False

    def _is_news_event(self, text: str) -> bool:
        for signal in self.NEWS_EVENT_SIGNALS:
            if signal in text:
                return True
        return False

    def _is_caption_only(self, theme: dict) -> bool:
        """
        判断是否为 caption-only：
        - normalized_theme 过短
        - 无视觉符号
        - theme_type 未被 Claude 识别（空或 pop_culture_moment 兜底）
        """
        name = theme.get("normalized_theme", "")
        words = re.findall(r'[a-zA-Z]+', name)
        if len(words) < self.MIN_THEME_WORD_COUNT:
            return True

        symbols = theme.get("candidate_visual_symbols", [])
        hooks = theme.get("candidate_emotional_hooks", [])
        interpretation = theme.get("one_line_interpretation", "")
        if not symbols and not hooks and not interpretation:
            return True

        return False
