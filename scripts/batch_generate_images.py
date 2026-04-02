"""Batch generate remaining sticker images using thread pool.

Scans all pack directories, skips already-generated images,
and generates remaining stickers with configurable concurrency.

Usage:
  python -m scripts.batch_generate_images \
      --batch-dir output/batch_v1/20260331_103426

  Custom concurrency (default 3):
  python -m scripts.batch_generate_images \
      --batch-dir output/batch_v1/20260331_103426 --workers 4

  Only specific packs:
  python -m scripts.batch_generate_images \
      --batch-dir output/batch_v1/20260331_103426 \
      --packs "Cowboy_Western,Hot_Dog_Cartoon"
"""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT
from src.services.ai.gemini_service import GeminiService
from src.services.batch.sticker_prompts import parse_prompt_builder_output

ROLE_ORDER = ["hero", "support", "text", "badge-label", "filler"]


@dataclass
class Job:
    pack_name: str
    sticker: dict
    output_dir: Path
    out_path: Path


@dataclass
class Stats:
    lock: threading.Lock = field(default_factory=threading.Lock)
    success: int = 0
    failed: int = 0
    total: int = 0
    start_time: float = 0.0

    def record(self, ok: bool):
        with self.lock:
            if ok:
                self.success += 1
            else:
                self.failed += 1

    def progress(self) -> str:
        done = self.success + self.failed
        elapsed = time.time() - self.start_time
        rate = done / elapsed * 60 if elapsed > 0 else 0
        return f"[{done}/{self.total}] ok={self.success} fail={self.failed} ({rate:.1f}/min)"


def safe_filename(name: str, idx: int) -> str:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
    safe = safe.replace(" ", "_").strip("_")[:40]
    return f"sticker_{idx:02d}_{safe}.png"


def collect_jobs(batch_dir: Path, pack_filter: set | None) -> list[Job]:
    """Scan all pack directories and collect remaining image generation jobs."""
    jobs: list[Job] = []
    manifest_path = batch_dir / "batch_manifest.json"

    if not manifest_path.exists():
        print(f"Error: batch_manifest.json not found in {batch_dir}")
        return jobs

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for theme_name, info in manifest.get("themes", {}).items():
        if info.get("status") != "completed":
            print(f"  SKIP {theme_name} — status: {info.get('status')}")
            continue

        out_dir_str = info.get("output_dir", "")
        pack_dir = PROJECT_ROOT / out_dir_str
        if not pack_dir.exists():
            print(f"  SKIP {theme_name} — dir not found: {pack_dir}")
            continue

        dir_name = pack_dir.name.split("_", 2)[-1] if "_" in pack_dir.name else pack_dir.name
        if pack_filter and dir_name not in pack_filter and theme_name not in pack_filter:
            continue

        pb_path = pack_dir / "prompt_builder_output.txt"
        if not pb_path.exists():
            print(f"  SKIP {theme_name} — no prompt_builder_output.txt")
            continue

        pb_text = pb_path.read_text(encoding="utf-8")
        parsed = parse_prompt_builder_output(pb_text)
        stickers = parsed["stickers"]

        if not stickers:
            print(f"  SKIP {theme_name} — 0 stickers parsed")
            continue

        images_dir = pack_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        existing = {f.name for f in images_dir.glob("sticker_*.png")}

        pack_new = 0
        for s in stickers:
            fname = safe_filename(s["name"], s["index"])
            if fname in existing:
                continue
            jobs.append(Job(
                pack_name=theme_name,
                sticker=s,
                output_dir=images_dir,
                out_path=images_dir / fname,
            ))
            pack_new += 1

        total = len(stickers)
        done = total - pack_new
        print(f"  {theme_name}: {pack_new} remaining ({done}/{total} already done)")

    return jobs


