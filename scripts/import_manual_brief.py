#!/usr/bin/env python3
"""Parse docs/ChatGPT-热点贴纸题材推荐.md into the V2 pipeline tables.

Maps the doc's three layers into our schema:

  ┌─────────────────────────┬──────────────────────────────────────┐
  │ doc section             │ V2 table(s)                          │
  ├─────────────────────────┼──────────────────────────────────────┤
  │ ## 8 个题材 (broad)     │ hot_topics × 8 (source='manual_brief│
  │                         │ ', status='selected', hot_score from │
  │                         │ priority order)                      │
  │                         │                                      │
  │ ## 5包内容设计方案      │ topic_plans × 1 (linked to the       │
  │  (detailed)             │ "Summer Camping" hot_topic since the │
  │                         │ 5 detailed packs all expand it)      │
  │                         │ + pack_series × 5                    │
  │                         │                                      │
  │ ### Prompt N-M (30 ea)  │ pack_previews × 30 with prompt_text  │
  │                         │ pre-populated (status='pending')     │
  └─────────────────────────┴──────────────────────────────────────┘

Idempotent: re-runs skip rows that already exist (matched on
``hot_topics.topic_name == BROAD_THEMES[i].title`` and
``topic_plans`` whose config carries ``manual_brief: true``).

Usage:
    python scripts/import_manual_brief.py
    python scripts/import_manual_brief.py --regenerate-previews   # also wipe
                                                                  # existing
                                                                  # prompts
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# Make `src.*` imports work when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DOC_PATH = Path("docs/ChatGPT-热点贴纸题材推荐.md")
DB_PATH = Path("data/ops_workbench.db")

# ------------------------------------------------------------------------- #
# Hand-curated mapping for the 8 broad themes (lines 147-272 of the doc)
# We tag the priority from the "我最建议你现在先做这 4 包" recap.
# ------------------------------------------------------------------------- #

PRIORITY_RANK = {  # 1=high, 2=medium-high, 3=medium, 4=med-low, 5=low
    "Mother's Day Growth Story Sticker Pack":      1,
    "Class of 2026 Graduation Sticker Pack":       2,
    "Teacher Appreciation Sticker Pack":           3,
    "World Soccer Fan Carnival Sticker Pack":      4,
    "Summer Camping / Road Trip Sticker Pack":     5,  # has detailed plan!
    "Mental Health / Self Care Sticker Pack":      6,
    "Gardening / Plant Mom Sticker Pack":          7,
    "Nurses Week / Healthcare Hero Sticker Pack":  8,
}

# ------------------------------------------------------------------------- #
# Parsers
# ------------------------------------------------------------------------- #

THEME_HEADER = re.compile(r"^### \d+\. (.+?)\s*$", re.M)


def parse_broad_themes(doc: str) -> list[dict]:
    """Pull the 8 broad themes from §147-272."""
    section = _slice(doc, "## 我建议优先做这 8 个题材", "## 我最建议你现在先做这 4 包")
    themes: list[dict] = []
    matches = list(THEME_HEADER.finditer(section))
    for i, m in enumerate(matches):
        title = m.group(1).strip().rstrip().rstrip("·").rstrip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        body = section[body_start:body_end].strip().rstrip("-").strip()
        # First non-empty line is usually the Chinese subtitle (in **)
        cn_subtitle = ""
        for line in body.splitlines():
            s = line.strip().strip("*").strip()
            if s and s != "":
                cn_subtitle = s
                break
        themes.append({
            "title":       title,
            "cn_subtitle": cn_subtitle,
            "body":        body,
            "rank":        PRIORITY_RANK.get(title, 99),
        })
    return themes


# Detailed pack section headers — match EXACTLY "## 方案N" (not the later "# 方案N"
# blocks which contain the ready prompts).
PACK_HEADER = re.compile(r"^## 方案(\d+)[：:]\s*(.+?)\s*$", re.M)


def _strip_pack_suffix(name: str) -> str:
    """Strip a trailing ' Sticker Pack' if present, then re-append cleanly."""
    name = name.strip()
    suffix = "Sticker Pack"
    if name.endswith(suffix):
        return name[: -len(suffix)].strip() + " Sticker Pack"
    return name + (" Sticker Pack" if not name.endswith("Pack") else "")


def parse_detailed_packs(doc: str) -> list[dict]:
    """Pull each '## 方案N' detailed design block.

    Restricted to the §304–880 'detailed design' region so we don't double-
    parse the later '# 方案N' prompt-block headers.
    """
    section = _slice(doc, "## 5包内容设计方案", "## 总览对比表",
                     soft_end=False)
    if not section:
        # The actual section header in this doc is different — fall back to
        # next H1-prompt-block header.
        section = _slice(doc, "## 5包内容设计方案", "\n# 方案", soft_end=True)
    packs: list[dict] = []
    matches = list(PACK_HEADER.finditer(section))
    for i, m in enumerate(matches):
        idx = int(m.group(1))
        name = _strip_pack_suffix(m.group(2))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        body = section[body_start:body_end].strip()
        packs.append({
            "idx":        idx,
            "name":       name,
            "name_cn":    _between(body, "### 中文名：", "###").strip().lstrip("*").strip(" *"),
            "audience":   _between(body, "### 适合用户：", "###").strip(),
            "style":      _between(body, "### 整体风格：", "###").strip(),
            "structure":  _between(body, "### 整包内容结构", "### 适合做的卖点").strip(),
            "selling":    _between(body, "### 适合做的卖点：", "##").strip(),
        })
    return packs


# Per-pack: 6 preview themes + 6 ready prompts. These live in §880-1393.
PACK_PROMPT_BLOCK = re.compile(
    r"^# 方案(\d+)[：:].+?$",
    re.M,
)
PREVIEW_HDR = re.compile(r"^### 预览图(\d+)[：:]\s*(.+?)\s*$", re.M)
PROMPT_HDR  = re.compile(r"^### Prompt (\d+)-(\d+)\s*$", re.M)


def parse_per_pack_previews(doc: str) -> dict[int, list[dict]]:
    """For each pack 1-5, return [{preview_idx, theme, stickers[], prompt}, ...]."""
    # Slice each pack-prompt block. Each starts with "# 方案N" line
    out: dict[int, list[dict]] = {}
    headers = list(PACK_PROMPT_BLOCK.finditer(doc))
    for i, h in enumerate(headers):
        pack_idx = int(h.group(1))
        block_start = h.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(doc)
        block = doc[block_start:block_end]

        # Pull all "### 预览图N: title" → stickers list (lines until next ###)
        previews: dict[int, dict] = {}
        prev_iter = list(PREVIEW_HDR.finditer(block))
        for j, pm in enumerate(prev_iter):
            pidx = int(pm.group(1))
            theme = pm.group(2).strip()
            stickers_start = pm.end()
            # End at next "###" (next preview) OR "## 英文提示词"
            next_anchor = (prev_iter[j + 1].start() if j + 1 < len(prev_iter)
                           else block.find("## 英文提示词", stickers_start))
            stickers_block = block[stickers_start:next_anchor].strip() if next_anchor > 0 else block[stickers_start:].strip()
            stickers = []
            for line in stickers_block.splitlines():
                # Lines like "1. sunset mountain road" or "1. Adventure Awaits"
                m = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
                if m:
                    stickers.append(m.group(1).strip())
            previews[pidx] = {
                "preview_idx": pidx,
                "theme":       theme,
                "stickers":    stickers,
            }

        # Now match prompts and attach them
        for pm in PROMPT_HDR.finditer(block):
            ppack = int(pm.group(1))
            ppidx = int(pm.group(2))
            if ppack != pack_idx or ppidx not in previews:
                continue
            prompt_start = pm.end()
            # Next "### " or end of block
            next_hash = block.find("\n### ", prompt_start)
            if next_hash < 0:
                next_hash = block.find("\n---", prompt_start)
            prompt_text = (block[prompt_start:next_hash].strip()
                           if next_hash > 0 else block[prompt_start:].strip())
            previews[ppidx]["prompt"] = prompt_text

        if previews:
            out[pack_idx] = sorted(previews.values(), key=lambda x: x["preview_idx"])
    return out


# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #

def _slice(text: str, start_marker: str, end_marker: str, *, soft_end: bool = False) -> str:
    s = text.find(start_marker)
    if s < 0:
        return ""
    s += len(start_marker)
    e = text.find(end_marker, s)
    if e < 0:
        if soft_end:
            return text[s:]
        return ""
    return text[s:e]


def _between(text: str, start_marker: str, end_marker: str) -> str:
    s = text.find(start_marker)
    if s < 0:
        return ""
    s += len(start_marker)
    e = text.find(end_marker, s)
    return text[s:e] if e > 0 else text[s:]


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ------------------------------------------------------------------------- #
# DB inserts
# ------------------------------------------------------------------------- #

MANUAL_SOURCE = "manual_brief"
PLAN_TAG = "manual_brief_2026_05"


def upsert_broad_themes(conn: sqlite3.Connection, themes: list[dict]) -> dict[str, int]:
    """Insert/update the 8 broad themes. Returns {title: hot_topic_id}."""
    now = int(time.time())
    out: dict[str, int] = {}
    for t in themes:
        existing = conn.execute(
            "SELECT id FROM hot_topics WHERE source = ? AND topic_name = ?",
            (MANUAL_SOURCE, t["title"]),
        ).fetchone()
        if existing:
            out[t["title"]] = existing["id"]
            continue
        # Build a theme_summary from the body for nice rendering on detail page.
        summary = (
            f"**Source**: docs/ChatGPT-热点贴纸题材推荐.md\n\n"
            f"**Chinese subtitle**: {t['cn_subtitle']}\n\n"
            f"**Operator priority rank (1=highest)**: {t['rank']}\n\n"
            f"---\n\n{t['body']}"
        )
        # high score for the top-4 prio, lower for the rest
        hot_score = max(20.0, 100.0 - (t["rank"] - 1) * 10.0)
        cur = conn.execute(
            """
            INSERT INTO hot_topics
                (source, query, topic_name, raw_payload, evidence_urls,
                 hot_score, region, fetched_at, status,
                 theme_summary, parent_topic_ids)
            VALUES (?, '', ?, ?, '[]', ?, 'US', ?, 'selected', ?, '[]')
            """,
            (MANUAL_SOURCE, t["title"],
             json.dumps({"manual_brief": True, "rank": t["rank"]}, ensure_ascii=False),
             hot_score, now, summary),
        )
        out[t["title"]] = cur.lastrowid
    conn.commit()
    return out


def upsert_topic_plan(
    conn: sqlite3.Connection,
    parent_topic_id: int,
    detailed_packs: list[dict],
    per_pack_previews: dict[int, list[dict]],
) -> int:
    """Create the topic_plan + 5 pack_series rows (idempotent)."""
    now = int(time.time())
    # Match by config.manual_brief_tag so re-runs are detectable
    existing = conn.execute(
        """
        SELECT id FROM topic_plans
         WHERE topic_id = ?
           AND config LIKE ?
        """,
        (parent_topic_id, f'%"{PLAN_TAG}"%'),
    ).fetchone()
    if existing:
        return existing["id"]

    config = {
        "series_count":         len(detailed_packs),
        "previews_per_series":  6,
        "stickers_per_preview": 10,
        "manual_brief_tag":     PLAN_TAG,
        "source_doc":           "docs/ChatGPT-热点贴纸题材推荐.md",
    }
    main_text = (
        "# Manually-curated 5-pack design from docs/ChatGPT-热点贴纸题材推荐.md\n"
        "(parsed by scripts/import_manual_brief.py — operator-blessed brief)\n"
    )
    # Build series_payload that mirrors what topic_plans._insert_series_rows expects
    series_payload = {"series": []}
    for p in detailed_packs:
        previews = per_pack_previews.get(p["idx"], [])
        preview_briefs = [
            {"preview_idx": pv["preview_idx"], "theme": pv["theme"],
             "stickers": pv["stickers"]}
            for pv in previews
        ]
        series_payload["series"].append({
            "series_name":         (p["name_cn"] or "") + " / " + p["name"],
            "positioning_cn":      p["name_cn"] or "",
            "target_users_cn":     [s.strip(" -") for s in p["audience"].splitlines() if s.strip()],
            "priority":            "high" if p["idx"] <= 2 else "medium",
            "style_anchor":        p["style"][:1500],
            "palette":             "",  # captured inside style text
            "pack_archetype":      f"camping_pack_{p['idx']}",
            "title_en":            p["name"],
            "target_audience_en":  "",
            "preview_briefs":      preview_briefs,
        })

    cur = conn.execute(
        """
        INSERT INTO topic_plans
            (topic_id, config, main_raw_text, series_payload, status,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ready', ?, ?)
        """,
        (parent_topic_id,
         json.dumps(config, ensure_ascii=False),
         main_text,
         json.dumps(series_payload, ensure_ascii=False),
         now, now),
    )
    plan_id = cur.lastrowid
    # Insert pack_series rows
    for idx, s in enumerate(series_payload["series"], 1):
        meta = {
            "positioning_cn":     s["positioning_cn"],
            "target_users_cn":    s["target_users_cn"],
            "title_en":           s["title_en"],
            "target_audience_en": s["target_audience_en"],
            "preview_briefs":     s["preview_briefs"],
        }
        conn.execute(
            """
            INSERT INTO pack_series
                (plan_id, series_idx, series_name, style_anchor, palette,
                 pack_archetype, priority, metadata_json, is_selected,
                 pack_uid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '')
            """,
            (plan_id, idx,
             s["series_name"][:200], s["style_anchor"], s["palette"],
             s["pack_archetype"], s["priority"],
             json.dumps(meta, ensure_ascii=False)),
        )
    conn.commit()
    return plan_id


def upsert_previews_with_prompts(
    conn: sqlite3.Connection,
    plan_id: int,
    per_pack_previews: dict[int, list[dict]],
    *,
    regenerate: bool = False,
) -> tuple[int, int]:
    """For each pack_series under plan_id, mint pack_uid and insert pack_previews
    rows whose prompt_text comes straight from the doc (no AIRouter call needed
    yet — operator clicks the per-series 生成 button to actually call image gen).

    Returns (pack_uids_minted, previews_inserted).
    """
    from src.services.storage.pack_store import make_pack_uid, get_pack_store
    store = get_pack_store()

    series_rows = conn.execute(
        """
        SELECT id, series_idx, series_name, style_anchor, pack_uid
          FROM pack_series WHERE plan_id = ? ORDER BY series_idx
        """,
        (plan_id,),
    ).fetchall()
    pack_uids_minted = 0
    previews_inserted = 0
    for s in series_rows:
        # Mint pack_uid if missing
        pack_uid = s["pack_uid"]
        if not pack_uid:
            pack_uid = make_pack_uid(s["series_name"] or f"series_{s['id']}")
            conn.execute(
                "UPDATE pack_series SET pack_uid = ? WHERE id = ?",
                (pack_uid, s["id"]),
            )
            store.init_pack_dir(pack_uid)
            store.write_style_anchor(pack_uid, s["series_idx"], s["style_anchor"] or "")
            pack_uids_minted += 1

        previews = per_pack_previews.get(s["series_idx"], [])
        for pv in previews:
            existing = conn.execute(
                "SELECT id FROM pack_previews WHERE series_id = ? AND preview_idx = ?",
                (s["id"], pv["preview_idx"]),
            ).fetchone()
            if existing and not regenerate:
                continue
            prompt = pv.get("prompt") or ""
            if existing and regenerate:
                conn.execute(
                    """
                    UPDATE pack_previews SET prompt_text = ?,
                           generation_status = 'pending', image_path = '',
                           model_used = '', generated_at = NULL
                     WHERE id = ?
                    """,
                    (prompt, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO pack_previews
                        (series_id, preview_idx, prompt_text, image_path,
                         model_used, generation_status, generated_at)
                    VALUES (?, ?, ?, '', '', 'pending', NULL)
                    """,
                    (s["id"], pv["preview_idx"], prompt),
                )
                previews_inserted += 1
    conn.commit()
    return pack_uids_minted, previews_inserted


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--regenerate-previews", action="store_true",
                        help="Overwrite existing pack_previews prompt_text + reset status to pending")
    args = parser.parse_args()

    if not DOC_PATH.is_file():
        print(f"ERROR: doc not found at {DOC_PATH}", file=sys.stderr)
        return 1

    doc = DOC_PATH.read_text(encoding="utf-8")
    print(f"Read {len(doc)} chars from {DOC_PATH}")

    themes = parse_broad_themes(doc)
    print(f"\nParsed {len(themes)} broad themes:")
    for t in themes:
        print(f"  rank={t['rank']:2d}  {t['title']}")

    detailed = parse_detailed_packs(doc)
    print(f"\nParsed {len(detailed)} detailed packs:")
    for p in detailed:
        print(f"  #{p['idx']}: {p['name']} ({p['name_cn']})")

    per_pack = parse_per_pack_previews(doc)
    print(f"\nParsed prompts for {len(per_pack)} packs:")
    for pidx, prevs in sorted(per_pack.items()):
        n_with_prompt = sum(1 for p in prevs if p.get("prompt"))
        print(f"  pack #{pidx}: {len(prevs)} previews, {n_with_prompt} have prompt_text")

    conn = _open_db()
    try:
        print("\n=== Upserting hot_topics (8 broad themes) ===")
        topic_ids = upsert_broad_themes(conn, themes)
        for title, tid in topic_ids.items():
            print(f"  hot_topic #{tid}: {title}")

        # The 5 detailed packs all expand "Summer Camping / Road Trip"
        camping_topic_id = topic_ids.get("Summer Camping / Road Trip Sticker Pack")
        if not camping_topic_id:
            print("ERROR: couldn't find the camping hot_topic to attach the plan to",
                  file=sys.stderr)
            return 1

        print(f"\n=== Upserting topic_plan (parent topic #{camping_topic_id}) ===")
        plan_id = upsert_topic_plan(conn, camping_topic_id, detailed, per_pack)
        print(f"  topic_plan #{plan_id} (5 pack_series)")

        print(f"\n=== Upserting pack_previews with prompts (regenerate={args.regenerate_previews}) ===")
        n_uids, n_previews = upsert_previews_with_prompts(
            conn, plan_id, per_pack, regenerate=args.regenerate_previews,
        )
        print(f"  minted {n_uids} new pack_uid(s)")
        print(f"  inserted {n_previews} new pack_previews")

        print(f"\n=== Final state ===")
        for sr in conn.execute(
            "SELECT id, series_idx, series_name, pack_uid FROM pack_series WHERE plan_id=? ORDER BY series_idx",
            (plan_id,),
        ).fetchall():
            n = conn.execute(
                "SELECT COUNT(*) FROM pack_previews WHERE series_id=?", (sr["id"],),
            ).fetchone()[0]
            print(f"  series #{sr['id']} idx={sr['series_idx']} previews={n}  uid={sr['pack_uid'][:30]}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
