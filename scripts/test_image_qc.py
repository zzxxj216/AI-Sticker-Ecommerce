"""Test: IMAGE_QC_SYSTEM - Single sticker image quality scoring

Uses Gemini vision model to QC-score generated sticker images.

Usage:
  Single image:
  python -m scripts.test_image_qc \
      --image output/test/images/sticker_01_Double_Cherry_Bow.png \
      --pack-foundation output/test/pack_foundation.txt \
      --prompt-builder-output output/test/prompt_builder_output.txt \
      --sticker-index 1

  Batch (entire directory):
  python -m scripts.test_image_qc \
      --image-dir output/test/images \
      --pack-foundation output/test/pack_foundation.txt \
      --prompt-builder-output output/test/prompt_builder_output.txt
"""

import sys
import re
import json
import base64
import argparse
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT

from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_prompts import parse_prompt_builder_output
from src.services.batch.image_generation import (
    IMAGE_QC_SYSTEM,
    build_image_qc_prompt,
    parse_image_qc_output,
)


def _extract_sticker_spec_block(parsed_stickers: list[dict], index: int) -> str:
    """Extract a single sticker's spec block from parse_prompt_builder_output results."""
    for s in parsed_stickers:
        if s["index"] == index:
            lines = [
                f"[Sticker #{s['index']}]",
                f"- Name: {s.get('name', '')}",
                f"- Role: {s.get('role', '')}",
                f"- Generation prompt: {s.get('prompt', '')}",
                f"- Negative guidance: {s.get('negative', '')}",
                f"- Consistency note: {s.get('consistency_note', '')}",
            ]
            return "\n".join(lines)
    return f"(Sticker #{index} spec not found)"


def run_single_qc(
    gemini: GeminiService,
    image_path: Path,
    sticker_index: int,
    sticker_name: str,
    sticker_role: str,
    sticker_spec_block: str,
    pack_foundation: str,
) -> dict:
    """Run QC scoring on a single image."""
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    qc_prompt = build_image_qc_prompt(
        sticker_name=sticker_name,
        sticker_role=sticker_role,
        sticker_spec_block=sticker_spec_block,
        pack_foundation=pack_foundation,
    )

    full_prompt = f"{IMAGE_QC_SYSTEM}\n\n{qc_prompt}"

    result = gemini.analyze_image(
        image_data=image_b64,
        prompt=full_prompt,
        media_type="image/png",
    )

    qc_text = result["text"]
    parsed = parse_image_qc_output(qc_text)
    parsed["raw_text"] = qc_text
    parsed["image_path"] = str(image_path)
    parsed["sticker_index"] = sticker_index
    return parsed


