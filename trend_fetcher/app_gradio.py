"""
交互式素材图片设计 Agent — Gradio Web UI

用法：
  python app_gradio.py
  打开浏览器访问 http://localhost:7860
"""
import sys
import os
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import gradio as gr
from agents import StickerDesignOrchestrator
from image_generator import StickerImageGenerator
from config import config

MAX_DIMS = 10
MAX_PROMPT_SLOTS = 10
REJECT_LABEL = "❌ 拒绝（重新生成此维度）"
SESSIONS_DIR = config.OUTPUT_DIR / "sessions"

CUSTOM_CSS = """
.step-title { margin: 1.2em 0 0.5em; padding: 0.6em 1em; border-radius: 8px;
              background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
              color: white !important; font-weight: bold; }
.step-title * { color: white !important; margin: 0; }
.prompt-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 8px; margin-bottom: 8px; }
"""


# ── Step 1: 分析图片 ──────────────────────────────────────────

def on_analyze(image_path, user_text, state, enable_smart):
    user_text = (user_text or "").strip()
    if image_path is None and not user_text:
        gr.Warning("请至少上传图片或输入文本描述")
        return {}, "⏳ 等待上传图片...", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), *dim_hidden()

    orch = StickerDesignOrchestrator(enable_smart_mode=enable_smart)
    result = orch.step1_analyze(image_path=image_path, user_text=user_text)

    if not result:
        gr.Warning("图片分析失败，请重试")
        return {}, "❌ 分析失败", gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), *dim_hidden()

    state = {"orch": orch, "image_path": image_path, "user_text": user_text}
    analysis = result.get("analysis", {})
    suggested = max(1, min(int(result.get("suggested_count", 5)), 10))
    dims = result.get("dimensions", [])

    # 检查是否有智能推荐
    smart_preset = result.get("smart_preset")
    if smart_preset and enable_smart:
        confidence = smart_preset.get("confidence", 0)
        preset_name = smart_preset.get("preset_name", "智能推荐")
        md = format_analysis(analysis, suggested)
        md += f"\n\n### 🤖 智能推荐（置信度: {confidence:.0%}）\n"
        md += f"基于你的历史偏好，系统为你推荐了「{preset_name}」配置。\n"
        md += "你可以直接点击「应用智能推荐」快速应用，或手动选择维度。"

        # 将智能推荐应用到维度显示中
        for dim in dims:
            dim_key = dim.get("key")
            if dim_key in smart_preset.get("selections", {}):
                dim["selection"] = smart_preset["selections"][dim_key]
                dim["selection_name"] = smart_preset["display_names"].get(dim_key, [])
                dim["status"] = "approved"

        dim_updates = build_dim_updates(dims)
        return state, md, gr.update(visible=True), gr.update(value=suggested), gr.update(visible=True), *dim_updates
    else:
        md = format_analysis(analysis, suggested)
        dim_updates = build_dim_updates(dims)
        return state, md, gr.update(visible=True), gr.update(value=suggested), gr.update(visible=False), *dim_updates


def format_analysis(a: dict, suggested: int) -> str:
    return (
        f"### ✅ 分析完成\n\n"
        f"| 项目 | 内容 |\n|---|---|\n"
        f"| 主体 | {a.get('subject', '—')} |\n"
        f"| 风格 | {a.get('style', '—')} |\n"
        f"| 配色 | {', '.join(a.get('colors', []))} |\n"
        f"| 氛围 | {a.get('mood', '—')} |\n"
        f"| 总结 | {a.get('summary', '—')} |\n"
        f"| 建议数量 | {suggested} |"
    )


