#!/usr/bin/env python3
"""Seed six recent curated hot-topic sticker packs for June 2026.

Each pack has 50 sticker briefs split into five 10-sticker preview sheets.
The script is idempotent and upgrades rows created by older runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.packs.service import get_pack_service
from src.services.preview_gen.prompts import build_preview_prompt
from src.services.preview_gen.service import PreviewGenService


DB_PATH = Path("data/ops_workbench.db")
SOURCE = "manual_curated_recent_202606"
TARGET_PREVIEWS = 5
STICKERS_PER_PREVIEW = 10
TARGET_STICKERS = TARGET_PREVIEWS * STICKERS_PER_PREVIEW


TOPICS: list[dict[str, Any]] = [
    {
        "topic_name": "PrideTikTok Chosen Family Joy Badges",
        "query": "Pride Month 2026 #PrideTikTok",
        "hot_score": 92,
        "region": "US/CA",
        "evidence_urls": [
            "https://newsroom.tiktok.com/pride-month-2026-celebrating-the-2slgbtqi-community-through-pridetiktok-ca?lang=en-CA",
            "https://www.hrc.org/press-releases/lets-get-free-hrc-celebrates-pride-month-as-it-grows-to-record-4-million-members-supporters",
        ],
        "theme_summary": "June Pride activity is active across social platforms. This pack is a non-logo chosen-family, visibility, allyship, and queer joy badge set.",
        "series_name": "PrideTikTok Chosen Family Joy Badges",
        "pack_archetype": "identity_badge_pack",
        "priority": "high",
        "palette": "rainbow accents, warm cream, sky blue, vivid pink, sunflower yellow, grass green, black ink, white die-cut border",
        "style_anchor": "Bright inclusive vector sticker sheet with soft rounded shapes, friendly English hand-lettering, celebratory pride colors, chosen-family picnic and parade details, no official Pride logos, no real people likenesses, no brand marks, respectful and joyful.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "chosen family badges",
                "stickers": [
                    "Chosen Family / rainbow picnic blanket badge with tiny hearts and safety pins",
                    "Seen & Loved / rounded speech bubble with rainbow sparkle border",
                    "Pride Picnic / lemonade cup, fruit bowl, and mini rainbow flag garland",
                    "Ally Energy / clean badge with starburst and small hands forming a heart",
                    "Queer Joy / bold bubbly lettering with confetti and sun rays",
                    "Safe Space / cozy doorway icon with rainbow welcome mat",
                    "Love Out Loud / megaphone with hearts and ribbon streamers",
                    "Soft Pride / pastel rainbow cloud with gentle smile and white outline",
                    "Chosen Table / picnic table with mixed cups, flowers, and tiny name tags",
                    "Together Here / linked paper-chain hearts in rainbow colors",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "parade and street fair",
                "stickers": [
                    "Parade Day / sneaker stepping through rainbow confetti",
                    "Glitter Route / map pin with rainbow route line and star stickers",
                    "Happy Pride / banner ribbon with sunburst and heart details",
                    "Community Booth / tiny folding table with stickers, pins, and lemonade",
                    "Wave Back / small handheld flag with motion lines and sparkles",
                    "Sidewalk Chalk Love / chalk heart drawing with rainbow dust",
                    "Pride Playlist / cassette tape with rainbow label and music notes",
                    "Street Fair Bloom / bouquet wrapped in rainbow tissue paper",
                    "Main Character Joy / star-shaped badge with celebratory lettering",
                    "Meet Me Here / location pin with two hearts and confetti",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "soft pride affirmations",
                "stickers": [
                    "You Belong / warm cloud label with tiny rainbow drops",
                    "Still Becoming / butterfly with rainbow trail and serif lettering",
                    "Loved As Is / heart-shaped seal with soft rays",
                    "No Hiding Today / open curtain icon with rainbow light",
                    "Gentle Pride / lavender flower with rainbow ribbon stem",
                    "Protect Queer Joy / shield badge with hearts, stars, and cream outline",
                    "Here & Proud / location marker with tiny sparkle burst",
                    "Soft But Loud / fluffy bubble letters with small megaphone",
                    "My Colors / paint palette with rainbow swatches",
                    "Safe With Me / keychain charm with heart lock and rainbow tag",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "ally and friend group",
                "stickers": [
                    "Ally On Duty / simple sash badge with stars and heart pin",
                    "Hold The Door / open door icon with rainbow threshold",
                    "Pronoun Pin Mix / three blank-style pronoun pins without specific names",
                    "Hype Friend / cheer pennant with rainbow tassels",
                    "Bring Snacks / tote bag with fruit, water bottle, and rainbow patch",
                    "Respect The Vibe / tidy badge with sparkle underline",
                    "Good Listener / ear icon with heart bubble and mini rainbow",
                    "Chosen Crew / group of abstract star characters, no faces",
                    "Share The Mic / microphone with two heart speech bubbles",
                    "Love Is Local / neighborhood sign with rainbow bunting",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "night pride sparkle",
                "stickers": [
                    "Afterglow Pride / moon with rainbow halo and tiny stars",
                    "Disco Heart / heart-shaped disco ball reflecting rainbow light",
                    "Glow Together / two glow sticks crossed into a heart",
                    "Rainbow Night Market / booth awning with string lights",
                    "Late Parade / night skyline with small rainbow flags",
                    "Shine Anyway / star badge with bold hand-lettering",
                    "Confetti Pocket / jacket pocket spilling rainbow confetti",
                    "Dance Floor Joy / shoes on sparkle tile floor",
                    "Moonlit Ally / crescent moon holding tiny flag garland",
                    "Proud All Year / calendar page with rainbow star sticker",
                ],
            },
        ],
    },
    {
        "topic_name": "Juneteenth Freedom Cookout & Culture",
        "query": "Juneteenth 2026 federal holiday community celebrations",
        "hot_score": 90,
        "region": "US",
        "evidence_urls": [
            "https://www.archives.gov/news/federal-holidays",
            "https://www.archives.gov/news/topics/juneteenth",
            "https://apnews.com/article/9bbef5c75ce3c2569c6804c0780206d5",
        ],
        "theme_summary": "Juneteenth falls on Friday, June 19, 2026. The pack is respectful and community-centered: freedom, history, music, food, stars, and gathering symbols without caricature.",
        "series_name": "Juneteenth Freedom Cookout & Culture",
        "pack_archetype": "cultural_celebration_pack",
        "priority": "high",
        "palette": "deep red, black, green, warm gold, cream, white die-cut border",
        "style_anchor": "Respectful Juneteenth community celebration sticker sheet, bold clean vector illustration, heritage-inspired red black green and gold palette, warm cookout and music motifs, no stereotypes, no political figures, no realistic portraits, no official seals.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "freedom day badges",
                "stickers": [
                    "Freedom Day / bold hand-lettered badge with gold star and ribbon",
                    "Juneteenth 1865 / circular date seal with sunrise rays and clean typography",
                    "Celebrate Freedom / waving bunting in red, black, green, and gold",
                    "History Lives / open book with glowing star bookmark and laurel leaves",
                    "Joy Is Resistance / strong typography badge with stars and sun rays",
                    "June 19 / calendar page with red flower and gold accent",
                    "Freedom Rings / bell icon with ribbon streamers and sparkle marks",
                    "Rooted In Freedom / tree roots forming a heart under a rising sun",
                    "Honor The Day / laurel badge with small starburst center",
                    "Black Joy / warm lettering with radiating hearts and dots",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "community cookout",
                "stickers": [
                    "Community Cookout / grill smoke, lemonade, and checkered picnic detail",
                    "Red Drink Table / pitcher and cups with lemon slices and star napkins",
                    "Family Reunion / table setting with plates, flowers, and warm lights",
                    "Cookout Crew / apron badge with tiny utensils and ribbon",
                    "Pass The Plate / picnic plate with corn, greens, and gold star pick",
                    "Front Yard Celebration / folding chair, cooler, and bunting",
                    "Sweet Tea Toast / two cups clinking with sparkle accents",
                    "Side Dish Legend / casserole dish badge with playful typography",
                    "Grill Master / spatula crossed with star-tipped fork",
                    "Gather Here / picnic blanket icon with warm string lights",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "music and parade",
                "stickers": [
                    "Lift Every Voice / vintage microphone with music notes and star accents",
                    "Drumline Joy / snare drum with red and gold ribbons",
                    "Porch Choir / music-note wreath with hand-lettered label",
                    "Freedom Playlist / record player with sunburst label",
                    "Parade Steps / clean sneaker pair with confetti trail",
                    "Brass Band Star / trumpet with green ribbon and sparkle notes",
                    "Block Party Beat / speaker icon with warm rays",
                    "Sing It Loud / speech bubble shaped like a music note",
                    "Rhythm & Roots / drumsticks crossed over leaf branch",
                    "Dance In The Street / abstract motion lines and star confetti",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "heritage and memory",
                "stickers": [
                    "Galveston Roots / tasteful Texas star postcard badge with wave line",
                    "Memory Quilt / patchwork heart in red, black, green, and gold",
                    "Read The Story / book stack with ribbon bookmark and gold star",
                    "Ancestor Garden / marigold-style flowers around a candle silhouette",
                    "Freedom Timeline / small scroll with 1865 marker and sunrise",
                    "Legacy Letter / envelope with wax star and leaf branch",
                    "Oral History / speech bubbles around vintage recorder icon",
                    "Heritage Hands / two abstract hands holding a star, no skin details",
                    "Remember & Rise / sunrise badge over simple hill line",
                    "Story Keeper / notebook with ribbon, stars, and clean lettering",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "modern celebration labels",
                "stickers": [
                    "Free-ish No More / modern typography badge with respectful tone",
                    "Made It To Freedom / ribbon label with star sparks",
                    "Juneteenth Cookout / rectangular label with bunting and grill icon",
                    "Celebrate With Care / heart badge with gold highlight",
                    "The Culture Shines / sunburst label with music notes",
                    "Freedom Looks Good / mirror badge with star outline",
                    "Community First / clean patch label with linked stars",
                    "Joy At The Table / table icon with flower centerpiece",
                    "Still Rising / upward sun icon with red and green stripes",
                    "June 19th Forever / classic label with bold border and stars",
                ],
            },
        ],
    },
    {
        "topic_name": "America 250 Neighborhood Picnic Badges",
        "query": "America 250 July 4 2026 semiquincentennial celebrations",
        "hot_score": 88,
        "region": "US",
        "evidence_urls": [
            "https://america250.org/july-4-moments/",
            "https://www.nps.gov/subjects/npscelebrates/usa-250.htm",
            "https://www.archives.gov/news/federal-holidays",
        ],
        "theme_summary": "America's 250th anniversary is ramping up ahead of July 4, 2026. Keep it civic, local, and nonpartisan: porch flags, fireworks, history notes, neighborhood picnic, and 1776-2026 milestone badges.",
        "series_name": "America 250 Neighborhood Picnic Badges",
        "pack_archetype": "seasonal_festival_pack",
        "priority": "high",
        "palette": "classic red, navy blue, cream, sky blue, warm gold, white die-cut border",
        "style_anchor": "Retro civic celebration sticker sheet for America's 250th birthday, screen-printed badge style, friendly neighborhood picnic tone, nonpartisan, no America250 official logo, no government seals, no political figures, vintage red navy cream palette.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "250th birthday badges",
                "stickers": [
                    "1776-2026 / retro milestone badge with small fireworks and stars",
                    "America 250 / non-logo birthday badge with bunting and cream border",
                    "Main Street 250 / vintage street sign with bunting and sunburst",
                    "Red White & Blue Crew / friendly block-letter phrase with stars",
                    "Time Capsule / sealed box with ribbon, date tag, and star stickers",
                    "Civic Birthday / layer cake with tiny flag candles and star sprinkles",
                    "250 Years / arched typography label with fireworks",
                    "Local History Club / small courthouse silhouette, no seal, with stars",
                    "Founding Notes / parchment note icon with feather pen and ribbon",
                    "Birthday Parade / tiny marching banner with 250 numerals",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "neighborhood picnic",
                "stickers": [
                    "Neighborhood Picnic / gingham basket, lemonade jar, and tiny flags",
                    "Community Cookout / grill, corn, and paper plate icon badge",
                    "Porch Parade / row of porch bunting with warm lanterns",
                    "Block Party Map / neighborhood map with stars and route dots",
                    "Lawn Chair Club / folding chair with bunting ribbon",
                    "Cooler Full Of Pops / retro cooler with star stickers",
                    "Picnic Permit / playful faux ticket label with stars",
                    "Front Porch Crew / porch swing with bunting and lemonade",
                    "Bring A Side / casserole dish with star toothpicks",
                    "Small Town Sparkle / mailbox with tiny flags and flower pot",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "fireworks and night sky",
                "stickers": [
                    "After The Fireworks / night sky badge with spark trails and blanket",
                    "Sparkler Hour / two sparklers crossed with cream smoke lines",
                    "Firework Watch Party / picnic blanket under starburst sky",
                    "Glow Stick Crew / red and blue glow sticks tied with ribbon",
                    "Boom Then Snacks / popcorn tub with star fireworks",
                    "Sky Full Of Stars / navy badge with cream constellation lines",
                    "Last Light Parade / sunset bunting with small fireworks",
                    "Firefly Fireworks / jar of glowing lights with star label",
                    "Quiet Fireworks Fan / earmuffs and star badge for sensitive viewers",
                    "Night Picnic / thermos, blanket, and fireworks reflection",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "roadside americana objects",
                "stickers": [
                    "Main Street Diner / pie slice and enamel mug badge",
                    "Roadside Postcard / blank postcard with stars and wavy stripes",
                    "County Fair Ribbon / blue ribbon with 250 year mark",
                    "Vintage Cooler / red metal cooler with star decals",
                    "Bunting Window / shop window with balloons and bunting",
                    "Lemonade Stand / hand-painted sign with patriotic colors",
                    "Bandstand Music / gazebo icon with music notes and flags",
                    "Parade Bike / bicycle with streamers and basket flowers",
                    "Summer Pie / berry pie with star crust cutouts",
                    "State Sticker Map / simple map patch without state names",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "memory and keepsake labels",
                "stickers": [
                    "Save The Date / July 4 2026 label with stars and ribbon",
                    "We Were Here / keepsake badge with tiny fireworks",
                    "Family Photo Spot / camera icon with bunting frame",
                    "Picnic Guest Book / notebook with star bookmark",
                    "Mailbox Memory / envelope with 250 stamp-style mark",
                    "Summer Of 250 / sun label with red and blue rays",
                    "Local Legend / trophy ribbon with small town skyline",
                    "Parade Souvenir / ticket stub with star punch holes",
                    "Stars On The Porch / rocking chair with star blanket",
                    "Remember The Spark / matchbook-style label with fireworks",
                ],
            },
        ],
    },
    {
        "topic_name": "Jelly Summer Capri Microtrend",
        "query": "summer 2026 fashion trends jelly shoes capri pants Gen Z",
        "hot_score": 84,
        "region": "US/UK",
        "evidence_urls": [
            "https://www.whowhatwear.com/fashion/trends/gen-z-minimalist-summer-trends-2026",
            "https://www.whowhatwear.com/fashion/trends/capri-trouser-trends-2026",
            "https://www.whowhatwear.com/fashion/shoes/jelly-shoe-trends-2026",
        ],
        "theme_summary": "Summer 2026 fashion coverage is repeatedly calling out capri pants, pedal pushers, and jelly shoes. Translate the microtrend into playful outfit-object stickers without celebrity or brand references.",
        "series_name": "Jelly Summer Capri Microtrend",
        "pack_archetype": "lifestyle_identity_pack",
        "priority": "medium",
        "palette": "clear aqua, jelly pink, lemon yellow, black, crisp white, chrome silver, pale denim blue",
        "style_anchor": "Playful Gen Z summer fashion object sticker sheet, glossy jelly textures, capri pants and pedal-pusher outfit details, minimalist editorial layout but sticker-friendly, no brand logos, no celebrity likenesses, no luxury designer references.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "jelly shoes and capri comeback",
                "stickers": [
                    "Jelly Summer / translucent pink jelly sandal with sparkle highlights",
                    "Capri Comeback / cropped black capri pants folded with tiny star pin",
                    "Pedal Pusher Club / playful badge with bike wheel and cropped trouser hem",
                    "Clear Jelly Tote / transparent mini tote with shell charm and lip gloss",
                    "Tiny Tank Big Mood / simple tank top with bold summer lettering",
                    "Polka Capri / dotted capri pants icon with lemon-yellow hanger",
                    "Glossy Flip / aqua jelly flip-flop with chrome sun charm",
                    "Outfit Check / mirror sticker with capri silhouette and heart flash",
                    "Low-Key Summer Uniform / tank, capri, and jelly sandal flat lay",
                    "Clear Strap Crush / glossy transparent sandal buckle detail",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "minimal resort closet",
                "stickers": [
                    "Resort Errands / woven market tote with jelly keychain",
                    "Capri & Coffee Run / cropped trouser leg and iced cup without brand",
                    "Micro Cardigan / tiny cardigan draped over hanger with shell charm",
                    "Beach To Bodega / flip-flop and receipt icon badge",
                    "Sleek Tank / black tank top with chrome star pin",
                    "White Linen Mood / folded linen shirt with aqua sunglasses",
                    "Tiny Scarf Trick / headscarf tied around jelly tote handle",
                    "Minimal Summer / simple serif label with chrome underline",
                    "Vacation Capsule / suitcase tag with capri and sandal icons",
                    "Clean Girl Heatwave / fan, tank, and glossy lip balm icon set",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "Y2K mall summer",
                "stickers": [
                    "Mall Walk Era / chunky jelly sandal on checker tile",
                    "Capri Queen / glitter bubble letters with cropped pant icon",
                    "Charm Anklet / silver anklet with sun and shell charms",
                    "Flip Phone Fit Check / tiny flip phone with outfit mirror flash",
                    "Glossy Hair Clip / butterfly clip in jelly pink and aqua",
                    "Pedal Pushers / playful cropped trouser badge with chrome outline",
                    "Summer Receipt / faux shopping receipt with star stamps",
                    "Mini Sunglasses / narrow sunglasses with sparkle lens",
                    "Jelly Stack / three translucent bracelets in citrus colors",
                    "Food Court Fit / tray icon with sunglasses and shopping bag",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "beach city accessories",
                "stickers": [
                    "Beach Metro / transit card and shell charm, no city logo",
                    "Jelly Bucket Hat / glossy bucket hat with tiny star pin",
                    "Capri Hem / close-up stitched hem with sparkle tag",
                    "Sunblock But Cute / sunscreen tube with chrome star",
                    "Clear Pouch / transparent pouch holding keys, balm, and shells",
                    "Aqua Slides / clear aqua slide sandals with water shine",
                    "Denim Capri / pale denim capri folded with belt loop charm",
                    "Hot Pavement / wavy heat lines around jelly sandal",
                    "Seaside Mirror / small compact mirror with outfit reflection",
                    "Late Checkout / hotel key tag with sandal doodle",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "fashion text labels",
                "stickers": [
                    "Jellies Are Back / bold glossy text with sandal charm",
                    "Capri Agenda / planner page with outfit checkboxes",
                    "No Full Length Pants / funny label with cropped trouser icon",
                    "Tiny Tank Season / stacked text badge with sun rays",
                    "Pedal Pusher Energy / retro script label with bike line art",
                    "Clear Shoe Club / transparent badge with chrome stars",
                    "Outfit Repeater / closet hanger with heart sticker",
                    "Bare Ankles Only / playful oval label with anklet detail",
                    "Glossy Errands / receipt-shaped text sticker",
                    "Summer Uniform / clean typography badge with three outfit icons",
                ],
            },
        ],
    },
    {
        "topic_name": "Strawberry Moon Solstice Picnic",
        "query": "June 2026 strawberry moon summer solstice ritual picnic",
        "hot_score": 82,
        "region": "US/Global",
        "evidence_urls": [
            "https://www.timeanddate.com/calendar/seasons.html",
            "https://www.almanac.com/content/full-moon-june",
            "https://science.nasa.gov/solar-system/skywatching/",
        ],
        "theme_summary": "June brings the strawberry moon and summer solstice window. This safe seasonal pack turns moon watching, fruit picnic, sunset ritual, and celestial journaling into visual stickers.",
        "series_name": "Strawberry Moon Solstice Picnic",
        "pack_archetype": "seasonal_aesthetic_pack",
        "priority": "medium",
        "palette": "strawberry red, moon cream, twilight lavender, midnight blue, sage green, warm gold, white die-cut border",
        "style_anchor": "Dreamy seasonal celestial picnic sticker sheet, soft gouache-vector hybrid, strawberry moon motifs, summer solstice sunset, picnic fruit, gentle witchy-cottage accents, no zodiac claims, no religious symbols, no dark occult imagery.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "strawberry moon night",
                "stickers": [
                    "Strawberry Moon / full moon with strawberry vines and tiny stars",
                    "Moon Picnic / blanket with strawberries, thermos, and crescent plate",
                    "Look Up Tonight / hand-lettered label with moon rays",
                    "Lunar Berry Basket / basket of strawberries under cream moon",
                    "Twilight Jar / glass jar catching moonlight and fireflies",
                    "Moon Map / simple sky map with strawberry-shaped marker",
                    "Soft Moon Club / cozy badge with cloud and berry accents",
                    "Midnight Jam / strawberry jam jar with moon label",
                    "Moonbeam Snack / berry tart with star sprinkles",
                    "Night Bloom / white flower opening under strawberry moon",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "summer solstice sunset",
                "stickers": [
                    "Longest Day / sunset badge with golden rays and wildflowers",
                    "Solstice Picnic / sun-shaped basket with fruit and blanket",
                    "Golden Hour / camera icon with sun flare and berry charm",
                    "Sun Tea / mason jar tea brewing with lemon and flower",
                    "Last Light Walk / sandals, wildflower, and warm shadow",
                    "Sunset Journal / notebook page with pressed flower",
                    "Daylight Saver / playful sun badge with tiny clock",
                    "Solstice Bloom / sunflower and strawberry bouquet",
                    "Evening Glow / hill silhouette with long sun rays",
                    "Stay Until Sunset / label with blanket and sparkling sky",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "celestial journaling",
                "stickers": [
                    "Moon Notes / journal with moon phase tabs and berry sticker",
                    "Sky Diary / open notebook with stars, pen, and lavender sprig",
                    "Tonight's Mood / mini checklist with moon and strawberry icons",
                    "Pressed Starlight / pressed flower card with crescent pin",
                    "Dream Log / sleepy cloud label with strawberry moon",
                    "Constellation Corner / corner label with gold star dots",
                    "Tiny Telescope / small telescope pointed at cream moon",
                    "Moon Phase Washi / roll of washi tape with red moon phases",
                    "Stargazer Sticker / thermos and blanket beside notebook",
                    "Quiet Magic / soft text badge with sparkles and berry vine",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "fruit picnic charms",
                "stickers": [
                    "Berry Basket / woven basket full of strawberries and daisies",
                    "Moon Milk / cream bottle with strawberry moon label",
                    "Picnic Plate / strawberry tart, fork, and tiny star napkin",
                    "Jam Session / jar trio with moon labels and gingham lids",
                    "Strawberry Crown / berry wreath with gold star center",
                    "Blanket Corner / red gingham blanket with wildflower pin",
                    "Lemon Berry Sparkle / drink cup with fruit slices and stars",
                    "Sweetest Night / dessert label with moon-shaped spoon",
                    "Fruit & Fireflies / strawberry skewer with glowing dots",
                    "Berry Good Moon / cute text badge with berry and crescent",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "soft ritual objects",
                "stickers": [
                    "Set An Intention / ribbon label with candle and strawberry",
                    "Moon Water Jar / clear jar on windowsill with moon reflection",
                    "Good Luck Berry / strawberry charm with gold thread",
                    "Sun To Moon / split sun-moon badge with berry vine",
                    "Gentle Reset / soft text sticker with lavender and stars",
                    "Window Moon / window frame with curtain and berry plant",
                    "Glow Bowl / bowl of water reflecting crescent moon",
                    "Solstice Letter / envelope sealed with strawberry wax mark",
                    "After Sunset / candle, book, and berry tea arrangement",
                    "Keep The Light / lantern with sun and moon charms",
                ],
            },
        ],
    },
    {
        "topic_name": "Summerween Pool Party Spooks",
        "query": "Summerween 2026 trend summer Halloween pool party",
        "hot_score": 80,
        "region": "US",
        "evidence_urls": [
            "https://trends.google.com/trends/",
            "https://www.pinterest.com/pinterestpredicts/",
            "https://www.tiktok.com/discover/summerween",
        ],
        "theme_summary": "Summerween keeps circulating as a social-friendly seasonal mashup. This pack uses generic summer Halloween motifs: pool ghosts, sunscreen pumpkins, popsicles, bats, and beach-party spookiness without IP references.",
        "series_name": "Summerween Pool Party Spooks",
        "pack_archetype": "seasonal_humor_pack",
        "priority": "medium",
        "palette": "pumpkin orange, pool aqua, black, ghost white, neon lime, hot pink, sunny yellow, white die-cut border",
        "style_anchor": "Cute summer Halloween sticker sheet, playful vector-kawaii style, pool party ghosts, pumpkins with sunglasses, spooky popsicles, bats at the beach, bright aqua and pumpkin palette, no copyrighted characters, no gore, no horror realism.",
        "preview_briefs": [
            {
                "preview_idx": 1,
                "theme": "pool party ghosts",
                "stickers": [
                    "Pool Ghoul / friendly ghost floating in aqua pool ring",
                    "Boo At The Pool / ghost towel badge with water droplets",
                    "Float Or Fright / pumpkin-orange inflatable with tiny bat",
                    "Sunblock Spirit / ghost applying sunscreen with sunglasses",
                    "Deep End Boo / pool ladder with playful ghost peeking out",
                    "Haunted Cabana / striped cabana with ghost sheet curtain",
                    "Lifeguard Phantom / whistle and float board with ghost face",
                    "Splash Scare / ghost jumping into pool with aqua splash",
                    "Cannonboo / funny ghost cannonball pose, no gore",
                    "Poolside Poltergeist / drink cup with ghost straw topper",
                ],
            },
            {
                "preview_idx": 2,
                "theme": "pumpkin beach day",
                "stickers": [
                    "Pumpkin Sunscreen / jack-o-lantern wearing sunblock stripe",
                    "Beach-O-Lantern / pumpkin in sunglasses on beach towel",
                    "Sandcastle Pumpkin / sandcastle shaped like a pumpkin bucket",
                    "Trick Or Tiki / tiki drink with pumpkin umbrella, no brand",
                    "Pumpkin Tan Lines / cute pumpkin with floatie ring",
                    "Surfing Pumpkin / pumpkin riding wave with bat trail",
                    "Spooky Shells / seashells with tiny pumpkin faces",
                    "Beach Bucket Boo / orange sand bucket with ghost sticker",
                    "Coconut Lantern / coconut drink with carved smile",
                    "Pumpkin Flip-Flops / orange flip-flops with black star charms",
                ],
            },
            {
                "preview_idx": 3,
                "theme": "spooky frozen treats",
                "stickers": [
                    "Boo Popsicle / ghost-shaped popsicle melting happily",
                    "Monster Slush / lime green slush cup with bat straw",
                    "Candy Corn Cone / ice cream cone in candy corn colors",
                    "Haunted Snow Cone / snow cone with tiny ghost sprinkles",
                    "Bat Bite Bar / chocolate ice cream bar with bat wings",
                    "Pumpkin Sorbet / orange scoop with jack-o face",
                    "Scream Cream / playful ice cream tub with lightning label",
                    "Witchy Lemonade / yellow lemonade with black star straw",
                    "Spooky Snack Tray / fruit tray with ghost toothpicks",
                    "Brain Freeze Boo / friendly ghost holding frozen drink",
                ],
            },
            {
                "preview_idx": 4,
                "theme": "summer bat accessories",
                "stickers": [
                    "Bat Sunglasses / wing-shaped sunglasses with aqua lenses",
                    "Beach Bat / cute bat holding tiny sunscreen bottle",
                    "Spooky Visor / orange visor with black bat patch",
                    "Goth Beach Bag / tote bag with towel, shells, and bat charm",
                    "Cemetery Sandals / black sandals with tiny pumpkin charms",
                    "Bat Floatie / bat-wing pool float with lime highlights",
                    "Shade Creature / beach umbrella with tiny hanging bats",
                    "Bats At Sunset / sunset badge with small bat silhouettes",
                    "Summer Goth Kit / sunscreen, fan, and black nail polish icons",
                    "Night Swim Bat / bat over moonlit pool ripple",
                ],
            },
            {
                "preview_idx": 5,
                "theme": "summerween party labels",
                "stickers": [
                    "Summerween / bold aqua and orange text with bat confetti",
                    "Half Beach Half Boo / split sun and moon badge",
                    "Too Hot To Haunt / funny ghost melting under sun",
                    "Spooky But Sweaty / text badge with fan and ghost",
                    "Pool Party From Beyond / ticket-style party label",
                    "Boo Crew Beach Day / group of tiny abstract ghosts with beach ball",
                    "Haunted Heatwave / thermometer with ghost face and stars",
                    "Trick Or Treat Yourself / popsicle and pumpkin badge",
                    "Creep It Cool / cooler box with bat stickers",
                    "October Can Wait / pumpkin in sunglasses with beach towel",
                ],
            },
        ],
    },
]


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def series_payload_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "series": [
            {
                "series_name": spec["series_name"],
                "style_anchor": spec["style_anchor"],
                "palette": spec["palette"],
                "pack_archetype": spec["pack_archetype"],
                "priority": spec["priority"],
                "positioning_cn": spec["theme_summary"],
                "title_en": spec["series_name"],
                "target_audience_en": "US buyers looking for timely, non-licensed seasonal and social-culture sticker sheets.",
                "preview_briefs": spec["preview_briefs"],
                "manual": True,
            }
        ]
    }


def metadata_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "positioning_cn": spec["theme_summary"],
        "title_en": spec["series_name"],
        "target_audience_en": "US buyers looking for timely, non-licensed seasonal and social-culture sticker sheets.",
        "preview_briefs": spec["preview_briefs"],
        "manual": True,
        "source": SOURCE,
        "evidence_urls": spec["evidence_urls"],
        "recommended_total_stickers": TARGET_STICKERS,
    }


def config_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "manual_recent_hot_topics": True,
        "seed_source": SOURCE,
        "previews_per_series": TARGET_PREVIEWS,
        "stickers_per_preview": STICKERS_PER_PREVIEW,
        "total_stickers": TARGET_STICKERS,
        "evidence_urls": spec["evidence_urls"],
    }


def seed_topic(conn: sqlite3.Connection, spec: dict[str, Any]) -> dict[str, int | bool]:
    now = int(time.time())
    row = conn.execute(
        "SELECT id FROM hot_topics WHERE source = ? AND topic_name = ?",
        (SOURCE, spec["topic_name"]),
    ).fetchone()
    if row:
        topic_id = int(row["id"])
        topic_created = False
        conn.execute(
            """
            UPDATE hot_topics
               SET query = ?, raw_payload = ?, evidence_urls = ?,
                   hot_score = ?, region = ?, status = 'selected',
                   theme_summary = ?
             WHERE id = ?
            """,
            (
                spec["query"],
                json.dumps({"curated_reason": spec["theme_summary"], "seed_script": Path(__file__).name}, ensure_ascii=False),
                json.dumps(spec["evidence_urls"], ensure_ascii=False),
                float(spec["hot_score"]),
                spec["region"],
                spec["theme_summary"],
                topic_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO hot_topics
                (source, query, topic_name, raw_payload, evidence_urls,
                 hot_score, region, fetched_at, status, theme_summary,
                 parent_topic_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'selected', ?, '[]')
            """,
            (
                SOURCE,
                spec["query"],
                spec["topic_name"],
                json.dumps({"curated_reason": spec["theme_summary"], "seed_script": Path(__file__).name}, ensure_ascii=False),
                json.dumps(spec["evidence_urls"], ensure_ascii=False),
                float(spec["hot_score"]),
                spec["region"],
                now,
                spec["theme_summary"],
            ),
        )
        topic_id = int(cur.lastrowid)
        topic_created = True

    config = config_for(spec)
    payload = series_payload_for(spec)
    existing_plan = conn.execute(
        """
        SELECT id FROM topic_plans
         WHERE topic_id = ?
           AND config LIKE ?
         ORDER BY id DESC LIMIT 1
        """,
        (topic_id, f"%{SOURCE}%"),
    ).fetchone()
    if existing_plan:
        plan_id = int(existing_plan["id"])
        plan_created = False
        conn.execute(
            """
            UPDATE topic_plans
               SET config = ?, main_raw_text = ?, series_payload = ?,
                   status = 'approved', updated_at = ?
             WHERE id = ?
            """,
            (
                json.dumps(config, ensure_ascii=False),
                spec["theme_summary"],
                json.dumps(payload, ensure_ascii=False),
                now,
                plan_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO topic_plans
                (topic_id, config, main_raw_text, series_payload, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 'approved', ?, ?)
            """,
            (
                topic_id,
                json.dumps(config, ensure_ascii=False),
                spec["theme_summary"],
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        plan_id = int(cur.lastrowid)
        plan_created = True

    metadata = metadata_for(spec)
    existing_series = conn.execute(
        "SELECT id FROM pack_series WHERE plan_id = ? AND series_name = ?",
        (plan_id, spec["series_name"]),
    ).fetchone()
    if existing_series:
        series_id = int(existing_series["id"])
        series_created = False
        conn.execute(
            """
            UPDATE pack_series
               SET style_anchor = ?, palette = ?, pack_archetype = ?,
                   priority = ?, metadata_json = ?, is_selected = 1
             WHERE id = ?
            """,
            (
                spec["style_anchor"],
                spec["palette"],
                spec["pack_archetype"],
                spec["priority"],
                json.dumps(metadata, ensure_ascii=False),
                series_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO pack_series
                (plan_id, series_idx, series_name, style_anchor, palette,
                 pack_archetype, priority, metadata_json, is_selected)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                plan_id,
                spec["series_name"],
                spec["style_anchor"],
                spec["palette"],
                spec["pack_archetype"],
                spec["priority"],
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        series_id = int(cur.lastrowid)
        series_created = True

    refresh_preview_rows(conn, series_id, spec)
    return {
        "topic_id": topic_id,
        "plan_id": plan_id,
        "series_id": series_id,
        "topic_created": topic_created,
        "plan_created": plan_created,
        "series_created": series_created,
    }


def refresh_preview_rows(conn: sqlite3.Connection, series_id: int, spec: dict[str, Any]) -> None:
    for brief in spec["preview_briefs"]:
        preview_idx = int(brief["preview_idx"])
        prompt_text = build_preview_prompt(
            style_anchor=spec["style_anchor"],
            palette=spec["palette"],
            preview_theme=str(brief["theme"]),
            stickers=list(brief["stickers"]),
        )
        row = conn.execute(
            "SELECT id, image_path, generation_status FROM pack_previews WHERE series_id = ? AND preview_idx = ?",
            (series_id, preview_idx),
        ).fetchone()
        if row:
            if row["image_path"]:
                conn.execute(
                    "UPDATE pack_previews SET prompt_text = ? WHERE id = ?",
                    (prompt_text, int(row["id"])),
                )
            else:
                conn.execute(
                    """
                    UPDATE pack_previews
                       SET prompt_text = ?, generation_status = 'pending',
                           model_used = '', generated_at = NULL
                     WHERE id = ?
                    """,
                    (prompt_text, int(row["id"])),
                )
        else:
            conn.execute(
                """
                INSERT INTO pack_previews
                    (series_id, preview_idx, prompt_text, image_path,
                     model_used, generation_status, generated_at)
                VALUES (?, ?, ?, '', '', 'pending', NULL)
                """,
                (series_id, preview_idx, prompt_text),
            )

    valid_idxs = tuple(int(b["preview_idx"]) for b in spec["preview_briefs"])
    placeholders = ",".join("?" for _ in valid_idxs)
    conn.execute(
        f"""
        DELETE FROM pack_previews
         WHERE series_id = ?
           AND image_path = ''
           AND preview_idx NOT IN ({placeholders})
        """,
        (series_id, *valid_idxs),
    )


def current_preview_summary(conn: sqlite3.Connection, series_id: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT generation_status, COUNT(*)
          FROM pack_previews
         WHERE series_id = ?
         GROUP BY generation_status
        """,
        (series_id,),
    ).fetchall()
    out = {"total": 0, "ok": 0, "pending": 0, "error": 0, "generating": 0}
    for status, count in rows:
        out["total"] += int(count)
        out[str(status)] = int(count)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-generate", action="store_true", help="Only seed database rows and prompts.")
    parser.add_argument("--skip-packs", action="store_true", help="Do not promote series into packs.")
    parser.add_argument("--size", default="1024x1024")
    args = parser.parse_args()

    seeded: list[dict[str, Any]] = []
    with open_db() as conn:
        for spec in TOPICS:
            item = dict(seed_topic(conn, spec))
            item["series_name"] = spec["series_name"]
            seeded.append(item)
        conn.commit()

    preview_service = PreviewGenService()
    pack_service = get_pack_service()
    for item in seeded:
        series_id = int(item["series_id"])
        prep = preview_service.prepare_previews(series_id)
        item["prepare"] = prep
        if not args.skip_generate:
            item["generate"] = preview_service.generate_pending_for_series(
                series_id,
                max_workers=1,
                size=args.size,
            )
        if not args.skip_packs:
            try:
                item["pack"] = pack_service.create_pack_from_series(
                    series_id,
                    display_name=str(item["series_name"]),
                    allow_pending_previews=True,
                )
                pack_id = int(item["pack"]["pack_id"])
                item["pack"]["total_stickers"] = pack_service.refresh_total_stickers(pack_id)
            except Exception as exc:
                item["pack_error"] = f"{type(exc).__name__}: {exc}"

    with open_db() as conn:
        for item in seeded:
            item["preview_summary"] = current_preview_summary(conn, int(item["series_id"]))

    print(json.dumps(seeded, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
