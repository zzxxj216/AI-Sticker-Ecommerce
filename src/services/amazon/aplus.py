"""Amazon A+ 内容生成(模块化图文,两步 AI)。

产出一组标准 A+ 模块(header / single_image_text / three_image_text / comparison / closing),
每个模块的图片用 image_idx 引用已上传 COS 的产品图。前端按模块类型可视化渲染。
提交到 Amazon 需中间层 A+ Content API(后续);本模块只管内容生成。
"""
from __future__ import annotations

from typing import Any

from src.services.ai.router import get_router

# 目标结构(同时作为对模型的约定)
_APLUS_SCHEMA: dict[str, Any] = {
    "modules": [
        {
            "type": "header | single_image_text | three_image_text | comparison | closing",
            "headline": "string, 模块标题(<=160 chars)",
            "body": "string, 正文(<=500 chars);comparison 模块可空",
            "cards": [
                {"heading": "string", "body": "string"}
            ],
            "comparison": {
                "columns": ["This Pack", "Typical Others"],
                "rows": [{"feature": "string", "values": ["string", "string"]}],
            },
        }
    ]
}

_SYSTEM = (
    "You are an Amazon A+ Content copywriter for a vinyl sticker brand. "
    "Write benefit-driven, compliant English module copy. No promotional claims "
    "(no 'best/free/sale/guarantee'), no emojis, no Chinese, no competitor brand names."
)


def generate_aplus(local_product: dict[str, Any], *, brand: str = "Inkelligent",
                   keywords: list[str] | None = None,
                   main_model: str | None = None, extract_model: str | None = None) -> dict[str, Any]:
    """根据 master 生成 A+ 模块 dict {modules:[...]}。

    keywords: 可选,真实搜索量高频词(扁平排序列表),作为模块文案要自然融入的主题。
    """
    title = (local_product.get("title") or "").strip()
    desc = (local_product.get("description_html") or "").strip()
    points = local_product.get("selling_points") or []
    if isinstance(points, str):
        points = [points]
    kw = [str(k).strip() for k in (keywords or []) if str(k).strip()][:12]
    kw_line = (f"- Weave these real-search keywords naturally into the copy: "
               f"{', '.join(kw)}\n") if kw else ""

    prompt = f"""Create Amazon A+ Content for a vinyl sticker pack.

Source:
- Product: {title}
- Description: {desc[:600]}
- Selling points: {', '.join(map(str, points))[:400]}
- Brand: {brand}
{kw_line}

Produce these 5 modules in order:
1) "header": brand banner headline + one-line tagline (body).
2) "single_image_text": what's in the pack / material quality.
3) "three_image_text": exactly 3 cards of use cases (e.g. Laptops, Water Bottles, Journals), each heading + short body.
4) "comparison": a comparison table, columns ["{brand}", "Typical Others"], 4-5 feature rows (Waterproof, Matte finish, Residue-free, Fade-resistant, Quantity) with values per column.
5) "closing": a warm closing headline + body (gift / personalization angle).

English only, compliant, benefit-driven, no emojis."""

    router = get_router()
    raw = router.text_complete(
        prompt, system=_SYSTEM, temperature=0.7, model=main_model,
        task="amazon_aplus", related_table="local_products", related_id=local_product.get("id"),
    )
    data = router.extract_json(
        raw, _APLUS_SCHEMA, model=extract_model,
        instructions="Return strictly the JSON object with a 'modules' array.",
        task="amazon_aplus_extract", related_table="local_products", related_id=local_product.get("id"),
    )
    return _normalize(data)


_ALLOWED = {"header", "single_image_text", "three_image_text", "comparison", "closing"}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    mods_in = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(mods_in, list):
        mods_in = []
    out = []
    img_idx = 0
    for m in mods_in:
        if not isinstance(m, dict):
            continue
        t = str(m.get("type") or "").strip()
        if t not in _ALLOWED:
            continue
        mod: dict[str, Any] = {
            "type": t,
            "headline": str(m.get("headline") or "").strip()[:160],
            "body": str(m.get("body") or "").strip()[:500],
        }
        if t in ("header", "single_image_text"):
            mod["image_idx"] = img_idx
            img_idx += 1
        if t == "three_image_text":
            cards = m.get("cards") or []
            mod["cards"] = []
            for cd in cards[:3]:
                if not isinstance(cd, dict):
                    continue
                mod["cards"].append({
                    "heading": str(cd.get("heading") or "").strip()[:80],
                    "body": str(cd.get("body") or "").strip()[:200],
                    "image_idx": img_idx,
                })
                img_idx += 1
        if t == "comparison":
            comp = m.get("comparison") or {}
            cols = comp.get("columns") or ["This Pack", "Typical Others"]
            rows = []
            for rw in (comp.get("rows") or [])[:6]:
                if not isinstance(rw, dict):
                    continue
                rows.append({
                    "feature": str(rw.get("feature") or "").strip()[:60],
                    "values": [str(v).strip()[:40] for v in (rw.get("values") or [])][:len(cols)],
                })
            mod["comparison"] = {"columns": [str(c)[:40] for c in cols][:3], "rows": rows}
        out.append(mod)
    return {"modules": out}
