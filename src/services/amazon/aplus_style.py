"""Amazon A+ 固定横幅样式 — 一套固定的宽幅设计图提示词 + 生成器。

中间层的 A+ 端点(POST /api/v1/amazon/aplus/documents)把每张公网图变成一个
STANDARD_HEADER_IMAGE_TEXT 模块(等比缩放+补白到 970x600,单文档 <=7 模块),
所以「A+ 固定样式」= 固定生成 3 张宽幅横幅设计图,顺序即模块顺序:

  1. brand_banner   — 品牌横幅:全套贴纸铺开 + 主题氛围(第一屏)
  2. quality_detail — 材质细节:防水/哑光/die-cut 白边特写(信任)
  3. usage_scene    — 使用场景:笔电/水杯/手账多场景拼贴(种草)

全部 AI 图生图(以 pack 真实贴纸为参考),Gemini 生成;提示词固定,改这里即可
全局调整样式。生成 → 存盘(output/packs/<uid>/products/amazon/aplus/)→ 传 COS
拿公网 URL。横幅构图按 970x600(约 1.6:1)的宽幅设计,中间层会自动 fit。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger

logger = get_logger("service.amazon.aplus_style")

# 宽幅构图 + 保真条款(不重绘贴纸图案/文字)。
_WIDE = (
    "Wide landscape banner composition, roughly 970x600 aspect (1.6:1), "
    "designed as an Amazon A+ content header image. "
)
_FIDELITY = (
    " Preserve the exact sticker artwork, colors and text from the reference "
    "image - do not redraw, restyle, recolor, blur, or add any watermark or "
    "logo. Photorealistic, soft even studio daylight, sharp focus, no overlay "
    "text anywhere in the image."
)

APLUS_BANNER_STYLE: list[dict[str, Any]] = [
    {
        "key": "brand_banner",
        "prompt": _WIDE + (
            "Hero brand banner: the full vinyl sticker pack from the reference "
            "laid out as a generous, slightly overlapping spread across the "
            "banner, every distinct design visible with clean die-cut white "
            "borders, on a bright clean surface with a subtle thematic "
            "backdrop that matches the pack's mood. Premium, airy, on-brand "
            "e-commerce look."
        ),
    },
    {
        "key": "quality_detail",
        "prompt": _WIDE + (
            "Material quality close-up: 2-3 die-cut vinyl stickers from the "
            "reference shown at a steep macro angle on a clean white surface, "
            "one with a corner slightly peeled to show the thick vinyl and "
            "backing paper, water droplets beaded on a matte waterproof "
            "surface of another. Communicates waterproof, durable, "
            "precision-die-cut quality without any words."
        ),
    },
    {
        "key": "usage_scene",
        "prompt": _WIDE + (
            "Lifestyle usage collage in ONE continuous scene: a cozy creator "
            "desk where stickers from the reference are applied on a silver "
            "laptop lid, a stainless-steel water bottle, and an open journal "
            "page, all visible together in a single wide shot with natural "
            "window light. Warm, aspirational, everyday-use feeling."
        ),
    },
]


def generate_aplus_banners(
    local_product: dict[str, Any],
    *,
    output_dir: str | Path,
    reference_image: Optional[str] = None,
    upload: bool = True,
) -> dict[str, Any]:
    """按固定样式生成 3 张 A+ 横幅,存盘并(可选)传 COS。

    reference_image: 贴纸参考图(建议 master 主图/总览图),保证图生图保真。
    返回 {ok, banners:[{key, local_path, cos_url, prompt, error}]};
    ok = 至少 1 张成功且(若 upload)拿到 cos_url。单张失败不阻断。
    """
    from src.services.ai.gemini_service import GeminiService

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cdn = None
    if upload:
        from .cdn import get_cdn
        cdn = get_cdn()
        if not cdn.is_configured():
            logger.warning("COS not configured; banners saved locally only")
            cdn = None

    try:
        gemini = GeminiService()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Gemini not configured: {e}", "banners": []}

    banners: list[dict[str, Any]] = []
    for idx, item in enumerate(APLUS_BANNER_STYLE):
        key = item["key"]
        prompt = (item["prompt"] or "").strip() + _FIDELITY
        fpath = out / f"aplus_{idx:02d}_{key}_{int(time.time())}.png"
        rec: dict[str, Any] = {
            "key": key, "prompt": prompt,
            "local_path": "", "cos_url": "", "error": "",
        }
        try:
            res = gemini.generate_image(
                prompt, reference_image=reference_image,
                output_path=fpath, enforce_white_bg=False,
            )
            if not res.get("success"):
                rec["error"] = str(res.get("error") or "generate failed")[:200]
                banners.append(rec)
                continue
            rec["local_path"] = res.get("image_path") or str(fpath)
            if cdn is not None:
                try:
                    rec["cos_url"] = cdn.upload_file(rec["local_path"])
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"COS upload failed: {str(e)[:160]}"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:200]
        banners.append(rec)
        logger.info("aplus banner %s -> %s%s", key,
                    "ok" if rec["local_path"] and not rec["error"] else "FAIL",
                    f" ({rec['error']})" if rec["error"] else "")

    good = [b for b in banners if b["local_path"] and not b["error"]]
    ok = bool(good) and (cdn is None or any(b["cos_url"] for b in good))
    return {"ok": ok, "banners": banners}
