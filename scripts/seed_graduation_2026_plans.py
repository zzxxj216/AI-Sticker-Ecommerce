"""Seed: import the 2026 graduation curated topic + 5 topic plans.

Source: docs/ChatGPT-毕业季卡贴题材设计.md (curated by GPT, refined by operator).

Layout:
  hot_topic: "2026 Graduation Stickers — Overseas Market"
  └ topic_plans (5):
      1) Black Gold Ceremony Pack
      2) Senior Year Memory Dump Pack
      3) Grad Party Favor Sticker Kit
      4) Main Character Graduate Meme Pack
      5) Aesthetic Grad Sticker Pack

Each plan's series_payload carries 5 preview prompt blocks (50 stickers
total per pack), ready for downstream pack_series creation.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/ops_workbench.db")
TOPIC_QUERY = "graduation stickers 2026"

HOT_TOPIC = {
    "source": "manual_curated",
    "query": TOPIC_QUERY,
    "topic_name": "2026 Graduation Stickers — Overseas Market",
    "region": "us",
    "hot_score": 92.0,
    "status": "approved",
    "theme_summary": (
        "海外毕业季卡贴 5 套差异化题材：黑金典礼 / 校园回忆 / 派对礼品 / "
        "搞笑梗图 / 审美手账。覆盖 Etsy/Amazon/TikTok Shop/Pinterest 4 平台 "
        "高频方向，避开 IP 风险，主打 Class of 2026 + 真实使用场景。"
    ),
    "evidence_urls": [
        "https://www.etsy.com/market/2026_graduation_stickers",
        "https://www.amazon.com/Graduation-Stickers-Decorations-Waterproof-Skateboard/dp/B0BTBXTLS1",
        "https://www.pinterest.com/ideas/class-of-2026-sticker/905390112848/",
        "https://shop.tiktok.com/us/k/graduation-sticker",
        "https://www.theguardian.com/technology/2025/dec/10/from-glacier-aesthetic-to-poetcore-pinterest-predicts-the-visual-trends-of-2026-based-on-its-search-data",
        "https://www.amazon.sg/Senior-Stickers-Graduation-Waterproof-Die-Cut/dp/B0FWQ92PGG",
    ],
    "raw_payload": {
        "overview": "5-pack curated lineup for 2026 graduation season, overseas market.",
        "packs": [
            {"idx": 1, "name": "Class of 2026 Black Gold Ceremony Pack",
             "priority": "★★★★★", "audience": "high school / college grads + parents",
             "palette": "black + gold + white + champagne"},
            {"idx": 2, "name": "Senior Year Memory Dump Pack",
             "priority": "★★★★☆", "audience": "US/UK/AU/CA high schoolers, journal users",
             "palette": "cream + denim blue + muted red + pencil yellow"},
            {"idx": 3, "name": "Grad Party Favor Sticker Kit",
             "priority": "★★★★★", "audience": "parents + party planners",
             "palette": "black gold / pink gold / blue silver"},
            {"idx": 4, "name": "Main Character Graduate Meme Pack",
             "priority": "★★★★☆", "audience": "Gen Z college / high school students",
             "palette": "cream base + Y2K pink/blue/purple + neon accents"},
            {"idx": 5, "name": "Aesthetic Grad Sticker Pack",
             "priority": "★★★★☆", "audience": "female grads, journal/scrapbook users",
             "palette": "pink gold + cream + sage green + ice blue + soft lavender"},
        ],
        "global_packaging_blurb": (
            "50–60 PCS / Waterproof Vinyl / No Duplicates / Class of 2026 / "
            "Laptop, Water Bottle, Phone, Scrapbook, Party Favor Use"
        ),
    },
}


# --- 5 plans -----------------------------------------------------------------

# Each plan: title / config / main_raw_text / series_payload (5 preview blocks)

PLAN_1 = {
    "title": "Class of 2026 Black Gold Ceremony Pack",
    "config": {
        "pack_archetype": "graduation_ceremony_premium",
        "style_anchor": (
            "luxury graduation sticker pack, black gold color palette, "
            "elegant celebratory style, classy graduation theme, glossy "
            "vinyl die-cut stickers, premium commercial product look, "
            "metallic gold accents, modern elegant typography, "
            "consistent visual identity across the whole series"
        ),
        "palette": "black + gold + white + champagne",
        "target_market": "US/UK/AU/CA",
        "audience": ["high school grads", "college grads", "parents gifting"],
        "platform_title": "Class of 2026 Graduation Stickers | Black Gold Senior Stickers | Waterproof Vinyl Grad Decals",
        "priority": 5,
    },
    "main_raw_text": (
        "套装 1：Class of 2026 Black Gold Ceremony Pack\n"
        "定位：最稳的基础爆款，适合大多数海外毕业生和家长购买。\n"
        "结构：12 张年份文字贴 / 10 张毕业帽证书图案 / 10 张祝福语 / "
        "8 张圆形封口 / 10–15 张异形大贴。\n"
        "建议优先级：★★★★★"
    ),
    "preview_briefs": [
        {
            "name": "Preview 1 — Foundation Phrases",
            "prompt": (
                "luxury graduation sticker pack preview, black gold color palette, "
                "elegant celebratory style, glossy vinyl die-cut stickers, premium "
                "commercial product look, clean white background, 10 unique stickers "
                "evenly arranged, bold readable English text, modern elegant typography, "
                "no duplicate stickers, print-ready, consistent visual identity"
            ),
            "stickers": [
                "\"Class of 2026\" with graduation cap and gold stars",
                "\"Congrats Grad\" with diploma scroll and ribbon",
                "\"Senior 2026\" in bold elegant typography",
                "\"You Did It\" with celebratory sparkles",
                "\"Officially Graduated\" with laurel wreath",
                "\"Grad Mode ON\" with cap and lightning accents",
                "\"Cap Toss Moment\" with flying graduation cap",
                "\"Proud Graduate\" with gold medal style badge",
                "\"Future Loading...\" with clean modern design",
                "\"Diploma Unlocked\" with certificate icon",
            ],
        },
        {
            "name": "Preview 2 — Future-Forward",
            "prompt": "(same anchor as Preview 1)",
            "stickers": [
                "\"Next Chapter Begins\" with open book and stars",
                "\"The Tassel Was Worth The Hassle\" with tassel graphic",
                "\"Dream Big Graduate\" with moon and stars",
                "\"Made It!\" with confetti burst",
                "\"Golden Future\" with abstract starburst",
                "\"Shine On Grad\" with sparkly gold effects",
                "\"Grad Energy\" in stylish bold text",
                "\"Proud Moment\" with elegant frame",
                "\"Started From Freshman Now We Here\" in classy playful typography",
                "\"Cheers to the Graduate\" with champagne glass icon",
            ],
        },
        {
            "name": "Preview 3 — Iconic Symbols",
            "prompt": "(same anchor)",
            "stickers": [
                "graduation cap with gold sparkle pattern",
                "rolled diploma tied with gold ribbon",
                "gold star cluster with \"2026\"",
                "elegant wreath badge with \"Graduate\"",
                "black and gold balloon bouquet sticker",
                "gold confetti burst sticker",
                "graduation gown silhouette with gold outline",
                "mortarboard hat with tassel and stars",
                "luxury seal sticker saying \"Top Grad\"",
                "black ribbon banner saying \"Success\"",
            ],
        },
        {
            "name": "Preview 4 — Identity Affirmations",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Proud Family Moment\"",
                "\"Graduate Era\"",
                "\"On To Bigger Things\"",
                "\"Mission Complete\"",
                "\"Hard Work Paid Off\"",
                "\"From Study Sessions to Success\"",
                "\"Walking Into My Future\"",
                "\"Classy, Smart, Graduated\"",
                "\"Smart Looks Good On Me\"",
                "\"Success Looks Good On You\"",
            ],
        },
        {
            "name": "Preview 5 — Round Seals & Banners",
            "prompt": "(same anchor)",
            "stickers": [
                "circular sticker \"Class of 2026\"",
                "round badge \"Congrats!\"",
                "seal sticker \"You Did It\"",
                "ribbon label \"Senior Year\"",
                "mini banner sticker \"Best Day Ever\"",
                "round label \"Celebrate the Grad\"",
                "party-style sticker with cap and confetti",
                "star-shaped sticker saying \"Graduate\"",
                "decorative frame sticker \"Well Done\"",
                "elegant label \"The Future Is Bright\"",
            ],
        },
    ],
}


PLAN_2 = {
    "title": "Senior Year Memory Dump Pack",
    "config": {
        "pack_archetype": "graduation_scrapbook_memory",
        "style_anchor": (
            "cute graduation sticker pack, senior year memory scrapbook style, "
            "youthful school-life aesthetic, soft retro colors, hand-drawn yet "
            "clean commercial sticker look, scrapbook collage feeling, yearbook "
            "and campus life theme, playful typography"
        ),
        "palette": "cream + denim blue + muted red + pencil yellow + gray-black outline",
        "target_market": "US/UK/AU/CA",
        "audience": ["high school seniors", "journal users", "scrapbook fans"],
        "platform_title": "Senior Year Memory Stickers | Graduation Scrapbook Stickers | High School Grad Sticker Pack",
        "priority": 4,
    },
    "main_raw_text": (
        "套装 2：Senior Year Memory Dump Pack\n"
        "定位：海外高中生为主，主打毕业回忆 + 校园生活情绪价值。\n"
        "建议优先级：★★★★☆"
    ),
    "preview_briefs": [
        {
            "name": "Preview 1 — Year-End Phrases",
            "prompt": "cute graduation sticker pack, scrapbook style, soft retro colors, hand-drawn commercial look, white bg, 10 unique die-cut stickers, scrapbook collage feel",
            "stickers": [
                "\"Senior Year\"", "\"Last First Day\"", "\"Class of 2026\"",
                "\"Memory Dump\"", "\"No More Homework\"", "\"Final Exam Survivor\"",
                "\"School's Out Forever\"", "\"One Last Bell\"",
                "\"Locker Memories\"", "\"We Made It\"",
            ],
        },
        {
            "name": "Preview 2 — Campus Objects",
            "prompt": "(same anchor)",
            "stickers": [
                "school locker with little stars", "yellow school bus",
                "stack of textbooks", "backpack with pins",
                "notebook page saying \"senior notes\"",
                "coffee cup with \"study fuel\"",
                "pencil and highlighter cluster",
                "laptop with study tabs open", "yearbook graphic",
                "classroom desk doodle sticker",
            ],
        },
        {
            "name": "Preview 3 — Senior Activities",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Prom Night\"", "\"Pep Rally\"", "\"Class Photo Day\"",
                "\"Lunch Break\"", "\"Senior Trip\"", "\"Besties Forever\"",
                "\"Cafeteria Chronicles\"", "\"Hallway Memories\"",
                "\"After School Vibes\"", "\"We'll Miss This Place\"",
            ],
        },
        {
            "name": "Preview 4 — Journal Decoration",
            "prompt": "(same anchor)",
            "stickers": [
                "polaroid frame with \"best day\"",
                "instant photo saying \"senior selfie\"",
                "paper note saying \"remember this\"",
                "ticket stub style sticker", "washi tape style sticker",
                "diary page sticker", "paper clip and note combo",
                "doodle smiley with stars",
                "mini planner label \"graduation week\"",
                "scrapbook frame \"core memory\"",
            ],
        },
        {
            "name": "Preview 5 — Farewell Phrases",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Senior Era\"", "\"Goodbye Campus\"", "\"This Is The End\"",
                "\"Best Years Ever\"", "\"Late Nights, Big Dreams\"",
                "\"So Much Changed\"", "\"Signed the Yearbook\"",
                "\"The Last Chapter\"", "\"Never Forget These Days\"",
                "\"Ready for the Next Adventure\"",
            ],
        },
    ],
}


PLAN_3 = {
    "title": "Grad Party Favor Sticker Kit",
    "config": {
        "pack_archetype": "graduation_party_favor",
        "style_anchor": (
            "graduation party sticker pack, festive party favor label style, "
            "cheerful clean commercial design, black gold and white with optional "
            "soft accent colors, die-cut sticker labels, circular labels, banner "
            "labels, bottle-label style graphics, suitable for graduation party "
            "gifts and decorations"
        ),
        "palette": "black gold + pink gold + blue silver alternates",
        "target_market": "US/UK/AU/CA",
        "audience": ["parents", "party planners", "graduation party hosts"],
        "platform_title": "Graduation Party Favor Stickers | Class of 2026 Thank You Labels | Grad Party Decoration Pack",
        "priority": 5,
    },
    "main_raw_text": (
        "套装 3：Grad Party Favor Sticker Kit\n"
        "定位：毕业派对装饰套装。家长 + 派对采购为主，客单价更高。\n"
        "建议优先级：★★★★★"
    ),
    "preview_briefs": [
        {
            "name": "Preview 1 — Core Party Phrases",
            "prompt": "graduation party sticker pack preview, festive party favor label style, black gold white, white bg, 10 unique stickers",
            "stickers": [
                "\"Class of 2026\"", "\"Congrats Grad\"",
                "\"Thank You For Celebrating\"", "\"Grad Party\"",
                "\"You Did It!\"", "\"Celebrate the Graduate\"",
                "\"Proud of You\"", "\"Best Day Ever\"",
                "\"The Future Is Bright\"", "\"Cheers to the Grad\"",
            ],
        },
        {
            "name": "Preview 2 — Round Stickers",
            "prompt": "(same anchor)",
            "stickers": [
                "round sticker \"Party Favor\"", "round sticker \"Thank You\"",
                "round sticker \"Congrats\"", "round sticker \"Graduate 2026\"",
                "round sticker \"Sweet Ending, New Beginning\"",
                "round sticker \"Celebrate\"", "round sticker \"Official Grad\"",
                "round sticker \"Cap Toss\"", "round sticker \"Well Done\"",
                "round sticker \"So Proud\"",
            ],
        },
        {
            "name": "Preview 3 — Bottle / Snack Labels",
            "prompt": "(same anchor)",
            "stickers": [
                "water bottle label \"Class of 2026\"",
                "water bottle label \"Congrats Grad\"",
                "water bottle label \"Sip Sip Hooray\"",
                "water bottle label \"Grad Party\"",
                "snack bag label \"Treat Yourself\"",
                "snack bag label \"Celebrate Big\"",
                "cup sticker \"Cheers\"", "candy bag label \"Sweet Success\"",
                "food table label \"Party Time\"", "dessert label \"Proud Graduate\"",
            ],
        },
        {
            "name": "Preview 4 — Envelope / Gift Tags",
            "prompt": "(same anchor)",
            "stickers": [
                "envelope seal \"Congrats!\"", "envelope seal \"Open for Celebration\"",
                "gift bag sticker \"For the Grad\"", "gift tag \"Special Day\"",
                "favor bag sticker \"Thanks for Coming\"",
                "mini label \"Celebrate Success\"", "seal sticker \"Class of 2026\"",
                "mini round sticker \"Party Guest\"", "tag style sticker \"With Love\"",
                "seal sticker \"Shine Bright\"",
            ],
        },
        {
            "name": "Preview 5 — Festive Icons",
            "prompt": "(same anchor)",
            "stickers": [
                "balloons with cap icon", "confetti burst",
                "graduation cake sticker", "party hat with tassel",
                "camera icon for photo booth", "fireworks celebration icon",
                "diploma with ribbon", "gift box with \"grad\"",
                "photo frame sticker \"Graduation Day\"",
                "banner sticker \"Let's Celebrate\"",
            ],
        },
    ],
}


PLAN_4 = {
    "title": "Main Character Graduate Meme Pack",
    "config": {
        "pack_archetype": "graduation_meme_genz",
        "style_anchor": (
            "funny graduation sticker pack, trendy gen-z meme aesthetic, cute "
            "ORIGINAL cartoon character style (no copyrighted IP), playful "
            "expressive animals, bold colorful accents, fun modern typography, "
            "highly shareable social-media-friendly design"
        ),
        "palette": "cream / white base + Y2K pink, blue, purple + neon green accents",
        "target_market": "US/UK/AU/CA",
        "audience": ["college students", "high school seniors", "TikTok Gen Z"],
        "platform_title": "Funny Graduation Stickers | Senior Meme Sticker Pack | Cute Grad Vinyl Stickers",
        "priority": 4,
        "ip_warning": "NO Disney/Sanrio/Pikachu/Stitch/etc — ALL characters must be original animals (cat, duck, frog, raccoon, bear, dog, bunny, hamster).",
    },
    "main_raw_text": (
        "套装 4：Main Character Graduate Meme Pack\n"
        "定位：年轻人最容易传播的一套，TikTok/Instagram Gen Z 审美。\n"
        "重要：不允许 IP 角色，全部用原创动物。\n"
        "建议优先级：★★★★☆"
    ),
    "preview_briefs": [
        {
            "name": "Preview 1 — Character + Phrase Combos",
            "prompt": "funny graduation sticker pack, gen-z meme aesthetic, original cartoon animals, white bg, 10 unique stickers",
            "stickers": [
                "\"Crying But Graduating\" with teary cartoon cat in grad cap",
                "\"Finally Done\" with tired duck holding diploma",
                "\"Academic Weapon\" with cool frog in cap and glasses",
                "\"Main Character Grad\" with sparkly raccoon",
                "\"Hot Grad Energy\" with confident cartoon dog",
                "\"Survived Senior Year\" with exhausted bear",
                "\"Mentally At Graduation\" with dizzy bunny",
                "\"This Degree Was Expensive\" with shocked cat",
                "\"Powered By Coffee\" with sleepy duck and coffee cup",
                "\"I Made It Somehow\" with chaotic frog character",
            ],
        },
        {
            "name": "Preview 2 — Pure Phrase Memes",
            "prompt": "(same anchor)",
            "stickers": [
                "\"No More Deadlines\"", "\"Too Smart To Explain\"",
                "\"I Passed, Don't Ask How\"", "\"Sleep Deprived Graduate\"",
                "\"Group Project Survivor\"", "\"Deadline Destroyer\"",
                "\"Brain Empty, Diploma Full\"", "\"One Degree Hotter\"",
                "\"Straight Outta Finals\"", "\"I Need A Nap\"",
            ],
        },
        {
            "name": "Preview 3 — Cartoon Character Showcase",
            "prompt": "(same anchor)",
            "stickers": [
                "cat in graduation cap screaming happily",
                "duck holding giant diploma",
                "frog with coffee and thesis papers",
                "sleepy bear hugging laptop",
                "raccoon throwing cap in the air",
                "bunny crying with confetti",
                "dog wearing sunglasses and grad stole",
                "shocked hamster with tuition bill",
                "cartoon cap with sparkly meme face",
                "diploma scroll with funny expression",
            ],
        },
        {
            "name": "Preview 4 — Trendy Phrases",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Main Character Moment\"", "\"Serving Grad Looks\"",
                "\"Brb Being Successful\"", "\"Degree Unlocked\"",
                "\"Too Glam To Fail\"", "\"Certified Smart\"",
                "\"Slayed Graduation\"", "\"Iconic Graduate\"",
                "\"Future CEO\"", "\"Watch Me Win\"",
            ],
        },
        {
            "name": "Preview 5 — Witty Closers",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Done and Dusty\"", "\"Smarter Than Yesterday\"",
                "\"Officially Unsupervised\"", "\"Too Tired To Celebrate\"",
                "\"Tiny Brain, Big Degree\"", "\"Graduate Loading Complete\"",
                "\"Just Here For The Pics\"", "\"I Came, I Saw, I Graduated\"",
                "\"Minimal Effort, Maximum Outcome\"", "\"Thanks, Coffee\"",
            ],
        },
    ],
}


PLAN_5 = {
    "title": "Aesthetic Grad Sticker Pack",
    "config": {
        "pack_archetype": "graduation_aesthetic_coquette",
        "style_anchor": (
            "aesthetic graduation sticker pack, soft feminine graduation theme, "
            "coquette and poetcore inspired style, elegant bows, ribbons, flowers, "
            "pearls, pastel colors, delicate modern typography, pretty scrapbook "
            "luxury aesthetic"
        ),
        "palette": "cream + pink + sage green + ice blue + lavender",
        "target_market": "US/UK/AU/CA",
        "audience": ["female grads", "journal users", "Pinterest/Etsy aesthetic fans"],
        "platform_title": "Aesthetic Graduation Stickers | Coquette Grad Sticker Pack | Cute Class of 2026 Decals",
        "priority": 4,
    },
    "main_raw_text": (
        "套装 5：Aesthetic Grad Sticker Pack\n"
        "定位：审美向，女生 + 手账 + Pinterest/Etsy 风格用户。\n"
        "建议优先级：★★★★☆"
    ),
    "preview_briefs": [
        {
            "name": "Preview 1 — Soft Affirmations",
            "prompt": "aesthetic graduation sticker pack, coquette poetcore style, pastel colors, bows ribbons flowers pearls, white bg, 10 unique stickers",
            "stickers": [
                "\"She Did It\"", "\"Class of 2026\"", "\"Next Chapter\"",
                "\"Dream Big\"", "\"Pretty Smart\"", "\"Blooming Graduate\"",
                "\"Signed, Sealed, Graduated\"", "\"Dear Future Me\"",
                "\"Soft Grad Era\"", "\"A Lovely Beginning\"",
            ],
        },
        {
            "name": "Preview 2 — Coquette Objects",
            "prompt": "(same anchor)",
            "stickers": [
                "satin bow with graduation cap", "pearl framed \"2026\"",
                "floral diploma sticker", "bouquet with ribbon",
                "lace-edged graduation cap", "open journal with flowers",
                "envelope with bow and seal", "little perfume bottle with stars",
                "stacked books with ribbon", "moon and stars in soft aesthetic style",
            ],
        },
        {
            "name": "Preview 3 — Romantic Phrases",
            "prompt": "(same anchor)",
            "stickers": [
                "\"Bow & Cap\"", "\"Future in Bloom\"", "\"Gentle Genius\"",
                "\"Smart Looks Pretty\"", "\"Celebrate Softly\"",
                "\"Lovely Graduate\"", "\"New Dreams Ahead\"",
                "\"The Sweetest Ending\"", "\"Gracefully Graduated\"",
                "\"Little Miss Graduate\"",
            ],
        },
        {
            "name": "Preview 4 — Scrapbook Decoration",
            "prompt": "(same anchor)",
            "stickers": [
                "polaroid frame with floral border", "diary page with bow",
                "pearl heart sticker", "lace ribbon sticker",
                "floral corner frame", "vintage-style letter sticker",
                "mini frame \"Grad Day\"", "botanical book sticker",
                "soft star cluster sticker", "ribbon tag with \"2026\"",
            ],
        },
        {
            "name": "Preview 5 — Dreamy Closers",
            "prompt": "(same anchor)",
            "stickers": [
                "\"On To Beautiful Things\"", "\"The Future Is Soft and Bright\"",
                "\"Cap, Gown, Glow\"", "\"Made With Grace\"",
                "\"Bloom Where You Go\"", "\"She Believed and Achieved\"",
                "\"A Beautiful Milestone\"", "\"Keep Growing\"",
                "\"Golden Girl Graduate\"", "\"Hello New Chapter\"",
            ],
        },
    ],
}

ALL_PLANS = [PLAN_1, PLAN_2, PLAN_3, PLAN_4, PLAN_5]


def upsert(conn: sqlite3.Connection) -> dict:
    now = int(time.time())

    # Idempotency: same query + topic_name → skip
    existing = conn.execute(
        "SELECT id FROM hot_topics WHERE query = ? AND topic_name = ?",
        (HOT_TOPIC["query"], HOT_TOPIC["topic_name"]),
    ).fetchone()
    if existing:
        return {"action": "skipped", "reason": "topic already exists",
                "topic_id": existing[0]}

    # 1) hot_topic
    cur = conn.execute(
        """INSERT INTO hot_topics
            (source, query, topic_name, raw_payload, evidence_urls,
             hot_score, region, fetched_at, status, theme_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            HOT_TOPIC["source"], HOT_TOPIC["query"], HOT_TOPIC["topic_name"],
            json.dumps(HOT_TOPIC["raw_payload"], ensure_ascii=False),
            json.dumps(HOT_TOPIC["evidence_urls"], ensure_ascii=False),
            HOT_TOPIC["hot_score"], HOT_TOPIC["region"],
            now, HOT_TOPIC["status"], HOT_TOPIC["theme_summary"],
        ),
    )
    topic_id = cur.lastrowid

    # 2) topic_plans (5)
    plan_ids: list[int] = []
    for plan in ALL_PLANS:
        cur = conn.execute(
            """INSERT INTO topic_plans
                (topic_id, config, main_raw_text, series_payload, status,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                topic_id,
                json.dumps({"title": plan["title"], **plan["config"]}, ensure_ascii=False),
                plan["main_raw_text"],
                json.dumps({
                    "title": plan["title"],
                    "series_count": 1,
                    "preview_briefs": plan["preview_briefs"],
                    "total_stickers": sum(len(b["stickers"]) for b in plan["preview_briefs"]),
                }, ensure_ascii=False),
                "approved",
                now, now,
            ),
        )
        plan_ids.append(cur.lastrowid)

    conn.commit()
    return {
        "action": "created",
        "topic_id": topic_id,
        "topic_name": HOT_TOPIC["topic_name"],
        "plan_ids": plan_ids,
        "plan_titles": [p["title"] for p in ALL_PLANS],
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