def build_dim_updates(dims):
    updates = []
    for i in range(MAX_DIMS):
        if i < len(dims):
            dim = dims[i]
            opts = dim.get("options", [])
            choices = [o["name"] for o in opts] + [REJECT_LABEL]
            label = f"🔹 {dim['label']}"
            if dim.get("description"):
                label += f" — {dim['description']}"

            pre_selected = []
            custom_txt = ""
            custom_visible = False
            if dim.get("status") == "approved" and dim.get("selection_name"):
                names = dim["selection_name"]
                if isinstance(names, list):
                    pre_selected = [n for n in names if n in choices]
                    if dim.get("selection") and "other" in (dim["selection"] if isinstance(dim["selection"], list) else [dim["selection"]]):
                        custom_item = next((c for c in choices if "自定义" in c), None)
                        if custom_item:
                            pre_selected.append(custom_item)
                        custom_txt = dim.get("custom_text", "")
                        custom_visible = True
                else:
                    sel = dim.get("selection", "")
                    if sel == "other":
                        custom_item = next((c for c in choices if "自定义" in c), None)
                        if custom_item:
                            pre_selected = [custom_item]
                        custom_txt = str(names)
                        custom_visible = True
                    elif names in choices:
                        pre_selected = [names]

            updates.extend([
                gr.update(choices=choices, value=pre_selected, label=label, visible=True),
                gr.update(value=custom_txt, visible=custom_visible),
            ])
        else:
            updates.extend([
                gr.update(choices=[], value=[], visible=False),
                gr.update(value="", visible=False),
            ])
    return updates


def dim_hidden():
    return [gr.update(visible=False), gr.update(visible=False)] * MAX_DIMS


def on_dim_change(values):
    if not values:
        return gr.update(visible=False)
    has_reject = any("拒绝" in v for v in values)
    has_custom = any("自定义" in v for v in values)
    if has_reject:
        return gr.update(visible=True, placeholder="请输入拒绝原因（可选）...", label="💬 拒绝原因（可选）")
    if has_custom:
        return gr.update(visible=True, placeholder="输入你想要的内容...", label="✏️ 自定义输入")
    return gr.update(visible=False)


# ── 会话记录 保存 / 加载 / 展示 ─────────────────────────────

def _save_session(state):
    """将当前会话数据保存为 JSON，同一 session 覆盖写入。"""
    if not state or "orch" not in state:
        return None
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    orch: StickerDesignOrchestrator = state["orch"]
    mem = orch.memory

    if "session_id" not in state:
        state["session_id"] = datetime.now().strftime("%Y%m%d_%H%M%S")

    a = (mem.analysis or {}).get("analysis", {})

    dims = []
    for key, s in mem.dimension_states.items():
        dims.append({
            "key": key,
            "label": s["dim_data"].get("label", key),
            "description": s["dim_data"].get("description", ""),
            "selected": s.get("selection_name") or [],
            "status": s["status"],
        })

    prompts = []
    for ps in mem.prompt_states:
        prompts.append({
            "index": ps["index"],
            "title": ps["data"].get("title", ""),
            "concept": ps["data"].get("concept", ""),
            "image_prompt": ps["data"].get("image_prompt", ""),
        })

    results = []
    if mem.results:
        for r in mem.results:
            results.append({
                "index": r.get("index"),
                "title": r.get("title", ""),
                "image_prompt": r.get("image_prompt", ""),
                "image_path": r.get("image_path"),
                "success": r.get("success", False),
                "error": r.get("error"),
            })

    session_data = {
        "session_id": state["session_id"],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input": {
            "image_path": state.get("image_path"),
            "user_text": state.get("user_text", ""),
        },
        "analysis": a,
        "dimensions": dims,
        "requirement_summary": mem.requirement_summary or "",
        "count": state.get("count", 5),
        "prompts": prompts,
        "results": results,
    }

    filepath = SESSIONS_DIR / f"session_{state['session_id']}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)
    print(f"  [Session] 已保存: {filepath.name}")
    return str(filepath)