def generate_one(gemini: GeminiService, job: Job, stats: Stats) -> dict:
    """Generate a single sticker image. Thread-safe."""
    s = job.sticker
    idx, name, prompt = s["index"], s["name"], s["prompt"]
    t0 = time.time()

    try:
        result = gemini.generate_image(prompt=prompt, output_path=job.out_path)
        elapsed = time.time() - t0
        ok = result.get("success", False)
        stats.record(ok)

        if ok:
            size_kb = result.get("size_kb", "?")
            print(f"  {stats.progress()} [{job.pack_name[:20]}] #{idx:02d} {name} — OK ({elapsed:.1f}s, {size_kb}KB)")
        else:
            err = result.get("error", "unknown")
            print(f"  {stats.progress()} [{job.pack_name[:20]}] #{idx:02d} {name} — FAIL: {err}")

        return {"index": idx, "name": name, "pack": job.pack_name, "ok": ok, "elapsed": elapsed}

    except Exception as e:
        elapsed = time.time() - t0
        stats.record(False)
        print(f"  {stats.progress()} [{job.pack_name[:20]}] #{idx:02d} {name} — ERROR: {e}")
        return {"index": idx, "name": name, "pack": job.pack_name, "ok": False, "elapsed": elapsed, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Batch generate remaining sticker images (multi-threaded)")
    parser.add_argument("--batch-dir", required=True, help="Path to batch output directory")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent workers (default: 3)")
    parser.add_argument("--packs", default=None, help="Comma-separated pack names to process (default: all)")
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir)
    if not batch_dir.is_absolute():
        batch_dir = PROJECT_ROOT / batch_dir

    pack_filter = None
    if args.packs:
        pack_filter = {p.strip() for p in args.packs.split(",")}

    print("=" * 70)
    print("Batch Image Generation (Multi-threaded)")
    print("=" * 70)
    print(f"Batch dir : {batch_dir}")
    print(f"Workers   : {args.workers}")
    print()

    jobs = collect_jobs(batch_dir, pack_filter)

    if not jobs:
        print("\nNo images to generate. All done!")
        return

    # Sort: hero first, then by pack, then by index
    def sort_key(j: Job):
        r = j.sticker.get("role", "").lower()
        role_idx = ROLE_ORDER.index(r) if r in ROLE_ORDER else len(ROLE_ORDER)
        return (role_idx, j.pack_name, j.sticker["index"])
    jobs.sort(key=sort_key)

    stats = Stats(total=len(jobs), start_time=time.time())

    print(f"\nTotal jobs: {stats.total}")
    print(f"Starting {args.workers} workers...\n")

    gemini = GeminiService()
    print(f"Model: {gemini.model}\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(generate_one, gemini, job, stats): job for job in jobs}
        for future in as_completed(futures):
            try:
                r = future.result()
                results.append(r)
            except Exception as e:
                job = futures[future]
                print(f"  UNEXPECTED ERROR: {job.pack_name} #{job.sticker['index']} — {e}")

    total_time = time.time() - stats.start_time
    print()
    print("=" * 70)
    print("BATCH GENERATION COMPLETE")
    print("=" * 70)
    print(f"Success : {stats.success}/{stats.total}")
    print(f"Failed  : {stats.failed}")
    print(f"Time    : {total_time:.0f}s ({total_time/60:.1f}min)")
    if stats.success > 0:
        print(f"Rate    : {stats.success / total_time * 60:.1f} images/min")
    print()

    # Per-pack summary
    pack_results: dict[str, dict] = {}
    for r in results:
        p = r["pack"]
        if p not in pack_results:
            pack_results[p] = {"ok": 0, "fail": 0}
        if r["ok"]:
            pack_results[p]["ok"] += 1
        else:
            pack_results[p]["fail"] += 1

    print(f"{'Pack':<35} {'OK':>5} {'Fail':>5}")
    print("-" * 50)
    for p, counts in sorted(pack_results.items()):
        print(f"{p:<35} {counts['ok']:>5} {counts['fail']:>5}")

    # Save results
    results_path = batch_dir / "image_gen_results.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
