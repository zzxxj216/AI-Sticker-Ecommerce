"""TikTok 热点数据持久化 — JSON 文件数据库

功能:
  - 按 hashtag_id 去重：同一个 hashtag 不会重复入库
  - 增量追加：每次只写入新 hashtag，已有的跳过
  - 保留完整原始爬取数据（list_data / detail_data / creators_raw）
  - 每条记录带 crawled_at 时间戳，方便追溯
  - 每次抓取生成爬取日志

数据库结构（单个 JSON 文件）:
    {
        "meta": { "last_updated": ..., "total": ... },
        "hashtags": {
            "<hashtag_id>": {
                "list_data": { ... 列表 API 原始数据 },
                "detail_data": { ... __NEXT_DATA__ 原始数据 | null },
                "creators_raw": [ ... creator API 原始 | [] ],
                "found_in_filters": ["all", "new_on_board"],
                "crawled_at": "2026-03-31T10:00:00+00:00",
                "first_seen_at": "2026-03-31T10:00:00+00:00",
                "last_seen_at": "2026-03-31T10:00:00+00:00",
            }
        },
        "crawl_log": [
            { "crawled_at": ..., "country": ..., "new": 5, "duplicate": 95, "total_after": 150 }
        ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrendStore:
    """基于 JSON 文件的热点数据库，支持增量去重。"""

    def __init__(self, db_path: str | Path = "data/tiktok_trends_db.json"):
        self.db_path = Path(db_path)
        self._db: dict[str, Any] | None = None

    # ── 数据库读写 ────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._db is not None:
            return self._db

        if self.db_path.exists():
            with open(self.db_path, "r", encoding="utf-8") as f:
                self._db = json.load(f)
        else:
            self._db = {
                "meta": {"created_at": _now_iso(), "last_updated": None, "total": 0},
                "hashtags": {},
                "crawl_log": [],
            }
        return self._db

    def _save(self) -> None:
        db = self._load()
        db["meta"]["total"] = len(db["hashtags"])
        db["meta"]["last_updated"] = _now_iso()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

    # ── 核心方法 ──────────────────────────────────────────

    def upsert_crawl(self, crawl_result: dict[str, Any]) -> dict[str, int]:
        """将一次爬取结果写入数据库，按 hashtag_id 去重。

        Args:
            crawl_result: TikTokFetcher.fetch() 的返回值
                {
                    "meta": { ... },
                    "hashtags": { "<hashtag_id>": { ... } }
                }

        Returns:
            {"new": N, "duplicate": M, "updated": K, "total": T}
        """
        db = self._load()
        existing = db["hashtags"]
        incoming = crawl_result.get("hashtags", {})
        crawl_meta = crawl_result.get("meta", {})
        crawl_ts = crawl_meta.get("crawled_at", _now_iso())

        new_count = 0
        dup_count = 0
        updated_count = 0

        for hid, data in incoming.items():
            if hid in existing:
                # 已存在：更新 last_seen_at，保留首次数据
                existing[hid]["last_seen_at"] = crawl_ts
                # 合并 found_in_filters（去重）
                old_filters = set(existing[hid].get("found_in_filters", []))
                new_filters = set(data.get("found_in_filters", []))
                existing[hid]["found_in_filters"] = sorted(old_filters | new_filters)
                dup_count += 1
            else:
                # 新 hashtag：完整写入
                entry = {
                    "list_data": data.get("list_data"),
                    "detail_data": data.get("detail_data"),
                    "creators_raw": data.get("creators_raw", []),
                    "found_in_filters": data.get("found_in_filters", []),
                    "crawled_at": data.get("crawled_at", crawl_ts),
                    "first_seen_at": crawl_ts,
                    "last_seen_at": crawl_ts,
                }
                existing[hid] = entry
                new_count += 1

        # 记录爬取日志
        log_entry = {
            "crawled_at": crawl_ts,
            "country": crawl_meta.get("country", ""),
            "period": crawl_meta.get("period", 0),
            "filters": crawl_meta.get("filters", []),
            "new": new_count,
            "duplicate": dup_count,
            "updated": updated_count,
            "total_after": len(existing),
        }
        db["crawl_log"].append(log_entry)

        self._save()

        stats = {
            "new": new_count,
            "duplicate": dup_count,
            "updated": updated_count,
            "total": len(existing),
        }
        return stats

    # ── 查询方法 ──────────────────────────────────────────

    def get_all_ids(self) -> set[str]:
        """返回数据库中所有 hashtag_id。"""
        db = self._load()
        return set(db["hashtags"].keys())

    def get_hashtag(self, hashtag_id: str) -> dict | None:
        db = self._load()
        return db["hashtags"].get(hashtag_id)

    def get_by_name(self, name: str) -> dict | None:
        db = self._load()
        for hid, data in db["hashtags"].items():
            if data.get("list_data", {}).get("hashtag_name") == name:
                return {**data, "hashtag_id": hid}
        return None

    def total(self) -> int:
        db = self._load()
        return len(db["hashtags"])

    def crawl_history(self) -> list[dict]:
        db = self._load()
        return db.get("crawl_log", [])

    def list_hashtags(self, limit: int = 0) -> list[dict]:
        """返回所有 hashtag 的简要列表（id, name, views, crawled_at）。"""
        db = self._load()
        items = []
        for hid, data in db["hashtags"].items():
            ld = data.get("list_data", {})
            items.append({
                "hashtag_id": hid,
                "hashtag_name": ld.get("hashtag_name", ""),
                "video_views": ld.get("video_views", 0),
                "publish_cnt": ld.get("publish_cnt", 0),
                "first_seen_at": data.get("first_seen_at", ""),
                "last_seen_at": data.get("last_seen_at", ""),
            })
        items.sort(key=lambda x: x["video_views"], reverse=True)
        return items[:limit] if limit else items


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