def _load_session_list():
    """扫描 sessions 目录，返回 (label, filepath) 列表供 Dropdown 使用。"""
    if not SESSIONS_DIR.exists():
        return []
    files = sorted(SESSIONS_DIR.glob("session_*.json"), reverse=True)
    choices = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            ts = data.get("timestamp", "")
            text = data.get("input", {}).get("user_text", "")[:30] or "无文本"
            n_prompts = len(data.get("prompts", []))
            n_images = sum(1 for r in data.get("results", []) if r.get("success"))
            label = f"{ts} | {text} | {n_prompts}条prompt {n_images}张图"
            choices.append((label, str(f)))
        except Exception:
            continue
    return choices


def _load_session_detail(selected_filepath):
    """根据文件路径加载会话详情，返回 (markdown, gallery_images)。"""
    if not selected_filepath:
        return "", []
    try:
        with open(selected_filepath, encoding="utf-8") as fp:
            data = json.load(fp)
        return _format_session_md(data), _collect_session_images(data)
    except Exception as e:
        return f"加载失败: {e}", []


def _format_session_md(data: dict) -> str:
    lines = [f"## 📅 {data.get('timestamp', '')}"]

    inp = data.get("input", {})
    if inp.get("user_text"):
        lines.append(f"**输入文本**: {inp['user_text']}")
    if inp.get("image_path"):
        lines.append(f"**输入图片**: `{inp['image_path']}`")

    a = data.get("analysis", {})
    if a:
        lines.append(
            f"\n| 项目 | 内容 |\n|---|---|\n"
            f"| 主体 | {a.get('subject', '—')} |\n"
            f"| 风格 | {a.get('style', '—')} |\n"
            f"| 配色 | {', '.join(a.get('colors', []))} |\n"
            f"| 氛围 | {a.get('mood', '—')} |"
        )

    summary = data.get("requirement_summary", "")
    if summary:
        lines.append(f"\n**需求摘要**: {summary}")

    dims = data.get("dimensions", [])
    if dims:
        lines.append("\n### 维度选择")
        for d in dims:
            sel = d.get("selected", [])
            sel_str = " + ".join(sel) if isinstance(sel, list) and sel else str(sel) or "—"
            lines.append(f"- **{d.get('label', '')}**: {sel_str}")

    prompts = data.get("prompts", [])
    if prompts:
        lines.append(f"\n### Prompts（{len(prompts)} 条）")
        for p in prompts:
            lines.append(f"**#{p.get('index', '')} {p.get('title', '')}** — {p.get('concept', '')}")
            lines.append(f"> {p.get('image_prompt', '')}")

    results = data.get("results", [])
    if results:
        success = sum(1 for r in results if r.get("success"))
        lines.append(f"\n### 生成结果: {success}/{len(results)} 成功")

    return "\n".join(lines)


def _collect_session_images(data: dict) -> list:
    images = []
    for r in data.get("results", []):
        if r.get("success") and r.get("image_path"):
            p = Path(r["image_path"])
            if p.exists():
                images.append((str(p), r.get("title", "")))
    return images


# ── Step 2: 确认维度 ─────────────────────────────────────────

