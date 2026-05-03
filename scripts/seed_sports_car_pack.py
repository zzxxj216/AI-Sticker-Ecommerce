"""Seed: Sports Car Sticker Pack — Racing Decals (curated 2026-05-01).

Full chain: hot_topic → topic_plan → pack_series → pack → tkshop_products.
Single-pack series (50 stickers, white-bg car-collection style).

Idempotent via pack_uid lookup.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("data/ops_workbench.db")
PACK_UID = "20260501_sports_car_sticker_pack"
TOPIC_QUERY = "sports car sticker pack"

# ---------------------------------------------------------------------------
# Hot topic (curated, single source of truth)
# ---------------------------------------------------------------------------

HOT_TOPIC = {
    "source": "manual_curated",
    "query": TOPIC_QUERY,
    "topic_name": "Sports Car Sticker Pack — Racing Decals for Kids/Teens",
    "region": "us",
    "hot_score": 80.0,
    "status": "approved",
    "theme_summary": (
        "海外汽车主题贴纸包：跑车 / 赛车 / 性能车视觉合集，多色（蓝黄红绿橙黑白），"
        "白底图鉴排版。主力受众 8–16 岁男孩 + 车迷 + 礼品场景；TikTok 冲动消费友好。"
    ),
    "evidence_urls": [],
    "raw_payload": {
        "positioning": "Sports Car Sticker Pack / Racing Car Decal Pack / Supercar Inspired Stickers",
        "key_audiences": [
            "8-16 year old boys / teens",
            "car enthusiasts (modders, garage culture, racing fans)",
            "parents searching 'car stickers for boys / kids'",
            "TikTok impulse buyers via short-video unboxing",
        ],
        "ip_warning": (
            "DO NOT use brand names: Ferrari / Lamborghini / Bugatti / Porsche / "
            "Tesla / BMW / Mercedes / Toyota / JDM brand names / 'Official' / "
            "'Licensed' — keep titles to 'sports car' / 'racing car' / 'performance car'."
        ),
        "content_angles": [
            "unboxing + flip-through video",
            "gift-set staging (birthday, party favors, classroom rewards)",
            "garage-culture desk / workshop scene",
        ],
    },
}

# ---------------------------------------------------------------------------
# Topic plan (single)
# ---------------------------------------------------------------------------

PLAN_CONFIG = {
    "title": "Sports Car Sticker Pack — Racing Decals (single series)",
    "target_market": "US/UK/AU/CA",
    "language": "en",
    "audience_overview": "kids/teens (esp. boys), car enthusiasts, gifting parents",
    "global_packaging_blurb": (
        "50 PCS / Assorted Sports Car Designs / No Duplicates / "
        "Laptop, Water Bottle, Phone, Notebook, Skateboard, Gift Use"
    ),
    "ip_warning": HOT_TOPIC["raw_payload"]["ip_warning"],
}

PLAN_RAW_TEXT = (
    "Sports Car Sticker Pack — 单系列 50 张设计规划。\n"
    "风格：白底抠图合集，多色跑车/性能车/SUV/新能源造型，类似汽车图鉴。\n"
    "受众：男孩/青少年、车迷、家长送礼。\n"
    "命名方向：Speed Garage / Dream Car Collection / Cool Car Stickers for Boys。\n"
    "推荐首发标题：50Pcs Sports Car Sticker Pack, Assorted Racing Car Decals for "
    "Laptop Water Bottle Notebook Skateboard, Car Lover Gift for Kids Teens.\n"
    "禁用：所有真实车品牌名、官方/授权暗示词、车标 logo。\n"
)

# ---------------------------------------------------------------------------
# Pack series (single, 50 stickers across 5 preview sheets)
# ---------------------------------------------------------------------------

SERIES_SPEC = {
    "name": "Sports Car Sticker Pack",
    "style_anchor": (
        "assorted sports car sticker pack, photo-realistic generic car illustrations "
        "(no real-world brand badges or model names), white background cutout layout, "
        "vehicle catalog aesthetic, multicolor: blue, yellow, red, green, orange, "
        "black, white, glossy metallic accents, sleek aerodynamic silhouettes, mix "
        "of supercars, coupes, performance sedans, sport SUVs and EV-inspired "
        "shapes, clean die-cut edges, consistent style across the whole 50-piece "
        "series, print-ready"
    ),
    "palette": "blue + yellow + red + green + orange + black + white + glossy metallic",
    "pack_archetype": "vehicle_collection_sports_car",
    "priority": 4,
    "platform_title": (
        "50Pcs Sports Car Sticker Pack, Assorted Racing Car Decals for Laptop Water "
        "Bottle Notebook Skateboard, Car Lover Gift for Kids Teens"
    ),
    "audience": ["boys 8-16", "teens", "car enthusiasts", "gifting parents"],
    "preview_briefs": [
        {
            "name": "Sheet 1 — Supercars (top-tier)",
            "stickers": [
                "Aggressive low-slung supercar, neon yellow body, side profile",
                "Wide-body coupe, deep blue, three-quarter front view",
                "Mid-engine sport coupe, candy red, side profile",
                "Hypercar with butterfly-style doors open, matte black",
                "Track-focused racer with rear wing, white with red livery",
                "Carbon-fiber accented coupe, silver, sleek silhouette",
                "Performance roadster top-down, lime green",
                "Twin-turbo sport coupe, electric orange, side profile",
                "Aero-kit equipped supercar, midnight purple, low-angle",
                "Limited-edition track car, gunmetal gray with neon yellow trim",
            ],
        },
        {
            "name": "Sheet 2 — Performance Sedans & Coupes",
            "stickers": [
                "Four-door sport sedan, racing red, side profile",
                "Compact sport hatchback, electric blue, three-quarter view",
                "Luxury grand tourer coupe, deep green, side profile",
                "Tuned drift coupe with widened arches, white with black accents",
                "Sport sedan with quad exhausts, jet black, rear angle",
                "Modified street coupe, sunset orange, with side decals",
                "Classic-shape sport coupe revival, cream white",
                "AWD performance hatchback, candy yellow, side profile",
                "Coupe with carbon roof, sapphire blue, three-quarter view",
                "Sport sedan with diffuser, racing silver, rear angle",
            ],
        },
        {
            "name": "Sheet 3 — Sport SUVs & Crossovers",
            "stickers": [
                "Compact sport crossover, ocean blue, three-quarter view",
                "High-performance SUV, racing red, side profile",
                "Lifted off-road SUV with roof rack, forest green",
                "Sleek coupe SUV, pearl white, three-quarter view",
                "Luxury SUV with chrome accents, jet black, side profile",
                "Subcompact crossover, sunny yellow, side profile",
                "Sport tourer SUV, gunmetal gray, three-quarter view",
                "Boxy adventure SUV, terracotta orange, side profile",
                "Performance SUV with quad exhausts, midnight blue, rear angle",
                "Premium electric-style SUV, frost silver, three-quarter view",
            ],
        },
        {
            "name": "Sheet 4 — EV & Concept Style",
            "stickers": [
                "Streamlined electric sedan, frost white, side profile",
                "Aerodynamic EV coupe, electric blue, three-quarter view",
                "Concept-style hypercar with light bar, neon green",
                "Boxy futuristic EV pickup, gunmetal gray, side profile",
                "Curved-roof EV crossover, glacier mint, three-quarter view",
                "Wedge-shape concept car, candy red, side profile",
                "Two-tone EV coupe, white over black, side profile",
                "Compact urban EV, taxicab yellow, three-quarter view",
                "Performance EV roadster, hot pink, side profile",
                "Glass-roof grand tourer EV, midnight purple, three-quarter view",
            ],
        },
        {
            "name": "Sheet 5 — Race-Spec & Tuner",
            "stickers": [
                "Time-attack track car with stickers and roll cage, white",
                "Drift-spec coupe with massive rear wing, neon orange",
                "Le Mans-style prototype, blue with white stripes",
                "Rally-spec hatchback with mud flaps and lights, red",
                "Touring car with full livery, black/yellow checker accents",
                "Stanced street tuner, deep purple, low-angle three-quarter",
                "Drag-prepped coupe with wheelie bars, lime green",
                "GT-class racer with side number, silver with red panels",
                "Off-road buggy, desert tan with orange accents",
                "Endurance race coupe with sponsor patches, white with rainbow stripes",
            ],
        },
    ],
}

# ---------------------------------------------------------------------------
# TKShop product copy
# ---------------------------------------------------------------------------

TITLE = (
    "50Pcs Sports Car Sticker Pack, Assorted Racing Car Decals for Laptop Water "
    "Bottle Notebook Skateboard, Car Lover Gift for Kids Teens"
)

DESCRIPTION_HTML = """\
<p>Bring racing-inspired style to your everyday items with this assorted sports car sticker pack. Each pack includes a variety of colorful car-themed stickers, featuring cool sports cars, performance vehicles, modern coupes and street-style designs.</p>

