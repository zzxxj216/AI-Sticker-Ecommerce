"""Amazon 商品主副图固定样式 — 一套固定提示词(合规、全 AI 图生图)。

与 image_studio(GPT 临时规划提示词)不同,这里是确定性的固定 5 图样式,
每个产品风格一致;改本文件即全局调整:

  1. main       — 主图:纯白底(#FFFFFF),贴纸包铺满约 85% 画面,无文字/道具/
                  水印/阴影杂物(Amazon 主图硬性合规)
  2. lifestyle  — 生活场景:笔电+手账创作桌面,贴纸真实贴附
  3. size_chart — 尺寸参考:顶视平铺 + 美元硬币比例(不烘焙文字)
  4. material   — 材质细节:防水水珠/掀角露底纸/die-cut 白边特写
  5. full_set   — 全套展示:所有设计整齐排开(购买者一眼看全)

全部以 pack 真实贴纸为参考(image-to-image,保真条款),Gemini 生成。
生成 → 存盘 → 传 COS → 覆盖写入 amazon_images(main 在前)。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger

logger = get_logger("service.amazon.image_style")

_FIDELITY = (
    " Preserve the exact sticker artwork, colors and text from the reference "
    "image - do not redraw, restyle, recolor, blur, or add any new text, "
    "watermark or logo. Photorealistic e-commerce product photography, soft "
    "even daylight, sharp focus."
)

# 每项:key、role(main/other)、是否强制白底、固定场景提示词。
AMAZON_IMAGE_STYLE: list[dict[str, Any]] = [
    {
        "key": "main",
        "role": "main",
        "white_bg": True,
        "prompt": (
            "Amazon-compliant MAIN product image: the vinyl sticker pack from "
            "the reference arranged as a tidy, slightly overlapping spread "
            "that fills about 85% of the frame, centered, on a pure plain "
            "white background (#FFFFFF). Every distinct design visible with "
            "clean die-cut white borders. Absolutely no props, no surface, no "
            "hands, no text overlay, no watermark, no logo badge, no heavy "
            "shadows. Bright even studio light, crisp catalog look."
        ),
    },
    {
        "key": "lifestyle",
        "role": "other",
        "white_bg": False,
        "prompt": (
            "Lifestyle photo: several die-cut vinyl stickers from the "
            "reference applied on a silver laptop lid and an open journal on "
            "a light wood desk, soft natural window light, cozy creator "
            "workspace, shallow depth of field. Stickers sit flat and "
            "realistic on the surfaces."
        ),
    },
    {
        "key": "size_chart",
        "role": "other",
        "white_bg": False,
        "prompt": (
            "Top-down flat-lay on a clean white surface: 5-7 die-cut vinyl "
            "stickers from the reference arranged neatly with even spacing, "
            "and a real US quarter coin beside them for size reference. "
            "Bright even overhead daylight, subtle soft shadows, true-to-life "
            "scale so buyers can judge sticker size. No baked-in text."
        ),
    },
    {
        "key": "material",
        "role": "other",
        "white_bg": False,
        "prompt": (
            "Material quality close-up: 2-3 die-cut vinyl stickers from the "
            "reference at a steep macro angle on a clean white surface, one "
            "corner slightly peeled showing thick vinyl and backing paper, "
            "water droplets beaded on another sticker's matte waterproof "
            "surface. Communicates waterproof, durable, precision die-cut "
            "quality without any words."
        ),
    },
    {
        "key": "full_set",
        "role": "other",
        "white_bg": False,
        "prompt": (
            "Complete-set display: every distinct sticker design from the "
            "reference laid out in a clean even grid on a soft light-grey "
            "studio background, equal spacing, nothing cropped, so a buyer "
            "can scan the whole collection at a glance. Bright even light."
        ),
    },
]


def generate_amazon_images(
    master: dict[str, Any],
    *,
    output_dir: str | Path,
    reference_image: Optional[str] = None,
    upload: bool = True,
) -> dict[str, Any]:
    """按固定样式生成 5 张主副图,存盘并(可选)传 COS。

    返回 {ok, images:[{key, role, local_path, cos_url, prompt, error}]};
    ok = 主图成功。单张失败不阻断其余。
    """
    from src.services.ai.gemini_service import GeminiService

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cdn = None
    if upload:
        from .cdn import get_cdn
        cdn = get_cdn()
        if not cdn.is_configured():
            logger.warning("COS not configured; images saved locally only")
            cdn = None

    try:
        gemini = GeminiService()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Gemini not configured: {e}", "images": []}

    images: list[dict[str, Any]] = []
    for idx, item in enumerate(AMAZON_IMAGE_STYLE):
        key = item["key"]
        prompt = (item["prompt"] or "").strip() + _FIDELITY
        fpath = out / f"{idx:02d}_{key}_{int(time.time())}.png"
        rec: dict[str, Any] = {
            "key": key, "role": item["role"], "prompt": prompt,
            "local_path": "", "cos_url": "", "error": "",
        }
        try:
            res = gemini.generate_image(
                prompt, reference_image=reference_image,
                output_path=fpath, enforce_white_bg=bool(item.get("white_bg")),
            )
            if not res.get("success"):
                rec["error"] = str(res.get("error") or "generate failed")[:200]
                images.append(rec)
                continue
            rec["local_path"] = res.get("image_path") or str(fpath)
            if cdn is not None:
                try:
                    rec["cos_url"] = cdn.upload_file(rec["local_path"])
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"COS upload failed: {str(e)[:160]}"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:200]
        images.append(rec)
        logger.info("amazon image %s -> %s%s", key,
                    "ok" if rec["local_path"] and not rec["error"] else "FAIL",
                    f" ({rec['error']})" if rec["error"] else "")

    ok = any(r["key"] == "main" and r["local_path"] and not r["error"] for r in images)
    return {"ok": ok, "images": images}