def on_confirm_dims(state, count_val, *dim_vals):
    if not state or "orch" not in state:
        gr.Warning("请先分析图片")
        no_change = [state, "", gr.update(), gr.update()] + list(dim_hidden()) + [gr.update(visible=False)]
        return no_change

    orch: StickerDesignOrchestrator = state["orch"]
    dims = orch.memory.get_all_dimensions()

    has_reject = False
    for i, dim_info in enumerate(dims):
        if i >= MAX_DIMS:
            break
        selected_values = dim_vals[i * 2] or []
        custom_val = dim_vals[i * 2 + 1] or ""

        if not selected_values:
            gr.Warning(f"请为「{dim_info['label']}」至少选择一个选项")
            no_change = [state, "", gr.update(), gr.update()] + list(dim_hidden()) + [gr.update(visible=False)]
            return no_change

        is_reject = any("拒绝" in v for v in selected_values)
        if is_reject:
            orch.step2_reject_dimension(dim_info["key"], custom_val.strip())
            has_reject = True
            continue

        normal_picks = [v for v in selected_values if "自定义" not in v and "拒绝" not in v]
        has_custom = any("自定义" in v for v in selected_values)
        option_ids, option_names = [], []

        for pick in normal_picks:
            opts = dim_info.get("options", [])
            matched = next((o for o in opts if o["name"] == pick), None)
            if matched:
                option_ids.append(matched["id"])
                option_names.append(pick)

        if has_custom:
            if not custom_val.strip():
                gr.Warning(f"请为「{dim_info['label']}」输入自定义内容")
                no_change = [state, "", gr.update(), gr.update()] + list(dim_hidden()) + [gr.update(visible=False)]
                return no_change
            option_ids.append("other")
            option_names.append(custom_val.strip())

        if not option_ids:
            gr.Warning(f"请为「{dim_info['label']}」至少选择一个有效选项")
            no_change = [state, "", gr.update(), gr.update()] + list(dim_hidden()) + [gr.update(visible=False)]
            return no_change

        orch.step2_approve_dimension(dim_info["key"], option_ids, option_names)

    if has_reject:
        rejected = orch.memory.get_rejected_dimensions()
        gr.Info(f"正在重新生成 {len(rejected)} 个被拒绝的维度...")
        refined = orch.step1_refine()
        if not refined:
            gr.Warning("维度重新生成失败，请重试")
            current_dims = orch.memory.get_all_dimensions()
            dim_updates = build_dim_updates(current_dims)
            return [state, "❌ 维度重新生成失败", gr.update(), gr.update()] + dim_updates + [gr.update(visible=False)]
        new_dims = orch.memory.get_all_dimensions()
        dim_updates = build_dim_updates(new_dims)
        return [state, "🔄 部分维度已重新生成，请重新选择", gr.update(), gr.update()] + dim_updates + [gr.update(visible=False)]

    summary = orch.step2_get_summary()
    count = int(count_val) if count_val else orch.memory.suggested_count
    state["count"] = max(1, min(count, 10))
    state["_summary_text"] = summary

    summary_md = f"### ✅ 维度选择完成，生成数量: {state['count']} 条"
    return [state, summary_md, gr.update(), gr.update()] + list(dim_hidden()) + [gr.update(visible=True)]


# ── Step 3: 生成 Prompt ───────────────────────────────────────

def _prompt_slot_hidden():
    return [gr.update(visible=False, value="")] * MAX_PROMPT_SLOTS


def _prompt_slot_updates(prompts: list[dict]) -> list:
    updates = []
    for i in range(MAX_PROMPT_SLOTS):
        if i < len(prompts):
            p = prompts[i]
            title = p.get("title", f"方案{i+1}")
            concept = p.get("concept", "")
            prompt_text = p.get("image_prompt", "")
            label = f"#{p.get('index', i+1)} {title} — {concept}"
            updates.append(gr.update(visible=True, value=prompt_text, label=label))
        else:
            updates.append(gr.update(visible=False, value=""))
    return updates


def _image_slot_hidden():
    return [gr.update(visible=False, value=None)] * MAX_PROMPT_SLOTS


def on_generate_prompts(state, edited_summary):
    if not state or "orch" not in state:
        gr.Warning("请先完成维度选择")
        state = state or {}
        state["_prompts_ok"] = False
        return [state, ""] + _prompt_slot_hidden()

    orch: StickerDesignOrchestrator = state["orch"]
    count = state.get("count", orch.memory.suggested_count)

    edited = (edited_summary or "").strip()
    if edited:
        orch.memory.requirement_summary = edited

    prompts = orch.step3_generate_prompts(count=count)
    if not prompts:
        gr.Warning("Prompt 生成失败")
        state["_prompts_ok"] = False
        return [state, "❌ Prompt 生成失败，请重试"] + _prompt_slot_hidden()

    state["_prompts_ok"] = True
    _save_session(state)
    md = f"### 设计方案（共 {len(prompts)} 条）\n可直接编辑 Prompt，然后点「生成全部图片」或单条「重新生成」。"
    slot_updates = _prompt_slot_updates(prompts)

    return [state, md] + slot_updates


