"""
Module C — Theme Abstractor
把帖子标题/新闻标题/搜索词抽象成"可商品化主题"。

核心逻辑：
  1. 去掉镜像源和过期项，收集有效标题
  2. 批量发给 GPT-5.4，要求返回结构化 JSON
  3. 如果 LLM 不可用，降级为规则分组
"""
import hashlib
import json
import re
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import config

THEME_ABSTRACTION_PROMPT = """You are a sticker product theme analyst.

Your job is to convert noisy trending titles into sticker-friendly product themes.

Important goals:
1. Identify only themes that are commercially meaningful for sticker products.
2. Group together only titles that clearly belong to the same reusable product theme.
3. Reject titles that are not suitable for sticker-theme abstraction.

Rules:
1. Each normalized_theme must be 2-4 words, lowercase.
2. Only merge titles when they share a clear product theme, emotional hook, or reusable visual system.
3. Do not force unrelated titles into the same group.
4. Skip and reject:
   - pure news
   - celebrity/person-dependent topics
   - brand-dependent topics
   - IP-dependent topics
   - sports score or match-result topics
   - vague caption-only titles that do not support a reusable sticker theme
   - topics that cannot support at least 4 stable visual symbols
5. theme_type must be one of:
   - evergreen_emotion
   - seasonal_event
   - lifestyle_identity
   - animal_cute
   - aesthetic_visual
   - humor_relatable
   - nature_outdoors
   - food_drink
   - object_icon
   - label_badge
6. candidate_visual_symbols must be conservative and grounded in the titles. Do not invent overly specific symbols without support.
7. candidate_emotional_hooks must be short and reusable for product planning.
8. If no valid themes can be extracted, return an empty JSON array.
9. Return JSON array only. No markdown. No explanation.

TITLES:
__TITLES_BLOCK__

JSON format per valid theme:
{{
  "normalized_theme": "cozy pet comfort",
  "theme_type": "animal_cute",
  "one_line_interpretation": "Warm, comforting pet moments suitable for sticker products.",
  "raw_titles": ["title1", "title2"],
  "candidate_visual_symbols": ["sleeping cat", "paw print", "heart", "soft blanket"],
  "candidate_emotional_hooks": ["warmth", "comfort", "gentleness"],
  "candidate_keywords": ["cute pet sticker", "cat lover sticker"]
}}"""


