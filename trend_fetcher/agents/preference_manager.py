"""
用户偏好管理器
学习并记住用户的历史选择，为后续生成提供智能推荐。
"""
import json
from pathlib import Path
from typing import Optional
from collections import defaultdict, Counter
from datetime import datetime


class PreferenceManager:
    """
    管理用户偏好，支持：
    1. 记录用户的历史选择
    2. 基于历史数据生成智能推荐
    3. 按主题分类（如宠物、人物、风景等）
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path("output/user_preferences.json")
        self.preferences = self._load_preferences()

    def _load_preferences(self) -> dict:
        """从文件加载偏好数据"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"  [PreferenceManager] 加载偏好失败: {e}")
        return {
            "categories": {},  # {category: {dim_key: {option_id: count}}}
            "history": [],     # 历史记录列表
            "last_updated": None,
        }

    def _save_preferences(self):
        """保存偏好数据到文件"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.preferences["last_updated"] = datetime.now().isoformat()
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.preferences, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [PreferenceManager] 保存偏好失败: {e}")

    def record_session(self, category: str, dimensions: list[dict],
                      analysis: Optional[dict] = None):
        """
        记录一次会话的用户选择。

        Args:
            category: 类别（如 "宠物-猫", "宠物-狗", "人物", "风景"）
            dimensions: 已通过的维度列表，每个含 key/selection/selection_name
            analysis: 可选的分析结果，用于更精确的分类
        """
        if category not in self.preferences["categories"]:
            self.preferences["categories"][category] = {}

        cat_prefs = self.preferences["categories"][category]

        # 统计每个维度的选项频率
        for dim in dimensions:
            dim_key = dim.get("key")
            if not dim_key:
                continue

            if dim_key not in cat_prefs:
                cat_prefs[dim_key] = {}

            selections = dim.get("selection", [])
            if not isinstance(selections, list):
                selections = [selections]

            for opt_id in selections:
                if opt_id:
                    cat_prefs[dim_key][opt_id] = cat_prefs[dim_key].get(opt_id, 0) + 1

        # 记录历史
        self.preferences["history"].append({
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "dimensions": [
                {
                    "key": d.get("key"),
                    "label": d.get("label"),
                    "selection": d.get("selection"),
                    "selection_name": d.get("selection_name"),
                }
                for d in dimensions
            ],
        })

        # 只保留最近 100 条历史
        if len(self.preferences["history"]) > 100:
            self.preferences["history"] = self.preferences["history"][-100:]

        self._save_preferences()
        print(f"  [PreferenceManager] 已记录偏好: {category}")

    def get_recommendations(self, category: str, dimensions: list[dict],
                           top_n: int = 3) -> dict:
        """
        基于历史偏好生成推荐。

        Args:
            category: 类别
            dimensions: 当前生成的维度列表（含所有选项）
            top_n: 每个维度推荐的选项数量

        Returns:
            {dim_key: [推荐的 option_id 列表]}
        """
        if category not in self.preferences["categories"]:
            return {}

        cat_prefs = self.preferences["categories"][category]
        recommendations = {}

        for dim in dimensions:
            dim_key = dim.get("key")
            if not dim_key or dim_key not in cat_prefs:
                continue

            # 获取该维度的历史选择频率
            freq = cat_prefs[dim_key]
            if not freq:
                continue

            # 按频率排序，取 top_n
            sorted_opts = sorted(freq.items(), key=lambda x: x[1], reverse=True)
            recommended_ids = [opt_id for opt_id, _ in sorted_opts[:top_n]]

            # 验证推荐的选项是否仍在当前维度中
            valid_ids = {opt["id"] for opt in dim.get("options", [])}
            recommended_ids = [oid for oid in recommended_ids if oid in valid_ids]

            if recommended_ids:
                recommendations[dim_key] = recommended_ids

        return recommendations

    def get_smart_preset(self, category: str, dimensions: list[dict]) -> Optional[dict]:
        """
        生成智能预设：基于历史数据，为每个维度自动选择最常用的选项。

        Args:
            category: 类别
            dimensions: 当前生成的维度列表

        Returns:
            {
                "preset_name": str,
                "confidence": float,  # 0-1，表示推荐的置信度
                "selections": {dim_key: [option_ids]},
                "display_names": {dim_key: [option_names]},
            }
        """
        recommendations = self.get_recommendations(category, dimensions, top_n=2)
        if not recommendations:
            return None

        # 计算置信度：有推荐的维度数 / 总维度数
        total_dims = len(dimensions)
        covered_dims = len(recommendations)
        confidence = covered_dims / total_dims if total_dims > 0 else 0

        # 如果覆盖率太低，不返回预设
        if confidence < 0.5:
            return None

        selections = {}
        display_names = {}

        for dim in dimensions:
            dim_key = dim.get("key")
            if dim_key in recommendations:
                rec_ids = recommendations[dim_key]
                selections[dim_key] = rec_ids

                # 获取对应的显示名称
                opts_map = {opt["id"]: opt["name"] for opt in dim.get("options", [])}
                display_names[dim_key] = [opts_map.get(oid, oid) for oid in rec_ids]

        return {
            "preset_name": f"{category} 常用配置",
            "confidence": confidence,
            "selections": selections,
            "display_names": display_names,
        }

    def get_category_stats(self, category: str) -> dict:
        """获取某个类别的统计信息"""
        if category not in self.preferences["categories"]:
            return {"total_sessions": 0, "dimensions": {}}

        cat_prefs = self.preferences["categories"][category]
        history_count = sum(
            1 for h in self.preferences["history"] if h.get("category") == category
        )

        dim_stats = {}
        for dim_key, opts in cat_prefs.items():
            total = sum(opts.values())
            dim_stats[dim_key] = {
                "total_selections": total,
                "top_options": sorted(opts.items(), key=lambda x: x[1], reverse=True)[:5],
            }

        return {
            "total_sessions": history_count,
            "dimensions": dim_stats,
        }

    def infer_category(self, analysis: dict, user_text: str = "") -> str:
        """
        根据分析结果推断类别。

        Args:
            analysis: 图片分析结果
            user_text: 用户输入的文本

        Returns:
            推断的类别字符串
        """
        subject = analysis.get("subject", "").lower()
        text = user_text.lower()
        combined = f"{subject} {text}"

        # 宠物类
        if any(kw in combined for kw in ["猫", "cat", "喵", "猫咪"]):
            return "宠物-猫"
        if any(kw in combined for kw in ["狗", "dog", "犬", "狗狗"]):
            return "宠物-狗"
        if any(kw in combined for kw in ["宠物", "pet", "动物", "animal"]):
            return "宠物-通用"

        # 人物类
        if any(kw in combined for kw in ["人", "人物", "portrait", "face", "woman", "man"]):
            return "人物"

        # 风景类
        if any(kw in combined for kw in ["风景", "landscape", "nature", "山", "海", "天空"]):
            return "风景"

        # 食物类
        if any(kw in combined for kw in ["食物", "food", "美食", "餐", "菜"]):
            return "食物"

        # 默认
        return "通用"

    def clear_category(self, category: str):
        """清除某个类别的偏好数据"""
        if category in self.preferences["categories"]:
            del self.preferences["categories"][category]
        self.preferences["history"] = [
            h for h in self.preferences["history"] if h.get("category") != category
        ]
        self._save_preferences()
        print(f"  [PreferenceManager] 已清除类别: {category}")

    def get_all_categories(self) -> list[str]:
        """获取所有已记录的类别"""
        return list(self.preferences["categories"].keys())
