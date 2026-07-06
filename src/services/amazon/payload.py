"""把 Amazon 文案 + 图片 + 价格 拼成 SP-API STICKER_DECAL attributes(纯函数,可单测)。

结构以已通过校验的 docs/amazon_sticker_decal_attributes.example.json 为准
(必填集 / unit_count.type.value 必须 'Count' / 商品标识豁免 / 图片定位符 等)。
详见记忆 amazon-field-facts。
"""
from __future__ import annotations

from typing import Any

US = "ATVPDKIKX0DER"


def _lt(value: Any, mp: str = US) -> list[dict]:
    return [{"value": value, "language_tag": "en_US", "marketplace_id": mp}]


def _l(value: Any, mp: str = US) -> list[dict]:
    return [{"value": value, "marketplace_id": mp}]


def build_sticker_attributes(
    copy: dict[str, Any],
    image_urls: list[str],
    *,
    seller_sku: str,
    price: float,
    quantity: int = 0,
    brand: str = "Inkelligent",
    marketplace_id: str = US,
    country_of_origin: str = "CN",
) -> dict[str, Any]:
    """copy = copy_generator 产出;image_urls[0]=主图,其余=副图(<=8)。

    quantity 默认 0(不可售草稿,安全);上架可售时由调用方传真实库存。
    """
    mp = marketplace_id
    n = int(copy.get("number_of_items") or 50)
    bullets = (copy.get("bullet_points") or [])[:5]

    attrs: dict[str, Any] = {
        "condition_type": _l("new_new", mp),
        "brand": _l(brand, mp),
        "manufacturer": _lt(brand, mp),
        "item_name": _lt((copy.get("item_name") or "").strip()[:200], mp),
        "bullet_point": [
            {"value": str(b).strip(), "language_tag": "en_US", "marketplace_id": mp}
            for b in bullets if str(b).strip()
        ],
        "product_description": _lt((copy.get("product_description") or "").strip(), mp),
        "country_of_origin": _l(country_of_origin, mp),
        "item_type_keyword": _l("vinyl-stickers", mp),
        "supplier_declared_dg_hz_regulation": _l("not_applicable", mp),
        "supplier_declared_has_product_identifier_exemption": _l(True, mp),
        "fulfillment_availability": [
            {"fulfillment_channel_code": "DEFAULT", "quantity": int(quantity)}
        ],
        "purchasable_offer": [{
            "currency": "USD",
            "our_price": [{"schedule": [{"value_with_tax": round(float(price), 2)}]}],
            "marketplace_id": mp,
        }],
        "list_price": [{"value": round(float(price), 2), "currency": "USD", "marketplace_id": mp}],
        "number_of_items": _l(n, mp),
        # unit_count.type.value 必须大写枚举 'Count'(实测小写 'count' 被真实提交拒)
        "unit_count": [{"value": n, "type": {"value": "Count", "language_tag": "en_US"},
                        "marketplace_id": mp}],
        "color": _lt(copy.get("color") or "Multicolor", mp),
        "theme": _lt(copy.get("theme") or "Aesthetic", mp),
        "subject_character": _lt(copy.get("subject_character") or "Assorted", mp),
        "special_feature": _lt(copy.get("special_feature") or "Waterproof", mp),
        "surface_recommendation": _lt(copy.get("surface_recommendation") or "Hard Surface", mp),
        "generic_keyword": _lt((copy.get("generic_keyword") or "").strip(), mp),
        "model_name": _lt(copy.get("model_name") or "Vinyl Sticker Pack", mp),
        "model_number": _l(seller_sku, mp),
        "part_number": _l(seller_sku, mp),
        "required_product_compliance_certificate": _l("Does Not Apply", mp),
    }

    # 图片:主图 + 副图(other_product_image_locator_1..8)
    urls = [u for u in (image_urls or []) if u]
    if urls:
        attrs["main_product_image_locator"] = [
            {"media_location": urls[0], "marketplace_id": mp}
        ]
        for i, u in enumerate(urls[1:9], start=1):
            attrs[f"other_product_image_locator_{i}"] = [
                {"media_location": u, "marketplace_id": mp}
            ]
    return attrs


def validate_ready(copy: dict[str, Any], image_urls: list[str], price: Any) -> list[str]:
    """推送前自检,返回缺失/不合格项的中文提示列表(空=可推送)。"""
    problems: list[str] = []
    if not (copy.get("item_name") or "").strip():
        problems.append("缺标题(item_name)")
    if len([b for b in (copy.get("bullet_points") or []) if str(b).strip()]) < 5:
        problems.append("五点描述不足 5 条")
    if not (copy.get("product_description") or "").strip():
        problems.append("缺产品描述")
    if not [u for u in (image_urls or []) if u]:
        problems.append("缺主图(需先上传图片到 COS)")
    try:
        if price is None or float(price) <= 0:
            problems.append("未设价格")
    except (TypeError, ValueError):
        problems.append("价格非法")
    return problems
