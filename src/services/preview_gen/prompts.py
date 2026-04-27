"""Preview-image prompt builder — A.3.

Assembles the per-preview image-gen prompt from the series's style_anchor
+ palette + the preview's sticker brief list, in the format the
docs/ChatGPT-毕业季卡贴题材设计.md reference doc settled on.
"""

from __future__ import annotations


# Tail clause appended to every prompt — keeps the AI from drifting into
# mockup hands, packaging shots, or layered product photos. From §二 of
# the reference doc.
UNIVERSAL_TAIL = (
    "sticker sheet preview only, show all stickers separately and fully "
    "visible, evenly spaced, no overlapping, no mockup hands, no packaging "
    "bag, no extra objects, clean white background, commercial product "
    "preview, consistent style, high detail, print-ready, suitable for "
    "e-commerce listing image."
)


def build_split_prompt(
    *,
    sticker_brief: str,
    sticker_idx: int,
    total_stickers: int,
    style_anchor: str,
) -> str:
    """Prompt for image_edit that extracts ONE sticker from a sheet preview.

    The source image is a {total_stickers}-sticker sheet; we want a single
    isolated die-cut sticker on a clean background, square format, ready
    to be sold individually or printed alone.
    """
    return (
        f"From this sticker sheet of {total_stickers} stickers, isolate ONLY "
        f"sticker #{sticker_idx}: \"{sticker_brief}\". "
        f"Re-render that single sticker as a standalone, centered, die-cut "
        f"vinyl sticker on a clean white background, with a thin white "
        f"border around the design, square 1:1 composition, no other "
        f"stickers visible, no sheet layout, print-ready, high detail. "
        f"Keep the exact text, colors, and visual style as it appears in "
        f"the sheet. Style reference: {style_anchor[:300]}"
    )


def build_preview_prompt(
    *,
    style_anchor: str,
    palette: str,
    preview_theme: str,
    stickers: list[str],
) -> str:
    """Assemble the prompt for one preview image.

    ``stickers`` is the raw list of `"贴纸文字 / english description"`
    strings extracted from the topic_plan. We pass them verbatim — the
    image model handles the typography.
    """
    n = len(stickers)
    sticker_lines = "\n".join(
        f"{idx}. {s}" for idx, s in enumerate(stickers, 1)
    )
    palette_clause = f", palette: {palette}" if palette.strip() else ""
    theme_clause = f" (subtheme: {preview_theme})" if preview_theme.strip() else ""

    return (
        f"{style_anchor}\n\n"
        f"Show {n} unique die-cut stickers{theme_clause}{palette_clause}:\n"
        f"{sticker_lines}\n\n"
        f"{UNIVERSAL_TAIL}"
    )
