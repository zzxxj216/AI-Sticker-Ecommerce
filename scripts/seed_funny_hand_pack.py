"""Seed script: import the curated "Talk to the Hand" funny mood sticker pack.

Builds a full chain: hot_topic → topic_plan → pack_series → pack →
tkshop_products. Idempotent via pack_uid lookup.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/ops_workbench.db")
PACK_UID = "20260501_funny_hand_gesture_meme"

ANALYSIS_RAW = """\
Funny Hand Gesture Meme Sticker Pack — full analysis (curated 2026-05-01).

VISUAL: Yellow hand-gesture characters (refusal, OK, wave, thumbs up, eye-cover,
button-push) + cute mascots (white blob, frog face, boba, coffee, heart, sun,
cloud) + bold English mood phrases.

PHRASES INCLUDED: "Not My Problem", "Leave Me Alone", "Do Not Disturb",
"Talk to the Hand", "Boba First", "Too Bright", "No Thanks", "Protect My Peace",
"But First, Coffee".

POSITIONING: Funny Hand Gesture Meme Stickers / Introvert Self-Care Laptop
Stickers / No Drama Vinyl Decals — emotion-expression vehicle, not just decor.

TARGET AUDIENCE: Gen Z / female students / office workers / introverts /
boba & coffee lovers / journal & DIY fans.

CONTENT-MARKETING ANGLE: TikTok short-video friendly, each sticker has a meme
hook, easy "today's mood" content.
"""

DESCRIPTION_HTML = """\
<p>Add some humor, attitude, and cute self-care energy to your everyday items with this funny hand gesture sticker pack.</p>

<p>This sticker set features a playful mix of yellow hand gestures, cute mood characters, boba drinks, coffee moments, and funny introvert-style phrases such as <strong>"Not My Problem," "Do Not Disturb," "Leave Me Alone," "Talk to the Hand," "Boba First,"</strong> and <strong>"Protect My Peace."</strong></p>

<p>These stickers are perfect for decorating laptops, water bottles, phone cases, notebooks, planners, journals, scrapbooks, luggage, and other smooth surfaces. Whether you are a student, office worker, boba lover, stationery fan, or someone who enjoys funny meme-style designs, this pack adds personality and humor to your daily life.</p>

<p><strong>Product Features:</strong></p>
<ul>
  <li>Cute funny hand gesture sticker designs</li>
  <li>Great for laptop, water bottle, notebook, phone case, planner, and journal decoration</li>
  <li>Perfect for DIY crafts, scrapbooking, gift wrapping, party favors, and small gifts</li>
  <li>Fun mood phrases for introverts, students, coworkers, and meme lovers</li>
  <li>Easy to peel and apply on clean, dry, smooth surfaces</li>
</ul>

<p><strong>How to Use:</strong> Clean the surface before applying. Peel off the sticker and press it firmly onto a smooth, dry surface. For best results, avoid dusty, oily, or uneven surfaces.</p>

<p><strong>Package Includes:</strong> 1 pack of funny hand gesture mood stickers. Please check the selected option or product images for the exact quantity and size.</p>

