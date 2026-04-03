from __future__ import annotations

import json
import logging
from datetime import datetime

from src.services.ops.db import OpsDatabase
from src.services.ops.job_service import JobService
from src.services.ops.sync_service import OpsSyncService
from src.models.ops import TrendBriefRecord

logger = logging.getLogger(__name__)


class BriefService:
    def __init__(self, db: OpsDatabase | None = None):
        self.db = db or OpsDatabase()

    def get_brief(self, trend_id: str) -> dict | None:
        return self.db.get_brief(trend_id)

    def save_brief(self, trend_id: str, brief_json: dict, edited_by: str = "system") -> dict:
        existing = self.db.get_brief(trend_id)
        from src.models.ops import TrendBriefRecord

        record = TrendBriefRecord(
            trend_id=trend_id,
            brief_status="ready",
            brief_json=brief_json,
            source_ref=(existing or {}).get("source_ref", "manual"),
            edited_by=edited_by,
        )
        self.db.upsert_brief(record)
        return self.db.get_brief(trend_id) or {}


class TrendService:
    def __init__(self, db: OpsDatabase | None = None):
        self.db = db or OpsDatabase()
        self.sync_service = OpsSyncService(self.db)
        self.job_service = JobService(self.db)
        self.brief_service = BriefService(self.db)

    def trigger_background_pipeline(self, background_tasks) -> str:
        job_id = f"job_crawl_{int(__import__('time').time())}"
        self.db.create_sys_task(job_id, "daily_pipeline")
        
        # Fire and forget
        background_tasks.add_task(self.run_daily_pipelines, job_id)
        
        return job_id

    def run_daily_pipelines(self, job_id: str):
        try:
            self.db.log_task_step(job_id, "Starting daily unified pipeline (News + TikTok)")
            
            # 1. Run News Pipeline
            self.db.log_task_step(job_id, "[Stage 1] Running News Aggregation Pipeline")
            import os
            from trend_fetcher.main import fetch_raw_data
            from trend_fetcher.sticker_pipeline import StickerOpportunityPipeline

            raw_data = fetch_raw_data()

            all_raw_items = []
            for items in raw_data.values():
                all_raw_items.extend(items)
                
            from datetime import datetime
            from datetime import timezone, timedelta
            batch_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            self.db.insert_raw_news(all_raw_items, batch_date)

            # Pass the db instance and job_id so pipeline logs to sys_task_logs and DB
            pipeline = StickerOpportunityPipeline(db=self.db, job_id=job_id)
            pipeline.run(all_raw_items)
            self.db.log_task_step(job_id, "[Stage 1] News Pipeline completed successfully")
            
            # 2. Run TikTok Pipeline
            self.db.log_task_step(job_id, "[Stage 2] Running TikTok Crawl Pipeline")
            self.crawl_tiktok(job_id=job_id)
            self.db.log_task_step(job_id, "[Stage 2] TikTok Crawl Pipeline completed successfully")

            # Mark as finished
            self.db.update_sys_task(job_id, "completed", '{"status": "success"}')
            self.db.log_task_step(job_id, "Daily unified pipeline completed entirely")
            
        except Exception as e:
            self.db.log_task_step(job_id, f"Pipeline Error: {e}", log_level="ERROR")
            self.db.update_sys_task(job_id, "failed", f'{{"error": "{str(e)}"}}')
            import traceback
            traceback.print_exc()

    def crawl_tiktok(self, job_id: str = None) -> dict:
        """Run TikTok fetcher → write to tk_ tables in ops_workbench.db → sync to ops db trend_items."""
        import os
        from pathlib import Path

        try:
            from trend_fetcher.fetchers.tiktok import TikTokFetcher
            from trend_fetcher.trend_db import TrendDB
        except ImportError as e:
            raise RuntimeError(
                f"TikTok 抓取依赖未安装: {e}. 请运行 pip install playwright && playwright install chromium"
            ) from e

        # We enforce single DB approach
        tiktok_db_path = Path("data/ops_workbench.db")

        fetcher = TikTokFetcher(
            country=os.getenv("TIKTOK_COUNTRY", "US"),
            period=int(os.getenv("TIKTOK_PERIOD", "7")),
            headed=False,
        )
        
        msg = f"Fetching TikTok data (country={fetcher.country}, period={fetcher.period}d)"
        if job_id:
            self.db.log_task_step(job_id, msg)

        crawl_result = fetcher.fetch(
            fetch_details=True,
            max_pages=2,
        )

        trend_db = TrendDB(tiktok_db_path)
        db_stats = trend_db.upsert_crawl(crawl_result)

        # AI Review + Brief Generation
        review_stats: dict = {}
        brief_stats: dict = {}
        if job_id:
            self.db.log_task_step(job_id, "[TikTok] Running AI topic review...")
        try:
            from trend_fetcher.topic_pipeline import TopicPipeline
            pipeline = TopicPipeline(db=trend_db)
            review_stats = pipeline.review_new_topics()
            if job_id:
                self.db.log_task_step(
                    job_id,
                    f"[TikTok] Review done: approve={review_stats.get('approve',0)}, "
                    f"watchlist={review_stats.get('watchlist',0)}, reject={review_stats.get('reject',0)}"
                )

            brief_stats = pipeline.generate_briefs()
            if job_id:
                self.db.log_task_step(
                    job_id,
                    f"[TikTok] Brief done: ready={brief_stats.get('ready',0)}, "
                    f"not_ready={brief_stats.get('not_ready',0)}"
                )
        except Exception as e:
            if job_id:
                self.db.log_task_step(job_id, f"[TikTok] AI pipeline error (non-fatal): {e}", log_level="WARN")
            import traceback
            traceback.print_exc()

        msg = "TikTok fetch & mapping to TrendItem (via sync)"
        if job_id:
            self.db.log_task_step(job_id, msg)
            
        sync_count = self.sync_service.sync_tiktok_pipeline()

        return {
            "new": db_stats.get("new", 0),
            "duplicate": db_stats.get("duplicate", 0),
            "total_in_db": db_stats.get("total", 0),
            "reviewed": review_stats.get("total", 0),
            "synced": sync_count,
        }

    def list_trends(self, source_type: str | None = None) -> list[dict]:
        return self.db.list_trends(source_type)
        
    def list_approved_trends(self) -> list[dict]:
        return self.db.list_approved_trends()

    def list_archive_trends(self, search_text: str | None = None, page: int = 1, per_page: int = 50) -> tuple[list[dict], int]:
        offset = (page - 1) * per_page
        return self.db.list_archive_trends(search_text=search_text, limit=per_page, offset=offset)

    def get_trend(self, trend_id: str) -> dict | None:
        trend = self.db.get_trend(trend_id)
        if not trend:
            return None
        trend["brief"] = self.db.get_brief(trend_id)
        return trend

    def approve_trend(self, trend_id: str, reviewed_by: str = "system") -> dict | None:
        self.db.set_trend_review(trend_id, "approved", "recommend", reviewed_by=reviewed_by)
        existing_brief = self.db.get_brief(trend_id)
        if not existing_brief or not existing_brief.get("brief_json"):
            self._generate_brief_on_approve(trend_id)
        return self.get_trend(trend_id)

    def _generate_brief_on_approve(self, trend_id: str) -> None:
        """人工采纳后，为没有 Brief 的 trend 补生成。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            return
        source_type = trend.get("source_type", "")
        try:
            if source_type == "tiktok":
                self._generate_tiktok_brief(trend_id, trend)
            else:
                self._generate_news_brief(trend_id, trend)
            logger.info("Brief generated on approve for %s", trend_id)
        except Exception as e:
            logger.warning("Brief generation failed for %s: %s", trend_id, e)

    def _generate_news_brief(self, trend_id: str, trend: dict) -> None:
        """用规则引擎为 News 类 trend 生成 Brief（和批量管线同一套逻辑）。"""
        from trend_fetcher.sticker_pipeline.brief_builder import BriefBuilder

        card = trend.get("raw_payload", {}).get("card", {})
        if not card:
            card = {
                "normalized_theme": trend.get("trend_name") or trend.get("title", ""),
                "theme_type": trend.get("trend_type", ""),
                "recommended_pack_archetype": trend.get("pack_archetype", "object_icon_pack"),
                "core_emotional_hook": trend.get("emotional_core", []),
                "suggested_visual_symbol_pool": trend.get("visual_symbols", []),
                "best_platform": trend.get("platform", []),
                "one_line_interpretation": trend.get("summary", ""),
                "sticker_opportunity_score": trend.get("score", 0),
                "trend_heat_score": trend.get("heat_score", 0),
                "risk_flags": trend.get("risk_flags", []),
            }

        builder = BriefBuilder()
        brief_data = builder._card_to_brief(card)
        self.db.upsert_brief(TrendBriefRecord(
            trend_id=trend_id,
            brief_status="generated",
            brief_json=brief_data,
        ))

    def _generate_tiktok_brief(self, trend_id: str, trend: dict) -> None:
        """用 LLM 为 TikTok 类 trend 生成 Brief（和 TopicPipeline 同一套 Prompt）。"""
        from trend_fetcher.topic_prompts import (
            TOPIC_TO_BRIEF_PROMPT, build_reviewed_card, parse_brief_response,
        )
        from trend_fetcher.config import config
        from openai import OpenAI

        raw = trend.get("raw_payload", {})
        review = raw.get("review", {})
        hashtag = raw.get("hashtag", {})

        row = {
            "hashtag_name": hashtag.get("hashtag_name", trend.get("title", "")),
            "decision": review.get("decision", "approve"),
            "normalized_theme": review.get("normalized_theme", trend.get("trend_name", "")),
            "theme_type": review.get("theme_type", trend.get("trend_type", "")),
            "one_line_interpretation": review.get("one_line_interpretation", trend.get("summary", "")),
            "pack_archetype": review.get("pack_archetype", trend.get("pack_archetype", "")),
            "best_platform": review.get("best_platform", ""),
            "visual_symbols": review.get("visual_symbols", ""),
            "emotional_hooks": review.get("emotional_hooks", ""),
            "risk_flags": review.get("risk_flags", ""),
            "score_total": review.get("score_total", 0),
            "sticker_fit_level": review.get("sticker_fit_level", ""),
        }

        card_text = build_reviewed_card(row)
        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL or None,
        )
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_TO_BRIEF_PROMPT},
                {"role": "user", "content": card_text},
            ],
            temperature=0.5,
            max_tokens=3000,
        )
        response_text = resp.choices[0].message.content or ""
        parsed = parse_brief_response(response_text)

        brief_data = {k: v for k, v in parsed.items() if k != "brief_status" and v}
        self.db.upsert_brief(TrendBriefRecord(
            trend_id=trend_id,
            brief_status="generated",
            brief_json=brief_data,
            source_ref=response_text[:2000],
        ))

    def restore_trend(self, trend_id: str, restored_by: str = "system") -> dict | None:
        """Restore a skipped trend back to pending status."""
        self.db.set_trend_review(trend_id, "pending", "", reviewed_by=restored_by)
        return self.get_trend(trend_id)

    def skip_trend(self, trend_id: str, reviewed_by: str = "system") -> dict | None:
        self.db.set_trend_review(trend_id, "skipped", "skip", reviewed_by=reviewed_by)
        self.db.set_trend_queue_status(trend_id, "idle")
        return self.get_trend(trend_id)

    def queue_trend(self, trend_id: str, created_by: str = "system") -> dict:
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")
        brief = self.db.get_brief(trend_id)
        if not brief or not brief.get("brief_json"):
            raise ValueError("Trend brief missing")
        trend_name = trend.get("trend_name") or trend.get("title") or trend_id
        job = self.job_service.create_job(trend_id, trend_name, created_by)
        self.job_service.start_job_async(job.id, brief["brief_json"], trend_name)
        return self.db.get_job(job.id) or job.model_dump()

    def list_jobs(self) -> list[dict]:
        return self.db.list_jobs()

    def get_job_detail(self, job_id: str) -> dict | None:
        job = self.db.get_job(job_id)
        if not job:
            return None
        job["outputs"] = self.db.list_outputs(job_id)
        return job

    def retry_job(self, job_id: str) -> dict:
        return self.job_service.retry_job(job_id)
