"""Planning Event -> Sticker Pack Design Directions

Given a calendar event, generates 2 differentiated sticker-pack design
directions via LLM, each with structured fields that map directly to
the existing StickerPackPipeline / PackGenerator inputs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.logger import get_logger
from src.services.ai.openai_service import OpenAIService
from src.services.ops.db import OpsDatabase

logger = get_logger("service.direction_gen")

_CN_TZ = timezone(timedelta(hours=8))

DIRECTION_SYSTEM = """\
You are a senior sticker-pack creative director for an e-commerce brand
selling sticker packs to overseas English-speaking markets.

Your job: given an event/holiday, produce **exactly 2** highly differentiated
sticker-pack design directions.  Each direction must be commercially viable,
visually distinctive, and appeal to different buyer segments.

IMPORTANT:
- ALL sticker content (text slogans, design element names) MUST be in English.
- name_en MUST be a short, punchy English direction name (3-6 words).
- name_zh is the Chinese translation of name_en.
- keywords, design_elements, text_slogans, decorative_elements are comma-separated English strings.
- Think about what would sell on Amazon / Etsy — practical, eye-catching, gift-worthy.
- The two directions should differ in style, mood, and target audience.
"""

DIRECTION_FEW_SHOT = """\
Here are 5 example directions for reference (style/format only — do NOT copy content):

1) Retro American Street:
   keywords: American retro, street fashion, pop art, black-red aesthetic, cartoon graffiti
   design_elements: vintage radio, cassette tape, gamepad, cola bottle, hamburger, skateboard
   text_slogans: RETRO VIBES, GAME OVER, OLD SCHOOL, CHILL OUT
   decorative_elements: pixel dots, stripes, checkerboard, stars, lightning

2) Japanese Yokai Spirit:
   keywords: ukiyo-e, mythical creatures, black-red aesthetic, cartoon graffiti, Japanese culture
   design_elements: daruma doll, hannya mask, lucky cat, fox mask, kimono, Japanese lantern
   text_slogans: HANNYA, DIVINE, WAFUU, KYOTO
   decorative_elements: cherry blossoms, ocean waves, cloud patterns, flames, kanji

3) Dark Cyberpunk:
   keywords: cyberpunk, futuristic, dark aesthetic, black-red, cartoon graffiti
   design_elements: neon lights, cables, mechanical arm, microchip, digital matrix, future cityscape
   text_slogans: NEON FUTURE, DATA LOST, SYSTEM ERROR, CYBERSPACE
   decorative_elements: geometric lines, circuit patterns, light beams, pixels, glitch effects

4) Cute Weird Doodle:
   keywords: cute, weird, hand-drawn doodle, black-red aesthetic, cartoon stickers
   design_elements: personified little monsters, big-eyed animals, strange plants, melting ice cream
   text_slogans: CUTE BUT WEIRD, FUNKY DAYS, GET LOST, DONT WORRY
   decorative_elements: ink splashes, line doodles, hearts, clouds, little stars

5) Pop Fashion:
   keywords: pop art, fashion trend, black-red aesthetic, cartoon graffiti, sticker aesthetic
   design_elements: sunglasses, high heels, lipstick, perfume bottle, fashion handbag, heart sunglasses
   text_slogans: FASHION KILLA, STAY GLAM, OUTFIT OF THE DAY, POP CULTURE
   decorative_elements: halftone dots, hearts, red lips, lightning, polka dots
"""


def _build_user_prompt(event: dict[str, Any]) -> str:
    title = event.get("title", "")
    category = event.get("category", "")
    region = event.get("region", "")
    start = event.get("start_date", "")
    end = event.get("end_date", "") or start
    desc = event.get("short_description", "")

    region_label = {"us": "United States", "ca": "Canada", "eu": "Europe"}.get(region, region)

    return f"""{DIRECTION_FEW_SHOT}

Now generate 2 design directions for the following event.

Event title: {title}
Category: {category}
Region: {region_label}
Date: {start} ~ {end}
Description: {desc}

Return a JSON array with exactly 2 objects. Each object must have these fields:
- name_en (string): English direction name, 3-6 words
- name_zh (string): Chinese translation
- keywords (string): comma-separated English keywords (5-8)
- design_elements (string): comma-separated English design elements (5-8)
- text_slogans (string): comma-separated English slogans for stickers (4-6)
- decorative_elements (string): comma-separated English decorative elements (4-6)

