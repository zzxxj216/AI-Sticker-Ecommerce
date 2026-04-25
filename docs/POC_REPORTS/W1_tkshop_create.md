# POC W1.9 — TikTok Shop end-to-end create_product

**Status**: ⏸️ **Blocked on credentials.** Will run once the four required env vars are set.

---

## What this POC will verify

End-to-end product creation against TikTok Shop, exercising the three API calls C.3 needs:

1. `POST /product/202309/images/upload` — upload a main image, capture the returned `tiktok_image_uri`
2. `POST /product/202309/products` — create a draft product, capture the returned `tiktok_product_id`
3. `POST /product/202309/products/{id}/activate` — attempt activation (known to return 40006 in some shops; this POC will document whether ours is affected)

Each call's full request, response, latency, and error code will be recorded so C.3 can:
- Verify the HMAC-SHA256 signature flow against the live API (not just the docs)
- Confirm the image upload uses `multipart/form-data` correctly with no shop_cipher
- Discover any field-shape surprises the docs miss
- Decide whether activate is reliable enough to call automatically or whether C.3 must mark products `manual_required` and surface a "go to TikTok Seller Center" link

## Why blocked

These env vars are not set in `.env`:

| Env var | Purpose |
|---|---|
| `TIKTOK_APP_KEY` | App key from TikTok Shop Partner Center |
| `TIKTOK_APP_SECRET` | App secret (signs every request) |
| `TIKTOK_ACCESS_TOKEN` | OAuth access token for the target shop |
| `TIKTOK_SHOP_CIPHER` | Shop cipher (encrypted shop ID, returned alongside access_token) |

Without these, no authenticated request can be signed against `open-api.tiktokglobalshop.com`.

## What's already ready

The downstream wiring is in place so the POC drops in cleanly when keys arrive:

- ✅ Schema: `tkshop_products`, `tkshop_product_images`, `tkshop_publish_logs` tables (W1.1)
- ✅ File layout: `output/packs/{pack_uid}/products/{id}/...` with main.png + title.txt + description.html slots (W1.4)
- ✅ Reference doc: complete API spec at `docs/TIKTOK_PRODUCT_API_GUIDE.md`
- ✅ AICallLog + scheduled_jobs observability for any retries

## When unblocked, the POC will:

1. Pick a real product candidate (one of the 348 seeded `hot_topics` rows) and a pre-uploaded test image
2. Run `upload_image` → write `tiktok_image_uri` to a `tkshop_product_images` row
3. Run `create_product` with category 928016 (Stickers) + the default template (price/inventory/weight/SKU per `default_template_json`)
4. Run `activate_product` and capture the result code
5. Write a per-step row to `tkshop_publish_logs` (request, response, error_code, latency)
6. Update this doc with: latencies, any field-shape gotchas, the 40006 outcome, and a green/yellow/red verdict for C.3

## Alternative path if credentials never come

If TikTok Shop Partner Center access is delayed indefinitely, C.3 implementation can still proceed in "draft mode" — generating titles / descriptions / images and persisting them locally — with the publish step gated behind a simple availability check. The product detail page would then show "ready to publish, awaiting credentials" instead of "live on TikTok Shop". This degrades gracefully and avoids blocking the entire C module on one upstream dependency.

---

**Action item for the user**: provide the four `TIKTOK_*` env vars (or confirm we proceed with draft-mode-only for C.3).
