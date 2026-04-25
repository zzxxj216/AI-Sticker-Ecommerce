#!/usr/bin/env python3
"""POC W1.8 — verify gpt-image-2 (or equivalent) for sticker pack split.

The A.3 step needs to take a 10-sticker preview image and produce 10
individual sticker images. Two approaches we want to verify:

1. **Generate** — confirm the image model can produce a usable
   10-sticker preview from text prompt alone (so we have something to
   split).
2. **Split via image edit** — confirm the image-to-image edit endpoint
   can extract a single sticker from a preview image.

Multiple AiHubMix model names exist for image generation; user's prior
demo referenced ``web-gpt-image-2-vip``. We try a few candidates so the
POC also discovers which name actually works on this proxy.

Run::

    python scripts/poc_w1_image_split.py
    python scripts/poc_w1_image_split.py --skip-generate  # uses prior preview if exists
    python scripts/poc_w1_image_split.py --models gpt-image-1,gpt-image-2

Output:
- output/poc_w1/preview.png        (generated 10-sticker preview)
- output/poc_w1/split_{n}.png      (extracted single stickers)
- docs/POC_REPORTS/W1_image_split.md  (findings + verdict)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

OUT_DIR = Path("output/poc_w1")
REPORT_PATH = Path("docs/POC_REPORTS/W1_image_split.md")

# Candidate model names to probe on the AiHubMix proxy.
DEFAULT_MODELS = ["gpt-image-1", "gpt-image-2", "web-gpt-image-2-vip", "dall-e-3"]

# 10-sticker preview prompt — minimal version of what A.3 will use.
PREVIEW_PROMPT = """
luxury graduation sticker pack preview, black gold color palette, elegant
celebratory style, glossy vinyl die-cut stickers, premium commercial product
look, clean white background, 10 unique stickers evenly arranged in a 2x5 grid,
bold readable English text, modern elegant typography, no duplicate stickers,
print-ready, high resolution.

Show 10 die-cut graduation stickers:
1. "Class of 2026" with graduation cap
2. "Congrats Grad" with diploma
3. "Senior 2026" bold typography
4. "You Did It" with sparkles
5. "Officially Graduated" with laurel wreath
6. "Grad Mode ON" with cap
7. "Cap Toss Moment" with flying cap
8. "Proud Graduate" badge
9. "Future Loading" modern design
10. "Diploma Unlocked" with certificate icon

