"""Restructure the 2026 graduation seed into the correct hierarchy:

  hot_topic (#384, already exists)
    └ topic_plan (NEW, single)
        ├ pack_series #1 — Class of 2026 Black Gold Ceremony Pack
        ├ pack_series #2 — Senior Year Memory Dump Pack
        ├ pack_series #3 — Grad Party Favor Sticker Kit
        ├ pack_series #4 — Main Character Graduate Meme Pack
        └ pack_series #5 — Aesthetic Grad Sticker Pack

The previous seed wrongly inserted 5 separate topic_plans (#13–17). This
script deletes those and creates the proper plan + 5 series structure.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/ops_workbench.db")
TOPIC_QUERY = "graduation stickers 2026"
WRONG_PLAN_IDS = (13, 14, 15, 16, 17)


SERIES_SPECS = [
    {
        "idx": 1,
        "name": "Class of 2026 Black Gold Ceremony Pack",
        "style_anchor": (
            "luxury graduation sticker pack, black gold color palette, elegant "
            "celebratory style, classy graduation theme, glossy vinyl die-cut "
            "stickers, premium commercial product look, metallic gold accents, "
            "modern elegant typography, consistent visual identity across the "
            "whole series"
        ),
        "palette": "black + gold + white + champagne",
        "pack_archetype": "graduation_ceremony_premium",
        "priority": 5,
        "platform_title": "Class of 2026 Graduation Stickers | Black Gold Senior Stickers | Waterproof Vinyl Grad Decals",
        "audience": ["high school grads", "college grads", "parents gifting"],
        "preview_briefs": [
            {
                "name": "Foundation Phrases",
                "stickers": [
                    '"Class of 2026" with graduation cap and gold stars',
                    '"Congrats Grad" with diploma scroll and ribbon',
                    '"Senior 2026" in bold elegant typography',
                    '"You Did It" with celebratory sparkles',
                    '"Officially Graduated" with laurel wreath',
                    '"Grad Mode ON" with cap and lightning accents',
                    '"Cap Toss Moment" with flying graduation cap',
                    '"Proud Graduate" with gold medal style badge',
                    '"Future Loading..." with clean modern design',
                    '"Diploma Unlocked" with certificate icon',
                ],
            },
            {
                "name": "Future-Forward",
                "stickers": [
                    '"Next Chapter Begins" with open book and stars',
                    '"The Tassel Was Worth The Hassle" with tassel graphic',
                    '"Dream Big Graduate" with moon and stars',
                    '"Made It!" with confetti burst',
                    '"Golden Future" with abstract starburst',
                    '"Shine On Grad" with sparkly gold effects',
                    '"Grad Energy" in stylish bold text',
                    '"Proud Moment" with elegant frame',
                    '"Started From Freshman Now We Here" in classy playful typography',
                    '"Cheers to the Graduate" with champagne glass icon',
                ],
            },
            {
                "name": "Iconic Symbols",
                "stickers": [
                    "graduation cap with gold sparkle pattern",
                    "rolled diploma tied with gold ribbon",
                    'gold star cluster with "2026"',
                    'elegant wreath badge with "Graduate"',
                    "black and gold balloon bouquet sticker",
                    "gold confetti burst sticker",
                    "graduation gown silhouette with gold outline",
                    "mortarboard hat with tassel and stars",
                    'luxury seal sticker saying "Top Grad"',
                    'black ribbon banner saying "Success"',
                ],
            },
            {
                "name": "Identity Affirmations",
                "stickers": [
                    '"Proud Family Moment"', '"Graduate Era"',
                    '"On To Bigger Things"', '"Mission Complete"',
                    '"Hard Work Paid Off"', '"From Study Sessions to Success"',
                    '"Walking Into My Future"', '"Classy, Smart, Graduated"',
                    '"Smart Looks Good On Me"', '"Success Looks Good On You"',
                ],
            },
            {
                "name": "Round Seals & Banners",
                "stickers": [
                    'circular sticker "Class of 2026"', 'round badge "Congrats!"',
                    'seal sticker "You Did It"', 'ribbon label "Senior Year"',
                    'mini banner sticker "Best Day Ever"',
                    'round label "Celebrate the Grad"',
                    "party-style sticker with cap and confetti",
                    'star-shaped sticker saying "Graduate"',
                    'decorative frame sticker "Well Done"',
                    'elegant label "The Future Is Bright"',
                ],
            },
        ],
    },
    {
        "idx": 2,
        "name": "Senior Year Memory Dump Pack",
        "style_anchor": (
            "cute graduation sticker pack, senior year memory scrapbook style, "
            "youthful school-life aesthetic, soft retro colors, hand-drawn yet "
            "clean commercial sticker look, scrapbook collage feeling, yearbook "
            "and campus life theme, playful typography"
        ),
        "palette": "cream + denim blue + muted red + pencil yellow + gray-black outline",
        "pack_archetype": "graduation_scrapbook_memory",
        "priority": 4,
        "platform_title": "Senior Year Memory Stickers | Graduation Scrapbook Stickers | High School Grad Sticker Pack",
        "audience": ["high school seniors", "journal users", "scrapbook fans"],
        "preview_briefs": [
            {
                "name": "Year-End Phrases",
                "stickers": [
                    '"Senior Year"', '"Last First Day"', '"Class of 2026"',
                    '"Memory Dump"', '"No More Homework"', '"Final Exam Survivor"',
                    '"School\'s Out Forever"', '"One Last Bell"',
                    '"Locker Memories"', '"We Made It"',
                ],
            },
            {
                "name": "Campus Objects",
                "stickers": [
                    "school locker with little stars", "yellow school bus",
                    "stack of textbooks", "backpack with pins",
                    'notebook page saying "senior notes"',
                    'coffee cup with "study fuel"',
                    "pencil and highlighter cluster",
                    "laptop with study tabs open", "yearbook graphic",
                    "classroom desk doodle sticker",
                ],
            },
            {
                "name": "Senior Activities",
                "stickers": [
                    '"Prom Night"', '"Pep Rally"', '"Class Photo Day"',
                    '"Lunch Break"', '"Senior Trip"', '"Besties Forever"',
                    '"Cafeteria Chronicles"', '"Hallway Memories"',
                    '"After School Vibes"', '"We\'ll Miss This Place"',
                ],
            },
            {
                "name": "Journal Decoration",
                "stickers": [
                    'polaroid frame with "best day"',
                    'instant photo saying "senior selfie"',
                    'paper note saying "remember this"',
                    "ticket stub style sticker", "washi tape style sticker",
                    "diary page sticker", "paper clip and note combo",
                    "doodle smiley with stars",
                    'mini planner label "graduation week"',
                    'scrapbook frame "core memory"',
                ],
            },
            {
                "name": "Farewell Phrases",
                "stickers": [
                    '"Senior Era"', '"Goodbye Campus"', '"This Is The End"',
                    '"Best Years Ever"', '"Late Nights, Big Dreams"',
                    '"So Much Changed"', '"Signed the Yearbook"',
                    '"The Last Chapter"', '"Never Forget These Days"',
                    '"Ready for the Next Adventure"',
                ],
            },
        ],
    },
    {
        "idx": 3,
        "name": "Grad Party Favor Sticker Kit",
        "style_anchor": (
            "graduation party sticker pack, festive party favor label style, "
            "cheerful clean commercial design, black gold and white with optional "
            "soft accent colors, die-cut sticker labels, circular labels, banner "
            "labels, bottle-label style graphics, suitable for graduation party "
            "gifts and decorations"
        ),
        "palette": "black gold + pink gold + blue silver alternates",
        "pack_archetype": "graduation_party_favor",
        "priority": 5,
        "platform_title": "Graduation Party Favor Stickers | Class of 2026 Thank You Labels | Grad Party Decoration Pack",
        "audience": ["parents", "party planners", "graduation party hosts"],
        "preview_briefs": [
            {
                "name": "Core Party Phrases",
                "stickers": [
                    '"Class of 2026"', '"Congrats Grad"',
                    '"Thank You For Celebrating"', '"Grad Party"',
                    '"You Did It!"', '"Celebrate the Graduate"',
                    '"Proud of You"', '"Best Day Ever"',
                    '"The Future Is Bright"', '"Cheers to the Grad"',
                ],
            },
            {
                "name": "Round Stickers",
                "stickers": [
                    'round sticker "Party Favor"', 'round sticker "Thank You"',
                    'round sticker "Congrats"', 'round sticker "Graduate 2026"',
                    'round sticker "Sweet Ending, New Beginning"',
                    'round sticker "Celebrate"', 'round sticker "Official Grad"',
                    'round sticker "Cap Toss"', 'round sticker "Well Done"',
                    'round sticker "So Proud"',
                ],
            },
            {
                "name": "Bottle / Snack Labels",
                "stickers": [
                    'water bottle label "Class of 2026"',
                    'water bottle label "Congrats Grad"',
                    'water bottle label "Sip Sip Hooray"',
                    'water bottle label "Grad Party"',
                    'snack bag label "Treat Yourself"',
                    'snack bag label "Celebrate Big"',
                    'cup sticker "Cheers"', 'candy bag label "Sweet Success"',
                    'food table label "Party Time"', 'dessert label "Proud Graduate"',
                ],
            },
            {
                "name": "Envelope / Gift Tags",
                "stickers": [
                    'envelope seal "Congrats!"',
                    'envelope seal "Open for Celebration"',
                    'gift bag sticker "For the Grad"', 'gift tag "Special Day"',
                    'favor bag sticker "Thanks for Coming"',
                    'mini label "Celebrate Success"',
                    'seal sticker "Class of 2026"',
                    'mini round sticker "Party Guest"',
                    'tag style sticker "With Love"',
                    'seal sticker "Shine Bright"',
                ],
            },
            {
                "name": "Festive Icons",
                "stickers": [
                    "balloons with cap icon", "confetti burst",
                    "graduation cake sticker", "party hat with tassel",
                    "camera icon for photo booth", "fireworks celebration icon",
                    "diploma with ribbon", 'gift box with "grad"',
                    'photo frame sticker "Graduation Day"',
                    'banner sticker "Let\'s Celebrate"',
                ],
            },
        ],
    },
    {
        "idx": 4,
        "name": "Main Character Graduate Meme Pack",
        "style_anchor": (
            "funny graduation sticker pack, trendy gen-z meme aesthetic, cute "
            "ORIGINAL cartoon character style (no copyrighted IP), playful "
            "expressive animals, bold colorful accents, fun modern typography, "
            "highly shareable social-media-friendly design"
        ),
        "palette": "cream / white base + Y2K pink, blue, purple + neon green accents",
        "pack_archetype": "graduation_meme_genz",
        "priority": 4,
        "platform_title": "Funny Graduation Stickers | Senior Meme Sticker Pack | Cute Grad Vinyl Stickers",
        "audience": ["college students", "high school seniors", "TikTok Gen Z"],
        "ip_warning": (
            "NO Disney / Sanrio / Pikachu / Stitch / etc — ALL characters must "
            "be ORIGINAL animals (cat, duck, frog, raccoon, bear, dog, bunny, "
            "hamster)."
        ),
        "preview_briefs": [
            {
                "name": "Character + Phrase Combos",
                "stickers": [
                    '"Crying But Graduating" with teary cartoon cat in grad cap',
                    '"Finally Done" with tired duck holding diploma',
                    '"Academic Weapon" with cool frog in cap and glasses',
                    '"Main Character Grad" with sparkly raccoon',
                    '"Hot Grad Energy" with confident cartoon dog',
                    '"Survived Senior Year" with exhausted bear',
                    '"Mentally At Graduation" with dizzy bunny',
                    '"This Degree Was Expensive" with shocked cat',
                    '"Powered By Coffee" with sleepy duck and coffee cup',
                    '"I Made It Somehow" with chaotic frog character',
                ],
            },
            {
                "name": "Pure Phrase Memes",
                "stickers": [
                    '"No More Deadlines"', '"Too Smart To Explain"',
                    '"I Passed, Don\'t Ask How"', '"Sleep Deprived Graduate"',
                    '"Group Project Survivor"', '"Deadline Destroyer"',
                    '"Brain Empty, Diploma Full"', '"One Degree Hotter"',
                    '"Straight Outta Finals"', '"I Need A Nap"',
                ],
            },
            {
                "name": "Cartoon Character Showcase",
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
                "name": "Trendy Phrases",
                "stickers": [
                    '"Main Character Moment"', '"Serving Grad Looks"',
                    '"Brb Being Successful"', '"Degree Unlocked"',
                    '"Too Glam To Fail"', '"Certified Smart"',
                    '"Slayed Graduation"', '"Iconic Graduate"',
                    '"Future CEO"', '"Watch Me Win"',
                ],
            },
            {
                "name": "Witty Closers",
                "stickers": [
                    '"Done and Dusty"', '"Smarter Than Yesterday"',
                    '"Officially Unsupervised"', '"Too Tired To Celebrate"',
                    '"Tiny Brain, Big Degree"', '"Graduate Loading Complete"',
                    '"Just Here For The Pics"', '"I Came, I Saw, I Graduated"',
                    '"Minimal Effort, Maximum Outcome"', '"Thanks, Coffee"',
                ],
            },
        ],
    },
    {
        "idx": 5,
        "name": "Aesthetic Grad Sticker Pack",
        "style_anchor": (
            "aesthetic graduation sticker pack, soft feminine graduation theme, "
            "coquette and poetcore inspired style, elegant bows, ribbons, flowers, "
            "pearls, pastel colors, delicate modern typography, pretty scrapbook "
            "luxury aesthetic"
        ),
        "palette": "cream + pink + sage green + ice blue + lavender",
        "pack_archetype": "graduation_aesthetic_coquette",
        "priority": 4,
        "platform_title": "Aesthetic Graduation Stickers | Coquette Grad Sticker Pack | Cute Class of 2026 Decals",
        "audience": ["female grads", "journal users", "Pinterest/Etsy aesthetic fans"],
        "preview_briefs": [
            {
                "name": "Soft Affirmations",
                "stickers": [
                    '"She Did It"', '"Class of 2026"', '"Next Chapter"',
                    '"Dream Big"', '"Pretty Smart"', '"Blooming Graduate"',
                    '"Signed, Sealed, Graduated"', '"Dear Future Me"',
                    '"Soft Grad Era"', '"A Lovely Beginning"',
                ],
            },
            {
                "name": "Coquette Objects",
                "stickers": [
                    "satin bow with graduation cap", 'pearl framed "2026"',
                    "floral diploma sticker", "bouquet with ribbon",
                    "lace-edged graduation cap", "open journal with flowers",
                    "envelope with bow and seal",
                    "little perfume bottle with stars",
                    "stacked books with ribbon",
                    "moon and stars in soft aesthetic style",
                ],
            },
            {
                "name": "Romantic Phrases",
                "stickers": [
                    '"Bow & Cap"', '"Future in Bloom"', '"Gentle Genius"',
                    '"Smart Looks Pretty"', '"Celebrate Softly"',
                    '"Lovely Graduate"', '"New Dreams Ahead"',
                    '"The Sweetest Ending"', '"Gracefully Graduated"',
                    '"Little Miss Graduate"',
                ],
            },
            {
                "name": "Scrapbook Decoration",
                "stickers": [
                    "polaroid frame with floral border", "diary page with bow",
                    "pearl heart sticker", "lace ribbon sticker",
                    "floral corner frame", "vintage-style letter sticker",
                    'mini frame "Grad Day"', "botanical book sticker",
                    "soft star cluster sticker", 'ribbon tag with "2026"',
                ],
            },
            {
                "name": "Dreamy Closers",
                "stickers": [
                    '"On To Beautiful Things"', '"The Future Is Soft and Bright"',
                    '"Cap, Gown, Glow"', '"Made With Grace"',
                    '"Bloom Where You Go"', '"She Believed and Achieved"',
                    '"A Beautiful Milestone"', '"Keep Growing"',
                    '"Golden Girl Graduate"', '"Hello New Chapter"',
                ],
            },
        ],
    },
]


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        topic_row = conn.execute(
            "SELECT id FROM hot_topics WHERE query = ?", (TOPIC_QUERY,),
        ).fetchone()
        if not topic_row:
            raise SystemExit("hot_topic for graduation 2026 not found — run seed first")
        topic_id = topic_row[0]

        # 1) delete the wrong 5 plans (and any orphan series under them, just in case)
        deleted_series = conn.execute(
            f"DELETE FROM pack_series WHERE plan_id IN ({','.join('?'*len(WRONG_PLAN_IDS))})",
            WRONG_PLAN_IDS,
        ).rowcount
        deleted_plans = conn.execute(
            f"DELETE FROM topic_plans WHERE id IN ({','.join('?'*len(WRONG_PLAN_IDS))})",
            WRONG_PLAN_IDS,
        ).rowcount

        now = int(time.time())

        # 2) create ONE topic_plan summarizing the 5-pack lineup
        plan_config = {
            "title": "2026 Graduation Sticker Lineup — 5 Packs",
            "target_market": "US/UK/AU/CA",
            "language": "en",
            "season": "graduation 2026",
            "audience_overview": (
                "high school + college grads, parents, party planners, Gen Z "
                "TikTok users, female journal/scrapbook users"
            ),
            "global_packaging_blurb": (
                "50–60 PCS / Waterproof Vinyl / No Duplicates / Class of 2026 / "
                "Laptop, Water Bottle, Phone, Scrapbook, Party Favor Use"
            ),
            "series_lineup": [
                {"idx": s["idx"], "name": s["name"], "priority": s["priority"]}
                for s in SERIES_SPECS
            ],
        }
        plan_payload = {
            "title": plan_config["title"],
            "series_count": len(SERIES_SPECS),
            "approach": (
                "5 differentiated packs covering ceremony / memory / party / "
                "meme / aesthetic — same season, distinct audience segments."
            ),
            "series_lineup": plan_config["series_lineup"],
        }
        cur = conn.execute(
            """INSERT INTO topic_plans
                (topic_id, config, main_raw_text, series_payload, status,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, 'approved', ?, ?)""",
            (
                topic_id,
                json.dumps(plan_config, ensure_ascii=False),
                (
                    "2026 海外毕业季卡贴 5 套差异化系列规划。\n"
                    "套装 1：黑金典礼款 (★★★★★) — 大众安全、家长送礼、走量。\n"
                    "套装 2：校园回忆款 (★★★★☆) — 高中生情绪价值、scrapbook。\n"
                    "套装 3：派对礼品款 (★★★★★) — 家长 + 派对策划，客单价高。\n"
                    "套装 4：搞笑梗图款 (★★★★☆) — Gen Z 自传播，原创动物角色禁 IP。\n"
                    "套装 5：审美手账款 (★★★★☆) — 女生 + Pinterest/Etsy 风格。\n"
                ),
                json.dumps(plan_payload, ensure_ascii=False),
                now, now,
            ),
        )
        plan_id = cur.lastrowid

        # 3) create 5 pack_series under that plan
        series_ids: list[int] = []
        for spec in SERIES_SPECS:
            metadata = {
                "preview_briefs": spec["preview_briefs"],
                "platform_title": spec["platform_title"],
                "audience": spec["audience"],
                "total_stickers": sum(len(b["stickers"]) for b in spec["preview_briefs"]),
            }
            if spec.get("ip_warning"):
                metadata["ip_warning"] = spec["ip_warning"]
            cur = conn.execute(
                """INSERT INTO pack_series
                    (plan_id, series_idx, series_name, style_anchor, palette,
                     pack_archetype, priority, metadata_json, is_selected, pack_uid)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '')""",
                (
                    plan_id, spec["idx"], spec["name"],
                    spec["style_anchor"], spec["palette"],
                    spec["pack_archetype"], spec["priority"],
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            series_ids.append(cur.lastrowid)

        conn.commit()

        result = {
            "deleted_wrong_plans": deleted_plans,
            "deleted_orphan_series": deleted_series,
            "topic_id": topic_id,
            "new_plan_id": plan_id,
            "new_series_ids": series_ids,
            "series_titles": [s["name"] for s in SERIES_SPECS],
        }
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
