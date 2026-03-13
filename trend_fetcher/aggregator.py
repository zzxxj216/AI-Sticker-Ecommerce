"""
热点数据聚合器
- 合并多个数据源的数据
- 去重、评分、排序
- 输出统一格式的热点列表
"""
from collections import Counter
from datetime import datetime
from typing import Optional
import re


class TrendAggregator:

    # 贴纸业务相关的高权重关键词
    STICKER_BOOST_KEYWORDS: list = [
        # 流行文化
        "meme", "viral", "trend", "aesthetic", "cute", "funny",
        "kawaii", "retro", "vintage", "y2k", "cottagecore", "dark academia",
        # 节假日 & 季节
        "christmas", "halloween", "valentine", "easter", "thanksgiving",
        "spring", "summer", "fall", "winter", "holiday",
        # 动物（贴纸超热门）
        "cat", "dog", "bunny", "fox", "bear", "panda", "frog",
        # 情感类
        "love", "heart", "smile", "happy", "sad", "anxiety", "mood",
        # 流行词汇
        "vibe", "slay", "bestie", "iconic", "based", "no cap",
    ]

    # 需要过滤掉的内容（避免版权 / 政治敏感）
    FILTER_KEYWORDS: list = [
        "trump", "biden", "war", "shooting", "death", "murder", "rape",
        "election", "protest", "riot", "bombing", "terrorist",
        "copyright", "trademark", "disney", "marvel", "nintendo",
        "pokemon", "harry potter", "star wars",  # 版权敏感IP
    ]

    def aggregate(self, all_data: dict[str, list]) -> dict:
        """
        聚合所有数据源，返回结构化热点报告

        Args:
            all_data: {"google_trends": [...], "reddit": [...], ...}

        Returns:
            {
                "summary": {...统计信息},
                "top_trends": [...排名后的热点],
                "by_source": {...各来源分类},
                "cross_source_trends": [...多源共现热点]
            }
        """
        print("\n[聚合器] 开始整合数据...")

        # 合并所有条目
        all_items = []
        source_counts = {}
        for source, items in all_data.items():
            source_counts[source] = len(items)
            all_items.extend(items)

        print(f"[聚合器] 总原始条目: {len(all_items)}")

        # 过滤敏感内容
        filtered = self._filter_sensitive(all_items)
        print(f"[聚合器] 过滤后: {len(filtered)} 条（过滤 {len(all_items) - len(filtered)} 条敏感内容）")

        # 提取关键词并去重聚合
        keyword_groups = self._group_by_keyword(filtered)

        # 对每个关键词计算综合热度评分
        scored_trends = self._score_trends(keyword_groups)

        # 按评分排序
        sorted_trends = sorted(scored_trends, key=lambda x: x["score"], reverse=True)

        # 找出多数据源共现的关键词（最可靠的热点）
        cross_source = [t for t in sorted_trends if len(t["sources"]) >= 2]

        result = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_raw_items": len(all_items),
                "after_filter": len(filtered),
                "unique_trends": len(sorted_trends),
                "cross_source_trends": len(cross_source),
                "source_breakdown": source_counts,
            },
            "top_trends": sorted_trends[:30],         # 综合 Top 30
            "cross_source_trends": cross_source[:15], # 多源共现 Top 15（最可信）
            "by_source": {
                source: self._get_top_from_source(filtered, source, 10)
                for source in all_data.keys()
            },
        }

        print(f"[聚合器] 最终产出 {len(sorted_trends)} 个独立热点，{len(cross_source)} 个跨源共现热点")
        return result

    def _filter_sensitive(self, items: list) -> list:
        """过滤政治敏感 / 版权风险内容"""
        filtered = []
        for item in items:
            keyword_lower = item.get("keyword", "").lower()
            if any(bad in keyword_lower for bad in self.FILTER_KEYWORDS):
                continue
            filtered.append(item)
        return filtered

    def _group_by_keyword(self, items: list) -> dict:
        """
        按关键词分组，使用简单的相似度合并
        例如 "cute cat meme" 和 "cat memes" 都归入 "cat"
        """
        groups = {}
        for item in items:
            keyword = item.get("keyword", "").strip()
            if not keyword:
                continue

            # 尝试找到最匹配的已有分组
            matched_key = self._find_similar_group(keyword, groups.keys())
            group_key = matched_key if matched_key else keyword

            if group_key not in groups:
                groups[group_key] = {
                    "primary_keyword": group_key,
                    "related_keywords": set(),
                    "items": [],
                    "sources": set(),
                }

            if keyword != group_key:
                groups[group_key]["related_keywords"].add(keyword)

            groups[group_key]["items"].append(item)
            groups[group_key]["sources"].add(item.get("source", "unknown"))

        return groups

    def _find_similar_group(self, keyword: str, existing_keys, threshold: int = 3) -> Optional[str]:
        """
        简单的关键词相似度匹配
        如果新关键词包含已有分组的核心词（>=3字符），则归并
        """
        kw_lower = keyword.lower()
        kw_words = set(re.findall(r'\w+', kw_lower))

        best_match = None
        best_overlap = 0

        for key in existing_keys:
            key_lower = key.lower()
            key_words = set(re.findall(r'\w+', key_lower))

            # 过滤停用词
            stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of", "and", "or"}
            kw_meaningful = kw_words - stop_words
            key_meaningful = key_words - stop_words

            if not kw_meaningful or not key_meaningful:
                continue

            # 计算词重叠率
            overlap = len(kw_meaningful & key_meaningful)
            min_len = min(len(kw_meaningful), len(key_meaningful))

            # 只有 50% 以上词重叠才认为是同一主题
            if overlap >= 1 and min_len <= 3 and overlap / min_len >= 0.5:
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = key

        return best_match if best_overlap >= 1 else None

    def _score_trends(self, keyword_groups: dict) -> list:
        """计算每个热点的综合热度评分"""
        scored = []

        for key, group in keyword_groups.items():
            items = group["items"]
            sources = group["sources"]

            score = 0.0

            # 1. 来源数量加分（多源共现 = 更可信）
            score += len(sources) * 20

            # 2. 条目数量加分
            score += min(len(items) * 5, 30)

            # 3. Reddit 投票数加分
            reddit_items = [i for i in items if i.get("source") == "Reddit"]
            if reddit_items:
                max_score = max(i.get("score", 0) for i in reddit_items)
                if max_score > 10000:
                    score += 30
                elif max_score > 5000:
                    score += 20
                elif max_score > 1000:
                    score += 10
                elif max_score > 100:
                    score += 5

            # 4. Google Trends 流量加分
            gt_items = [i for i in items if i.get("source") == "Google Trends"]
            if gt_items:
                for gt in gt_items:
                    traffic_str = gt.get("traffic") or ""
                    traffic_num = self._parse_traffic(traffic_str)
                    if traffic_num > 1000000:
                        score += 25
                    elif traffic_num > 100000:
                        score += 15
                    elif traffic_num > 10000:
                        score += 8

            # 5. 贴纸相关关键词加分
            kw_lower = key.lower()
            sticker_boost = sum(1 for kw in self.STICKER_BOOST_KEYWORDS if kw in kw_lower)
            score += sticker_boost * 8

            # 6. 关键词长度惩罚（太长的标题不适合做贴纸主题）
            word_count = len(key.split())
            if word_count <= 3:
                score += 10
            elif word_count <= 6:
                score += 5
            elif word_count > 10:
                score -= 10

            scored.append({
                "keyword": key,
                "score": round(score, 1),
                "sources": list(sources),
                "source_count": len(sources),
                "item_count": len(items),
                "related_keywords": list(group["related_keywords"])[:5],
                "sticker_relevance": sticker_boost,
                "top_reddit_score": max(
                    (i.get("score", 0) for i in items if i.get("source") == "Reddit"),
                    default=0
                ),
                "google_traffic": next(
                    (i.get("traffic") for i in items if i.get("source") == "Google Trends" and i.get("traffic")),
                    None
                ),
                "sample_url": items[0].get("url", "") if items else "",
            })

        return scored

    def _parse_traffic(self, traffic_str: str) -> int:
        """解析流量字符串，如 '200,000+' -> 200000"""
        if not traffic_str:
            return 0
        cleaned = re.sub(r'[,+\s]', '', str(traffic_str))
        try:
            return int(cleaned)
        except ValueError:
            return 0

    def _get_top_from_source(self, items: list, source_key: str, limit: int) -> list:
        """获取某来源的 Top 条目"""
        source_map = {
            "google_trends": "Google Trends",
            "reddit": "Reddit",
            "rss": "RSS",
            "newsapi": "NewsAPI",
        }
        source_name = source_map.get(source_key, source_key)
        source_items = [i for i in items if i.get("source") == source_name]

        # Reddit 按投票排序
        if source_name == "Reddit":
            source_items.sort(key=lambda x: x.get("score", 0), reverse=True)

        return source_items[:limit]