def _after_generate_prompts(state):
    if state and state.get("_prompts_ok"):
        return gr.update(visible=True), gr.update(visible=True)
    return gr.update(visible=False), gr.update(visible=False)


# ── Step 4: 并发生成全部图片 ──────────────────────────────────

def on_generate_all_images(state, *prompt_vals):
    """读取编辑后的 prompt，并发生成所有图片，结果填入对应 Image 组件。"""
    if not state or "orch" not in state:
        gr.Warning("请先完成方案生成")
        return [state, ""] + _image_slot_hidden()

    orch: StickerDesignOrchestrator = state["orch"]
    all_ps = orch.memory.get_all_prompts_with_status()

    ideas_to_gen = []
    for i, ps in enumerate(all_ps):
        if i >= MAX_PROMPT_SLOTS:
            break
        edited_text = (prompt_vals[i] or "").strip()
        if edited_text:
            ps_data = orch.memory.prompt_states[i]["data"]
            ps_data["image_prompt"] = edited_text
            orch.memory.approve_prompt(ps["index"])
            ideas_to_gen.append(ps_data)

    if not ideas_to_gen:
        gr.Warning("请至少保留一条 Prompt")
        return [state, "⚠️ 无可生成方案"] + _image_slot_hidden()

    gr.Info(f"开始并发生成 {len(ideas_to_gen)} 张图片...")
    results = orch.sticker_gen.generate(ideas_to_gen, ref_image=orch.memory.image_path)
    orch.memory.results = results

    result_map = {}
    for r in results:
        result_map[r.get("index")] = r

    image_updates = []
    success, fail = 0, 0
    for i in range(MAX_PROMPT_SLOTS):
        if i < len(all_ps):
            ps = all_ps[i]
            r = result_map.get(ps["index"])
            if r and r["success"] and r.get("image_path"):
                image_updates.append(gr.update(visible=True, value=r["image_path"]))
                success += 1
            elif r:
                image_updates.append(gr.update(visible=True, value=None))
                fail += 1
            else:
                image_updates.append(gr.update(visible=False, value=None))
        else:
            image_updates.append(gr.update(visible=False, value=None))

    _save_session(state)
    md = f"### 生成完成！{success}/{success + fail} 张成功"
    return [state, md] + image_updates


# ── 单条重新生成 Prompt ───────────────────────────────────────

def _make_regen_prompt_fn(slot_idx: int):
    """为第 slot_idx 个槽位创建「重新生成 Prompt」回调，返回新 prompt 文本。"""
    def regen_prompt(state):
        if not state or "orch" not in state:
            gr.Warning("请先完成初始化")
            return gr.update()

        orch: StickerDesignOrchestrator = state["orch"]
        all_ps = orch.memory.get_all_prompts_with_status()

        if slot_idx >= len(all_ps):
            gr.Warning("无对应方案")
            return gr.update()

        ps = all_ps[slot_idx]
        old_prompt = orch.memory.prompt_states[slot_idx]["data"]

        others = [
            orch.memory.prompt_states[j]["data"]
            for j in range(len(all_ps)) if j != slot_idx
        ]
        rejected = [{**old_prompt, "feedback": "用户希望重新生成该条 prompt"}]

        summary = orch.memory.requirement_summary or orch.memory.build_requirement_summary()
        gr.Info(f"正在重新生成 #{ps['index']} 的 Prompt...")

        try:
            new_prompts = orch.prompt_gen.regenerate(
                analysis=orch.memory.analysis,
                requirement_summary=summary,
                image_path=orch.memory.image_path or "",
                approved_prompts=others,
                rejected_prompts=rejected,
            )
        except Exception as e:
            gr.Warning(f"Prompt 重新生成异常: {e}")
            return gr.update()

        if new_prompts:
            new_p = new_prompts[0]
            new_p["index"] = ps["index"]
            orch.memory.prompt_states[slot_idx]["data"] = new_p
            new_text = new_p.get("image_prompt", "")
            title = new_p.get("title", f"方案{slot_idx+1}")
            concept = new_p.get("concept", "")
            label = f"#{ps['index']} {title} — {concept}"
            return gr.update(value=new_text, label=label)
        else:
            gr.Warning("Prompt 重新生成失败")
            return gr.update()

    return regen_prompt


