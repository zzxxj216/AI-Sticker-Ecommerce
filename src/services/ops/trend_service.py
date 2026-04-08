from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from src.core.logger import get_logger
from src.services.ops.db import OpsDatabase
from src.services.ops.job_service import JobService
from src.services.ops.sync_service import OpsSyncService
from src.models.ops import TrendBriefRecord

logger = logging.getLogger(__name__)
_brief_diag = get_logger("ops.brief", enable_file=True)


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
        try:
            self.db.log_brief_generation(
                trend_id,
                f"手动保存 Brief（{edited_by}）",
                source="manual",
            )
        except Exception:
            pass
        return self.db.get_brief(trend_id) or {}


class TrendService:
    def __init__(self, db: OpsDatabase | None = None):
        self.db = db or OpsDatabase()
        self.sync_service = OpsSyncService(self.db)
        self.job_service = JobService(self.db)
        self.brief_service = BriefService(self.db)

    def sync(self) -> dict[str, int]:
        """TikTok 等数据同步到 trend_items，供 FastAPI startup 调用。"""
        return self.sync_service.sync_all()

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
            stale_news = self.db.skip_stale_pending_trends("news", batch_date)
            if job_id:
                self.db.log_task_step(
                    job_id,
                    f"[Stage 1] Auto-skipped {stale_news} stale pending news (batch_date < {batch_date})",
                )
            self.db.log_task_step(job_id, "[Stage 1] News Pipeline completed successfully")
            
            # 2. Run TikTok Pipeline
            self.db.log_task_step(job_id, "[Stage 2] Running TikTok Crawl Pipeline")
            self.crawl_tiktok(job_id=job_id)
            self.db.log_task_step(job_id, "[Stage 2] TikTok Crawl Pipeline completed successfully")

            # Mark as finished
            self.db.update_sys_task(job_id, "completed", '{"status": "success"}')
            self.db.log_task_step(job_id, "Daily unified pipeline completed entirely")
            
        except Exception as e:
            import traceback as _tb
            tb_str = _tb.format_exc()
            self.db.log_task_step(job_id, f"Pipeline Error: {e}\n{tb_str}", log_level="ERROR")
            self.db.update_sys_task(job_id, "failed", json.dumps({"error": str(e)}))
            logger.exception("run_daily_pipelines failed")

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

        # AI Review + Theme Family Generation (v2 pipeline)
        review_stats: dict = {}
        family_stats: dict = {}
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

            family_stats = pipeline.generate_theme_families()
            if job_id:
                self.db.log_task_step(
                    job_id,
                    f"[TikTok] Theme Family done: generated={family_stats.get('generated',0)}, "
                    f"error={family_stats.get('error',0)}"
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

        from datetime import datetime, timezone, timedelta
        _cn = timezone(timedelta(hours=8))
        tk_batch = datetime.now(_cn).strftime("%Y-%m-%d")
        stale_tk = self.db.skip_stale_pending_trends("tiktok", tk_batch)
        if job_id:
            self.db.log_task_step(
                job_id,
                f"[TikTok] Auto-skipped {stale_tk} stale pending (batch_date < {tk_batch})",
            )

        return {
            "new": db_stats.get("new", 0),
            "duplicate": db_stats.get("duplicate", 0),
            "total_in_db": db_stats.get("total", 0),
            "reviewed": review_stats.get("total", 0),
            "synced": sync_count,
        }

    def list_trends(
        self,
        source_type: str | None = None,
        status: str | None = 'pending',
        batch_date: str | None = None,
    ) -> list[dict]:
        return self.db.list_trends(source_type, status=status, batch_date=batch_date)
        
    def list_approved_trends(self) -> list[dict]:
        return self.db.list_approved_trends()

    def list_archive_trends(self, search_text: str | None = None, page: int = 1, per_page: int = 50,
                            sort_by: str = 'created_at', sort_dir: str = 'desc',
                            date_from: str = '', date_to: str = '') -> tuple[list[dict], int]:
        offset = (page - 1) * per_page
        return self.db.list_archive_trends(search_text=search_text, limit=per_page, offset=offset,
                                           sort_by=sort_by, sort_dir=sort_dir,
                                           date_from=date_from, date_to=date_to)

    def get_trend(self, trend_id: str) -> dict | None:
        trend = self.db.get_trend(trend_id)
        if not trend:
            return None
        trend["brief"] = self.db.get_brief(trend_id)
        return trend

    def approve_trend(self, trend_id: str, reviewed_by: str = "system") -> dict | None:
        self.db.set_trend_review(trend_id, "approved", "recommend", reviewed_by=reviewed_by)
        return self.get_trend(trend_id)

    def enqueue_brief_after_approve_if_needed(
        self,
        trend_id: str,
        reviewer: str,
        log_source: str,
        background_tasks,
    ) -> None:
        """列表页 API 与详情页表单采纳共用：无可用 Brief 时排队后台生成。"""
        existing_brief = self.db.get_brief(trend_id)
        if existing_brief and existing_brief.get("brief_json"):
            return
        self.db.upsert_brief(
            TrendBriefRecord(
                trend_id=trend_id,
                brief_status="generating",
                brief_json={},
            )
        )
        self.db.log_brief_generation(
            trend_id,
            f"采纳后已排队后台 Brief 生成（审核人 {reviewer}）",
            source=log_source,
        )
        background_tasks.add_task(self.generate_brief_background, trend_id)

    def _brief_trace(
        self,
        trend_id: str,
        message: str,
        log_level: str = "INFO",
        source: str = "trend_service",
    ) -> None:
        try:
            self.db.log_brief_generation(trend_id, message, log_level, source)
        except Exception as exc:
            logger.warning("persist brief gen log failed: %s", exc)
        line = f"[{trend_id}] {message}"
        if log_level == "ERROR":
            _brief_diag.error(line)
        elif log_level in ("WARN", "WARNING"):
            _brief_diag.warning(line)
        else:
            _brief_diag.info(line)

    def _generate_brief_on_approve(self, trend_id: str) -> None:
        """人工采纳后，为没有 Brief 的 trend 补生成（同步调用，可由后台任务驱动）。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            self._brief_trace(trend_id, "跳过生成：trend 不存在", "WARN", "sync_brief")
            return
        source_type = trend.get("source_type", "")
        self._brief_trace(
            trend_id,
            f"开始生成 Brief（source_type={source_type}）",
            source="sync_brief",
        )
        try:
            if source_type == "tiktok":
                self._generate_tiktok_brief(trend_id, trend)
            else:
                self._generate_news_brief(trend_id, trend)
            logger.info("Brief generated on approve for %s", trend_id)
        except Exception as e:
            logger.warning("Brief generation failed for %s: %s", trend_id, e)
            raise

    def generate_brief_background(self, trend_id: str) -> None:
        """后台任务入口：调用 LLM 生成 Brief。调用前应先标记 brief_status=generating。"""
        t0 = time.monotonic()
        self._brief_trace(trend_id, "后台 AI Brief 任务开始执行", source="background")
        try:
            self._generate_brief_on_approve(trend_id)
            elapsed = time.monotonic() - t0
            self._brief_trace(
                trend_id,
                f"后台 AI Brief 生成成功，耗时 {elapsed:.1f}s",
                source="background",
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            self._brief_trace(
                trend_id,
                f"后台 AI Brief 失败（{elapsed:.1f}s）: {e}",
                "ERROR",
                "background",
            )
            self.db.upsert_brief(TrendBriefRecord(
                trend_id=trend_id,
                brief_status="failed",
                brief_json={},
                source_ref=str(e)[:500],
            ))
            logger.exception("Background brief generation failed for %s", trend_id)

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
        self._brief_trace(trend_id, "规则引擎 Brief 已写入（非 LLM）", source="news_brief")

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
        # 人工采纳后生成 Brief：系统 Prompt 要求 Watchlist 可不写 Brief，会导致模型拒答或只写 PART1
        card_text += (
            "\n\n【工作台强制指令】运营已在系统内点击「采纳」，必须交付下游卡贴可用的完整 Brief。"
            "无论卡片上原决策为 Approve 或 Watchlist，均须输出「Brief Ready」"
            "并写满 PART 2 全部字段；保持 ===== PART 1/2/3 ===== 三段结构；"
            "PART 2 中每个字段以「-- 字段名」单独起行（与系统模板一致）。"
            "禁止仅输出 Brief Not Ready 或省略 PART 2。"
        )
        base = (config.OPENAI_BASE_URL or "").strip() or "default"
        self._brief_trace(
            trend_id,
            f"调用 LLM：model={config.OPENAI_MODEL} base={base[:80]}",
            source="tiktok_brief",
        )
        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL or None,
        )
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_TO_BRIEF_PROMPT},
                {"role": "user", "content": card_text},
            ],
            temperature=0.5,
            max_tokens=3000,
        )
        api_s = time.monotonic() - t0
        response_text = resp.choices[0].message.content or ""
        self._brief_trace(
            trend_id,
            f"LLM 返回：{api_s:.1f}s，原文长度 {len(response_text)} 字符",
            source="tiktok_brief",
        )
        parsed = parse_brief_response(response_text)

        brief_data = {k: v for k, v in parsed.items() if k != "brief_status" and v}
        if not brief_data:
            self._brief_trace(
                trend_id,
                "Brief 解析后仍无有效字段，请重试或手动编辑 Brief",
                "ERROR",
                source="tiktok_brief",
            )
            raise ValueError(
                "TikTok Brief 解析失败：模型未按「-- 字段名」或 PART 结构输出。"
                "请点击「重新生成 Brief」；若多次失败请手动填写 Brief。"
            )
        self.db.upsert_brief(TrendBriefRecord(
            trend_id=trend_id,
            brief_status="generated",
            brief_json=brief_data,
            source_ref=response_text[:2000],
        ))
        self._brief_trace(
            trend_id,
            f"TikTok AI Brief 已落库，有效字段数 {len(brief_data)}",
            source="tiktok_brief",
        )

    def restore_trend(self, trend_id: str, restored_by: str = "system") -> dict | None:
        """Restore a skipped trend back to pending status."""
        self.db.set_trend_review(trend_id, "pending", "", reviewed_by=restored_by)
        return self.get_trend(trend_id)

    def revert_approved_awaiting_brief_to_pending(self, reverted_by: str = "system:revert-awaiting-brief") -> int:
        """待生产素材中：已采纳但 Brief 未就绪的条目全部改回「需要审核」。"""
        return self.db.revert_approved_awaiting_brief_to_pending(reverted_by=reverted_by)

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
            brief_status = (brief or {}).get("brief_status", "none")
            if brief_status == "generating":
                raise ValueError("Brief 正在由 AI 生成中，请稍后再试")
            raise ValueError("Brief 尚未生成，请等待 AI 生成完成或在详情页手动编辑 Brief")
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
        fid = job.get("family_id")
        if fid:
            try:
                tdb = self._get_trend_db()
                fam = tdb.get_theme_family(int(fid))
                if fam:
                    job["parent_theme"] = fam.get("parent_theme") or ""
            except (ValueError, TypeError):
                job["parent_theme"] = ""
        return job

    def retry_job(self, job_id: str) -> dict:
        return self.job_service.retry_job(job_id)

    # ── Theme Family Service Methods ─────────────────────

    def _get_trend_db(self):
        from trend_fetcher.trend_db import TrendDB
        return TrendDB("data/ops_workbench.db")

    def get_theme_family(self, trend_id: str) -> dict | None:
        """获取 trend 对应的主题家族详情（含子题材和 brief 状态）。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            return None

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "")
        if not hashtag_id:
            source_item_id = trend.get("source_item_id", "")
            if source_item_id:
                hashtag_id = source_item_id

        if not hashtag_id:
            return None

        tdb = self._get_trend_db()
        family = tdb.get_family_by_hashtag(hashtag_id)
        if not family:
            return None

        subthemes = tdb.list_subthemes(family["id"])
        for sub in subthemes:
            brief = tdb.get_subtheme_brief(sub["id"])
            sub["brief"] = brief

        family["subthemes"] = subthemes
        family["trend"] = trend
        return family

    def generate_theme_family_background(self, trend_id: str) -> dict:
        """后台为单个 trend 生成主题家族。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "")
        if not hashtag_id:
            hashtag_id = trend.get("source_item_id", "")

        tdb = self._get_trend_db()

        existing = tdb.get_family_by_hashtag(hashtag_id)
        if existing:
            tdb.delete_family(existing["id"])

        from trend_fetcher.topic_pipeline import TopicPipeline
        pipeline = TopicPipeline(db=tdb)

        hashtag_row = tdb.get_hashtag(hashtag_id)
        if not hashtag_row:
            raise ValueError(f"TikTok hashtag not found: {hashtag_id}")

        review = tdb.conn.execute(
            "SELECT * FROM tk_topic_reviews WHERE hashtag_id = ? ORDER BY id DESC LIMIT 1",
            (hashtag_id,)
        ).fetchone()
        if not review:
            raise ValueError(f"No review found for hashtag: {hashtag_id}")
        review = dict(review)

        from trend_fetcher.topic_prompts import (
            TOPIC_TO_THEME_FAMILY_PROMPT,
            build_family_input,
            parse_theme_family_response,
        )

        merged = {**hashtag_row, **review}
        input_text = build_family_input(merged, merged)

        from trend_fetcher.config import config
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL or None)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_TO_THEME_FAMILY_PROMPT},
                {"role": "user", "content": input_text},
            ],
            temperature=0.5,
            max_tokens=4000,
        )
        response_text = resp.choices[0].message.content or ""
        parsed = parse_theme_family_response(response_text)
        family_id = tdb.save_theme_family(hashtag_id, review["id"], parsed, response_text)

        self.sync_service.sync_tiktok_pipeline()

        return {"family_id": family_id, "subtheme_count": len(parsed.get("subthemes", []))}

    # ── 部分重生成 / 补充扩展 ─────────────────────────────

    def _resolve_hashtag_and_family(self, trend_id: str):
        """内部工具：从 trend_id 解析 hashtag_id、tdb、family、review、hashtag_row。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "") or trend.get("source_item_id", "")

        tdb = self._get_trend_db()
        family = tdb.get_family_by_hashtag(hashtag_id)
        if not family:
            raise ValueError("Theme family not found")

        hashtag_row = tdb.get_hashtag(hashtag_id)
        if not hashtag_row:
            raise ValueError(f"TikTok hashtag not found: {hashtag_id}")

        review = tdb.conn.execute(
            "SELECT * FROM tk_topic_reviews WHERE hashtag_id = ? ORDER BY id DESC LIMIT 1",
            (hashtag_id,)
        ).fetchone()
        if not review:
            raise ValueError(f"No review found for hashtag: {hashtag_id}")
        review = dict(review)

        return tdb, family, hashtag_row, review

    def _call_family_llm(self, review_row, topic_row, **build_kwargs) -> dict:
        """调用 LLM 生成 theme family 并返回 parsed JSON。"""
        from trend_fetcher.topic_prompts import (
            TOPIC_TO_THEME_FAMILY_PROMPT,
            build_family_input,
            parse_theme_family_response,
        )
        from trend_fetcher.config import config
        from openai import OpenAI

        input_text = build_family_input(review_row, topic_row, **build_kwargs)
        client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL or None)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_TO_THEME_FAMILY_PROMPT},
                {"role": "user", "content": input_text},
            ],
            temperature=0.5,
            max_tokens=4000,
        )
        return parse_theme_family_response(resp.choices[0].message.content or "")

    def regenerate_selected_subthemes(self, trend_id: str, subtheme_ids: list[int]) -> dict:
        """删除选中的子题材，让 AI 生成替换子题材写回同一家族。"""
        tdb, family, hashtag_row, review = self._resolve_hashtag_and_family(trend_id)

        all_subs = tdb.list_subthemes(family["id"])
        to_replace = [s for s in all_subs if s["id"] in subtheme_ids]
        remaining = [s for s in all_subs if s["id"] not in subtheme_ids]

        if not to_replace:
            raise ValueError("No matching subthemes found to replace")

        merged = {**hashtag_row, **review}
        parsed = self._call_family_llm(
            merged, merged,
            existing_subthemes=remaining,
            mode="replace",
        )
        new_subs = parsed.get("subthemes", [])

        tdb.delete_subthemes_by_ids(subtheme_ids)
        new_ids = tdb.add_subthemes_to_family(family["id"], new_subs)

        self.sync_service.sync_tiktok_pipeline()
        return {"replaced": len(subtheme_ids), "new_count": len(new_ids), "new_ids": new_ids}

    def supplement_theme_family(self, trend_id: str) -> dict:
        """保留现有子题材，AI 补充新的子题材到家族中。"""
        tdb, family, hashtag_row, review = self._resolve_hashtag_and_family(trend_id)

        existing_subs = tdb.list_subthemes(family["id"])
        merged = {**hashtag_row, **review}
        parsed = self._call_family_llm(
            merged, merged,
            existing_subthemes=existing_subs,
            mode="supplement",
        )
        new_subs = parsed.get("subthemes", [])
        new_ids = tdb.add_subthemes_to_family(family["id"], new_subs)

        self.sync_service.sync_tiktok_pipeline()
        return {"existing": len(existing_subs), "added": len(new_ids), "new_ids": new_ids}

    def save_allocation(self, trend_id: str, allocations: list[dict]) -> dict:
        """保存人工确认的配量。allocations: [{subtheme_id, selected, priority, target_sticker_count, allocation_notes}]"""
        tdb = self._get_trend_db()
        for alloc in allocations:
            tdb.update_subtheme_allocation(alloc["subtheme_id"], alloc)

        trend = self.db.get_trend(trend_id)
        raw = trend.get("raw_payload", {}) if trend else {}
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "") or trend.get("source_item_id", "")

        family = tdb.get_family_by_hashtag(hashtag_id)
        if family:
            tdb.finalize_allocation(family["id"], hashtag_id)

        self.sync_service.sync_tiktok_pipeline()
        return {"status": "ok", "updated": len(allocations)}

    def confirm_ai_allocation(self, trend_id: str) -> dict:
        """一键确认 AI 推荐配量。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "") or trend.get("source_item_id", "")

        tdb = self._get_trend_db()
        family = tdb.get_family_by_hashtag(hashtag_id)
        if not family:
            raise ValueError("Theme family not found")

        tdb.confirm_ai_allocation(family["id"])
        tdb.finalize_allocation(family["id"], hashtag_id)
        self.sync_service.sync_tiktok_pipeline()
        return {"status": "ok", "family_id": family["id"]}

    def generate_subtheme_briefs_background(self, trend_id: str) -> dict:
        """为 trend 下的选中子题材批量生成 Brief。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "") or trend.get("source_item_id", "")

        tdb = self._get_trend_db()
        family = tdb.get_family_by_hashtag(hashtag_id)
        if not family:
            raise ValueError("Theme family not found")

        from trend_fetcher.topic_pipeline import TopicPipeline
        pipeline = TopicPipeline(db=tdb)
        stats = pipeline.generate_subtheme_briefs(family["id"])

        self.sync_service.sync_tiktok_pipeline()
        return stats

    def queue_subtheme(self, subtheme_id: int, created_by: str = "system") -> dict:
        """单个子题材入队规划。"""
        tdb = self._get_trend_db()
        sub = tdb.get_subtheme(subtheme_id)
        if not sub:
            raise ValueError(f"Subtheme not found: {subtheme_id}")

        brief = tdb.get_subtheme_brief(subtheme_id)
        if not brief or not brief.get("brief_json"):
            raise ValueError("Subtheme Brief 尚未生成")

        family = tdb.get_theme_family(sub["family_id"])
        if not family:
            raise ValueError("Theme family not found")

        hashtag = tdb.get_hashtag(family["hashtag_id"])
        hashtag_name = (hashtag or {}).get("hashtag_name", "")

        trend_id = f"tiktok:{family['hashtag_id']}"
        trend_name = sub.get("subtheme_name", "")

        brief_json = json.loads(brief["brief_json"]) if isinstance(brief["brief_json"], str) else brief["brief_json"]

        job = self.job_service.create_job(
            trend_id, trend_name, created_by,
            family_id=str(family["id"]),
            subtheme_id=subtheme_id,
            variant_label=trend_name,
        )
        self.job_service.start_job_async(job.id, brief_json, trend_name)

        tdb.conn.execute(
            "UPDATE tk_subthemes SET status = 'queued' WHERE id = ?", (subtheme_id,)
        )
        tdb.conn.commit()

        return self.db.get_job(job.id) or job.model_dump()

    def queue_family(self, trend_id: str, created_by: str = "system") -> list[dict]:
        """批量入队整个家族中已有 Brief 的选中子题材。"""
        trend = self.db.get_trend(trend_id)
        if not trend:
            raise ValueError(f"Trend not found: {trend_id}")

        raw = trend.get("raw_payload", {})
        hashtag_id = raw.get("hashtag", {}).get("hashtag_id", "") or trend.get("source_item_id", "")

        tdb = self._get_trend_db()
        family = tdb.get_family_by_hashtag(hashtag_id)
        if not family:
            raise ValueError("Theme family not found")

        ready_subs = tdb.get_subthemes_with_brief(family["id"])
        jobs = []
        for sub in ready_subs:
            if sub.get("status") in ("queued", "completed"):
                continue
            try:
                job = self.queue_subtheme(sub["id"], created_by)
                jobs.append(job)
            except Exception as e:
                logger.warning("Failed to queue subtheme %s: %s", sub["id"], e)
        return jobs
