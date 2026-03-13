"""对话驱动的批量生成会话

用户通过自然对话输入主题 → 自动驱动 6 线程批量生成管线。
每步结果可保存、打印。完成后进入编辑阶段，按「话题+序号」修改。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Callable

from src.core.logger import get_logger
from src.services.ai.claude_service import ClaudeService
from src.services.ai.gemini_service import GeminiService

from src.models.batch import (
    BatchConfig,
    BatchResult,
    BATCH_PHASE_INPUT,
    BATCH_PHASE_PLANNING,
    BATCH_PHASE_GENERATING,
    BATCH_PHASE_PREVIEW,
    BATCH_PHASE_EDITING,
    BATCH_PHASE_DONE,
)
from src.services.batch.batch_pipeline import BatchPipeline
from src.services.batch.batch_prompts import build_edit_intent_prompt

logger = get_logger("service.batch_session")

ProgressCallback = Callable[[str, Dict[str, Any]], None]


# ------------------------------------------------------------------
# 会话响应
# ------------------------------------------------------------------

class BatchSessionResponse:
    """BatchSession.process_message() 的返回值。"""

    def __init__(
        self,
        message: str,
        phase: str = BATCH_PHASE_INPUT,
        data: Optional[Dict[str, Any]] = None,
        batch_config: Optional[BatchConfig] = None,
        batch_result: Optional[BatchResult] = None,
    ):
        self.message = message
        self.phase = phase
        self.data = data or {}
        self.batch_config = batch_config
        self.batch_result = batch_result

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "message": self.message,
            "phase": self.phase,
            "data": self.data,
        }
        if self.batch_config:
            d["batch_config"] = self.batch_config.model_dump(mode="json")
        if self.batch_result:
            d["batch_result"] = self.batch_result.to_summary()
        return d


# ------------------------------------------------------------------
# 系统 Prompt
# ------------------------------------------------------------------

BATCH_CHAT_SYSTEM = """You are a friendly sticker design assistant. You help users create sticker packs through a conversational interface. You speak in Chinese.

The user wants to use the BATCH mode to generate sticker packs in parallel.

Your task:
1. Help the user clarify their theme/topic for the batch generation.
2. Optionally collect style preferences (visual style, color mood).
3. Optionally collect how many packs they want (1-10, default 6 if not specified).
4. Ask if they have any extra notes or preferences.
5. When you have enough info, confirm with the user and trigger the pipeline.

Current state:
- Theme: {theme_value}
- Style: {style_value}
- Color mood: {color_mood_value}
- Pack count: {pack_count_value}
- Extra: {extra_value}

If all required info is collected, ask the user to confirm so you can start generating.
When mentioning pack count, tell the user the current value and that default is 6 if they don't specify.

