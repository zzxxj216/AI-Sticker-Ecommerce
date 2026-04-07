"""Topic 评审 + Brief 生成流水线

流程:
  1. review_new_topics() → 从 DB 读取未审核 hashtag → 组装 Topic Card → AI 审核 → 写回 DB
  2. generate_briefs()   → 从 DB 读取 Approved 且无 Brief → 组装 Reviewed Card → AI 生成 → 写回 DB
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from trend_fetcher.config import config
from trend_fetcher.trend_db import TrendDB
from trend_fetcher.topic_prompts import (
    TOPIC_REVIEW_PROMPT,
    BATCH_TOPIC_REVIEW_PROMPT,
    TOPIC_TO_BRIEF_PROMPT,
    build_topic_card,
    build_reviewed_card,
    parse_review_response,
    parse_batch_review_response,
    parse_brief_response,
)


def _batch_id() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


class TopicPipeline:
    """Topic 评审 + Brief 生成流水线。"""

    def __init__(
        self,
        db: TrendDB,
        *,
        batch_size: int = 10,
        model: str | None = None,
        temperature: float = 0.5,
        max_tokens: int = 3000,
    ):
        self.db = db
        self.batch_size = batch_size
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model or config.OPENAI_MODEL

        self._client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL or None,
        )

    def _chat(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ── Phase A: Topic Review ────────────────────────────

    def review_new_topics(self) -> dict[str, int]:
        """批量审核所有未审核的 hashtag，每次发送 batch_size 条给 LLM。"""
        unreviewed = self.db.get_unreviewed()
        total = len(unreviewed)

        if total == 0:
            row = self.db.conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM tk_hashtags),
                    (SELECT COUNT(*) FROM tk_hashtags WHERE review_status = 'pending'),
                    (SELECT COUNT(*) FROM tk_hashtags
                     WHERE review_status = 'pending' AND (
                         detail_data_json IS NULL OR trim(detail_data_json) = ''
                     )),
                    (SELECT COUNT(*) FROM tk_hashtags WHERE review_status = 'reviewed')
                """
            ).fetchone()
            print(
                "[Pipeline] 没有待审核的话题 "
                f"(库内共 {row[0]} 条；pending {row[1]}；pending 且无详情 {row[2]}；已 reviewed {row[3]})。"
                "若已 reviewed 需重新评分：请重新抓取并写入详情（本次已修复重复行会更新详情并重置为 pending）。"
            )
            return {"total": 0, "approve": 0, "watchlist": 0, "reject": 0, "error": 0}

        print(f"[Pipeline] 开始批量审核 {total} 个话题 (batch_size={self.batch_size})")

        bid = _batch_id()
        stats = {"total": total, "approve": 0, "watchlist": 0, "reject": 0, "error": 0}

        batches = [
            unreviewed[i:i + self.batch_size]
            for i in range(0, total, self.batch_size)
        ]

        processed = 0
        for batch_idx, batch in enumerate(batches, 1):
            names = [r["hashtag_name"] for r in batch]
            print(f"\n  ── 批次 {batch_idx}/{len(batches)} ({len(batch)} 条) ──")
            for n in names:
                print(f"    #{n}")

            cards = [build_topic_card(row) for row in batch]
            combined = "\n\n---TOPIC---\n\n".join(cards)

            try:
                response = self._chat(
                    BATCH_TOPIC_REVIEW_PROMPT,
                    combined,
                    max_tokens=6000,
                )
                parsed_list = parse_batch_review_response(response)

                if len(parsed_list) != len(batch):
                    print(f"  [WARN] LLM 返回 {len(parsed_list)} 条，预期 {len(batch)} 条")

                for j, (row, parsed) in enumerate(zip(batch, parsed_list)):
                    hid = row["hashtag_id"]
                    name = row["hashtag_name"]
                    self.db.save_review(hid, parsed, response if j == 0 else "(batch)", bid)

                    decision = parsed.get("decision", "unknown")
                    stats[decision] = stats.get(decision, 0) + 1
                    score = parsed.get("score_total", 0)
                    processed += 1
                    label = decision.upper()
                    print(f"  [{processed:3d}/{total}] #{name:<30s}  -> {label} ({score}/100)")

            except Exception as e:
                print(f"  [ERROR] 批次 {batch_idx} 失败: {e}")
                # Fallback: 逐条处理
                for row in batch:
                    hid = row["hashtag_id"]
                    name = row["hashtag_name"]
                    processed += 1
                    try:
                        card_text = build_topic_card(row)
                        resp = self._chat(TOPIC_REVIEW_PROMPT, card_text)
                        p = parse_review_response(resp)
                        self.db.save_review(hid, p, resp, bid)
                        d = p.get("decision", "unknown")
                        stats[d] = stats.get(d, 0) + 1
                        s = p.get("score_total", 0)
                        print(f"  [{processed:3d}/{total}] #{name:<30s}  -> {d.upper()} ({s}/100) [fallback]")
                    except Exception as e2:
                        print(f"  [{processed:3d}/{total}] #{name:<30s}  -> ERROR: {e2}")
                        stats["error"] += 1

            if batch_idx < len(batches):
                time.sleep(1)

        print(f"\n[Pipeline] 审核完成: "
              f"Approve {stats['approve']} / "
              f"Watchlist {stats['watchlist']} / "
              f"Reject {stats['reject']} / "
              f"Error {stats['error']}")

        return stats

    # ── Phase B: Brief Generation ────────────────────────

    def generate_briefs(self) -> dict[str, int]:
        """为所有 Approved 且无 Brief 的话题生成 Brief。"""
        pending = self.db.get_approved_without_brief()
        total = len(pending)

        if total == 0:
            print("[Pipeline] 没有待生成 Brief 的话题")
            return {"total": 0, "ready": 0, "not_ready": 0, "error": 0}

        print(f"[Pipeline] 开始生成 Brief: {total} 个话题")

        bid = _batch_id()
        stats = {"total": total, "ready": 0, "not_ready": 0, "error": 0}

        for i, row in enumerate(pending, 1):
            hid = row["hashtag_id"]
            name = row["hashtag_name"]
            review_id = row["review_id"]
            try:
                card_text = build_reviewed_card(row)
                response = self._chat(TOPIC_TO_BRIEF_PROMPT, card_text)
                parsed = parse_brief_response(response)
                self.db.save_brief(hid, review_id, parsed, response, bid)

                brief_status = parsed.get("brief_status", "not_ready")
                stats[brief_status] = stats.get(brief_status, 0) + 1
                trend_name = parsed.get("trend_name", "")
                extra = f" [{trend_name}]" if trend_name else ""
                print(
                    f"  [{i:3d}/{total}] #{name:<30s}  → {brief_status.upper()}{extra}",
                    flush=True,
                )

            except Exception as e:
                print(f"  [{i:3d}/{total}] #{name:<30s}  → ERROR: {e}", flush=True)
                stats["error"] += 1

            if i % self.batch_size == 0 and i < total:
                print(f"  ── 批次 {i // self.batch_size} 完成，休息 2s ──")
                time.sleep(2)

        print(f"\n[Pipeline] Brief 生成完成: "
              f"Ready {stats['ready']} / "
              f"Not Ready {stats['not_ready']} / "
              f"Error {stats['error']}")

        return stats

    # ── Full Pipeline ────────────────────────────────────

    def run_full(self) -> dict[str, Any]:
        """执行完整流水线: review → brief。"""
        print(f"\n{'='*60}")
        print("  Phase A: Topic Review")
        print(f"{'='*60}")
        review_stats = self.review_new_topics()

        print(f"\n{'='*60}")
        print("  Phase B: Brief Generation")
        print(f"{'='*60}")
        brief_stats = self.generate_briefs()

        return {"review": review_stats, "brief": brief_stats}
