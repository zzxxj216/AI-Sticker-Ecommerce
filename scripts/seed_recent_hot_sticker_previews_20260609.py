#!/usr/bin/env python3
"""Seed six non-duplicate curated hot-topic sticker preview packs for June 2026.

Each pack has 50 sticker briefs split into five 10-sticker preview sheets.
Generation uses the existing PreviewGenService path, which routes to gpt-image-2.
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
SOURCE = "manual_curated_recent_20260609"
TARGET_PREVIEWS = 5
STICKERS_PER_PREVIEW = 10
TARGET_STICKERS = TARGET_PREVIEWS * STICKERS_PER_PREVIEW


TOPICS: list[dict[str, Any]] = [
    {
        "topic_name": "WWDC Glass UI Developer Desk",
        "query": "WWDC 2026 AI announcements liquid glass developer conference",
        "hot_score": 93,
        "region": "GLOBAL",
        "evidence_urls": [
            "https://www.apple.com/newsroom/2026/05/apple-kicks-off-worldwide-developers-conference-on-june-8/",
            "https://www.techradar.com/phones/ios/how-to-watch-wwdc-2026",
        ],
        "theme_summary": "WWDC 2026 is running June 8-12 with AI and interface-design conversation. Make this a non-logo developer desk sticker pack with translucent UI, code notes, launch-day caffeine, and glassmorphism objects.",
        "series_name": "WWDC Glass UI Developer Desk",
        "pack_archetype": "tech_event_pack",
        "priority": "high",
        "palette": "frosted white, electric blue, graphite black, neon green, soft cyan, lavender glow, chrome silver",
        "style_anchor": "Futuristic developer desk sticker sheet, translucent glass UI panels, code snippets as abstract symbols, desk gadgets, launch-day energy, crisp vector with glossy depth, no Apple logo, no product likeness, no real app icons, no trademarked OS names.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "keynote watch desk", "stickers": [
                "Keynote Mode / laptop silhouette with frosted UI windows and sparkle cursor",
                "One More Thing? / speech bubble with abstract glass button and tiny stars",
                "Live Notes / notebook with code marks, cyan tabs, and chrome pen",
                "Dev Fuel / iced coffee cup with binary steam and blue straw",
                "Watch Party / small screen frame with glow dots and calendar tag",
                "Beta Brain / brain-shaped chip icon with neon green trace lines",
                "Refresh Feed / circular arrow badge with glass highlight",
                "Conference Week / desk calendar marked June 8-12 without logos",
                "Pixel Confetti / tiny squares bursting from a command key icon",
                "Ship Later / sticky note with progress bar and sleepy moon",
            ]},
            {"preview_idx": 2, "theme": "glass interface objects", "stickers": [
                "Liquid Glass / translucent rounded panel with blue light refraction",
                "Frosted Toggle / glossy switch with cyan glow and shadow edge",
                "Blur Layer / stacked transparent cards with grid lines",
                "Floating Dock / row of generic glass circles, no app symbols",
                "Glow Cursor / arrow cursor leaving lavender light trail",
                "Depth Slider / UI slider with chrome knob and soft highlight",
                "Glass Modal / tiny pop-up window with abstract controls",
                "Soft Shadow / sticker label showing layered UI shadows",
                "Refraction Grid / bent grid pattern inside a clear tile",
                "No Hard Edges / rounded type badge with translucent outline",
            ]},
            {"preview_idx": 3, "theme": "AI developer tools", "stickers": [
                "Prompt Lab / terminal window with star prompt and neon caret",
                "Local Model / small server cube with friendly pulse light",
                "Agent Queue / checklist of tiny robot-task icons, no character IP",
                "Thinking Tokens / coin-like tokens orbiting a command prompt",
                "Context Window / wide glass rectangle with layered text lines",
                "Compile Dreams / moon, code bracket, and loading spinner",
                "Bug Whisperer / small bug icon with magnifier and calm label",
                "Autofill Magic / wand cursor drawing abstract code lines",
                "Model Picker / segmented control with colored dots only",
                "Deploy Brain / rocket-shaped chip with blue exhaust sparks",
            ]},
            {"preview_idx": 4, "theme": "developer survival kit", "stickers": [
                "Do Not Disturb / frosted desk sign with terminal caret",
                "Snack Stack / energy bar, coffee, and cable loop flat lay",
                "Cable Nest / tidy coil of white cables with chrome sparkle",
                "Release Notes / folded paper with abstract bullets and blue tabs",
                "Dark Mode Eyes / glasses reflecting cyan UI panels",
                "Patch Day / bandage sticker over tiny code window",
                "Keyboard Sprint / command keys with motion lines",
                "Standup Later / sticky note with tiny clock and mug",
                "Build Passing / green check badge inside glass tile",
                "Feature Freeze / snowflake over translucent code card",
            ]},
            {"preview_idx": 5, "theme": "launch week memes", "stickers": [
                "It Works On My Machine / desktop badge with tiny confetti",
                "Beta Season / glass jar full of colorful warning triangles",
                "Please Be Stable / polite text label with folded hands icon",
                "Hot Reload Hope / flame and refresh symbol on frosted card",
                "I Read The Docs / book stack with glowing bookmark",
                "Tiny Breaking Change / small cracked glass tile with sparkle",
                "Launch Week Survivor / ribbon badge with coffee and cursor",
                "Changelog Goblet / trophy cup filled with bullet points",
                "Still Debugging / sleepy moon over terminal window",
                "Ship The Pixels / parcel box with pixel confetti and blue tape",
            ]},
        ],
    },
    {
        "topic_name": "World Ocean Day Reef Guardian Labels",
        "query": "World Ocean Day 2026 Reimagine beyond the world we know",
        "hot_score": 91,
        "region": "GLOBAL",
        "evidence_urls": [
            "https://www.un.org/sustainabledevelopment/blog/2026/06/press-release-as-ocean-pressures-mount-united-nations-report-calls-for-urgent-global-collaboration-to-protect-marine-ecosystems/",
            "https://www.timeanddate.com/holidays/un/oceans-day",
        ],
        "theme_summary": "World Ocean Day is observed June 8 and 2026 coverage emphasizes reimagining our relationship with the ocean. Build reef guardian, cleanup, tidepool, blue-future stickers; distinct from beach road-trip packs.",
        "series_name": "World Ocean Day Reef Guardian Labels",
        "pack_archetype": "eco_badge_pack",
        "priority": "high",
        "palette": "deep ocean blue, coral pink, seafoam green, pearl white, kelp green, sunny yellow, aqua",
        "style_anchor": "Clean eco-ocean sticker sheet, hopeful reef guardian theme, rounded vector-gouache hybrid, coral, kelp, tidepools, reusable bottles, cleanup badges, no scary disaster imagery, no beach vacation road-trip vibe, no official UN logos.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "reef guardian badges", "stickers": [
                "Reef Guardian / coral reef badge with tiny fish and pearl bubbles",
                "Reimagine Ocean / wave-shaped text label with seafoam highlights",
                "Protect The Blue / shield badge with coral branch and kelp leaves",
                "Tiny Reef Big Future / small coral garden under sun rays",
                "Ocean Ally / round patch with turtle silhouette and bubbles",
                "No Trash Tides / crossed-out bottle floating above clean wave",
                "Blue Planet Friend / globe icon with ocean heart center",
                "Coral Comeback / coral sprout with sparkle growth marks",
                "Tide Team / three abstract wave characters with no faces",
                "Keep It Wild / kelp forest badge with tiny starfish",
            ]},
            {"preview_idx": 2, "theme": "cleanup kit", "stickers": [
                "Beach Cleanup Kit / gloves, bag, and reusable bottle icon",
                "Pick It Up / grabber tool holding wrapper with clean check mark",
                "Refill Not Landfill / water bottle with wave label",
                "Trash Bag Hero / tied cleanup bag with blue cape shape",
                "Leave Only Footprints / sandy footprint with tiny shell",
                "Microplastic Patrol / magnifier over tiny dots and wave line",
                "Clean Coast Club / patch with bucket and seafoam bubbles",
                "Bring A Bag / folded reusable tote with coral print",
                "Sort It Shore / three mini bins with ocean icons",
                "Tiny Action Big Tide / small hand holding shell and sparkle",
            ]},
            {"preview_idx": 3, "theme": "tidepool friends", "stickers": [
                "Tidepool Window / clear water circle with anemone and shell",
                "Starfish Slow Day / starfish with gentle motion lines",
                "Hermit Crab Home / crab carrying shell with kelp ribbon",
                "Sea Glass Find / handful of sea glass in aqua and white",
                "Kelp Curl / curled kelp leaf forming a heart",
                "Moon Snail Trail / shell with dotted path and blue shimmer",
                "Little Limpet / tiny shell sticker with pearl outline",
                "Low Tide Notes / notebook with tide chart and pencil",
                "Respect The Pool / signpost with tidepool and heart",
                "Pocket Shells / small pocket spilling shells and bubbles",
            ]},
            {"preview_idx": 4, "theme": "blue future labels", "stickers": [
                "Blue Future / futuristic wave badge with chrome edge",
                "Ocean Optimist / sunny wave with coral-pink rays",
                "New Relationship / two hands holding clear water drop",
                "Vote For Reefs / generic ballot badge with coral check mark",
                "Sea Change / arrow looping through wave and kelp",
                "Climate + Ocean / linked icons of cloud, wave, and leaf",
                "Guard The Current / current-line badge with tiny fish",
                "More Kelp Please / playful text label with kelp sprigs",
                "Restore The Shore / small dune grass and clean wave icon",
                "Future Is Blue / bold rounded text with pearl bubbles",
            ]},
            {"preview_idx": 5, "theme": "ocean classroom", "stickers": [
                "Ocean Facts / clipboard with wave and coral checkboxes",
                "Ask A Scientist / lab flask shaped like a wave, no logos",
                "Marine Map / folded map with reef dots and compass",
                "Water Cycle Notes / cloud, rain, river, and ocean arrows",
                "Tiny Plankton Power / glowing plankton dots in magnifier",
                "Read The Tide / tide chart card with moon and wave",
                "Reef Report / paper sheet with coral graph and sticker stars",
                "Citizen Science / phone camera icon over tidepool, no brand",
                "Blue Notebook / journal with shell bookmark and elastic band",
                "Learn The Current / educational badge with arrows and bubbles",
            ]},
        ],
    },
    {
        "topic_name": "Basketball Finals Watch Party Stickers",
        "query": "2026 NBA Finals schedule June 2026 watch party",
        "hot_score": 89,
        "region": "US",
        "evidence_urls": [
            "https://cdn-uat.nba.com/news/2026-nba-finals-schedule",
            "https://www.kiplinger.com/personal-finance/family-savings/how-to-watch-the-nba-finals",
        ],
        "theme_summary": "The 2026 basketball finals are active in June. Avoid team names, league marks, and player likenesses; build a generic finals watch-party pack with brackets, snacks, couches, scoreboards, and fan energy.",
        "series_name": "Basketball Finals Watch Party Stickers",
        "pack_archetype": "sports_watch_party_pack",
        "priority": "high",
        "palette": "orange basketball, black, white, arena gold, electric blue, popcorn yellow, court wood tan",
        "style_anchor": "Energetic basketball watch-party sticker sheet, generic finals atmosphere, snack table, couch fans, scoreboards, brackets, court lines, bold vector illustration, no NBA logos, no team names, no player likenesses, no trademarked uniforms.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "watch party table", "stickers": [
                "Game Night / basketball on snack table with glow from TV",
                "Finals Snacks / popcorn bucket, nachos, and tiny basketball picks",
                "Couch Coach / couch cushion with clipboard and whistle",
                "Big Screen Energy / TV silhouette with court reflection",
                "Timeout Snacks / timer badge with chips and dip",
                "Remote Control MVP / remote with basketball button icons",
                "Halftime Refill / soda cup and popcorn refill label",
                "No Spoilers / phone face-down with warning sticker",
                "Living Room Arena / couch, rug court lines, and confetti",
                "Watch Party Crew / pennant label with generic basketball icon",
            ]},
            {"preview_idx": 2, "theme": "court and scoreboard", "stickers": [
                "Clutch Time / scoreboard showing abstract 4th quarter, no teams",
                "Buzzer Beater / ball arcing toward hoop with star trail",
                "Full Court Mood / court diagram badge with orange arrows",
                "Shot Clock Stress / digital clock icon with sweat drops",
                "Overtime Please / big text label with hoop and sparks",
                "Fast Break / sneaker and motion line over court wood",
                "Swish Only / net with clean orange swoosh line",
                "Box Score Nerd / stat sheet with generic bars and check marks",
                "Bracket Brain / bracket diagram wrapped around basketball",
                "Home Court Couch / couch on tiny court baseline",
            ]},
            {"preview_idx": 3, "theme": "fan reactions", "stickers": [
                "I Can't Look / foam finger covering eyes, no team color",
                "Refs Please / whistle with comic burst and stars",
                "That Was A Foul / speech bubble with basketball texture",
                "Heat Check / flame badge around basketball",
                "Cold From Three / icy three-point line badge",
                "Bench Mob / row of abstract cheering seats, no faces",
                "Living Room Timeout / stop sign with couch and snacks",
                "Nail Biter / bitten popcorn kernel and tense clock",
                "Clap Clap Defense / text sticker with court lines",
                "My Heart Is Overtime / heart-shaped ball with timer",
            ]},
            {"preview_idx": 4, "theme": "basketball food labels", "stickers": [
                "Dunk Dip / dip bowl with hoop rim and chips",
                "Three-Point Pizza / pizza slice with three-dot trail",
                "Free Throw Fries / fries cup with court stripe label",
                "Popcorn Press / popcorn bucket with basketball print",
                "Slam Dunk Soda / soda cup with orange splash",
                "Wing Zone / chicken wing badge with court arrows",
                "Crunch Time / chips bag with clock icon",
                "Halftime Hot Dog / generic hot dog with orange pennant",
                "Snack Bracket / tournament-style snack chart",
                "Game Seven Guac / guacamole bowl with star confetti",
            ]},
            {"preview_idx": 5, "theme": "finals night keepsakes", "stickers": [
                "Finals Night / ticket-stub style label without league name",
                "Series Diary / notebook with basketball bookmark",
                "Game 7 Energy / bold badge with lightning and ball",
                "Lucky Socks / striped socks with tiny basketball charm",
                "Rally Towel / blank towel waving with confetti",
                "Prediction Card / small card with checkboxes and hoop icon",
                "Arena At Home / living-room lamp as arena spotlight",
                "Postgame Mood / sleepy couch with basketball blanket",
                "Championship Snacks / trophy-shaped snack tray, no league mark",
                "See You Next Game / calendar sticker with ball and stars",
            ]},
        ],
    },
    {
        "topic_name": "Broadway Awards Theater Night Pack",
        "query": "Tony Awards 2026 Broadway awards June 7 2026",
        "hot_score": 86,
        "region": "US",
        "evidence_urls": [
            "https://www.tonyawards.com/news/152-project-jake-bell-kenn-lubin-and-loren-plotkin-to-receive-the-2026-tony-honors-for-excellence-in-the-theatre/",
            "https://apnews.com/article/5774d8b78360e0ca2861e6b37e30ffac",
        ],
        "theme_summary": "Broadway awards discussion is current after the June 7 ceremony. Avoid Tony branding and named productions; make a generic theater-night pack with stage lights, playbills, costumes, backstage notes, and drama-club energy.",
        "series_name": "Broadway Awards Theater Night Pack",
        "pack_archetype": "culture_event_pack",
        "priority": "medium",
        "palette": "theater red, marquee gold, black, ivory, velvet purple, spotlight white, rose pink",
        "style_anchor": "Broadway-inspired theater night sticker sheet, glamorous stage objects, backstage tools, award-season mood, vintage marquee styling, no Tony logo, no named shows, no celebrity likenesses, no copyrighted production references.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "marquee night", "stickers": [
                "Theater Night / glowing marquee sign with generic stars",
                "Best Seat / red velvet theater chair with gold ticket",
                "Curtain Call / red curtains opening with spotlight beams",
                "Opening Night / ticket stub with rose and confetti",
                "Marquee Glow / row of warm bulbs around blank sign",
                "Dress Code Drama / bow tie and pearl earring flat lay",
                "Playbill Stack / generic program stack with no title",
                "Balcony View / small balcony rail with spotlight haze",
                "Standing Ovation / clapping hands silhouette with stars",
                "Stage Door / door sign with gold star and flower",
            ]},
            {"preview_idx": 2, "theme": "backstage kit", "stickers": [
                "Backstage Pass / blank lanyard with gold star",
                "Quick Change / hanger, scarf, and timer badge",
                "Prop Table / labeled table with rose, cup, and notebook",
                "Mic Check / headset mic with spotlight sparkle",
                "Cue Sheet / clipboard with cues and red pencil",
                "Dressing Room Glow / mirror bulbs and lipstick mark",
                "Places Please / stage manager call bubble",
                "Costume Rack / rolling rack with generic velvet pieces",
                "Stage Tape / tape roll with gold star sticker",
                "Green Room Snacks / tea cup, crackers, and script pages",
            ]},
            {"preview_idx": 3, "theme": "award season objects", "stickers": [
                "Envelope Please / sealed envelope with gold star",
                "Winner Energy / generic trophy silhouette with confetti",
                "Nominee Notes / card stack with pencil and rose",
                "Red Carpet Pocket / clutch bag, ticket, and lipstick",
                "Speech Draft / folded paper with tiny tear and sparkle",
                "Afterparty Shoes / heels and dress shoes under confetti",
                "Golden Applause / clapping hands inside gold circle",
                "Big Night / ribbon badge with spotlight and stars",
                "Seat Card / place card with theater mask icon",
                "Award Watch Party / TV frame with theater curtains",
            ]},
            {"preview_idx": 4, "theme": "drama club humor", "stickers": [
                "Theater Kid Forever / bold label with tiny comedy mask",
                "Too Dramatic? / speech bubble with red curtain edge",
                "I Need A Spotlight / mini spotlight on wheels",
                "Projection Voice / megaphone with star bursts",
                "Act Two Energy / coffee cup with stage-light steam",
                "No Small Parts / ensemble badge with tiny shoes",
                "Cue The Tears / tissue box with gold stars",
                "Understudy Ready / script with backup tab",
                "Drama Is My Cardio / tap shoe with motion lines",
                "Five Minute Call / alarm clock with curtain tassel",
            ]},
            {"preview_idx": 5, "theme": "stage craft details", "stickers": [
                "Light Board / console sliders with colored gels",
                "Gel Swatch / fan of spotlight color filters",
                "Set Model / tiny stage model with cardboard flats",
                "Sound Booth / headphones with music-wave line",
                "Fly Rail Magic / ropes and pulleys with gold sparkles",
                "Painted Flat / scenic wall panel with brush",
                "Orchestra Pit / music stand with rose and bow",
                "Stage Dust / broom with glitter and black handle",
                "Spotlight Cone / white beam cutting through purple haze",
                "Strike The Set / toolbox and gloves badge",
            ]},
        ],
    },
    {
        "topic_name": "Nashville Country Fest Street Party",
        "query": "CMA Fest 2026 Nashville June 4-7 country music festival",
        "hot_score": 84,
        "region": "US",
        "evidence_urls": [
            "https://cmafest.com/",
            "https://www.axios.com/local/nashville/2026/02/25/cma-fest-2026-headliners-tim-mcgraw-keith-urban",
        ],
        "theme_summary": "CMA Fest ran June 4-7 in Nashville. Avoid artist names, CMA marks, and venue logos; make a generic country-music street-party pack with boots, guitars, riverfront stages, clear-bag rules, and festival wristbands.",
        "series_name": "Nashville Country Fest Street Party",
        "pack_archetype": "music_festival_pack",
        "priority": "medium",
        "palette": "denim blue, neon pink, warm gold, black, white, guitar brown, turquoise, sunset orange",
        "style_anchor": "Modern country music festival sticker sheet, Nashville street-party energy, boots, guitars, denim, neon signs, wristbands, summer night stage lights, no CMA logo, no artist names, no venue logos, no cowboy-western rodeo theme.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "festival street kit", "stickers": [
                "Country Fest Weekend / denim patch badge with guitar pick",
                "Wristband Stack / colorful festival wristbands on hand silhouette",
                "Clear Bag Crew / transparent bag with sunscreen and lip balm",
                "Downtown Stage Hop / map pin trail with music notes",
                "Boots On Broadway / boots stepping through neon confetti",
                "Festival Schedule / folded schedule with star tabs",
                "Meet At The Stage / signpost with guitar and arrows",
                "Sunset Soundcheck / stage lights over orange sky",
                "Hydrate Y'all / water bottle with turquoise label",
                "Four Day Pass / generic pass card with stars, no logo",
            ]},
            {"preview_idx": 2, "theme": "country music objects", "stickers": [
                "Guitar Pick Magic / oversized pick with sparkle strings",
                "Fiddle Break / fiddle with neon pink bow",
                "Steel Guitar Glow / pedal steel silhouette with gold lights",
                "Mic Stand Moment / microphone wrapped in bandana",
                "Setlist Stars / handwritten setlist with no artist names",
                "Acoustic Heart / guitar body shaped like heart",
                "Drum Kick Boots / kick drum with boot print badge",
                "Banjo Sunburst / banjo with orange sun rays",
                "Chorus Ready / lyric notebook with music-note tab",
                "Encore Energy / stage light beam and confetti burst",
            ]},
            {"preview_idx": 3, "theme": "festival fashion", "stickers": [
                "Denim Vest / vest with star patches and turquoise pin",
                "Fringe Bag / small fringe bag with guitar charm",
                "Boot Stitch / embroidered boot close-up with pink flowers",
                "Bandana Stack / folded bandanas in sunset colors",
                "Hat Hair Don't Care / wide-brim hat with tiny fan",
                "Rhinestone Ready / sunglasses with star rhinestones",
                "Neon Belt Buckle / generic buckle with music note",
                "Summer Boots / ankle boots beside sunscreen tube",
                "Festival Fit Check / mirror with denim and boots silhouette",
                "Sparkle Spurs / decorative spur charm without rodeo imagery",
            ]},
            {"preview_idx": 4, "theme": "Nashville night snacks", "stickers": [
                "Hot Chicken Break / generic spicy sandwich with music note toothpick",
                "Sweet Tea Refill / iced tea cup with lemon and stars",
                "Late Night Fries / fries carton with guitar pick sticker",
                "Food Truck Line / tiny truck with blank sign and neon stars",
                "Riverfront Lemonade / lemonade jar with bridge line silhouette",
                "After Show Tacos / taco tray with confetti",
                "Snack Setlist / snack checklist shaped like concert card",
                "Cooler Crew / small cooler with star stickers",
                "Pre-Show Pretzel / pretzel with pink neon outline",
                "Midnight Sundae / ice cream cup with guitar-pick spoon",
            ]},
            {"preview_idx": 5, "theme": "street party labels", "stickers": [
                "Nashville Nights / neon-style text label, no official logo",
                "Sing It Loud / speech bubble with microphone and stars",
                "Front Row Friend / ribbon badge with guitar strings",
                "Boot Scoot Later / playful text with motion-line boots",
                "Stage Hop Club / oval patch with arrows and music notes",
                "No Bad Seats / folding chair with sparkle lights",
                "Summer Setlist / sun and guitar label",
                "Country Crowd / abstract hands and hats, no faces",
                "One More Song / text badge with tiny encore lights",
                "Neon On Repeat / pink neon sign sticker with star border",
            ]},
        ],
    },
    {
        "topic_name": "Run Club Meet-Cute Morning Miles",
        "query": "2026 run club trend Gen Z dating apps social running",
        "hot_score": 82,
        "region": "US/UK",
        "evidence_urls": [
            "https://www.axios.com/local/des-moines/2026/04/23/gen-z-is-turning-run-clubs-into-social-spaces-in-dsm",
            "https://www.vice.com/en/article/gen-z-are-using-these-4-hobbies-to-find-love-instead-of-downloading-dating-apps/",
        ],
        "theme_summary": "Run clubs continue to be covered as Gen Z social spaces and app-fatigue meet-cutes. Make a summer morning miles pack with shoes, coffee, pace groups, no app logos, and friendly social fitness energy.",
        "series_name": "Run Club Meet-Cute Morning Miles",
        "pack_archetype": "social_fitness_pack",
        "priority": "medium",
        "palette": "sunrise orange, electric blue, lime green, asphalt gray, white, blush pink, hydration teal",
        "style_anchor": "Friendly social run club sticker sheet, sunrise city runs, pace groups, coffee after miles, meet-cute energy, sporty vector style with warm analog community feeling, no dating app logos, no sports brand logos, no race official marks.",
        "preview_briefs": [
            {"preview_idx": 1, "theme": "morning miles kit", "stickers": [
                "Morning Miles / sunrise badge with running shoe and steam lines",
                "Run Club Roll Call / clipboard with pace checkboxes",
                "Coffee After / iced coffee cup with tiny sneaker charm",
                "Pace Group / three colored dots on route line",
                "Laces Ready / tied shoe laces forming a heart",
                "Hydration Check / teal bottle with sunrise sticker",
                "Meet At 7 / alarm clock with running shoe icon",
                "Warm Up Circle / abstract circle of shoes, no faces",
                "Easy Pace / calm text badge with cloud and shoe",
                "Post-Run Glow / towel, coffee, and little sun",
            ]},
            {"preview_idx": 2, "theme": "meet-cute running", "stickers": [
                "Accidental Pace Match / two route lines meeting in heart shape",
                "Asked For My Route / map pin with blush-pink sparkle",
                "No Swipe Just Stride / text label with shoe and arrow",
                "Cute Runner Ahead / generic road sign with heart",
                "Shared Water Stop / two cups at aid table, no people",
                "Long Run Chemistry / beaker with sneaker and heart bubbles",
                "See You Next Lap / track oval with tiny stars",
                "Bib Number Crush / blank bib with heart pin",
                "Playlist Exchange / headphones and music-note card",
                "Same Pace Energy / matching shoe icons side by side",
            ]},
            {"preview_idx": 3, "theme": "city route stickers", "stickers": [
                "Neighborhood Loop / simple city route map with sunrise",
                "Bridge Sprint / bridge silhouette with motion lines",
                "Park Lap / park path with trees and shoe prints",
                "Corner Water Stop / hydrant, cup, and route arrow",
                "Asphalt Sunrise / road texture badge with sun glow",
                "Stoop Stretch / front steps with stretching shoe icon",
                "Bus Stop Start / transit sign with running arrow, no city logo",
                "Finish At Cafe / cafe awning with shoe and coffee",
                "Mile Marker 3 / marker post with lime green flag",
                "Run The Block / block party-style route label",
            ]},
            {"preview_idx": 4, "theme": "runner bag essentials", "stickers": [
                "Gel Pocket / tiny gel packets with no brand names",
                "Safety Pin Set / race pins forming a star",
                "Sweatband Stack / colorful wristbands with sunrise dots",
                "Mini Towel / rolled towel with electric blue stripe",
                "Recovery Socks / socks with lime lightning bolt",
                "Phone Arm Band / generic armband with route line",
                "Cap And Shades / running cap and sunglasses flat lay",
                "Foam Roller Friend / foam roller with smile-free sparkle",
                "Stretch Strap / teal strap loop with motion lines",
                "Tiny First Aid / bandage kit with sneaker sticker",
            ]},
            {"preview_idx": 5, "theme": "run club slogans", "stickers": [
                "Miles Before Messages / bold text with phone on silent",
                "Pace Not Pressure / rounded label with shoe print",
                "Slow Group Strong / ribbon badge with sunrise icon",
                "Run First Flirt Later / playful text with heart route",
                "Community Miles / linked route dots and stars",
                "PR In Friendship / stopwatch with heart sparkle",
                "Sweaty But Social / towel badge with coffee cup",
                "Found My People / route map forming a circle",
                "Meet-Cute Mile / mile marker with blush heart",
                "See You Saturday / calendar page with shoe and sun",
            ]},
        ],
    },
]


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def series_payload_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {"series": [{
        "series_name": spec["series_name"],
        "style_anchor": spec["style_anchor"],
        "palette": spec["palette"],
        "pack_archetype": spec["pack_archetype"],
        "priority": spec["priority"],
        "positioning_cn": spec["theme_summary"],
        "title_en": spec["series_name"],
        "target_audience_en": "US buyers looking for timely, non-licensed event and culture sticker sheets.",
        "preview_briefs": spec["preview_briefs"],
        "manual": True,
    }]}


def metadata_for(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "positioning_cn": spec["theme_summary"],
        "title_en": spec["series_name"],
        "target_audience_en": "US buyers looking for timely, non-licensed event and culture sticker sheets.",
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
            "SELECT id, image_path FROM pack_previews WHERE series_id = ? AND preview_idx = ?",
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


def update_pack_cover(pack_id: int) -> None:
    with open_db() as conn:
        conn.execute(
            """
            UPDATE packs
               SET cover_image_path = (
                   SELECT pp.image_path
                     FROM pack_previews pp
                    WHERE pp.series_id = packs.series_id
                      AND pp.preview_idx = 1
                      AND pp.image_path != ''
                    LIMIT 1
               )
             WHERE id = ?
            """,
            (pack_id,),
        )
        conn.commit()


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
                update_pack_cover(pack_id)
            except Exception as exc:
                item["pack_error"] = f"{type(exc).__name__}: {exc}"

    with open_db() as conn:
        for item in seeded:
            item["preview_summary"] = current_preview_summary(conn, int(item["series_id"]))

    print(json.dumps(seeded, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