Respond naturally in Chinese. Do NOT output JSON.
"""


# ------------------------------------------------------------------
# BatchSession
# ------------------------------------------------------------------

class BatchSession:
    """对话驱动的批量生成会话管理器。

    Phases:
        batch_input     → 收集主题/风格信息
        batch_planning  → 规划中（自动）
        batch_generating → 内容生成中
        batch_preview   → 预览图生成完成，展示结果
        batch_editing   → 用户可按话题+序号编辑
        batch_done      → 全部完成
    """

    def __init__(
        self,
        claude_service: Optional[ClaudeService] = None,
        gemini_service: Optional[GeminiService] = None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        self.claude = claude_service or ClaudeService()
        self.gemini = gemini_service or GeminiService()
        self._on_progress = on_progress

        self._phase: str = BATCH_PHASE_INPUT
        self._theme: Optional[str] = None
        self._style: Optional[str] = None
        self._color_mood: Optional[str] = None
        self._pack_count: int = 6  # 默认 6 包，用户可通过自然语言调整 (1-6)
        self._extra: str = ""
        self._confirmed: bool = False

        self._pipeline: Optional[BatchPipeline] = None
        self._history: List[Dict[str, str]] = []

        self._step_results: Dict[str, Any] = {}

        logger.info("BatchSession created")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def pipeline(self) -> Optional[BatchPipeline]:
        return self._pipeline

    @property
    def step_results(self) -> Dict[str, Any]:
        return self._step_results

    def process_message(self, user_input: str) -> BatchSessionResponse:
        """处理一轮用户消息。"""
        user_input = user_input.strip()
        if not user_input:
            return BatchSessionResponse(
                message="请告诉我你想做什么主题的贴纸包！",
                phase=self._phase,
            )

        self._history.append({"role": "user", "content": user_input})

        if self._phase == BATCH_PHASE_INPUT:
            return self._process_input_phase(user_input)
        elif self._phase == BATCH_PHASE_EDITING:
            return self._process_editing_phase(user_input)
        elif self._phase in (BATCH_PHASE_PLANNING, BATCH_PHASE_GENERATING, BATCH_PHASE_PREVIEW):
            return BatchSessionResponse(
                message="正在生成中，请稍候...",
                phase=self._phase,
            )
        else:
            return BatchSessionResponse(
                message="批量生成已完成！如需重新开始，请输入 /reset",
                phase=self._phase,
            )

    def start_pipeline(
        self,
        pack_count: Optional[int] = None,
        target_per_pack: int = 55,
        stickers_per_topic: int = 8,
        skip_images: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ) -> BatchResult:
        """启动完整批量生成管线（阻塞式）。

        Args:
            pack_count: 卡包数量。若为 None 则使用用户通过对话指定的数量（默认 6）。
        """
        if not self._theme:
            raise ValueError("Theme not set. Call process_message() first to collect info.")

        actual_count = pack_count if pack_count is not None else self._pack_count
        actual_count = max(1, min(actual_count, 10))

        progress_cb = on_progress or self._on_progress

        self._pipeline = BatchPipeline(
            claude_service=self.claude,
            gemini_service=self.gemini,
            max_pack_workers=min(actual_count, 6),
        )

        def _capture_progress(event: str, data: Dict[str, Any]):
            self._step_results[event] = data
            if progress_cb:
                progress_cb(event, data)

        self._phase = BATCH_PHASE_PLANNING
        _capture_progress("session_start", {
            "theme": self._theme,
            "style": self._style,
            "color_mood": self._color_mood,
            "pack_count": actual_count,
            "extra": self._extra,
        })

        result = self._pipeline.run_full_pipeline(
            theme=self._theme,
            pack_count=actual_count,
            target_per_pack=target_per_pack,
            stickers_per_topic=stickers_per_topic,
            user_style=self._style,
            user_color_mood=self._color_mood,
            user_extra=self._extra,
            skip_images=skip_images,
            on_progress=_capture_progress,
        )

        self._phase = BATCH_PHASE_EDITING
        return result

    def start_pipeline_async(
        self,
        pack_count: Optional[int] = None,
        target_per_pack: int = 55,
        stickers_per_topic: int = 8,
        skip_images: bool = False,
        on_progress: Optional[ProgressCallback] = None,
        on_complete: Optional[Callable[[BatchResult], None]] = None,
    ) -> threading.Thread:
        """在后台线程启动管线（非阻塞）。"""
        def _run():
            try:
                result = self.start_pipeline(
                    pack_count=pack_count,
                    target_per_pack=target_per_pack,
                    stickers_per_topic=stickers_per_topic,
                    skip_images=skip_images,
                    on_progress=on_progress,
                )
                if on_complete:
                    on_complete(result)
            except Exception as e:
                logger.error("Async pipeline failed: %s", e)
                self._phase = BATCH_PHASE_INPUT

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def reset(self) -> None:
        """重置会话状态。"""
        self._phase = BATCH_PHASE_INPUT
        self._theme = None
        self._style = None
        self._color_mood = None
        self._pack_count = 6
        self._extra = ""
        self._confirmed = False
        self._pipeline = None
        self._history.clear()
        self._step_results.clear()
        logger.info("BatchSession reset")

    # ------------------------------------------------------------------
    # 获取各步结果
    # ------------------------------------------------------------------

    def get_plan_summary(self) -> str:
        """获取规划结果摘要。"""
        if self._pipeline:
            return self._pipeline.format_batch_summary()
        return "(尚未规划)"

    def get_preview_paths(self) -> List[Dict[str, str]]:
        """获取所有话题预览图路径。"""
        if not self._pipeline or not self._pipeline.batch_config:
            return []

        paths = []
        for pc in self._pipeline.batch_config.pack_configs:
            for topic in pc.topics:
                if topic.preview_image_path:
                    paths.append({
                        "pack_index": pc.pack_index,
                        "pack_name": pc.pack_name,
                        "topic_name": topic.topic_name,
                        "image_path": topic.preview_image_path,
                    })
        return paths

    def print_step_results(self) -> str:
        """将所有步骤结果格式化打印。"""
        lines = ["=== Batch Pipeline Step Results ===\n"]
        for event, data in self._step_results.items():
            lines.append(f"[{event}]")
            if isinstance(data, dict):
                for k, v in data.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {data}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Input phase
    # ------------------------------------------------------------------

    def _process_input_phase(self, user_input: str) -> BatchSessionResponse:
        """收集主题/风格信息阶段。"""
        extracted = self._extract_batch_info(user_input)

        if extracted.get("theme"):
            self._theme = extracted["theme"]
        if extracted.get("style"):
            self._style = extracted["style"]
        if extracted.get("color_mood"):
            self._color_mood = extracted["color_mood"]
        if extracted.get("extra"):
            self._extra = extracted["extra"]

        pack_count_raw = extracted.get("pack_count")
        if pack_count_raw is not None:
            try:
                n = int(pack_count_raw)
                if 1 <= n <= 10:
                    self._pack_count = n
                    logger.info("Pack count set to %d from user input", n)
            except (ValueError, TypeError):
                pass

        if extracted.get("confirmed") and self._theme:
            self._confirmed = True

        if self._confirmed and self._theme:
            reply = self._generate_chat_reply()
            self._history.append({"role": "assistant", "content": reply})

            return BatchSessionResponse(
                message=reply,
                phase=BATCH_PHASE_PLANNING,
                data={
                    "theme": self._theme,
                    "style": self._style,
                    "color_mood": self._color_mood,
                    "pack_count": self._pack_count,
                    "extra": self._extra,
                    "ready": True,
                },
            )

        reply = self._generate_chat_reply()
        self._history.append({"role": "assistant", "content": reply})

        return BatchSessionResponse(
            message=reply,
            phase=BATCH_PHASE_INPUT,
            data={
                "theme": self._theme,
                "style": self._style,
                "color_mood": self._color_mood,
                "pack_count": self._pack_count,
                "extra": self._extra,
                "ready": False,
            },
        )

    def _extract_batch_info(self, user_input: str) -> Dict[str, Any]:
        """从用户消息中提取批量生成信息。"""
        prompt = f"""Read the user message and extract information for batch sticker generation.

