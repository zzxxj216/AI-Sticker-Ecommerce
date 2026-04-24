"""Pack-scoped file storage layout for the V2 pipeline.

Every pack-derived artifact lives under ``output/packs/{pack_uid}/...`` so
that any object detail page can show "files on disk" alongside the
database row, and a single ``rm -rf`` cleans up everything tied to one
pack. Layout::

    output/packs/{pack_uid}/
      plan.json              # extracted topic_plans.series_payload
      plan.main_raw.md       # creative model's free-form output (2-step gen)
      series_{n}/
        style_anchor.txt
        previews/
          {idx}.png
          {idx}.prompt.txt
        stickers/
          {idx}.png
          {idx}.meta.json
      videos/
        {video_id}/
          local.mp4
          caption_main_raw.md
      products/
        {product_id_or_draft_id}/
          detail_main_raw.md
          title.txt
          description.html
          selling_points.json
          keywords.json
          images/
            main.png
            secondary_1.png
            ...

``pack_uid`` is ``{YYYYMMDD}_{slug}_{4hex}`` — date for sortability, slug
for human readability, hex for collision avoidance.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.logger import get_logger

logger = get_logger("service.storage.pack_store")

DEFAULT_OUTPUT_ROOT = Path("output/packs")


# ----------------------------------------------------------------------
# pack_uid generation
# ----------------------------------------------------------------------

_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str, max_len: int = 30) -> str:
    """Lowercase, ASCII-safe slug from a topic name. Falls back to 'topic'."""
    s = _SLUG_CLEAN_RE.sub("_", name.lower()).strip("_")
    if not s:
        s = "topic"
    return s[:max_len]


def make_pack_uid(topic_name: str, *, when: Optional[datetime] = None) -> str:
    """Generate a pack_uid like ``20260424_graduation_a3f9``."""
    when = when or datetime.now()
    return f"{when.strftime('%Y%m%d')}_{_slugify(topic_name)}_{secrets.token_hex(2)}"


# ----------------------------------------------------------------------
# PackStore
# ----------------------------------------------------------------------

class PackStore:
    """File-system writer/reader for one or many packs.

    Methods write atomically by writing to a tempfile and renaming
    where it matters (image bytes, large JSON). Small text files are
    written directly — partial writes here are recoverable from the DB.
    """

    def __init__(self, root: Path = DEFAULT_OUTPUT_ROOT) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Pack directory
    # ------------------------------------------------------------------

    def pack_dir(self, pack_uid: str) -> Path:
        return self.root / pack_uid

    def init_pack_dir(self, pack_uid: str) -> Path:
        d = self.pack_dir(pack_uid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_pack_dir(self, pack_uid: str) -> None:
        d = self.pack_dir(pack_uid)
        if d.is_dir():
            shutil.rmtree(d)
            logger.info("Removed pack dir %s", d)

    # ------------------------------------------------------------------
    # A.2 plan
    # ------------------------------------------------------------------

    def write_plan(self, pack_uid: str, plan: dict) -> Path:
        p = self.init_pack_dir(pack_uid) / "plan.json"
        _write_json_atomic(p, plan)
        return p

    def read_plan(self, pack_uid: str) -> dict:
        p = self.pack_dir(pack_uid) / "plan.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def write_plan_main_raw(self, pack_uid: str, text: str) -> Path:
        p = self.init_pack_dir(pack_uid) / "plan.main_raw.md"
        p.write_text(text, encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    # A.3 series / previews / stickers
    # ------------------------------------------------------------------

    def series_dir(self, pack_uid: str, series_idx: int) -> Path:
        d = self.pack_dir(pack_uid) / f"series_{series_idx}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_style_anchor(self, pack_uid: str, series_idx: int, text: str) -> Path:
        p = self.series_dir(pack_uid, series_idx) / "style_anchor.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def write_preview(
        self,
        pack_uid: str,
        series_idx: int,
        preview_idx: int,
        image_bytes: bytes,
        prompt_text: str = "",
    ) -> Path:
        d = self.series_dir(pack_uid, series_idx) / "previews"
        d.mkdir(parents=True, exist_ok=True)
        img_path = d / f"{preview_idx}.png"
        _write_bytes_atomic(img_path, image_bytes)
        if prompt_text:
            (d / f"{preview_idx}.prompt.txt").write_text(prompt_text, encoding="utf-8")
        return img_path

    def write_sticker(
        self,
        pack_uid: str,
        series_idx: int,
        sticker_idx: int,
        image_bytes: bytes,
        meta: Optional[dict] = None,
    ) -> Path:
        d = self.series_dir(pack_uid, series_idx) / "stickers"
        d.mkdir(parents=True, exist_ok=True)
        img_path = d / f"{sticker_idx}.png"
        _write_bytes_atomic(img_path, image_bytes)
        if meta is not None:
            _write_json_atomic(d / f"{sticker_idx}.meta.json", meta)
        return img_path

    def list_stickers(self, pack_uid: str, series_idx: int) -> list[Path]:
        d = self.series_dir(pack_uid, series_idx) / "stickers"
        if not d.is_dir():
            return []
        return sorted(d.glob("*.png"))

    # ------------------------------------------------------------------
    # B.1 videos
    # ------------------------------------------------------------------

    def video_dir(self, pack_uid: str, video_id: int) -> Path:
        d = self.pack_dir(pack_uid) / "videos" / str(video_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_video_file(
        self,
        pack_uid: str,
        video_id: int,
        src_path: Path,
        *,
        target_filename: str = "local.mp4",
    ) -> Path:
        """Move/copy an uploaded video into the pack tree. Returns final path."""
        target = self.video_dir(pack_uid, video_id) / target_filename
        shutil.copy2(src_path, target)
        return target

    def write_caption_main_raw(self, pack_uid: str, video_id: int, text: str) -> Path:
        p = self.video_dir(pack_uid, video_id) / "caption_main_raw.md"
        p.write_text(text, encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    # C.1 / C.2 products
    # ------------------------------------------------------------------

    def product_dir(self, pack_uid: str, product_key: str) -> Path:
        """``product_key`` can be the tiktok_product_id or a draft_id like 'draft_42'."""
        d = self.pack_dir(pack_uid) / "products" / str(product_key)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_product_detail(
        self,
        pack_uid: str,
        product_key: str,
        *,
        title: str = "",
        description_html: str = "",
        selling_points: Optional[list] = None,
        keywords: Optional[list] = None,
        main_raw: str = "",
    ) -> Path:
        d = self.product_dir(pack_uid, product_key)
        if main_raw:
            (d / "detail_main_raw.md").write_text(main_raw, encoding="utf-8")
        if title:
            (d / "title.txt").write_text(title, encoding="utf-8")
        if description_html:
            (d / "description.html").write_text(description_html, encoding="utf-8")
        if selling_points is not None:
            _write_json_atomic(d / "selling_points.json", selling_points)
        if keywords is not None:
            _write_json_atomic(d / "keywords.json", keywords)
        return d

    def write_product_image(
        self,
        pack_uid: str,
        product_key: str,
        filename: str,
        image_bytes: bytes,
    ) -> Path:
        """Save a product image (main.png / secondary_1.png / lifestyle_1.png / ...)."""
        d = self.product_dir(pack_uid, product_key) / "images"
        d.mkdir(parents=True, exist_ok=True)
        p = d / filename
        _write_bytes_atomic(p, image_bytes)
        return p

    def list_product_images(self, pack_uid: str, product_key: str) -> list[Path]:
        d = self.product_dir(pack_uid, product_key) / "images"
        if not d.is_dir():
            return []
        return sorted(d.iterdir())


# ----------------------------------------------------------------------
# Atomic write helpers
# ----------------------------------------------------------------------

def _write_bytes_atomic(target: Path, data: bytes) -> None:
    """Write bytes to a tempfile and rename; avoids torn files on crash."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)


def _write_json_atomic(target: Path, obj) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


# ----------------------------------------------------------------------
# Module-level singleton
# ----------------------------------------------------------------------

_store: Optional[PackStore] = None


def get_pack_store() -> PackStore:
    global _store
    if _store is None:
        _store = PackStore()
    return _store
