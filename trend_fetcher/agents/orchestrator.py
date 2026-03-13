"""
编排器 — 协调 3 个 Agent + SessionMemory，驱动多轮交互流程。
"""
from typing import Optional

from .image_analyzer import ImageAnalyzerAgent
from .prompt_generator import PromptGeneratorAgent
from .sticker_generator import StickerGeneratorAgent
from .session_memory import SessionMemory
from .preference_manager import PreferenceManager


class StickerDesignOrchestrator:
    """
    贴纸设计编排器。

    流程：
      Step 1  analyze      → 图片分析 + 动态维度生成
      Step 1b refine_dims  → 重新生成被拒维度（可选迭代）
      Step 2  set_pref     → 逐维度用户选择 → 生成总体要求
      Step 3  gen_prompts  → 生成多条 image_prompt
      Step 3b refine_prts  → 重新生成被拒 prompt（可选迭代）
      Step 4  gen_stickers → 批量生成贴纸图片
    """

    def __init__(self, enable_smart_mode: bool = True):
        self.analyzer = ImageAnalyzerAgent()
        self.prompt_gen = PromptGeneratorAgent()
        self.sticker_gen = StickerGeneratorAgent()
        self.memory = SessionMemory()
        self.preference_mgr = PreferenceManager()
        self.enable_smart_mode = enable_smart_mode
        self.current_category: Optional[str] = None

    # ── Step 1: 图片分析 ───────────────────────────────────────

    def step1_analyze(self, image_path: Optional[str], user_text: str = "") -> Optional[dict]:
        """
        分析图片并生成动态维度。

        Returns:
            分析结果 dict（含 analysis + dimensions），失败返回 None
        """
        self.memory.set_input_context(image_path=image_path, user_text=user_text)

        print()
        print("=" * 55)
        print("  Step 1 / 4  图片分析 + 维度生成")
        print("=" * 55)

        result = self.analyzer.analyze(image_path=image_path, user_text=user_text)
        if not result:
            return None

        self.memory.set_analysis(result)

        # 推断类别并获取智能推荐
        if self.enable_smart_mode:
            self.current_category = self.preference_mgr.infer_category(
                result.get("analysis", {}), user_text
            )
            print(f"  [智能模式] 识别类别: {self.current_category}")

            smart_preset = self.preference_mgr.get_smart_preset(
                self.current_category,
                result.get("dimensions", [])
            )

            if smart_preset:
                confidence = smart_preset["confidence"]
                print(f"  [智能推荐] 找到历史偏好（置信度: {confidence:.0%}）")
                result["smart_preset"] = smart_preset
            else:
                print(f"  [智能推荐] 暂无历史数据，将记录本次选择")

        self._print_analysis(result)
        return result

    def step1_refine(self) -> Optional[dict]:
        """
        针对被拒绝的维度重新生成选项。

        Returns:
            更新后的完整分析结果，失败返回 None
        """
        rejected = self.memory.get_rejected_dimensions()
        approved = self.memory.get_approved_dimensions()

        if not rejected:
            print("  [Orchestrator] 没有被拒绝的维度，无需重新生成")
            return self.memory.analysis

        print()
        print("-" * 55)
        print(f"  重新生成 {len(rejected)} 个维度...")
        print("-" * 55)

        context = self.analyzer.build_refinement_context(rejected, approved)
        result = self.analyzer.analyze(
            image_path=self.memory.image_path,
            user_text=self.memory.user_text or "",
            refinement_context=context,
        )
        if not result:
            return None

        # 替换被拒维度：允许模型返回新的 key/label（不仅是改选项）
        new_dims = result.get("dimensions", [])
        rejected_keys = {d["key"] for d in rejected}
        approved_keys = {d["key"] for d in approved}

        # 优先取非已通过维度（通常就是重生成的维度）
        replacement_dims = [d for d in new_dims if d.get("key") not in approved_keys]

        # 兜底：若返回中仅包含旧 key，则按被拒 key 取
        if not replacement_dims:
            replacement_dims = [d for d in new_dims if d.get("key") in rejected_keys]

        # 再兜底：直接使用返回结果
        if not replacement_dims:
            replacement_dims = new_dims

        # 控制替换数量与被拒维度一致，避免误替换过多
        replacement_dims = replacement_dims[:max(1, len(rejected_keys))]

        self.memory.replace_rejected_dimensions(rejected_keys, replacement_dims)

        for dim in replacement_dims:
            print(f"  [OK] 已更新维度「{dim['label']}」({len(dim.get('options', []))} 个选项)")

        return self.memory.analysis

    # ── Step 2: 设置偏好 ───────────────────────────────────────

    def step2_approve_dimension(self, dim_key: str,
                                 option_ids: list[str] | str,
                                 display_names: list[str] | str = "") -> bool:
        """
        审批单个维度（支持多选）。

        Args:
            dim_key: 维度 key
            option_ids: 选项 id 列表（或单个 id），"other" 表示自定义
            display_names: 对应的显示名称列表（或单个名称）

        Returns:
            True if approved successfully
        """
        state = self.memory.dimension_states.get(dim_key)
        if not state:
            print(f"  [Orchestrator] 未知维度: {dim_key}")
            return False

        if isinstance(option_ids, str):
            option_ids = [option_ids]
        if isinstance(display_names, str):
            display_names = [display_names] if display_names else []

        self.memory.approve_dimension(dim_key, option_ids, display_names)
        return True

    def step2_reject_dimension(self, dim_key: str, feedback: str = ""):
        """拒绝单个维度并记录反馈"""
        self.memory.reject_dimension(dim_key, feedback)

    def step2_get_summary(self) -> str:
        """生成总体要求摘要"""
        summary = self.memory.build_requirement_summary()
        print()
        print("=" * 55)
        print("  Step 2 / 4  总体要求摘要")
        print("=" * 55)
        print(f"  {summary}")

        # 记录用户偏好
        if self.enable_smart_mode and self.current_category:
            approved_dims = self.memory.get_approved_dimensions()
            self.preference_mgr.record_session(
                category=self.current_category,
                dimensions=approved_dims,
                analysis=self.memory.analysis.get("analysis") if self.memory.analysis else None
            )
            print(f"  [智能模式] 已记录偏好到类别: {self.current_category}")

        return summary

    # ── 智能推荐相关方法 ────────────────────────────────────────

    def apply_smart_preset(self, preset: dict) -> bool:
        """
        应用智能预设到当前会话。

        Args:
            preset: get_smart_preset 返回的预设数据

        Returns:
            是否成功应用
        """
        if not preset or not self.memory.analysis:
            return False

        selections = preset.get("selections", {})
        display_names = preset.get("display_names", {})

        for dim_key, option_ids in selections.items():
            names = display_names.get(dim_key, [])
            self.memory.approve_dimension(dim_key, option_ids, names)

        print(f"  [智能模式] 已应用预设「{preset.get('preset_name', '未命名')}」")
        return True

    def get_preference_stats(self) -> dict:
        """获取当前类别的偏好统计"""
        if not self.current_category:
            return {}
        return self.preference_mgr.get_category_stats(self.current_category)

    def clear_current_preferences(self):
        """清除当前类别的偏好数据"""
        if self.current_category:
            self.preference_mgr.clear_category(self.current_category)
            print(f"  [智能模式] 已清除类别偏好: {self.current_category}")

    # ── Step 3: 生成 Prompt ────────────────────────────────────

    def step3_generate_prompts(self, count: Optional[int] = None) -> list[dict]:
        """
        生成多条 image_prompt。

        Args:
            count: 生成数量，默认使用分析推荐数量

        Returns:
            prompt 列表
        """
        if not self.memory.all_dimensions_approved():
            pending = self.memory.get_pending_dimensions()
            rejected = self.memory.get_rejected_dimensions()
            remaining = len(pending) + len(rejected)
            print(f"  [Orchestrator] 还有 {remaining} 个维度未通过，请先完成维度审批")
            return []

        if count is None:
            count = self.memory.suggested_count

        summary = self.memory.requirement_summary or self.memory.build_requirement_summary()

        print()
        print("=" * 55)
        print(f"  Step 3 / 4  生成 {count} 条 Prompt")
        print("=" * 55)

        prompts = self.prompt_gen.generate(
            analysis=self.memory.analysis,
            requirement_summary=summary,
            image_path=self.memory.image_path,
            count=count,
        )

        if prompts:
            self.memory.set_prompts(prompts)
            self._print_prompts(prompts)

        return prompts

    def step3_refine_prompts(self) -> list[dict]:
        """
        重新生成被拒绝的 prompt。

        Returns:
            新生成的 prompt 列表
        """
        rejected = self.memory.get_rejected_prompts()
        approved = self.memory.get_approved_prompts()

        if not rejected:
            print("  [Orchestrator] 没有被拒绝的 prompt，无需重新生成")
            return []

        print()
        print("-" * 55)
        print(f"  重新生成 {len(rejected)} 条 prompt...")
        print("-" * 55)

        summary = self.memory.requirement_summary or self.memory.build_requirement_summary()

        new_prompts = self.prompt_gen.regenerate(
            analysis=self.memory.analysis,
            requirement_summary=summary,
            image_path=self.memory.image_path,
            approved_prompts=approved,
            rejected_prompts=rejected,
        )

        if new_prompts:
            self.memory.replace_prompts(new_prompts)
            self._print_prompts(new_prompts)

        return new_prompts

    # ── Step 4: 生成贴纸 ───────────────────────────────────────

    def step4_generate_stickers(self) -> list[dict]:
        """
        批量生成贴纸图片。

        Returns:
            生成结果列表
        """
        if not self.memory.all_prompts_approved():
            print("  [Orchestrator] 还有 prompt 未通过审批，请先完成审批")
            return []

        prompts = self.memory.get_approved_prompts()

        print()
        print("=" * 55)
        print(f"  Step 4 / 4  批量生成 {len(prompts)} 张贴纸")
        print("=" * 55)

        results = self.sticker_gen.generate(prompts, ref_image=self.memory.image_path)
        self.memory.results = results

        success = [r for r in results if r["success"]]
        print()
        print("=" * 55)
        print(f"  完成！成功 {len(success)}/{len(results)} 张")
        print("=" * 55)

        return results

    # ── 打印辅助 ───────────────────────────────────────────────

    def _print_analysis(self, result: dict):
        """打印分析结果"""
        a = result.get("analysis", {})
        print()
        print(f"  主体: {a.get('subject', '—')}")
        print(f"  风格: {a.get('style', '—')}")
        print(f"  配色: {', '.join(a.get('colors', []))}")
        print(f"  氛围: {a.get('mood', '—')}")
        print(f"  总结: {a.get('summary', '—')}")
        print()

        dims = result.get("dimensions", [])
        print(f"  生成了 {len(dims)} 个选择维度：")
        for dim in dims:
            opts = dim.get("options", [])
            opt_names = [o["name"] for o in opts]
            print(f"    [{dim['key']}] {dim['label']}: {' / '.join(opt_names)}")

        print(f"\n  建议生成数量: {result.get('suggested_count', 5)}")

    def _print_prompts(self, prompts: list[dict]):
        """打印 prompt 列表"""
        for p in prompts:
            print(f"    #{p.get('index', '?'):02d} {p.get('title', '—')}")
            print(f"        概念: {p.get('concept', '—')}")
            prompt_preview = p.get("image_prompt", "")[:80]
            print(f"        Prompt: {prompt_preview}...")
