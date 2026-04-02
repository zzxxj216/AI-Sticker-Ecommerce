"""
Sticker Opportunity Pipeline — 编排器
把 6 个模块串成完整流水线：

  latest.json (聚合后热点)
     │
     ▼
  [B] SourceNormalizer     → normalized_trend_items.json
     │
     ▼
  [C] ThemeAbstractor      → theme_candidates.json
     │
     ▼
  [D] HardFilter           → filtered_theme_candidates.json
     │
     ▼
  [E] OpportunityScorer    → scored 候选
     │
     ▼
  [F] ArchetypeMapper      → mapped 候选
     │
     ▼
  [G] OpportunityCardBuilder → sticker_candidates.json + sticker_watchlist.json
     │
     ▼
  [H] BriefBuilder         → trend_briefs.json
"""
import json
import time
from datetime import datetime
from pathlib import Path
import os
import sys
import builtins

fetcher_dir = os.path.dirname(os.path.dirname(__file__))
if fetcher_dir not in sys.path:
    sys.path.insert(0, fetcher_dir)
from config import config

project_root = os.path.dirname(fetcher_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from src.services.ops.db import OpsDatabase, TrendItem, TrendBriefRecord

_original_print = builtins.print
def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    _original_print(*args, **kwargs)

from .source_normalizer import SourceNormalizer
from .theme_abstractor import ThemeAbstractor
from .hard_filter import HardFilter
from .opportunity_scorer import OpportunityScorer
from .archetype_mapper import ArchetypeMapper
from .opportunity_card_builder import OpportunityCardBuilder
from .brief_builder import BriefBuilder


class StickerOpportunityPipeline:
    """
    吃 aggregator 输出的 raw items，产出 sticker opportunity cards + trend briefs。
    """

    def __init__(self, output_dir: Path = None, db: OpsDatabase = None, job_id: str = None):
        self.output_dir = output_dir or config.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db = db if db is not None else OpsDatabase()
        self.job_id = job_id

    def run(self, raw_items: list[dict]) -> dict:
        """
        执行完整管线。

        Args:
            raw_items: 聚合前的所有原始 trend items
                       （不是 aggregator 输出的 top_trends，而是 all_items）

        Returns:
            包含各阶段产物的 dict
        """
        start = time.time()
        print("\n" + "=" * 60)
        print("  Sticker Opportunity Pipeline")
        print("=" * 60)

        # ---- [B] Source Normalization ----
        print("\n[B] Source Normalization...")
        normalizer = SourceNormalizer()
        normalized = normalizer.normalize(raw_items)
        self._save("normalized_trend_items.json", normalized)

        # ---- [C] Theme Abstraction ----
        print("\n[C] Theme Abstraction...")
        abstractor = ThemeAbstractor()
        themes = abstractor.abstract(normalized)
        self._save("theme_candidates.json", themes)

        if not themes:
            print("\n[Pipeline] 主题抽象后无候选，终止")
            return {"themes": 0, "approved": 0, "briefs": 0}

        # ---- [D] Hard Filter ----
        print("\n[D] Hard Filter...")
        hard_filter = HardFilter()
        filtered, rejected = hard_filter.filter(themes)
        self._save("filtered_theme_candidates.json", filtered)
        self._save("rejected_theme_candidates.json", rejected)

        if not filtered:
            print("\n[Pipeline] 硬过滤后无候选，终止")
            return {"themes": len(themes), "approved": 0, "briefs": 0}

        # ---- [E] Opportunity Scoring ----
        print("\n[E] Sticker Opportunity Scoring...")
        scorer = OpportunityScorer()
        scored = scorer.score(filtered)

        # ---- [F] Pack Archetype Mapping ----
        print("\n[F] Pack Archetype Mapping...")
        mapper = ArchetypeMapper()
        mapped = mapper.map(scored)

        # ---- [G] Opportunity Card Building ----
        self._log("Opportunity Card Building...")
        card_builder = OpportunityCardBuilder()
        recommend, review = card_builder.build(mapped)

        # ---- [H] Brief Building ----
        briefs = []
        if recommend:
            self._log("Trend Brief Generation (recommend)...")
            brief_builder = BriefBuilder()
            briefs = brief_builder.build(recommend)
        else:
            self._log("无 recommend 候选，跳过 brief 生成")

        self._log(f"Saving {len(recommend)} recommended and {len(review)} review items directly to Database.")
        self._save_to_db(recommend, review, briefs)

        # ---- 摘要 ----
        elapsed = round(time.time() - start, 1)
        result = {
            "raw_items": len(raw_items),
            "normalized": len(normalized),
            "themes": len(themes),
            "filtered": len(filtered),
            "rejected": len(rejected),
            "recommend": len(recommend),
            "review": len(review),
            "briefs": len(briefs),
            "elapsed_seconds": elapsed,
        }

        self._print_summary(result, recommend, review, briefs)
        return result

    def run_from_latest(self) -> dict:
        """从 latest.json 加载原始数据并运行管线。"""
        latest_path = self.output_dir / "latest.json"
        if not latest_path.exists():
            print(f"[Pipeline] latest.json 不存在: {latest_path}")
            print("[Pipeline] 请先运行 python main.py --no-ai 获取热点数据")
            return {}

        with open(latest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        seen_keywords = set()
        raw_items = []

        # by_source 里每个源只保存了 Top 10，先收集
        for source_key, items in data.get("by_source", {}).items():
            for item in items:
                kw = item.get("keyword", "")
                if kw and kw not in seen_keywords:
                    raw_items.append(item)
                    seen_keywords.add(kw)

        # top_trends 补充更多条目（字段结构不同，需适配）
        for t in data.get("top_trends", []):
            kw = t.get("keyword", "")
            if kw and kw not in seen_keywords:
                raw_items.append({
                    "source": ", ".join(t.get("sources", [])),
                    "keyword": kw,
                    "score": t.get("top_reddit_score", 0),
                    "traffic": t.get("google_traffic"),
                    "url": t.get("sample_url", ""),
                })
                seen_keywords.add(kw)

        if not raw_items:
            print("[Pipeline] latest.json 中无可用数据")
            return {}

        print(f"[Pipeline] 从 latest.json 加载 {len(raw_items)} 条数据")
        return self.run(raw_items)

    # ------------------------------------------------------------------

    def _log(self, msg: str):
        print("\n" + msg)
        if self.db and self.job_id:
            self.db.log_task_step(self.job_id, msg, "Pipeline")

    def _save(self, filename: str, data) -> Path:
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return path

    def _save_to_db(self, recommend: list, review: list, briefs: list):
        if not self.db:
            return

        date_suffix = datetime.now().strftime("%Y%m%d")
        
        # 写入 TrendItems (recommend + review 都写进去)
        all_cards = recommend + review
        
        # Convert Brief list to dict for lookup
        brief_map = {b["trend_name"]: b for b in briefs}

        for card in all_cards:
            # Create a unique ID for this day -> e.g. "news:hashtag-20231018"
            base_hashtag = card["normalized_theme"].replace(" ", "_").lower()
            trend_id = f"news:{base_hashtag}-{date_suffix}"
            
            # Review decision implies if the AI auto-recommends. But EVERYTHING goes to `pending` in UI anyway.
            # We map decision="review" or "recommend" both to 'pending' as defined earlier for manual confirmation.
            
            item = TrendItem(
                id=trend_id,
                source_type="news",
                source_item_id=base_hashtag,
                title=card["normalized_theme"],
                summary=card.get("one_line_interpretation", ""),
                trend_name=card["normalized_theme"],
                trend_type=card.get("theme_type", ""),
                score=card.get("sticker_opportunity_score", 0),
                heat_score=card.get("trend_heat_score", 0),
                fit_level=card.get("sticker_fit_level", ""),
                pack_archetype=card.get("recommended_pack_archetype", ""),
                review_status="pending",  # Enforce manual review
                queue_status="idle",
                decision=card.get("decision", ""),
                platform=card.get("best_platform", []),
                risk_flags=card.get("risk_flags", []),
                visual_symbols=card.get("suggested_visual_symbol_pool", []),
                emotional_core=card.get("core_emotional_hook", []),
                raw_payload={"card": card},
                source_url=""
            )
            self.db.upsert_trend_item(item)
            
            # 写入 TrendBriefRecord
            if card["normalized_theme"] in brief_map:
                brief = brief_map[card["normalized_theme"]]
                self.db.upsert_brief(
                    TrendBriefRecord(
                        trend_id=trend_id,
                        brief_status="generated",
                        brief_json=json.dumps(brief, ensure_ascii=False)
                    )
                )

    def _print_summary(self, result: dict, recommend: list,
                       review: list, briefs: list):
        print("\n" + "=" * 60)
        print("  Sticker Opportunity Pipeline — 结果摘要")
        print("=" * 60)

        print(f"\n[漏斗] 数据转化:")
        print(f"   原始条目:     {result['raw_items']}")
        print(f"   标准化后:     {result['normalized']}")
        print(f"   抽象主题:     {result['themes']}")
        print(f"   硬过滤通过:   {result['filtered']} (拒绝 {result['rejected']})")
        print(f"   推荐(>=70):   {result['recommend']}")
        print(f"   待审核(50-69):{result['review']}")
        print(f"   Trend Briefs: {result['briefs']}")

        if recommend:
            print(f"\n[RECOMMEND] 推荐贴纸选题（>=70 直接生产）:")
            for i, card in enumerate(recommend[:10], 1):
                theme = card["normalized_theme"]
                score = card["sticker_opportunity_score"]
                archetype = card["recommended_pack_archetype"]
                platforms = ", ".join(card.get("best_platform", [])[:2])
                fit = card["sticker_fit_level"]
                print(f"   {i:2d}. {theme[:40]}")
                print(f"       [{archetype}] 机会分:{score} | "
                      f"适配:{fit} | 平台:{platforms}")

        if review:
            print(f"\n[REVIEW] 待审核选题（50-69 需人工确认）:")
            for i, card in enumerate(review[:10], 1):
                theme = card["normalized_theme"]
                score = card["sticker_opportunity_score"]
                archetype = card["recommended_pack_archetype"]
                print(f"   {i:2d}. {theme[:40]}")
                print(f"       [{archetype}] 机会分:{score}")

        if briefs:
            print(f"\n[BRIEF] 已生成 {len(briefs)} 份 trend briefs:")
            for b in briefs[:5]:
                print(f"   >> {b['trend_name']} [{b['trend_type']}] "
                      f"pack: {b['pack_size_goal'].get('sticker_count_range', '?')}")

        print(f"\n[完成] Pipeline 耗时 {result['elapsed_seconds']}s")
        print(f"[输出] {self.output_dir}")
        print("=" * 60)
