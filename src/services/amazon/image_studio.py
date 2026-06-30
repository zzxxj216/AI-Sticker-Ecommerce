"""Amazon 主副图 AI 生成(Gemini 出图 → COS)。

两步:
  1. build_image_plan() —— AIRouter 两步法产出"出图计划":1 张主图 + N 张副图,
     每项给一条**视觉化英文出图 prompt** + 一句 caption(+副图的 overlay_lines)。
     关键词可传入,作为副图主题/卖点的取向(真实搜索词驱动)。
  2. studio_generate() —— 按计划逐张调 Gemini 生成:
       主图  enforce_white_bg=True (亚马逊主图硬性白底)
       副图  enforce_white_bg=False(场景/信息图底图,文字走 overlay 后处理,不靠模型渲染)
     生成的图存本地 → upload 到 COS 拿公网 URL(亚马逊抓图必须公网)。

注意:Gemini 渲染图内文字不稳,**副图的文案/数据放 overlay_lines 当数据返回**,
由前端/后处理叠字,不烤进底图。详见 docs/amazon_custom_listing_design.md 图片层。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import get_router

logger = get_logger("services.amazon.image_studio")

# 副图常见类型(对应设计文档:尺寸示意/场景/材质/变体一览/包装/卖点信息图)
_SECONDARY_ROLES = ("scene", "size_chart", "material", "feature", "variants", "packaging")

_PLAN_SCHEMA: dict[str, Any] = {
    "main": {
        "gen_prompt": "string, 给 AI 出图模型的一条视觉化英文 prompt(贴纸包产品图,纯白底,无文字)",
        "caption": "string, 一句话说明这张图(中/英)",
    },
    "secondary": [
        {
            "role": "scene | size_chart | material | feature | variants | packaging",
            "gen_prompt": "string, 视觉化英文出图 prompt(真实场景/特写,不要求渲染文字)",
            "caption": "string, 一句话说明",
            "overlay_lines": ["string, 该副图要叠加的短文字行(后处理叠字,不烤进底图)"],
        }
    ],
}

_SYSTEM = (
    "You are an Amazon image art director for a vinyl sticker brand. "
    "You write concise, visual, photographic English prompts for an AI image "
    "generator, plus short captions. Prompts describe composition, subject, "
    "lighting and mood — never ask the model to render readable text or logos "
    "(text is added later as an overlay). Compliant, no brand names of others, "
    "no people's faces, no promotional words."
)


def build_image_plan(
    master: dict[str, Any],
    *,
    keywords: Optional[list[str]] = None,
    n_secondary: int = 4,
    brand: str = "Inkelligent",
    main_model: str | None = None,
    extract_model: str | None = None,
) -> dict[str, Any]:
    """产出 {main:{gen_prompt,caption}, secondary:[{role,gen_prompt,caption,overlay_lines}]}。"""
    title = (master.get("title") or "").strip()
    desc = (master.get("description_html") or "").strip()
    points = master.get("selling_points") or []
    if isinstance(points, str):
        points = [points]
    kw = [k for k in (keywords or []) if str(k).strip()][:15]
    roles = list(_SECONDARY_ROLES[:max(1, n_secondary)])

    prompt = f"""Plan Amazon listing images for a vinyl sticker pack.

Product:
- Title: {title}
- Description: {desc[:500]}
- Selling points: {', '.join(map(str, points))[:400]}
- Brand: {brand}
- Top search keywords (weave these themes into secondary images): {', '.join(kw) or '(none)'}

Produce a JSON plan:
1) "main": ONE hero product shot of the sticker pack on a pure white background,
   no text, no props — Amazon main-image compliant. Give a vivid gen_prompt + caption.
2) "secondary": EXACTLY {len(roles)} images, one per role in this order: {roles}.
   For each: a realistic gen_prompt (scene/close-up/flat-lay as fits the role),
   a caption, and overlay_lines = the short text we will overlay on top later
   (e.g. size numbers for size_chart, benefit phrases for feature). 2-4 lines each.

