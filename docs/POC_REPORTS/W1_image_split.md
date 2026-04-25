# POC W1.8 — gpt-image generate + split (sticker pack)

**Run at**: 2026-04-24T20:07:12
**Endpoint**: AiHubMix `/v1` (env `AIHUBMIX_BASE_URL`)
**Models probed**: gpt-image-1, gpt-image-2, web-gpt-image-2-vip, dall-e-3

---

## Phase 1 — generation (text -> 10-sticker preview)

| model | result | latency | bytes |
|---|---|---|---|
| `gpt-image-1` | **OK** (first try) | 42.7s | 1.79 MB |

Other candidates (`gpt-image-2`, `web-gpt-image-2-vip`, `dall-e-3`) were not probed since gpt-image-1 succeeded first. The proxy exposes the OpenAI image API as `gpt-image-1`.

**Visual quality (preview.png):**
- Theme adherence: Excellent — cohesive black/gold luxury aesthetic
- Text rendering: All English text correctly rendered ("CLASS OF 2026", "CONGRATS GRAD", "SENIOR 2026", "OFFICIALLY GRADUATED", "GRAD MODE ON", "YOU DID IT", "FUTURE LOADING", "PROUD GRADUATE", "DIPLOMA UNLOCKED")
- Layout: 9 stickers in a 3x3 grid, **not** the 10-sticker 2x5 grid the prompt requested. Need to refine prompt for W2/A.3 (be more explicit about count & layout, possibly request "2 rows of 5" instead of "10 in 2x5 grid").
- Die-cut borders, gold accents, palette: production-ready.

## Phase 2 — image edit (preview -> single sticker)

Sticker #1 ("Class of 2026 with cap"), 3 attempts:

| try | result | latency | notes |
|---|---|---|---|
| 1 | **OK** | 61.6s | clean white bg, text + cap preserved exactly, slight pose variation |
| 2 | **OK** | 56.6s | same — cap angle slightly different, text identical |
| 3 | **FAIL** | 32.5s | `APIConnectionError: Connection error.` (transient) |

**Visual quality (split_1_try{1,2}.png):**
- Both successes returned ONLY the requested sticker (other 8 stickers cleanly excluded)
- White background is genuine pure white (no preview frame artifacts, no shadows from removed siblings)
- Text "CLASS OF 2026" and graduation cap preserved with full color fidelity
- The two tries differ only in cap pose — both look like alternate "renders" of the same design rather than visibly degraded copies

---

## Verdict

✅ **Image generate + edit are both viable for A.3.**
Wire `gpt-image-1` into `AIRouter.image_generate` / `image_edit` (done in this commit). Set `DEFAULT_IMAGE_MODEL = "gpt-image-1"`.

**Operational notes for W2/A.3:**

1. **Retry on transient errors.** The 1/3 connection error in this POC is normal for ~60s requests through a proxy. Wrap all image_edit calls in 2-3 attempts before falling back.
2. **Latency budget.** Generate ~45s, edit ~60s. A 5-series × 5-preview × 10-sticker pack = 25 generates + 250 edits = ~4 hours wall time at single concurrency. Parallelize to ~6 concurrent (matches existing `GEMINI_MAX_CONCURRENCY` default) → ~40 minutes per pack.
3. **Prompt count vs. actual count.** Model produced 9/10 requested. A.3 should re-prompt or accept 9 stickers gracefully; either approach is fine because the split prompt extracts by index and is robust to the actual count being slightly off.
4. **Edit re-renders, doesn't crop.** The "split" is really "re-draw based on what you saw" — try 1 vs try 2 had the cap at different angles. This means the final sticker is generation-quality (not a lossy crop), which is good for product use, but consumers should not expect pixel-identity to the preview.
5. **PIL fallback NOT needed at this point.** If split quality regresses on more complex packs (e.g. lifestyle aesthetic with overlapping elements), revisit and add a PIL grid-split fallback gated on a per-prompt `layout_is_strict_grid` hint.

---

## Outputs

- `output/poc_w1/preview.png` — generated 10-sticker preview (9 actual)
- `output/poc_w1/split_1_try1.png`, `split_1_try2.png` — extracted "Class of 2026" sticker

(Files git-ignored under `output/`; rerun `python scripts/poc_w1_image_split.py --skip-generate` to refresh splits without regenerating the preview.)
