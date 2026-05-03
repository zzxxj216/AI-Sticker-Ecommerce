# POC W3 — `gpt-image-2-edit` with `n=10` on one preview

**Run date**: 2026-04-28  
**Target preview**: `preview_id=20`  
**Source image**: `output/packs/20260428_coastal_summer_road_trip_stick_caaa/series_3/previews/4.png`  
**Theme**: `Summer Text Stickers A`

## Purpose

Test whether one `image_edit` request with `n=10` can replace the current
"1 sticker = 1 request" split flow for a 10-sticker preview sheet.

The prompt asked for:

- exactly 10 output images
- one distinct sticker per output
- fixed order: output #1 = sticker #1, output #2 = sticker #2, ...

## Runs

| Run | Elapsed | Requested `n` | Returned images | Visual result |
|---|---:|---:|---:|---|
| `20260428_141010_preview_20` | 120.53s | 10 | 6 | all returned images were the same first sticker (`Summer State of Mind`) |
| `20260428_141331_preview_20` | 110.44s | 10 | 4 | same failure pattern; all outputs repeated the first sticker |
| `20260428_141606_preview_20` | 107.02s | 10 | 4 | same failure pattern; all outputs repeated the first sticker |

## Artifacts

- `output/poc_n10_batch_split/20260428_141010_preview_20/`
- `output/poc_n10_batch_split/20260428_141331_preview_20/`
- `output/poc_n10_batch_split/20260428_141606_preview_20/`

Each run directory contains:

- `request.json`
- `response.json`
- `summary.json`
- `result_*.png`
- `contact_sheet.png`

## Verdict

For this preview and prompt shape, **`n=10` does not behave like "batch split 10
different stickers in one request"`**.

Observed behavior:

1. Returned image count was **smaller than requested** (`6`, `4`, `4` instead of `10`).
2. Returned images were **not mapped to the 10 requested stickers**.
3. Outputs collapsed to **repeated variants of the first sticker** instead of 10
   distinct stickers.
4. A simple perceptual-hash check showed each run's images stayed extremely close
   to the first image (distance only `0-4 / 64`), which matches the visual
   conclusion that they are near-duplicates, not 10 different stickers.

## Practical conclusion

Current production assumption remains valid:

- `image_edit(n=10)` is not a safe replacement for deterministic per-sticker split.
- If request count must drop materially, the next serious path is **local CV/PIL split**,
  not multi-output `image_edit`.