class ThemeAbstractor:
    """把标题列表抽象成可商品化主题。"""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if not config.OPENAI_API_KEY:
                return None
            from openai import OpenAI
            kwargs = {"api_key": config.OPENAI_API_KEY}
            if config.OPENAI_BASE_URL:
                kwargs["base_url"] = config.OPENAI_BASE_URL
            self._client = OpenAI(**kwargs)
        return self._client

    def abstract(self, normalized_items: list[dict]) -> list[dict]:
        effective = [
            item for item in normalized_items
            if not item.get("is_mirrored_source", False)
            and item.get("is_recent_enough", True)
        ]

        titles = []
        title_to_items: dict[str, list[dict]] = {}
        for item in effective:
            kw = (item.get("keyword") or "").strip()
            if not kw or len(kw) < 5:
                continue
            if kw not in title_to_items:
                titles.append(kw)
                title_to_items[kw] = []
            title_to_items[kw].append(item)

        print(f"  [ThemeAbstractor] 有效标题 {len(titles)} 条（排除镜像/过期）",
              flush=True)

        if not titles:
            return []

        if self.client:
            themes = self._abstract_via_llm(titles, title_to_items)
        else:
            print("  [ThemeAbstractor] LLM 未配置，使用规则降级模式")
            themes = self._abstract_via_rules(titles, title_to_items)

        for theme in themes:
            if "theme_id" not in theme:
                raw = theme.get("normalized_theme", "")
                theme["theme_id"] = hashlib.md5(raw.encode()).hexdigest()[:12]
            theme["source_items"] = []
            for t in theme.get("raw_titles", []):
                theme["source_items"].extend(title_to_items.get(t, []))

        print(f"  [ThemeAbstractor] 产出 {len(themes)} 个主题", flush=True)
        return themes

    # ------------------------------------------------------------------
    # GPT-5.4 模式
    # ------------------------------------------------------------------

    def _abstract_via_llm(self, titles: list[str],
                          title_to_items: dict) -> list[dict]:
        batch_size = 50
        all_themes = []

        for i in range(0, len(titles), batch_size):
            batch = titles[i:i + batch_size]
            numbered = "\n".join(f"{j+1}. {t[:80]}" for j, t in enumerate(batch))
            prompt = THEME_ABSTRACTION_PROMPT.replace("__TITLES_BLOCK__", numbered)

            try:
                model = config.OPENAI_MODEL
                print(f"  [ThemeAbstractor] LLM 请求 batch {i//batch_size+1} "
                      f"({len(batch)} 标题, model={model})...", flush=True)
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_text = response.choices[0].message.content.strip()
                themes = self._parse_json_response(raw_text)

                tokens_in = getattr(response.usage, "prompt_tokens", 0)
                tokens_out = getattr(response.usage, "completion_tokens", 0)
                print(f"  [ThemeAbstractor] batch {i//batch_size+1}: "
                      f"{len(themes)} 主题, tokens={tokens_in}+{tokens_out}",
                      flush=True)
                all_themes.extend(themes)
            except Exception as e:
                print(f"  [ThemeAbstractor] LLM 调用失败: {e}", flush=True)
                all_themes.extend(
                    self._abstract_via_rules(titles[i:i + batch_size], title_to_items)
                )

        return self._merge_duplicate_themes(all_themes)

    def _parse_json_response(self, text: str) -> list[dict]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "themes" in data:
                return data["themes"]
        except json.JSONDecodeError:
            arr_match = re.search(r'\[[\s\S]*\]', text)
            if arr_match:
                try:
                    return json.loads(arr_match.group())
                except json.JSONDecodeError:
                    pass

        print(f"  [ThemeAbstractor] JSON 解析失败，响应前 200 字: {text[:200]}")
        return []

    def _merge_duplicate_themes(self, themes: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for theme in themes:
            key = theme.get("normalized_theme", "").lower().strip()
            if not key:
                continue
            if key in merged:
                existing = merged[key]
                existing["raw_titles"] = list(set(
                    existing.get("raw_titles", []) + theme.get("raw_titles", [])
                ))
                for field in ("candidate_visual_symbols",
                              "candidate_emotional_hooks", "candidate_keywords"):
                    existing[field] = list(set(
                        existing.get(field, []) + theme.get(field, [])
                    ))
            else:
                merged[key] = theme
        return list(merged.values())

    # ------------------------------------------------------------------
    # 规则降级模式
    # ------------------------------------------------------------------

    def _abstract_via_rules(self, titles: list[str],
                            title_to_items: dict) -> list[dict]:
        themes = []
        for title in titles:
            words = set(re.findall(r'[a-zA-Z]{3,}', title.lower()))
            theme_type = self._guess_type_by_keywords(words)
            themes.append({
                "normalized_theme": title[:60].lower().strip(),
                "theme_type": theme_type,
                "one_line_interpretation": "",
                "raw_titles": [title],
                "candidate_visual_symbols": [],
                "candidate_emotional_hooks": [],
                "candidate_keywords": list(words)[:5],
            })
        return themes

    @staticmethod
    def _guess_type_by_keywords(words: set[str]) -> str:
        animal_words = {"cat", "dog", "kitten", "puppy", "bunny", "frog",
                        "panda", "bear", "fox", "bird", "hamster", "pet"}
        emotion_words = {"love", "happy", "sad", "anxiety", "mood", "smile",
                         "heart", "cry", "joy", "vibe"}
        humor_words = {"meme", "funny", "lol", "lmao", "humor", "joke"}
        seasonal_words = {"christmas", "halloween", "valentine", "easter",
                          "summer", "winter", "spring", "fall", "holiday"}
        food_words = {"coffee", "tea", "food", "cook", "bake", "pizza",
                      "sushi", "ramen", "cake"}

        if words & animal_words:
            return "animal_cute"
        if words & humor_words:
            return "humor_relatable"
        if words & emotion_words:
            return "evergreen_emotion"
        if words & seasonal_words:
            return "seasonal_event"
        if words & food_words:
            return "food_drink"
        return "pop_culture_moment"