Visual, English, compliant. Do NOT bake readable text into gen_prompt."""

    router = get_router()
    raw = router.text_complete(
        prompt, system=_SYSTEM, temperature=0.7, model=main_model,
        task="amazon_image_plan", related_table="local_products", related_id=master.get("id"),
    )
    data = router.extract_json(
        raw, _PLAN_SCHEMA, model=extract_model,
        instructions="Return strictly the JSON object with 'main' and 'secondary'.",
        task="amazon_image_plan_extract", related_table="local_products", related_id=master.get("id"),
    )
    return _normalize_plan(data, roles=roles)


def _normalize_plan(data: dict[str, Any], *, roles: list[str]) -> dict[str, Any]:
    def s(v: Any) -> str:
        return (str(v).strip() if v is not None else "")

    main_in = data.get("main") if isinstance(data, dict) else None
    main_in = main_in if isinstance(main_in, dict) else {}
    main = {
        "gen_prompt": s(main_in.get("gen_prompt")) or "A neat flat-lay of a vinyl sticker pack, vibrant die-cut stickers spread out, studio lighting.",
        "caption": s(main_in.get("caption")) or "Main product image",
    }

    sec_in = data.get("secondary") if isinstance(data, dict) else None
    sec_in = sec_in if isinstance(sec_in, list) else []
    secondary: list[dict[str, Any]] = []
    for i, item in enumerate(sec_in):
        if not isinstance(item, dict):
            continue
        role = s(item.get("role")) or (roles[i] if i < len(roles) else "scene")
        ovl = item.get("overlay_lines") or []
        if isinstance(ovl, str):
            ovl = [ovl]
        secondary.append({
            "role": role,
            "gen_prompt": s(item.get("gen_prompt")),
            "caption": s(item.get("caption")),
            "overlay_lines": [s(x) for x in ovl if s(x)][:4],
        })
    return {"main": main, "secondary": secondary}


def studio_generate(
    master: dict[str, Any],
    *,
    output_dir: str | Path,
    keywords: Optional[list[str]] = None,
    n_secondary: int = 4,
    reference_image: Optional[str] = None,
    upload: bool = True,
    plan: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """按计划生成主副图,存本地并(可选)传 COS。

    reference_image: 风格参考图(本地路径/URL),建议传 master 首图,保持品牌一致。
    返回 {ok, plan, images:[{role, local_path, cos_url, caption, overlay_lines, prompt, error}]}。
    单张失败不阻断其余;ok = 至少主图成功。
    """
    from src.services.ai.gemini_service import GeminiService

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if plan is None:
        plan = build_image_plan(master, keywords=keywords, n_secondary=n_secondary)

    cdn = None
    if upload:
        from .cdn import get_cdn
        cdn = get_cdn()
        if not cdn.is_configured():
            logger.warning("COS 未配置,生成的图只存本地不上传")
            cdn = None

    try:
        gemini = GeminiService()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Gemini 未配置: {e}", "plan": plan, "images": []}

    # (role, gen_prompt, caption, overlay_lines, enforce_white_bg)
    jobs: list[tuple[str, str, str, list[str], bool]] = [
        ("main", plan["main"]["gen_prompt"], plan["main"]["caption"], [], True),
    ]
    for sec in plan.get("secondary") or []:
        jobs.append((sec["role"], sec["gen_prompt"], sec["caption"],
                     sec.get("overlay_lines") or [], False))

    images: list[dict[str, Any]] = []
    for idx, (role, gprompt, caption, overlay, white_bg) in enumerate(jobs):
        if not gprompt:
            continue
        fname = f"{idx:02d}_{role}_{int(time.time())}.png"
        fpath = out / fname
        rec: dict[str, Any] = {
            "role": role, "caption": caption, "overlay_lines": overlay,
            "prompt": gprompt, "local_path": "", "cos_url": "", "error": "",
        }
        try:
            res = gemini.generate_image(
                gprompt, reference_image=reference_image,
                output_path=fpath, enforce_white_bg=white_bg,
            )
            if not res.get("success"):
                rec["error"] = res.get("error") or "生成失败"
                images.append(rec)
                continue
            rec["local_path"] = res.get("image_path") or str(fpath)
            if cdn is not None:
                try:
                    rec["cos_url"] = cdn.upload_file(rec["local_path"])
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"上传失败: {str(e)[:160]}"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:200]
        images.append(rec)

    ok = any(r["role"] == "main" and r["local_path"] and not r["error"] for r in images)
    return {"ok": ok, "plan": plan, "images": images}