Return ONLY the JSON array — no markdown fences, no explanation."""


class DirectionGenerator:
    """Generates sticker-pack design directions from planning events."""

    def __init__(self, db: OpsDatabase | None = None, openai_svc: OpenAIService | None = None):
        self.db = db or OpsDatabase()
        self._openai = openai_svc

    @property
    def openai(self) -> OpenAIService:
        if self._openai is None:
            self._openai = OpenAIService()
        return self._openai

    def generate_directions(self, event_id: str) -> list[dict[str, Any]]:
        """Generate 2 design directions for a planning event and persist them."""
        event = self.db.get_planning_event(event_id)
        if not event:
            raise ValueError(f"Event not found: {event_id}")

        logger.info("Generating directions for event: %s (%s)", event["title"], event_id)

        user_prompt = _build_user_prompt(event)
        result = self.openai.generate(
            prompt=user_prompt,
            system=DIRECTION_SYSTEM,
            temperature=0.85,
            max_tokens=4000,
        )

        raw_text = result.get("text", "")
        directions = self._parse_response(raw_text)
        if len(directions) < 2:
            raise ValueError(f"Expected 2 directions, got {len(directions)}")

        now = datetime.now(_CN_TZ).isoformat()
        saved: list[dict[str, Any]] = []
        for idx, d in enumerate(directions[:2]):
            rec = {
                "id": f"dir_{uuid.uuid4().hex[:12]}",
                "event_id": event_id,
                "direction_index": idx,
                "name_en": d.get("name_en", "").strip(),
                "name_zh": d.get("name_zh", "").strip(),
                "keywords": d.get("keywords", "").strip(),
                "design_elements": d.get("design_elements", "").strip(),
                "text_slogans": d.get("text_slogans", "").strip(),
                "decorative_elements": d.get("decorative_elements", "").strip(),
                "preview_path": "",
                "preview_status": "pending",
                "gen_status": "pending",
                "job_id": "",
                "created_at": now,
            }
            self.db.insert_planning_direction(rec)
            saved.append(rec)
            logger.info("  Direction %d: %s", idx, rec["name_en"])

        return saved

    def generate_preview(self, direction_id: str) -> dict[str, Any]:
        """Generate a collection-sheet preview image for one direction."""
        from pathlib import Path

        d = self.db.get_direction(direction_id)
        if not d:
            raise ValueError(f"Direction not found: {direction_id}")

        self.db.update_direction(direction_id, preview_status="generating")

        try:
            from src.services.sticker.pack_generator import PackGenerator

            pack_gen = PackGenerator()
            theme = d["name_en"]
            pack_name = f"THE {theme.upper()} STICKER PACK"

            result = pack_gen.generate_preview(
                theme=theme,
                pack_name=pack_name,
                count=10,
                use_claude_prompt=True,
            )

            preview_img = result.get("preview_image", {})
            if preview_img.get("success"):
                img_path = str(preview_img["image_path"]).replace("\\", "/")
                marker = "data/output/images/"
                idx = img_path.find(marker)
                if idx >= 0:
                    web_path = "/preview-images/" + img_path[idx + len(marker):]
                else:
                    marker2 = "output/images/"
                    idx2 = img_path.find(marker2)
                    if idx2 >= 0:
                        web_path = "/preview-images/" + img_path[idx2 + len(marker2):]
                    else:
                        web_path = img_path
                self.db.update_direction(
                    direction_id,
                    preview_path=web_path,
                    preview_status="completed",
                )
                logger.info("Preview generated for direction %s: %s", direction_id, web_path)
            else:
                error = preview_img.get("error", "unknown")
                self.db.update_direction(direction_id, preview_status="failed")
                logger.error("Preview generation failed: %s", error)

            return {
                "direction_id": direction_id,
                "preview_image": preview_img,
                "pack_name": pack_name,
            }
        except Exception as e:
            self.db.update_direction(direction_id, preview_status="failed")
            logger.error("Preview generation error: %s", e, exc_info=True)
            raise

    def start_full_generation(self, direction_id: str, created_by: str = "system") -> dict[str, Any]:
        """Trigger full sticker pack generation via StickerPackPipeline."""
        d = self.db.get_direction(direction_id)
        if not d:
            raise ValueError(f"Direction not found: {direction_id}")

        self.db.update_direction(direction_id, gen_status="generating")

        from src.services.ops.job_service import JobService

        job_svc = JobService(self.db)
        event = self.db.get_planning_event(d["event_id"])
        event_title = (event or {}).get("title", "Unknown Event")

        trend_id = f"planning:{d['event_id']}"
        trend_name = f"{event_title} — {d['name_en']}"

        job = job_svc.create_job(trend_id, trend_name, created_by)
        self.db.update_direction(direction_id, job_id=job.id)

        extras = []
        if d.get("design_elements"):
            extras.append(f"Design elements: {d['design_elements']}")
        if d.get("text_slogans"):
            extras.append(f"Text slogans for stickers: {d['text_slogans']}")
        if d.get("decorative_elements"):
            extras.append(f"Decorative elements: {d['decorative_elements']}")
        user_extra = "\n".join(extras)

        import threading

        def _run():
            try:
                from src.services.batch.sticker_pipeline import StickerPackPipeline
                from src.services.ai.gemini_service import GeminiService

                pipeline = StickerPackPipeline(
                    openai_service=self.openai,
                    gemini_service=GeminiService(),
                    output_dir="output/h5_jobs",
                )
                result = pipeline.run(
                    theme=d["name_en"],
                    user_style=d.get("keywords", ""),
                    user_extra=user_extra,
                )
                from pathlib import Path

                outputs = JobService._collect_outputs(job.id, Path(str(pipeline.output_dir)))
                self.db.replace_outputs(job.id, outputs)
                status = result.status if result.status in {"completed", "failed"} else "completed"
                self.db.update_job(
                    job.id,
                    status=status,
                    output_dir=str(pipeline.output_dir),
                    image_count=len(result.image_paths),
                    error_message=result.error or "",
                    finished_at=datetime.now(_CN_TZ),
                )
                self.db.update_direction(direction_id, gen_status=status)
                logger.info("Full generation done for direction %s, job %s", direction_id, job.id)
            except Exception as exc:
                logger.error("Full generation failed: %s", exc, exc_info=True)
                self.db.update_job(
                    job.id, status="failed",
                    error_message=str(exc),
                    finished_at=datetime.now(_CN_TZ),
                )
                self.db.update_direction(direction_id, gen_status="failed")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return {"direction_id": direction_id, "job_id": job.id, "status": "queued"}

    @staticmethod
    def _parse_response(text: str) -> list[dict[str, Any]]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start >= 0 and end > start:
                parsed = json.loads(cleaned[start : end + 1])
            else:
                raise ValueError(f"Cannot parse direction JSON from: {cleaned[:200]}")

        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
        if isinstance(parsed, list):
            return parsed
        raise ValueError("Unexpected JSON structure")