Black, gold, white, champagne palette. White void background.
""".strip()

SPLIT_PROMPT_TEMPLATE = """
Extract sticker number {n} from the input preview (counting left-to-right,
top-to-bottom). Output ONLY that single sticker, centered, on a clean pure
white background. Preserve the sticker's exact text, colors, and die-cut
border. No other stickers, no preview frame, no captions.
""".strip()


# ----------------------------------------------------------------------
# OpenAI client helper (works with aihubmix /v1)
# ----------------------------------------------------------------------

def _make_client():
    from openai import OpenAI
    api_key = os.getenv("AIHUBMIX_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1")
    if base_url.rstrip("/").endswith("chat/completions"):
        base_url = base_url.rstrip("/").rsplit("/", 2)[0]
    return OpenAI(api_key=api_key, base_url=base_url, timeout=120)


# ----------------------------------------------------------------------
# Generation probe: which model name works?
# ----------------------------------------------------------------------

def probe_generate(client, model: str, prompt: str) -> dict[str, Any]:
    started = time.time()
    try:
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            size="1024x1024",
        )
        elapsed = time.time() - started
        # Response shape: data: [{ b64_json: ..., url: ... }]
        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
        if b64:
            img_bytes = base64.b64decode(b64)
        elif url:
            import httpx
            img_bytes = httpx.get(url, timeout=60).content
        else:
            return {"ok": False, "model": model, "elapsed_s": round(elapsed, 2),
                    "error": "no b64_json or url in response"}
        return {
            "ok": True,
            "model": model,
            "elapsed_s": round(elapsed, 2),
            "img_bytes": img_bytes,
            "size": len(img_bytes),
        }
    except Exception as e:
        return {"ok": False, "model": model,
                "elapsed_s": round(time.time() - started, 2),
                "error": f"{e.__class__.__name__}: {str(e)[:300]}"}


def probe_edit(client, model: str, image_bytes: bytes, prompt: str) -> dict[str, Any]:
    """Try images.edit (image-to-image) for sticker splitting."""
    import io
    started = time.time()
    try:
        # OpenAI SDK expects image as a file-like object with PNG content.
        img_stream = io.BytesIO(image_bytes)
        img_stream.name = "input.png"  # SDK needs a filename
        resp = client.images.edit(
            model=model,
            image=img_stream,
            prompt=prompt,
            n=1,
            size="1024x1024",
        )
        elapsed = time.time() - started
        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
        if b64:
            img_bytes_out = base64.b64decode(b64)
        elif url:
            import httpx
            img_bytes_out = httpx.get(url, timeout=60).content
        else:
            return {"ok": False, "model": model, "elapsed_s": round(elapsed, 2),
                    "error": "no b64_json or url in response"}
        return {
            "ok": True,
            "model": model,
            "elapsed_s": round(elapsed, 2),
            "img_bytes": img_bytes_out,
            "size": len(img_bytes_out),
        }
    except Exception as e:
        return {"ok": False, "model": model,
                "elapsed_s": round(time.time() - started, 2),
                "error": f"{e.__class__.__name__}: {str(e)[:300]}"}


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="Comma-separated list of model names to probe")
    parser.add_argument("--skip-generate", action="store_true",
                        help="Reuse output/poc_w1/preview.png if it exists")
    parser.add_argument("--split-tries", type=int, default=3,
                        help="How many split attempts per probe")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    models_to_probe = [m.strip() for m in args.models.split(",") if m.strip()]
    client = _make_client()
    findings: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "models_probed": models_to_probe,
        "generate": [],
        "split":    [],
    }

    # ---- Phase 1: generation probe ----
    preview_path = OUT_DIR / "preview.png"
    preview_bytes: bytes | None = None
    winning_model: str | None = None

    if args.skip_generate and preview_path.is_file():
        preview_bytes = preview_path.read_bytes()
        print(f"[gen] reusing existing {preview_path} ({len(preview_bytes)} bytes)")
        findings["generate"].append({"reused_existing": True, "size": len(preview_bytes)})
        # Pick the first candidate as the assumed model for split phase
        winning_model = models_to_probe[0]
    else:
        print(f"[gen] probing {len(models_to_probe)} model(s) for generate...")
        for m in models_to_probe:
            print(f"  - {m}", end=" ... ", flush=True)
            r = probe_generate(client, m, PREVIEW_PROMPT)
            findings["generate"].append({k: v for k, v in r.items() if k != "img_bytes"})
            if r["ok"]:
                preview_path.write_bytes(r["img_bytes"])
                preview_bytes = r["img_bytes"]
                winning_model = m
                print(f"OK ({r['size']} bytes, {r['elapsed_s']}s) -> {preview_path}")
                break
            else:
                print(f"FAIL ({r['error'][:80]})")

    # ---- Phase 2: split probe ----
    if preview_bytes and winning_model:
        print(f"\n[split] using model {winning_model}, {args.split_tries} tries on sticker #1...")
        for i in range(args.split_tries):
            r = probe_edit(client, winning_model, preview_bytes,
                           SPLIT_PROMPT_TEMPLATE.format(n=1))
            findings["split"].append({k: v for k, v in r.items() if k != "img_bytes"})
            if r["ok"]:
                p = OUT_DIR / f"split_1_try{i+1}.png"
                p.write_bytes(r["img_bytes"])
                print(f"  try {i+1}: OK ({r['size']} bytes, {r['elapsed_s']}s) -> {p}")
            else:
                print(f"  try {i+1}: FAIL ({r['error'][:80]})")
    else:
        print("\n[split] skipped — no preview to work from")

    # ---- Report ----
    write_report(findings)
    print(f"\nReport: {REPORT_PATH}")
    return 0


def write_report(f: dict) -> None:
    lines = [f"# POC W1.8 — gpt-image-2 generate + split\n"]
    lines.append(f"**Run at**: {f['started_at']}")
    lines.append(f"**Models probed**: {', '.join(f['models_probed'])}\n")

    lines.append("## Phase 1 — generation\n")
    if not f["generate"]:
        lines.append("- (no probes ran)")
    for r in f["generate"]:
        if r.get("reused_existing"):
            lines.append(f"- (reused existing preview from prior run, {r['size']} bytes)")
            continue
        marker = "OK" if r.get("ok") else "FAIL"
        line = f"- `{r['model']}` -> **{marker}** ({r.get('elapsed_s')}s"
        if r.get("ok"):
            line += f", {r.get('size')} bytes)"
        else:
            line += f", error: `{r.get('error')}`)"
        lines.append(line)

    lines.append("\n## Phase 2 — image edit / split\n")
    if not f["split"]:
        lines.append("- (no split probes ran)")
    for i, r in enumerate(f["split"], 1):
        marker = "OK" if r.get("ok") else "FAIL"
        line = f"- try {i}: `{r['model']}` -> **{marker}** ({r.get('elapsed_s')}s"
        if r.get("ok"):
            line += f", {r.get('size')} bytes)"
        else:
            line += f", error: `{r.get('error')}`)"
        lines.append(line)

    # Verdict
    gen_ok = any(r.get("ok") for r in f["generate"]) or any(r.get("reused_existing") for r in f["generate"])
    split_ok_count = sum(1 for r in f["split"] if r.get("ok"))
    split_total = len(f["split"])

    lines.append("\n## Verdict\n")
    if not gen_ok:
        lines.append("- ❌ **No image generation model worked.** All candidates failed. "
                     "A.3 needs a different image provider (Gemini, native OpenAI, or self-hosted SD).")
    elif split_total == 0:
        lines.append("- ⚠️  Generation worked but split phase did not run.")
    elif split_ok_count == split_total:
        lines.append(f"- ✅ Image edit (split) is **viable** — {split_ok_count}/{split_total} attempts succeeded. "
                     "Wire `images.edit` into AIRouter.image_edit and proceed.")
    elif split_ok_count > 0:
        lines.append(f"- ⚠️  Image edit is **flaky** — {split_ok_count}/{split_total} attempts succeeded. "
                     "Use it as primary path with PIL grid-split fallback.")
    else:
        lines.append("- ❌ **Image edit failed every attempt.** A.3 must use PIL grid-split (preview prompt "
                     "must enforce 2x5 evenly-spaced layout) or Gemini bbox detection + crop.")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
