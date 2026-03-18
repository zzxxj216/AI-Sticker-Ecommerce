"""Blog image generator.

Parses [Image: description | alt text] placeholders from blog drafts,
generates images via GeminiService, and replaces placeholders with
actual markdown image references.

Image prompts are wrapped with sticker-product context to ensure
generated images match the e-commerce use case (vinyl sticker designs,
product flat-lays, application scenes) rather than random illustrations.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, List

from src.core.logger import get_logger
from src.services.ai.gemini_service import GeminiService
from src.utils.text_utils import sanitize_filename

logger = get_logger("blog.images")

PLACEHOLDER_PATTERN = re.compile(r"\[Image:\s*(.+?)\s*\|\s*(.+?)\s*\]")

STICKER_IMAGE_STYLE_PREFIX = (
    "Product photography style image for a vinyl sticker e-commerce blog. "
    "The image should look like a real sticker product photo or lifestyle flat-lay. "
)

STICKER_IMAGE_STYLE_SUFFIX = (
    " Style: clean, vibrant colors, high contrast, commercial product photography. "
    "The stickers should look like actual die-cut vinyl stickers with visible edges. "
    "DO NOT include: random unrelated objects, blurry backgrounds, text watermarks, "
    "human faces, hands holding items, or anything that looks AI-generated/surreal. "
    "Keep the scene realistic and focused on the sticker product."
)


@dataclass
class ImagePlaceholder:
    full_match: str
    description: str
    alt_text: str


class BlogImageGenerator:
    """Generates images for blog drafts by replacing [Image: ...] placeholders."""

    def __init__(self, gemini_service: GeminiService, max_workers: int = 3):
        self.gemini = gemini_service
        self.max_workers = max_workers

    def _build_image_prompt(self, description: str) -> str:
        """Wrap a raw image description with sticker-product context.

        This ensures Gemini generates images that look like actual sticker
        products or lifestyle shots, not random illustrations.
        """
        return f"{STICKER_IMAGE_STYLE_PREFIX}{description}{STICKER_IMAGE_STYLE_SUFFIX}"

    def extract_placeholders(self, content: str) -> List[ImagePlaceholder]:
        """Extract all [Image: description | alt text] placeholders from content."""
        placeholders = []
        for match in PLACEHOLDER_PATTERN.finditer(content):
            placeholders.append(ImagePlaceholder(
                full_match=match.group(0),
                description=match.group(1).strip(),
                alt_text=match.group(2).strip(),
            ))
        return placeholders

    async def generate_images(
        self,
        content: str,
        images_dir: Path,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Generate images for all placeholders and return updated content.

        Args:
            content: Markdown content with [Image: ...] placeholders.
            images_dir: Directory to save generated images.
            progress_callback: Optional progress callback.

        Returns:
            Updated content with placeholders replaced by ![alt](path).
        """
        placeholders = self.extract_placeholders(content)
        if not placeholders:
            logger.info("No image placeholders found in draft")
            return content

        images_dir.mkdir(parents=True, exist_ok=True)
        total = len(placeholders)
        self._progress(progress_callback, f"Generating {total} blog images...")

        semaphore = asyncio.Semaphore(self.max_workers)
        results: dict[int, Optional[str]] = {}

        async def _gen_one(idx: int, placeholder: ImagePlaceholder):
            async with semaphore:
                safe_alt = sanitize_filename(placeholder.alt_text[:40])
                filename = f"image_{idx + 1:02d}_{safe_alt}.png"
                output_path = images_dir / filename

                self._progress(
                    progress_callback,
                    f"  [{idx + 1}/{total}] Generating: {placeholder.alt_text[:50]}...",
                )

                enhanced_prompt = self._build_image_prompt(placeholder.description)
                logger.debug(f"Enhanced image prompt: {enhanced_prompt[:120]}...")

                result = await asyncio.to_thread(
                    self.gemini.generate_image,
                    prompt=enhanced_prompt,
                    output_path=output_path,
                )

                if result.get("success"):
                    logger.info(
                        f"Image {idx + 1}/{total} generated: {filename} "
                        f"({result.get('size_kb', 0)} KB, {result.get('elapsed', 0):.1f}s)"
                    )
                    results[idx] = str(output_path)
                else:
                    logger.error(
                        f"Image {idx + 1}/{total} failed: {result.get('error')}"
                    )
                    results[idx] = None

        tasks = [_gen_one(i, p) for i, p in enumerate(placeholders)]
        await asyncio.gather(*tasks)

        updated_content = content
        success_count = 0
        for idx, placeholder in enumerate(placeholders):
            image_path = results.get(idx)
            if image_path:
                relative_path = Path(image_path).relative_to(
                    images_dir.parent
                )
                md_image = f"![{placeholder.alt_text}]({relative_path.as_posix()})"
                updated_content = updated_content.replace(
                    placeholder.full_match, md_image, 1
                )
                success_count += 1
            else:
                md_fallback = f"<!-- Image generation failed: {placeholder.alt_text} -->"
                updated_content = updated_content.replace(
                    placeholder.full_match, md_fallback, 1
                )

        self._progress(
            progress_callback,
            f"Image generation complete: {success_count}/{total} succeeded",
        )
        return updated_content

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
