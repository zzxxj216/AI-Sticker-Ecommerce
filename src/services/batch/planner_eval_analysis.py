"""Batch-level anti-templating heuristics (struct repetition, hollow-phrase frequency, example-count patterns, etc.)."""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# Common hollow commercial copy (many hits → batch warning)
HOLLOW_PHRASES = (
    "gift appeal",
    "premium feel",
    "worth buying",
    "cohesion",
    "premium",
    "giftable",
    "cohesive",
    "high-end",
    "viral hit",
    "worth owning",
)

# Structure block keywords (hit ratio helps spot mechanical hero/icon/text stacking)
STRUCTURE_KEYWORDS = (
    "hero",
    "icon",
    "text",
    "badge",
    "label",
    "filler",
    "pattern",
    "main image",
)


def _read_planning_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bigrams(s: str) -> Counter:
    s = re.sub(r"\s+", "", s)
    if len(s) < 2:
        return Counter()
    return Counter(s[i : i + 2] for i in range(len(s) - 1))


def jaccard_bigrams(a: str, b: str) -> float:
    ca, cb = _bigrams(a), _bigrams(b)
    if not ca and not cb:
        return 1.0
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 0.0


def extract_pack_structure_block(text: str, max_len: int = 2500) -> str:
    """Roughly extract the sticker-pack structure section; supports common English headings."""
    markers = (
        "2. Sticker pack structure",
        "Sticker pack structure",
        "## Sticker pack structure",
        "Part 2 — Sticker pack structure",
    )
    start = -1
    for m in markers:
        i = text.find(m)
        if i != -1 and (start == -1 or i < start):
            start = i
    if start == -1:
        return ""

    chunk = text[start : start + max_len]
    end_markers = (
        "\n3. Design style",
        "\n3. Design",
        "\n4. Sticker format",
        "\n===== PART 4",
    )
    end = len(chunk)
    for em in end_markers:
        j = chunk.find(em)
        if j != -1:
            end = min(end, j)
    return chunk[:end].strip()


def extract_hit_selling_block(text: str, max_len: int = 2000) -> str:
    markers = (
        "5. Hit-selling details",
        "Hit-selling",
        "Best-seller details",
        "5. Best-seller angles",
    )
    start = -1
    for m in markers:
        i = text.find(m)
        if i != -1 and (start == -1 or i < start):
            start = i
    if start == -1:
        return ""
    chunk = text[start : start + max_len]
    end_markers = (
        "\n6. Example bundle",
        "\n6. Examples",
        "\n===== PART 4",
    )
    end = len(chunk)
    for em in end_markers:
        j = chunk.find(em)
        if j != -1:
            end = min(end, j)
    return chunk[:end].strip()


def extract_style_variant_lines(text: str) -> list[str]:
    """Short bullet lines under PART 4 or Style variants."""
    m = re.search(r"PART 4[:\s]*STYLE VARIANTS.*", text, re.IGNORECASE | re.DOTALL)
    block = text[m.start() :] if m else text
    lines = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(("-", "*", "•")) or re.match(r"^\d+[\.)]\s", line):
            cleaned = re.sub(r'^[-*•\d\.\)\s]+', "", line)
            if 2 <= len(cleaned) <= 40:
                lines.append(cleaned)
    return lines[:20]


def extract_bundle_numbers(text: str) -> list[str]:
    """Capture numeric bundle-size patterns from full text to spot repeated sizing."""
    return re.findall(
        r"\d+\s*[-~to]\s*\d+\s*(?:stickers?|pcs|sheets)|\d+\s*(?:stickers?|pcs|sheets)",
        text,
        flags=re.IGNORECASE,
    )


def analyze_run_directory(run_dir: Path) -> dict[str, Any]:
    """Read planning.md under each run subfolder; return a JSON-serializable batch-analysis dict."""
    run_dir = Path(run_dir)
    rows: list[dict[str, Any]] = []
    for sub in sorted(run_dir.iterdir()):
        if not sub.is_dir():
            continue
        pm = sub / "planning.md"
        if not pm.exists():
            continue
        text = _read_planning_text(pm)
        fid = sub.name
        st = extract_pack_structure_block(text)
        hs = extract_hit_selling_block(text)
        rows.append(
            {
                "fixture_id": fid,
                "pack_structure_excerpt_len": len(st),
                "hit_selling_excerpt_len": len(hs),
                "pack_structure": st[:1200],
                "hit_selling": hs[:800],
            }
        )

    structures = [r["pack_structure"] for r in rows if r["pack_structure"]]
    n = len(structures)
    pairwise: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairwise.append(jaccard_bigrams(structures[i], structures[j]))

    hollow_hits: Counter[str] = Counter()
    full_text_concat = ""
    for sub in sorted(run_dir.iterdir()):
        if not sub.is_dir():
            continue
        pm = sub / "planning.md"
        if pm.exists():
            t = _read_planning_text(pm)
            full_text_concat += t + "\n"
            for ph in HOLLOW_PHRASES:
                if ph in t:
                    hollow_hits[ph] += 1

    kw_hits: Counter[str] = Counter()
    for st in structures:
        low = st.lower()
        for kw in STRUCTURE_KEYWORDS:
            if kw.lower() in low or kw in st:
                kw_hits[kw] += 1

    bundle_nums = extract_bundle_numbers(full_text_concat)

    return {
        "run_dir": str(run_dir.resolve()),
        "fixtures_with_output": len(rows),
        "structure_pairwise_jaccard_avg": round(sum(pairwise) / len(pairwise), 4)
        if pairwise
        else None,
        "structure_pairwise_jaccard_max": round(max(pairwise), 4) if pairwise else None,
        "hollow_phrase_hits_per_fixture": dict(hollow_hits),
        "structure_keyword_fixture_hits": dict(kw_hits),
        "bundle_number_snippets_sample": bundle_nums[:40],
        "interpretation": {
            "structure_avg_above_0_35": bool(pairwise and sum(pairwise) / len(pairwise) > 0.35),
            "many_hollow_phrases": any(c >= max(3, n * 0.25) for c in hollow_hits.values()),
        },
    }


def write_batch_report(run_dir: Path, out_path: Path | None = None) -> Path:
    data = analyze_run_directory(run_dir)
    out = out_path or (Path(run_dir) / "batch_anti_template_report.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