<p>These stickers are great for decorating laptops, water bottles, notebooks, skateboards, phone cases, journals, scrapbooks, lockers, gift bags and more. They are also a fun gift choice for kids, teens, students and car lovers.</p>

<p><strong>Package Includes:</strong> 1 pack of assorted sports car stickers (50 pcs).</p>

<p><strong>Product Features:</strong></p>
<ul>
  <li>Assorted sports car designs — colorful mix of racing-inspired modern cars, coupes, performance vehicles, SUVs and street-style designs</li>
  <li>Great for kids, teens, students and car lovers who enjoy racing, garage culture, cool vehicles and collectible sticker packs</li>
  <li>Perfect for laptops, water bottles, notebooks, skateboards, phone cases, journals, scrapbooks, lockers and gift bags</li>
  <li>Easy to peel and apply — press firmly onto clean, smooth, dry surfaces</li>
  <li>Fun gift choice for birthdays, party favors, classroom rewards, holiday stockings, goodie bags and car-themed events</li>
</ul>

<p><strong>How to Use:</strong> Clean the surface before applying. Make sure the surface is smooth and dry. Peel off the backing, place the sticker, and press firmly. For best results, apply to clean, flat and smooth surfaces.</p>

<p><em>Note: Colors may vary slightly due to screen settings and lighting. Please allow slight size differences due to manual measurement.</em></p>
"""

SELLING_POINTS = [
    "Assorted sports car designs: racing cars, coupes, performance SUVs, EV-style shapes",
    "Multicolor mix: blue, yellow, red, green, orange, black, white, glossy metallic",
    "Great for laptop, water bottle, phone case, notebook, skateboard, journal, locker, gift bag",
    "Easy peel-and-stick on clean smooth surfaces; perfect for DIY decoration",
    "Fun gift idea for kids, teens, car lovers, birthday parties, classroom rewards, goodie bags",
]

KEYWORDS = [
    "sports car stickers",
    "racing car stickers",
    "car sticker pack",
    "car decals",
    "cool stickers for boys",
    "stickers for teens",
    "laptop stickers",
    "water bottle stickers",
    "skateboard stickers",
    "car lover gift",
    "racing stickers",
    "vehicle stickers",
    "assorted car decals",
    "stickers for kids",
    "supercar stickers",
]


def upsert(conn: sqlite3.Connection) -> dict:
    now = int(time.time())
    existing = conn.execute(
        "SELECT id FROM packs WHERE pack_uid = ?", (PACK_UID,),
    ).fetchone()
    if existing:
        return {"action": "skipped", "reason": "pack_uid already exists",
                "pack_id": existing[0], "pack_uid": PACK_UID}

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

    # 2) topic_plan
    plan_payload = {
        "title": PLAN_CONFIG["title"],
        "series_count": 1,
        "approach": "single sports-car series, 50 stickers across 5 preview sheets",
        "series_lineup": [{"idx": 1, "name": SERIES_SPEC["name"], "priority": SERIES_SPEC["priority"]}],
    }
    cur = conn.execute(
        """INSERT INTO topic_plans
            (topic_id, config, main_raw_text, series_payload, status,
             created_at, updated_at)
           VALUES (?, ?, ?, ?, 'approved', ?, ?)""",
        (
            topic_id,
            json.dumps(PLAN_CONFIG, ensure_ascii=False),
            PLAN_RAW_TEXT,
            json.dumps(plan_payload, ensure_ascii=False),
            now, now,
        ),
    )
    plan_id = cur.lastrowid

    # 3) pack_series
    metadata = {
        "preview_briefs": SERIES_SPEC["preview_briefs"],
        "platform_title": SERIES_SPEC["platform_title"],
        "audience": SERIES_SPEC["audience"],
        "total_stickers": sum(len(b["stickers"]) for b in SERIES_SPEC["preview_briefs"]),
        "ip_warning": PLAN_CONFIG["ip_warning"],
    }
    cur = conn.execute(
        """INSERT INTO pack_series
            (plan_id, series_idx, series_name, style_anchor, palette,
             pack_archetype, priority, metadata_json, is_selected, pack_uid)
           VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            plan_id, SERIES_SPEC["name"], SERIES_SPEC["style_anchor"],
            SERIES_SPEC["palette"], SERIES_SPEC["pack_archetype"],
            SERIES_SPEC["priority"],
            json.dumps(metadata, ensure_ascii=False),
            PACK_UID,
        ),
    )
    series_id = cur.lastrowid

    # 4) pack
    cur = conn.execute(
        """INSERT INTO packs
            (pack_uid, series_id, display_name, cover_image_path,
             total_stickers, status, created_at)
           VALUES (?, ?, ?, '', ?, 'active', ?)""",
        (PACK_UID, series_id, "Sports Car Sticker Pack", 50, now),
    )
    pack_id = cur.lastrowid

    # 5) tkshop_product
    seller_sku = "INK-SPORTSCAR-50"
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
            pack_id, PLAN_RAW_TEXT, TITLE, DESCRIPTION_HTML,
            json.dumps(SELLING_POINTS, ensure_ascii=False),
            json.dumps(KEYWORDS, ensure_ascii=False),
            "928016", now, seller_sku,
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
        "title": TITLE,
        "title_chars": len(TITLE),
        "seller_sku": seller_sku,
        "stickers_total": metadata["total_stickers"],
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