# ── 单条重新生成图片 ──────────────────────────────────────────

def _make_regen_image_fn(slot_idx: int):
    """为第 slot_idx 个槽位创建「生成/重新生成图片」回调。"""
    def regen_image(state, prompt_text):
        if not state or "orch" not in state:
            gr.Warning("请先完成初始化")
            return gr.update()
        if not (prompt_text or "").strip():
            gr.Warning("Prompt 为空，无法生成")
            return gr.update()

        orch: StickerDesignOrchestrator = state["orch"]
        all_ps = orch.memory.get_all_prompts_with_status()

        if slot_idx >= len(all_ps):
            gr.Warning("无对应方案")
            return gr.update()

        ps = all_ps[slot_idx]
        idea = {
            "index": ps["index"],
            "title": ps.get("title", f"方案{slot_idx+1}"),
            "image_prompt": prompt_text.strip(),
        }
        orch.memory.prompt_states[slot_idx]["data"]["image_prompt"] = prompt_text.strip()

        gr.Info(f"正在生成 #{ps['index']} 的图片...")
        try:
            result = orch.sticker_gen.gemini.generate_one(idea, ref_image=orch.memory.image_path)
        except Exception as e:
            gr.Warning(f"图片生成异常: {e}")
            return gr.update(visible=True, value=None)

        if result["success"] and result.get("image_path"):
            if not orch.memory.results:
                orch.memory.results = []
            existing = [r for r in orch.memory.results if r.get("index") != ps["index"]]
            existing.append(result)
            orch.memory.results = existing
            _save_session(state)
            return gr.update(visible=True, value=result["image_path"])
        else:
            gr.Warning(f"图片生成失败: {result.get('error', '未知错误')}")
            return gr.update(visible=True, value=None)

    return regen_image


# ── 构建 App ──────────────────────────────────────────────────