def main():
    parser = argparse.ArgumentParser(description="Test IMAGE_QC_SYSTEM")
    parser.add_argument("--image", default=None, help="Single image path")
    parser.add_argument("--image-dir", default=None, help="Directory of sticker images")
    parser.add_argument("--pack-foundation", required=True, help="pack_foundation.txt")
    parser.add_argument("--prompt-builder-output", required=True, help="prompt_builder_output.txt")
    parser.add_argument("--sticker-index", type=int, default=None, help="Sticker index (for --image)")
    args = parser.parse_args()

    if not args.image and not args.image_dir:
        print("Error: Provide --image or --image-dir")
        sys.exit(1)

    pf_path = Path(args.pack_foundation)
    pb_path = Path(args.prompt_builder_output)

    if not pf_path.exists():
        print(f"Error: Pack foundation not found: {pf_path}")
        sys.exit(1)
    if not pb_path.exists():
        print(f"Error: Prompt builder output not found: {pb_path}")
        sys.exit(1)

    pack_foundation = pf_path.read_text(encoding="utf-8").strip()
    pb_text = pb_path.read_text(encoding="utf-8").strip()
    pb_parsed = parse_prompt_builder_output(pb_text)
    stickers_info = pb_parsed["stickers"]

    # ---- collect images to evaluate ----
    image_jobs: list[dict] = []

    if args.image:
        img_path = Path(args.image)
        if not img_path.exists():
            print(f"Error: Image not found: {img_path}")
            sys.exit(1)
        idx = args.sticker_index
        if idx is None:
            m = re.search(r"sticker_(\d+)", img_path.stem)
            idx = int(m.group(1)) if m else 1
        s_info = next((s for s in stickers_info if s["index"] == idx), None)
        image_jobs.append({
            "path": img_path,
            "index": idx,
            "name": s_info["name"] if s_info else img_path.stem,
            "role": s_info["role"] if s_info else "unknown",
        })
    else:
        img_dir = Path(args.image_dir)
        if not img_dir.is_dir():
            print(f"Error: Image dir not found: {img_dir}")
            sys.exit(1)
        for img_path in sorted(img_dir.glob("sticker_*.png")):
            m = re.search(r"sticker_(\d+)", img_path.stem)
            if not m:
                continue
            idx = int(m.group(1))
            s_info = next((s for s in stickers_info if s["index"] == idx), None)
            image_jobs.append({
                "path": img_path,
                "index": idx,
                "name": s_info["name"] if s_info else img_path.stem,
                "role": s_info["role"] if s_info else "unknown",
            })

    if not image_jobs:
        print("No sticker images found to evaluate.")
        sys.exit(0)

    print("=" * 60)
    print("IMAGE QC Test")
    print("=" * 60)
    print(f"Images to evaluate: {len(image_jobs)}")
    print(f"Pack foundation   : {pf_path} ({len(pack_foundation)} chars)")
    print()

    gemini = GeminiService()
    print(f"Model: {gemini.model}")
    print()

    all_results: list[dict] = []

    for job in image_jobs:
        idx = job["index"]
        name = job["name"]
        role = job["role"]
        img_path = job["path"]

        spec_block = _extract_sticker_spec_block(stickers_info, idx)

        print(f"  #{idx:2d} {name} [{role}] ... ", end="", flush=True)

        try:
            qc = run_single_qc(
                gemini=gemini,
                image_path=img_path,
                sticker_index=idx,
                sticker_name=name,
                sticker_role=role,
                sticker_spec_block=spec_block,
                pack_foundation=pack_foundation,
            )
            total = qc["total"]
            decision = qc["decision"]
            hr = " [HARD REJECT]" if qc["hard_reject"] else ""
            print(f"{total}/100  {decision}{hr}")
            all_results.append(qc)
        except Exception as e:
            print(f"ERROR — {e}")
            all_results.append({
                "sticker_index": idx,
                "sticker_name": name,
                "role": role,
                "error": str(e),
            })

    # ---- summary ----
    print()
    print("=" * 60)
    print("QC SUMMARY")
    print("=" * 60)
    print(f"{'#':>3}  {'Name':<28} {'Role':<12} {'Score':>5}  {'Decision':<12}")
    print("-" * 70)

    scored = [r for r in all_results if "total" in r]
    for r in scored:
        hr_tag = " *HR*" if r.get("hard_reject") else ""
        print(
            f"#{r['sticker_index']:2d}  {r.get('sticker_name',''):<28} "
            f"{r.get('role',''):<12} {r['total']:>3}/100  "
            f"{r.get('decision',''):<12}{hr_tag}"
        )

    if scored:
        avg = sum(r["total"] for r in scored) / len(scored)
        print(f"\nAverage score: {avg:.1f}/100")

        keeps = [r for r in scored if r.get("decision", "").lower() == "keep"]
        compares = [r for r in scored if r.get("decision", "").lower() == "compare"]
        regens = [r for r in scored if r.get("decision", "").lower() == "regenerate"]
        rejects = [r for r in scored if r.get("decision", "").lower() == "reject"]
        hard_rejects = [r for r in scored if r.get("hard_reject")]

        print(f"Keep: {len(keeps)}  Compare: {len(compares)}  "
              f"Regenerate: {len(regens)}  Reject: {len(rejects)}  "
              f"Hard Reject: {len(hard_rejects)}")

    # ---- save detailed results ----
    output_dir = Path("output/test")
    output_dir.mkdir(parents=True, exist_ok=True)

    qc_detail_path = output_dir / "image_qc_results.json"
    serializable = []
    for r in all_results:
        entry = {k: v for k, v in r.items() if k != "raw_text"}
        serializable.append(entry)
    qc_detail_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDetailed results saved to: {qc_detail_path}")

    qc_raw_dir = output_dir / "image_qc_raw"
    qc_raw_dir.mkdir(parents=True, exist_ok=True)
    for r in all_results:
        if "raw_text" in r:
            idx = r.get("sticker_index", 0)
            raw_path = qc_raw_dir / f"qc_{idx:02d}.txt"
            raw_path.write_text(r["raw_text"], encoding="utf-8")

    print(f"Raw QC texts saved to: {qc_raw_dir}/")
    print()
    print("=== IMAGE QC Test COMPLETE ===")


if __name__ == "__main__":
    main()
