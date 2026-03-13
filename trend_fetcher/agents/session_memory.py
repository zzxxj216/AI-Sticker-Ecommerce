"""
会话记忆模块
在会话内跟踪每个维度和每条 prompt 的审批状态，支持局部修改。
"""
from datetime import datetime
from typing import Optional


class SessionMemory:
    """
    管理贴纸设计 Agent 的会话状态。

    支持两层迭代记忆：
    1. 维度级：逐维度审批，满意的保留，不满意的重新生成
    2. Prompt 级：逐条审批 prompt，支持局部重新生成
    """

    def __init__(self):
        self.image_path: Optional[str] = None
        self.user_text: str = ""
        self.analysis: Optional[dict] = None

        # {dim_key: {"status": "approved"|"rejected"|"pending",
        #            "dim_data": {...},              # 原始维度数据
        #            "selection": list[str]|None,     # 用户选择的 option id 列表
        #            "selection_name": list[str]|None, # 选择的显示名称列表
        #            "feedback": str|None}}           # 拒绝时的反馈
        self.dimension_states: dict = {}

        # [{"index": int, "status": "approved"|"rejected"|"pending",
        #   "data": {...},            # prompt 数据（title/concept/image_prompt）
        #   "feedback": str|None}]    # 拒绝时的反馈
        self.prompt_states: list = []

        self.requirement_summary: Optional[str] = None
        self.suggested_count: int = 5
        self.results: Optional[list] = None

        self.revision_history: list = []

    # ── 图片 & 分析 ────────────────────────────────────────────

    def set_input_context(self, image_path: Optional[str], user_text: str = ""):
        self.image_path = image_path
        self.user_text = (user_text or "").strip()

    def set_analysis(self, analysis_result: dict):
        """保存分析结果，初始化所有维度为 pending"""
        self.analysis = analysis_result
        self.suggested_count = analysis_result.get("suggested_count", 5)

        self.dimension_states.clear()
        for dim in analysis_result.get("dimensions", []):
            key = dim["key"]
            self.dimension_states[key] = {
                "status": "pending",
                "dim_data": dim,
                "selection": None,
                "selection_name": None,
                "feedback": None,
            }

    def update_dimensions(self, new_dims: list[dict]):
        """更新（替换）指定维度的数据，重置为 pending 状态"""
        for dim in new_dims:
            key = dim["key"]
            self.dimension_states[key] = {
                "status": "pending",
                "dim_data": dim,
                "selection": None,
                "selection_name": None,
                "feedback": None,
            }
        self._sync_analysis_dimensions()

    def replace_rejected_dimensions(self, rejected_keys: set[str], new_dims: list[dict]):
        """
        用新维度整体替换被拒维度（支持 key/名称变化），保留已通过维度。
        """
        if not rejected_keys:
            return

        for key in list(rejected_keys):
            self.dimension_states.pop(key, None)

        for dim in new_dims:
            key = dim["key"]
            self.dimension_states[key] = {
                "status": "pending",
                "dim_data": dim,
                "selection": None,
                "selection_name": None,
                "feedback": None,
            }

        self._sync_analysis_dimensions()

    # ── 维度审批 ────────────────────────────────────────────────

    def approve_dimension(self, dim_key: str,
                          option_ids: list[str] | str,
                          display_names: list[str] | str = ""):
        """标记维度已通过并记录用户的选择（支持多选）"""
        if dim_key not in self.dimension_states:
            return
        if isinstance(option_ids, str):
            option_ids = [option_ids]
        if isinstance(display_names, str):
            display_names = [display_names] if display_names else []

        state = self.dimension_states[dim_key]
        state["status"] = "approved"
        state["selection"] = option_ids
        state["selection_name"] = display_names
        state["feedback"] = None

    def reject_dimension(self, dim_key: str, feedback: str = ""):
        """标记维度需修改并记录反馈"""
        if dim_key not in self.dimension_states:
            return
        state = self.dimension_states[dim_key]
        state["status"] = "rejected"
        state["feedback"] = feedback

        self.revision_history.append({
            "time": datetime.now().isoformat(),
            "type": "dimension_rejected",
            "key": dim_key,
            "label": state["dim_data"].get("label", ""),
            "feedback": feedback,
        })

    def get_all_dimensions(self) -> list[dict]:
        """获取所有维度及其状态"""
        result = []
        for key, state in self.dimension_states.items():
            result.append({
                "key": key,
                **state["dim_data"],
                "status": state["status"],
                "selection": state["selection"],
                "selection_name": state["selection_name"],
                "feedback": state["feedback"],
            })
        return result

    def get_pending_dimensions(self) -> list[dict]:
        """获取尚未确认的维度"""
        return [
            {"key": k, **s["dim_data"]}
            for k, s in self.dimension_states.items()
            if s["status"] == "pending"
        ]

    def get_rejected_dimensions(self) -> list[dict]:
        """获取被拒绝的维度（含反馈）"""
        return [
            {"key": k, **s["dim_data"], "feedback": s["feedback"]}
            for k, s in self.dimension_states.items()
            if s["status"] == "rejected"
        ]

    def get_approved_dimensions(self) -> list[dict]:
        """获取已通过的维度（含选择）"""
        return [
            {
                "key": k,
                **s["dim_data"],
                "selection": s["selection"],
                "selection_name": s["selection_name"],
            }
            for k, s in self.dimension_states.items()
            if s["status"] == "approved"
        ]

    def all_dimensions_approved(self) -> bool:
        """检查是否所有维度都已通过"""
        if not self.dimension_states:
            return False
        return all(
            s["status"] == "approved"
            for s in self.dimension_states.values()
        )

    # ── Prompt 审批 ─────────────────────────────────────────────

    def set_prompts(self, prompts: list[dict]):
        """保存 prompt 列表，初始化为 pending 状态"""
        self.prompt_states = [
            {
                "index": p.get("index", i + 1),
                "status": "pending",
                "data": p,
                "feedback": None,
            }
            for i, p in enumerate(prompts)
        ]

    def approve_prompt(self, index: int):
        """标记指定 prompt 为已通过"""
        for ps in self.prompt_states:
            if ps["index"] == index:
                ps["status"] = "approved"
                ps["feedback"] = None
                break

    def reject_prompt(self, index: int, feedback: str = ""):
        """标记指定 prompt 为已拒绝"""
        for ps in self.prompt_states:
            if ps["index"] == index:
                ps["status"] = "rejected"
                ps["feedback"] = feedback
                self.revision_history.append({
                    "time": datetime.now().isoformat(),
                    "type": "prompt_rejected",
                    "index": index,
                    "title": ps["data"].get("title", ""),
                    "feedback": feedback,
                })
                break

    def approve_all_prompts(self):
        """批量通过所有 pending 的 prompt"""
        for ps in self.prompt_states:
            if ps["status"] == "pending":
                ps["status"] = "approved"

    def get_approved_prompts(self) -> list[dict]:
        """获取已通过的 prompt 数据"""
        return [ps["data"] for ps in self.prompt_states if ps["status"] == "approved"]

    def get_rejected_prompts(self) -> list[dict]:
        """获取被拒绝的 prompt（含 feedback）"""
        return [
            {**ps["data"], "feedback": ps["feedback"]}
            for ps in self.prompt_states
            if ps["status"] == "rejected"
        ]

    def get_all_prompts_with_status(self) -> list[dict]:
        """获取所有 prompt 及其状态"""
        return [
            {
                "index": ps["index"],
                "status": ps["status"],
                "feedback": ps["feedback"],
                **ps["data"],
            }
            for ps in self.prompt_states
        ]

    def all_prompts_approved(self) -> bool:
        """检查是否所有 prompt 都已通过"""
        if not self.prompt_states:
            return False
        return all(ps["status"] == "approved" for ps in self.prompt_states)

    def replace_prompts(self, new_prompts: list[dict]):
        """用新生成的 prompt 替换对应 index 的被拒 prompt，重置为 pending"""
        new_by_index = {p["index"]: p for p in new_prompts}
        for ps in self.prompt_states:
            if ps["index"] in new_by_index:
                ps["data"] = new_by_index[ps["index"]]
                ps["status"] = "pending"
                ps["feedback"] = None

    # ── 要求摘要 ───────────────────────────────────────────────

    def build_requirement_summary(self) -> str:
        """根据已通过的维度选择，组合出总体要求描述（支持多选）"""
        parts = []

        if self.analysis:
            a = self.analysis.get("analysis", {})
            subject = a.get("subject", "")
            if subject:
                parts.append(f"基于图片主体「{subject}」")

        for key, state in self.dimension_states.items():
            if state["status"] != "approved":
                continue
            label = state["dim_data"].get("label", key)
            names = state["selection_name"] or state["selection"] or []
            if isinstance(names, list):
                joined = " + ".join(names) if names else ""
            else:
                joined = str(names)
            if joined:
                parts.append(f"{label}: {joined}")

        summary = "；".join(parts) if parts else "（未指定具体要求）"
        self.requirement_summary = summary
        return summary

    # ── 上下文输出 ──────────────────────────────────────────────

    def get_revision_context(self) -> str:
        """将修改历史格式化为文本，供 Claude 参考"""
        if not self.revision_history:
            return ""

        lines = ["用户的修改历史："]
        for entry in self.revision_history[-10:]:
            if entry["type"] == "dimension_rejected":
                lines.append(
                    f"  - 拒绝维度「{entry['label']}」: {entry['feedback']}"
                )
            elif entry["type"] == "prompt_rejected":
                lines.append(
                    f"  - 拒绝 prompt #{entry['index']}「{entry['title']}」: {entry['feedback']}"
                )
        return "\n".join(lines)

    def _sync_analysis_dimensions(self):
        """将当前维度状态回写到 analysis，保持上下文一致。"""
        if self.analysis is None:
            return
        self.analysis["dimensions"] = [
            state["dim_data"] for state in self.dimension_states.values()
        ]