def create_app():
    with gr.Blocks() as app:
        state = gr.State({})

        gr.Markdown("# 🎨 交互式素材图片设计 Agent\n上传图片和/或输入文本描述，AI 分析后生成一系列风格统一的素材图片")

        # ── Step 1 ──
        gr.HTML('<div class="step-title"><h3>Step 1 — 输入图片/文本 & AI 分析</h3></div>')
        with gr.Row():
            with gr.Column(scale=1):
                image_input = gr.Image(type="filepath", label="上传图片", height=300)
                text_input = gr.Textbox(
                    label="文本描述（可选）",
                    placeholder="可输入风格、主体、用途等补充要求；可与图片同时输入",
                    lines=4,
                )
                smart_mode_checkbox = gr.Checkbox(
                    label="🤖 启用智能推荐模式",
                    value=True,
                    info="基于历史偏好自动推荐维度选项"
                )
                analyze_btn = gr.Button("🔍 分析图片", variant="primary", size="lg")
            with gr.Column(scale=1):
                analysis_md = gr.Markdown("⏳ 等待上传图片...")

        # ── 智能推荐按钮 ──
        with gr.Group(visible=False) as smart_preset_group:
            apply_preset_btn = gr.Button("✨ 应用智能推荐", variant="secondary", size="lg")

        # ── Step 2 ──
        with gr.Group(visible=False) as dim_group:
            gr.HTML('<div class="step-title"><h3>Step 2 — 选择偏好（可多选组合）</h3></div>')
            gr.Markdown("每个维度可**多选**，AI 会根据所有选中项组合生成方案。选择 **❌ 拒绝** 可替换整个维度。")

            dim_checkboxes = []
            dim_customs = []
            for i in range(MAX_DIMS):
                cb = gr.CheckboxGroup(choices=[], label=f"维度 {i+1}", visible=False)
                c = gr.Textbox(label="✏️ 输入", visible=False)
                dim_checkboxes.append(cb)
                dim_customs.append(c)

            count_input = gr.Number(label="生成数量（最多10）", value=5, minimum=1, maximum=10, precision=0)
            dim_status_md = gr.Markdown("")
            confirm_btn = gr.Button("✅ 确认选择", variant="primary", size="lg")

        # ── Step 2b: 需求摘要编辑 + 生成按钮 ──
        with gr.Group(visible=False) as gen_prompt_group:
            summary_editor = gr.Textbox(
                label="📝 总体需求摘要（可手动编辑，修改后将直接影响 Prompt 生成）",
                lines=4,
                max_lines=10,
                interactive=True,
            )
            gen_prompt_btn = gr.Button("🎯 生成设计方案", variant="primary", size="lg")

        # ── Step 3+4: Prompt 编辑 + 图片展示 ──
        prompt_review_md = gr.Markdown("")

        prompt_editors = []
        prompt_images = []
        regen_prompt_btns = []
        regen_image_btns = []

        with gr.Group(visible=False) as prompt_edit_group:
            gr.HTML('<div class="step-title"><h3>Step 3 — 编辑 Prompt & 生成图片</h3></div>')
            gr.Markdown("编辑 Prompt 后可「生成全部」，或对单条分别「重新生成 Prompt」/「重新生成图片」。")

            for i in range(MAX_PROMPT_SLOTS):
                tb = gr.Textbox(
                    label=f"方案 {i+1}",
                    visible=False,
                    lines=3,
                    max_lines=6,
                )
                with gr.Row():
                    img = gr.Image(
                        label=f"生成结果 #{i+1}",
                        visible=False,
                        height=256,
                        type="filepath",
                        interactive=False,
                    )
                with gr.Row():
                    rp_btn = gr.Button(f"🔄 重新生成 Prompt #{i+1}", visible=False, size="sm")
                    ri_btn = gr.Button(f"🖼️ 生成图片 #{i+1}", visible=False, size="sm")
                prompt_editors.append(tb)
                prompt_images.append(img)
                regen_prompt_btns.append(rp_btn)
                regen_image_btns.append(ri_btn)

            gen_all_btn = gr.Button("🚀 生成全部图片", variant="primary", size="lg")

        result_md = gr.Markdown("")

        # ── 历史记录 ──
        with gr.Accordion("📋 历史记录", open=False):
            with gr.Row():
                history_dropdown = gr.Dropdown(
                    choices=_load_session_list(),
                    label="选择历史会话",
                    scale=5,
                    interactive=True,
                )
                history_refresh_btn = gr.Button("🔄 刷新列表", scale=1, size="sm")
            history_detail_md = gr.Markdown("")
            history_gallery = gr.Gallery(label="生成的图片", columns=4, height="auto")

        # ── 事件绑定 ──────────────────────────

        # Step 1
        analyze_outputs = [state, analysis_md, dim_group, count_input, smart_preset_group]
        for i in range(MAX_DIMS):
            analyze_outputs.extend([dim_checkboxes[i], dim_customs[i]])
        analyze_btn.click(fn=on_analyze, inputs=[image_input, text_input, state, smart_mode_checkbox], outputs=analyze_outputs)

        # 应用智能推荐
        def on_apply_preset(state):
            if not state or "orch" not in state:
                gr.Warning("请先分析图片")
                return state, gr.update(visible=False)

            orch: StickerDesignOrchestrator = state["orch"]
            if not orch.memory.analysis:
                gr.Warning("没有可用的分析结果")
                return state, gr.update(visible=False)

            smart_preset = orch.memory.analysis.get("smart_preset")
            if not smart_preset:
                gr.Warning("没有可用的智能推荐")
                return state, gr.update(visible=False)

            # 应用预设
            orch.apply_smart_preset(smart_preset)
            gr.Info("✅ 已应用智能推荐，可直接点击「确认选择」")

            return state, gr.update(visible=False)

        apply_preset_btn.click(
            fn=on_apply_preset,
            inputs=[state],
            outputs=[state, smart_preset_group]
        )

        for i in range(MAX_DIMS):
            dim_checkboxes[i].change(fn=on_dim_change, inputs=[dim_checkboxes[i]], outputs=[dim_customs[i]])

        # Step 2
        confirm_inputs = [state, count_input]
        for i in range(MAX_DIMS):
            confirm_inputs.extend([dim_checkboxes[i], dim_customs[i]])
        confirm_outputs = [state, dim_status_md, dim_group, count_input]
        for i in range(MAX_DIMS):
            confirm_outputs.extend([dim_checkboxes[i], dim_customs[i]])
        confirm_outputs.append(gen_prompt_group)
        confirm_btn.click(
            fn=on_confirm_dims, inputs=confirm_inputs, outputs=confirm_outputs
        ).then(
            fn=lambda s: gr.update(value=s.get("_summary_text", "")) if s and s.get("_summary_text") else gr.update(),
            inputs=[state],
            outputs=[summary_editor],
        )

        # Step 3: 生成 prompt
        gen_prompt_outputs = [state, prompt_review_md] + prompt_editors
        gen_prompt_btn.click(
            fn=on_generate_prompts, inputs=[state, summary_editor], outputs=gen_prompt_outputs
        ).then(
            fn=_after_generate_prompts,
            inputs=[state],
            outputs=[prompt_edit_group, gen_all_btn],
        ).then(
            fn=lambda state: _show_regen_buttons(state),
            inputs=[state],
            outputs=regen_prompt_btns + regen_image_btns,
        )

        # Step 4: 并发生成全部图片
        gen_all_inputs = [state] + prompt_editors
        gen_all_outputs = [state, result_md] + prompt_images
        gen_all_btn.click(fn=on_generate_all_images, inputs=gen_all_inputs, outputs=gen_all_outputs)

        # 单条重新生成 Prompt
        for i in range(MAX_PROMPT_SLOTS):
            regen_prompt_btns[i].click(
                fn=_make_regen_prompt_fn(i),
                inputs=[state],
                outputs=[prompt_editors[i]],
            )

        # 单条重新生成图片
        for i in range(MAX_PROMPT_SLOTS):
            regen_image_btns[i].click(
                fn=_make_regen_image_fn(i),
                inputs=[state, prompt_editors[i]],
                outputs=[prompt_images[i]],
            )

        # 历史记录
        def _refresh_history():
            new_choices = _load_session_list()
            return gr.update(choices=new_choices, value=None)

        history_refresh_btn.click(
            fn=_refresh_history,
            outputs=[history_dropdown],
        )
        history_dropdown.change(
            fn=_load_session_detail,
            inputs=[history_dropdown],
            outputs=[history_detail_md, history_gallery],
        )

    return app


def _show_regen_buttons(state):
    """根据实际 prompt 数量显示/隐藏两组重新生成按钮。"""
    if not state or "orch" not in state:
        return [gr.update(visible=False)] * (MAX_PROMPT_SLOTS * 2)

    orch: StickerDesignOrchestrator = state["orch"]
    count = len(orch.memory.get_all_prompts_with_status())
    updates = []
    for i in range(MAX_PROMPT_SLOTS):
        updates.append(gr.update(visible=(i < count)))
    for i in range(MAX_PROMPT_SLOTS):
        updates.append(gr.update(visible=(i < count)))
    return updates


if __name__ == "__main__":
    app = create_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=gr.themes.Soft(),
        css=CUSTOM_CSS,
        allowed_paths=[str(config.IMAGE_OUTPUT_DIR)],
        share=True
    )