User message: {user_input}

Current state:
- Theme: {self._theme or '(not set)'}
- Style: {self._style or '(not set)'}
- Color mood: {self._color_mood or '(not set)'}
- Pack count: {self._pack_count}
- Extra: {self._extra or '(none)'}

Extract and return JSON:
{{"theme": "extracted theme or null", "style": "extracted style or null", "color_mood": "extracted color mood or null", "pack_count": null, "extra": "extra notes or null", "confirmed": false}}

Rules:
- theme: the main topic/subject for sticker packs
- style: visual art style preference
- color_mood: color/mood preference
- pack_count: number of sticker packs to generate (integer 1-10). Extract from phrases like "生成2包", "2个卡包", "做3套", "来4个", "two packs", etc. Return null if not mentioned. Default is 6 if user never specifies.
- confirmed: true ONLY if user explicitly says "confirm", "start", "go", "开始", "确认", "好的开始", "可以了"
- Return null for fields not mentioned in this message
- Return JSON only.
"""
        try:
            raw = self.claude.generate_json(
                prompt=prompt,
                system="You are a JSON extraction bot. Return valid JSON only.",
                max_tokens=300,
                temperature=0.1,
            )
            return raw
        except Exception as e:
            logger.warning("Batch info extraction failed: %s", e)
            if not self._theme:
                return {"theme": user_input}
            return {}

    def _generate_chat_reply(self) -> str:
        """生成对话回复。"""
        system = BATCH_CHAT_SYSTEM.format(
            theme_value=self._theme or "(未设置)",
            style_value=self._style or "(未指定，系统将自动分配不同风格)",
            color_mood_value=self._color_mood or "(未指定)",
            pack_count_value=f"{self._pack_count} 包",
            extra_value=self._extra or "(无)",
        )

        messages = self._history[-10:]

        try:
            result = self.claude.generate_multiturn(
                messages=messages,
                system=system,
                max_tokens=1000,
                temperature=0.7,
            )
            return result["text"].strip()
        except Exception as e:
            logger.error("Chat reply generation failed: %s", e)
            if self._theme and not self._confirmed:
                return (
                    f"好的，我已经记录了你的主题：「{self._theme}」。\n"
                    f"卡包数量：{self._pack_count} 包\n"
                    f"风格：{self._style or '(系统自动分配)'}\n"
                    f"色彩情绪：{self._color_mood or '(系统自动分配)'}\n\n"
                    "如果信息确认无误，请输入「确认」或「开始」来启动并行生成！"
                )
            return "请告诉我你想创建什么主题的贴纸包？默认生成 6 个不同风格的卡包，你也可以指定数量，比如「生成2包」。"

    # ------------------------------------------------------------------
    # Editing phase
    # ------------------------------------------------------------------

    def _process_editing_phase(self, user_input: str) -> BatchSessionResponse:
        """编辑阶段：按话题+序号定位并修改。"""
        if user_input.lower() in ("/done", "完成", "结束"):
            self._phase = BATCH_PHASE_DONE
            return BatchSessionResponse(
                message="批量生成已完成！所有结果已保存。",
                phase=BATCH_PHASE_DONE,
            )

        if user_input.lower() in ("/summary", "摘要", "总结"):
            summary = self.get_plan_summary()
            return BatchSessionResponse(
                message=summary,
                phase=BATCH_PHASE_EDITING,
            )

        if user_input.lower() in ("/previews", "预览"):
            paths = self.get_preview_paths()
            if paths:
                lines = ["话题预览图列表：\n"]
                for p in paths:
                    lines.append(
                        f"  Pack {p['pack_index']} ({p['pack_name']}) - {p['topic_name']}: {p['image_path']}"
                    )
                return BatchSessionResponse(
                    message="\n".join(lines),
                    phase=BATCH_PHASE_EDITING,
                    data={"previews": paths},
                )
            return BatchSessionResponse(
                message="暂无预览图。",
                phase=BATCH_PHASE_EDITING,
            )

        if not self._pipeline:
            return BatchSessionResponse(
                message="管线未初始化，无法编辑。请输入 /reset 重新开始。",
                phase=BATCH_PHASE_EDITING,
            )

        packs_summary = self._build_packs_summary_for_edit()

        edit_prompt = build_edit_intent_prompt(
            user_message=user_input,
            packs_summary=packs_summary,
        )

        try:
            intent = self.claude.generate_json(
                prompt=edit_prompt,
                system="You are a JSON extraction bot. Return valid JSON only.",
                max_tokens=400,
                temperature=0.1,
            )
        except Exception as e:
            logger.warning("Edit intent parsing failed: %s", e)
            return BatchSessionResponse(
                message="抱歉，没能理解你的修改意图。请用类似「卡包1话题『加班』的第3张改成更温馨一点」的格式描述。",
                phase=BATCH_PHASE_EDITING,
            )

        if not intent.get("understood", True):
            return BatchSessionResponse(
                message="没能完全理解你的修改意图，能再具体描述一下吗？\n例如：「卡包1话题『加班』的第3张 prompt 改成更温馨一点」",
                phase=BATCH_PHASE_EDITING,
            )

        action = intent.get("action", "view")

        if action == "view":
            info = self._pipeline.get_sticker_info(
                pack_index=intent.get("pack_index", 1),
                topic_identifier=str(intent.get("topic_identifier", "1")),
                sticker_index_in_topic=intent.get("sticker_index_in_topic", 1),
            )
            if info:
                msg = (
                    f"卡包 {info['pack_index']}（{info['pack_name']}）\n"
                    f"话题：{info['topic_name']}\n"
                    f"第 {info['sticker_index']} 张：{info['title']}\n"
                    f"概念：{info['concept']}\n"
                    f"Prompt：{info['image_prompt']}\n"
                    f"文字：{info['text_overlay'] or '(无)'}"
                )
            else:
                msg = "找不到对应的贴纸，请检查卡包序号、话题名和序号。"
            return BatchSessionResponse(message=msg, phase=BATCH_PHASE_EDITING)

        result = self._pipeline.regenerate_sticker(
            pack_index=intent.get("pack_index", 1),
            topic_identifier=str(intent.get("topic_identifier", "1")),
            sticker_index_in_topic=intent.get("sticker_index_in_topic", 1),
            new_prompt=intent.get("new_prompt"),
            modification_intent=intent.get("modification_intent"),
        )

        if result["success"]:
            msg = (
                f"✅ 重新生成成功！\n"
                f"新 Prompt：{result['new_prompt']}\n"
                f"图片路径：{result['image_path']}"
            )
        else:
            msg = f"❌ 重新生成失败：{result.get('error', '未知错误')}"

        return BatchSessionResponse(
            message=msg,
            phase=BATCH_PHASE_EDITING,
            data=result,
        )

    def _build_packs_summary_for_edit(self) -> str:
        """构建用于编辑意图解析的卡包摘要。"""
        if not self._pipeline or not self._pipeline.batch_config:
            return "(no packs)"

        lines = []
        for pc in self._pipeline.batch_config.pack_configs:
            lines.append(f"Pack {pc.pack_index}: {pc.pack_name} ({pc.direction})")
            for t in pc.topics:
                lines.append(f"  Topic '{t.topic_name}' ({len(t.ideas)} stickers)")
                for i, idea in enumerate(t.ideas, 1):
                    title = idea.get("title", f"Sticker {i}")
                    lines.append(f"    {i}. {title}")
        return "\n".join(lines)
