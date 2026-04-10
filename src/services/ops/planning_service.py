"""选品日历服务 — 基于 aihubmix :surfing 联网搜索获取各地区事件"""

from __future__ import annotations

import json
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from src.core.config import config
from src.services.ops.db import OpsDatabase

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8))

REGIONS: dict[str, str] = {
    "us": "美国（United States）",
    "ca": "加拿大（Canada）",
    "eu": "欧洲主要市场（United Kingdom, Germany, France, etc.）",
}

_SYSTEM_PROMPT = (
    "你是一位资深电商选品顾问，熟悉全球各地区的节日、文化活动、体育赛事和商业事件。"
    "请严格按要求输出 JSON 数组，不要添加任何 markdown 包裹或额外文字。"
)


def _build_prompt(region_name: str, year: int, month: int) -> str:
    return f"""请搜索 **{region_name}** 市场 **{year}年{month}月** 所有重要内容热点。
请尽可能全面、详尽，覆盖以下所有类别：

1. **holiday** 
2. **event**
3. **cultural** 
4. **sports** 

规则：
- 单日事件：end_date 设为 null
- 多日事件（如音乐节、展会、Heritage Month）：用 start_date 和 end_date 标注首尾日期
- 只列出真正重要、有商业价值的事件，不要凑数
- 按 start_date 升序排列

JSON 数组格式，每条：
- start_date: YYYY-MM-DD
- end_date: YYYY-MM-DD 或 null
- title: 事件标题（英文）
- category: holiday / event / cultural / sports
- short_description: 1-2句英文描述
- source: 来源 URL

只输出 JSON 数组，不要其他文字。"""


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len]


class PlanningService:
    def __init__(self, db: OpsDatabase | None = None):
        self.db = db or OpsDatabase()

    def fetch_events(self, region: str, year: int, month: int) -> dict[str, Any]:
        """调用 aihubmix :surfing 联网搜索获取指定地区/月份事件，解析后存入 DB。"""
        if region not in REGIONS:
            return {"ok": False, "error": f"Unknown region: {region}"}

        api_key = config.aihubmix_api_key
        base_url = config.aihubmix_base_url
        model = config.aihubmix_model

        if not api_key:
            return {"ok": False, "error": "AIHUBMIX_API_KEY not configured"}

        region_name = REGIONS[region]
        prompt = _build_prompt(region_name, year, month)
        batch = f"{year:04d}-{month:02d}_{region}"

        logger.info("Fetching planning events: region=%s month=%d-%02d model=%s", region, year, month, model)

        try:
            resp = requests.post(
                url=base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
                timeout=180,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("AiHubMix request failed: %s", e)
            return {"ok": False, "error": str(e)}

        result = resp.json()
        if "error" in result:
            return {"ok": False, "error": result["error"]}

        choices = result.get("choices", [])
        if not choices:
            return {"ok": False, "error": "Empty choices from API"}

        text = choices[0].get("message", {}).get("content", "")
        items = self._parse_json_response(text)
        if items is None:
            return {"ok": False, "error": "Failed to parse JSON from AI response", "raw": text[:500]}

        self.db.delete_planning_events_by_batch(batch)

        db_events: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start = item.get("start_date") or item.get("date", "")
            if not start:
                continue
            title = item.get("title", "untitled")
            event_id = f"pe:{region}:{start.replace('-', '')}:{_slugify(title)}"
            db_events.append({
                "id": event_id,
                "title": title,
                "category": item.get("category", ""),
                "region": region,
                "start_date": start,
                "end_date": item.get("end_date"),
                "short_description": item.get("short_description", ""),
                "source": item.get("source", ""),
                "raw": item,
                "fetch_batch": batch,
            })

        count = self.db.insert_planning_events(db_events)
        usage = result.get("usage", {})
        logger.info("Saved %d planning events for batch=%s (tokens: %s)", count, batch, usage.get("total_tokens", "?"))

        return {
            "ok": True,
            "region": region,
            "month": f"{year}-{month:02d}",
            "count": count,
            "tokens": usage.get("total_tokens", 0),
        }

    def fetch_all_regions(self, year: int, month: int) -> list[dict[str, Any]]:
        results = []
        for region in REGIONS:
            r = self.fetch_events(region, year, month)
            results.append(r)
        return results

    def get_calendar_data(
        self,
        year: int,
        month: int,
        region: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.db.list_planning_events(region=region, year=year, month=month)

    def get_stats(self) -> dict[str, Any]:
        return self.db.get_planning_stats()

    @staticmethod
    def _parse_json_response(text: str) -> list[dict] | None:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", clean, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return None
            else:
                return None

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
        return None
