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
from typing import Optional, Callable, List, Dict

from src.core.logger import get_logger
from src.models.blog import ContentPlan
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
        """Wrap a raw image description with sticker-product context."""
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
        refined_prompts: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        on_image_ready: Optional[Callable[[Path, str, str, int], None]] = None,
    ) -> tuple[str, List[Dict]]:
        """Generate images for all placeholders and return updated content.

        Args:
            content: Markdown content with [Image: ...] placeholders.
            images_dir: Directory to save generated images.
            refined_prompts: Optional dict mapping original placeholder text
                             to refined image prompts from ImageDirector.
            progress_callback: Optional progress callback.
            on_image_ready: Optional callback(path, alt_text, prompt, index)
                            fired after each image is generated successfully.

        Returns:
            Tuple of (updated_content, image_records) where image_records is
            a list of dicts with keys: index, prompt, alt_text, path.
        """
        placeholders = self.extract_placeholders(content)
        if not placeholders:
            logger.info("No image placeholders found in draft")
            return content, []

        images_dir.mkdir(parents=True, exist_ok=True)
        total = len(placeholders)
        self._progress(progress_callback, f"正在生成 {total} 张配图...")

        semaphore = asyncio.Semaphore(self.max_workers)
        results: dict[int, Optional[str]] = {}
        prompts_used: dict[int, str] = {}

        async def _gen_one(idx: int, placeholder: ImagePlaceholder):
            async with semaphore:
                safe_alt = sanitize_filename(placeholder.alt_text[:40])
                filename = f"image_{idx + 1:02d}_{safe_alt}.png"
                output_path = images_dir / filename

                self._progress(
                    progress_callback,
                    f"  生成配图 [{idx + 1}/{total}]: {placeholder.alt_text[:50]}...",
                )

                if refined_prompts and placeholder.full_match in refined_prompts:
                    raw_prompt = refined_prompts[placeholder.full_match]
                    enhanced_prompt = self._build_image_prompt(raw_prompt)
                    logger.info(f"Using Image Director refined prompt for image {idx + 1}")
                else:
                    raw_prompt = placeholder.description
                    enhanced_prompt = self._build_image_prompt(raw_prompt)

                prompts_used[idx] = raw_prompt
                logger.debug(f"Enhanced image prompt: {enhanced_prompt[:120]}...")

                result = await asyncio.to_thread(
                    self.gemini.generate_image,
                    prompt=enhanced_prompt,
                    output_path=output_path,
                    enforce_white_bg=False,
                )

                if result.get("success"):
                    logger.info(
                        f"Image {idx + 1}/{total} generated: {filename} "
                        f"({result.get('size_kb', 0)} KB, {result.get('elapsed', 0):.1f}s)"
                    )
                    results[idx] = str(output_path)
                    if on_image_ready:
                        try:
                            on_image_ready(output_path, placeholder.alt_text, raw_prompt, idx)
                        except Exception as e:
                            logger.warning(f"on_image_ready callback error: {e}")
                else:
                    logger.error(
                        f"Image {idx + 1}/{total} failed: {result.get('error')}"
                    )
                    results[idx] = None

        tasks = [_gen_one(i, p) for i, p in enumerate(placeholders)]
        await asyncio.gather(*tasks)

        updated_content = content
        image_records: List[Dict] = []
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
                image_records.append({
                    "index": idx,
                    "prompt": prompts_used.get(idx, placeholder.description),
                    "alt_text": placeholder.alt_text,
                    "path": str(image_path),
                })
            else:
                md_fallback = f"<!-- Image generation failed: {placeholder.alt_text} -->"
                updated_content = updated_content.replace(
                    placeholder.full_match, md_fallback, 1
                )

        self._progress(
            progress_callback,
            f"配图生成完成: {success_count}/{total} 张成功",
        )
        return updated_content, image_records

    def build_refined_prompts_from_plan(
        self,
        content: str,
        content_plan: ContentPlan,
    ) -> Dict[str, str]:
        """Match [Image:] placeholders to ImagePlan descriptions.

        The writer places images using the plan's descriptions, so we
        fuzzy-match each placeholder back to the best-matching ImagePlan
        to use its high-quality, pre-reviewed description as the prompt.

        Returns:
            Dict mapping placeholder full_match text -> refined description.
        """
        placeholders = self.extract_placeholders(content)
        if not placeholders or not content_plan.image_plans:
            return {}

        refined: Dict[str, str] = {}
        used_plan_indices: set[int] = set()

        for ph in placeholders:
            best_idx = -1
            best_score = 0.0
            ph_words = set(ph.description.lower().split())

            for i, ip in enumerate(content_plan.image_plans):
                if i in used_plan_indices:
                    continue
                plan_words = set(ip.description.lower().split())
                if not ph_words or not plan_words:
                    continue
                overlap = len(ph_words & plan_words)
                score = overlap / max(len(ph_words), len(plan_words))
                if score > best_score:
                    best_score = score
                    best_idx = i

            if best_idx >= 0 and best_score > 0.15:
                used_plan_indices.add(best_idx)
                refined[ph.full_match] = content_plan.image_plans[best_idx].description
                logger.debug(
                    f"Matched placeholder to plan image {best_idx + 1} "
                    f"(score={best_score:.2f})"
                )
            else:
                logger.debug(
                    f"No plan match for placeholder: {ph.alt_text[:50]}..."
                )

        logger.info(
            f"Refined {len(refined)}/{len(placeholders)} image prompts from plan"
        )
        return refined

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