<p><em>Note: Colors may vary slightly due to screen settings and lighting. Please allow slight size differences due to manual measurement.</em></p>
"""

SELLING_POINTS = [
    "Funny mood designs: hand gestures, introvert quotes, boba, coffee, no-drama vibes",
    "Great for laptop, water bottle, phone case, notebook, planner, journal, luggage",
    "Cute self-care phrases: Not My Problem, Do Not Disturb, Boba First, Protect My Peace",
    "Easy peel-and-stick on any clean smooth surface; perfect for DIY decoration",
    "Small affordable gift idea for teens, students, coworkers, boba & meme lovers",
]

KEYWORDS = [
    "funny stickers",
    "hand gesture stickers",
    "meme stickers",
    "introvert stickers",
    "self care stickers",
    "no drama stickers",
    "boba stickers",
    "laptop stickers",
    "water bottle stickers",
    "cute vinyl decals",
    "planner stickers",
    "journal stickers",
    "teen gifts",
    "coworker gifts",
    "mood stickers",
]

PREVIEW_BRIEFS = [
    {
        "name": "Hand Gestures Sheet",
        "stickers": [
            "Yellow hand gesture giving OK sign with cute eyes",
            "Yellow hand pushing 'Not My Problem' button",
            "Yellow hand waving with 'Bye Drama' bubble",
            "Yellow thumbs-down with 'No Thanks' tag",
            "Yellow hand covering eyes with 'Too Bright' label",
            "Yellow hand pointing with 'Talk to the Hand' speech bubble",
        ],
    },
    {
        "name": "Mood Phrases Sheet",
        "stickers": [
            "Cloud-shaped 'Do Not Disturb' badge with z-z-z's",
            "Heart-shape 'Protect My Peace' icon in pastel pink",
            "Bubble tea cup with 'Boba First' caption",
            "Coffee cup with 'But First, Coffee' label",
            "Sleepy frog face with 'Leave Me Alone' text",
            "Sun face with 'Not Today' frown",
        ],
    },
    {
        "name": "Cute Mascots Sheet",
        "stickers": [
            "White blob mascot rolling its eyes",
            "Frog face with smug grin",
            "Crying-laughing emoji-style sticker",
            "Sleepy moon face with 'Brb Napping'",
            "Tiny ghost saying 'Boo You'",
            "Cloud with 'Anti-Stress Shield' label",
        ],
    },
]


def upsert(conn: sqlite3.Connection) -> dict:
    now = int(time.time())
    cur = conn.execute("SELECT id FROM packs WHERE pack_uid = ?", (PACK_UID,))
    existing = cur.fetchone()
    if existing:
        return {"action": "skipped", "reason": "already exists",
                "pack_id": existing[0], "pack_uid": PACK_UID}

    # 1) hot_topics
    cur = conn.execute(
        """INSERT INTO hot_topics
            (source, query, topic_name, raw_payload, evidence_urls,
             hot_score, region, fetched_at, status, theme_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "manual_curated",
            "funny hand gesture meme stickers",
            "Funny Hand Gesture Meme Stickers",
            json.dumps({"analysis": ANALYSIS_RAW,
                        "phrases": ["Not My Problem", "Leave Me Alone",
                                    "Do Not Disturb", "Talk to the Hand",
                                    "Boba First", "Too Bright", "No Thanks",
                                    "Protect My Peace", "But First Coffee"]},
                       ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            85.0,
            "us",
            now,
            "approved",
            "Yellow hand gestures + meme mood phrases — TikTok-native introvert/self-care humor.",
        ),
    )
    topic_id = cur.lastrowid

    # 2) topic_plans
    cur = conn.execute(
        """INSERT INTO topic_plans
            (topic_id, config, main_raw_text, series_payload, status,
             created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            topic_id,
            json.dumps({
                "target_market": "US/UK/AU/CA",
                "style": "Western TikTok meme aesthetic",
                "audience": ["Gen Z", "office workers", "introverts",
                             "boba lovers", "students"],
                "language": "en",
            }, ensure_ascii=False),
            ANALYSIS_RAW,
            json.dumps({
                "series_count": 1,
                "approach": "single hand-gesture-meme series with 50 stickers",
            }, ensure_ascii=False),
            "completed",
            now, now,
        ),
    )
    plan_id = cur.lastrowid

    # 3) pack_series
    metadata = {
        "preview_briefs": PREVIEW_BRIEFS,
        "internal_name_zh": "拒绝内耗手势情绪卡贴包",
        "subtitle": "Funny Introvert & No Drama Hand Gesture Stickers",
    }
    cur = conn.execute(
        """INSERT INTO pack_series
            (plan_id, series_idx, series_name, style_anchor, palette,
             pack_archetype, priority, metadata_json, is_selected, pack_uid)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan_id,
            1,
            "Talk to the Hand Mood Sticker Pack",
            "Yellow hand-gesture characters + bold black/white meme phrases + "
            "cute mascot accents (boba, coffee, heart, frog, blob). Bold "
            "outlines, flat colors, slightly oversaturated, sticker-pack "
            "aesthetic in the style of TikTok meme art.",
            "yellow + black + cream + soft pink + mint pastel accents",
            "mood_meme_introvert",
            5,
            json.dumps(metadata, ensure_ascii=False),
            1,
            PACK_UID,
        ),
    )
    series_id = cur.lastrowid

    # 4) packs
    cur = conn.execute(
        """INSERT INTO packs
            (pack_uid, series_id, display_name, cover_image_path,
             total_stickers, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            PACK_UID,
            series_id,
            "Talk to the Hand Mood Sticker Pack",
            "",
            50,
            "active",
            now,
        ),
    )
    pack_id = cur.lastrowid

    # 5) tkshop_products
    title = ("50Pcs Funny Hand Gesture Meme Stickers, Cute Self Care Vinyl "
             "Decals for Laptop Water Bottle Phone Case Journal")
    seller_sku = "INK-FUNNYHAND-50"
    cur = conn.execute(
        """INSERT INTO tkshop_products
            (pack_id, tiktok_product_id, detail_main_raw_text,
             title, description_html, selling_points, keywords,
             category_id, default_template_json, publish_status,
             created_at, published_at,
             seller_sku, tiktok_sku_id, auto_fix_attempts, last_fix_diff)
           VALUES (?, '', ?, ?, ?, ?, ?, ?, '{}', 'draft', ?, NULL,
                   ?, '', 0, '')""",
        (
            pack_id,
            ANALYSIS_RAW,
            title,
            DESCRIPTION_HTML,
            json.dumps(SELLING_POINTS, ensure_ascii=False),
            json.dumps(KEYWORDS, ensure_ascii=False),
            "928016",
            now,
            seller_sku,
        ),
    )
    product_id = cur.lastrowid

    conn.commit()
    return {
        "action": "created",
        "topic_id": topic_id,
        "plan_id": plan_id,
        "series_id": series_id,
        "pack_id": pack_id,
        "pack_uid": PACK_UID,
        "product_id": product_id,
        "title": title,
        "seller_sku": seller_sku,
    }


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        result = upsert(conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
