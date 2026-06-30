"""Amazon 文案生成(两步法,全走 AIRouter)。

text_complete(创意稿) → extract_json(结构化)。产出亚马逊风格 listing 文案 + 属性枚举,
喂给 payload.build_sticker_attributes 拼成 SP-API attributes。
"""
from __future__ import annotations

from typing import Any

from src.services.ai.router import get_router

# extract_json 的目标结构(同时作为对模型的约定)
_COPY_SCHEMA: dict[str, Any] = {
    "item_name": "string, <=200 chars, 亚马逊标题: 品牌+品类+数量+尺寸+材质+关键词,无促销词",
    "bullet_points": ["string x5, 每条大写开头短语 + 卖点/场景/材质"],
    "product_description": "string, 一段式英文描述",
    "search_terms": "string, 空格分隔关键词, <=250 bytes, 不重复标题词",
    "color": "string, 如 Multicolor",
    "theme": "string, 如 Aesthetic / Cute / Floral",
    "subject_character": "string, 如 Assorted / Cat",
    "special_feature": "string, 如 Waterproof",
    "surface_recommendation": "string, 如 Hard Surface",
    "material": "string, 如 Vinyl",
    "generic_keyword": "string, 空格分隔, 与 search_terms 可不同",
    "model_name": "string, 如 Vinyl Sticker Pack",
}

_SYSTEM = (
    "You are an Amazon US listing copywriter for a vinyl sticker brand. "
    "Write compliant, keyword-rich English copy. No promotional claims "
    "(no 'best', 'free', 'sale'), no emojis, no Chinese."
)


def generate_copy(
    local_product: dict[str, Any],
    *,
    number_of_items: int = 50,
    brand: str = "Inkelligent",
    keyword_tiers: dict[str, list[str]] | None = None,
    main_model: str | None = None,
    extract_model: str | None = None,
) -> dict[str, Any]:
    """根据 master(local_product)生成 Amazon 文案 dict。

    local_product 需含 title / description_html / selling_points / keywords 等
    (get_tkshop_service().get_local_product 的返回结构)。

    keyword_tiers: 可选,真实搜索量分级词(helium10_import.tier_keywords 的产出),
        形如 {title:[...], bullets:[...], long_tail:[...], backend:[...]}。
        传入则**优先用真实搜索词**做精准投放(标题/五点/后端按层放),不传则沿用 master.keywords。
    """
    title = (local_product.get("title") or "").strip()
    desc = (local_product.get("description_html") or "").strip()
    points = local_product.get("selling_points") or []
    keywords = local_product.get("keywords") or []
    if isinstance(points, str):
        points = [points]
    if isinstance(keywords, str):
        keywords = [keywords]

    kw_block = _keyword_block(keyword_tiers, fallback=keywords)

    prompt = f"""Write Amazon US listing copy for a vinyl sticker pack.

Source material (from our master product):
- Working title: {title}
- Description: {desc[:800]}
- Selling points: {', '.join(map(str, points))[:500]}
{kw_block}
- Pack size: {number_of_items} stickers
- Brand: {brand}

Produce:
1) A keyword-rich Amazon title (<=200 chars): brand + product + count + size + material + top keywords.
2) Exactly 5 bullet points, each starting with an UPPERCASE lead phrase.
3) One product description paragraph.
4) Backend search terms (space separated, <=250 bytes, no words already in the title).
5) Attribute values: color, theme, subject_character, special_feature, surface_recommendation, material, model_name.

Compliant, English only, no emojis, no promotional words."""

    router = get_router()
    raw = router.text_complete(
        prompt, system=_SYSTEM, temperature=0.7,
        model=main_model, task="amazon_copy",
        related_table="local_products", related_id=local_product.get("id"),
    )
    data = router.extract_json(
        raw, _COPY_SCHEMA, model=extract_model,
        instructions="Return strictly the JSON object. bullet_points must have exactly 5 strings.",
        task="amazon_copy_extract",
        related_table="local_products", related_id=local_product.get("id"),
    )
    return _normalize(data, number_of_items=number_of_items)


def _keyword_block(
    tiers: dict[str, list[str]] | None,
    *,
    fallback: list[Any],
) -> str:
    """把分级关键词渲染成给模型的投放指令;无分级则回退 master 关键词。"""
    if tiers:
        def line(label: str, key: str, limit: int) -> str:
            vals = [str(k).strip() for k in (tiers.get(key) or []) if str(k).strip()][:limit]
            return f"- {label}: {', '.join(vals)}" if vals else ""
        parts = [
            "- REAL-search-volume keywords (place by tier, do not invent):",
            line("  MUST appear in Title", "title", 6),
            line("  MUST appear across Bullets", "bullets", 15),
            line("  Use in Description", "long_tail", 20),
            line("  Backend search terms only", "backend", 30),
        ]
        return "\n".join(p for p in parts if p)
    return f"- Keywords: {', '.join(map(str, fallback))[:300]}"


def _normalize(data: dict[str, Any], *, number_of_items: int) -> dict[str, Any]:
    """裁剪/兜底,保证字段齐全可用。"""
    def s(v: Any, default: str = "") -> str:
        return (str(v).strip() if v is not None else default) or default

    bullets = data.get("bullet_points") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    bullets = [s(b) for b in bullets if s(b)][:5]

    return {
        "item_name": s(data.get("item_name"))[:200],
        "bullet_points": bullets,
        "product_description": s(data.get("product_description")),
        "search_terms": s(data.get("search_terms"))[:250],
        "color": s(data.get("color"), "Multicolor"),
        "theme": s(data.get("theme"), "Aesthetic"),
        "subject_character": s(data.get("subject_character"), "Assorted"),
        "special_feature": s(data.get("special_feature"), "Waterproof"),
        "surface_recommendation": s(data.get("surface_recommendation"), "Hard Surface"),
        "material": s(data.get("material"), "Vinyl"),
        "generic_keyword": s(data.get("generic_keyword")) or s(data.get("search_terms")),
        "model_name": s(data.get("model_name"), "Vinyl Sticker Pack"),
        "number_of_items": int(number_of_items or 50),
    }
