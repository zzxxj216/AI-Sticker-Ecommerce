# POC W1.7 — Web Search via Configured Endpoints

**Run at**: 2026-04-24T20:02:58
**Query**: `graduation 2026 stickers trends`

## Verdict

- Path A failed — middleman likely doesn't forward Responses API.
- Path B (AiHubMix surfing model) returned URLs — viable.

---

## Path A — OpenAI Responses API + web_search_preview tool

- **model**: `gpt-5.4`
- **base_url**: `https://api.jiekou.ai/openai/`
- **elapsed**: 1.97s
- **status**: error
- **error**: `NotFoundError: 404 page not found`

---

## Path B — AiHubMix surfing model (chat.completions)

- **model**: `gpt-5.4:surfing`
- **base_url**: `https://aihubmix.com/v1`
- **elapsed**: 11.03s
- **status**: ok
- **citations found**: 5
- **first 5 URLs**:
  - https://www.redbubble.com/shop/2026+graduation+stickers
  - https://www.voilaprint.com/products/personalized-photo-graduation-2026-stickers?srsltid=AfmBOoqCPU-OcHTJPt70AWBfEI8lBrlfsVyUWmuncLViHoqCEdk7fnDz
  - https://www.etsy.com/market/2026_graduation_vinyl_stickers?page=2
  - https://www.etsy.com/market/class_of_2026_stickers
  - https://www.zazzle.com/graduation+stickers

**Text preview (first 1000 chars):**

```
Here are 5 fresh sources on **graduation 2026 stickers trends** based on the provided reference materials and closely related live marketplace/category sources:

1. **2026 Graduation Stickers - Redbubble**  
URL: https://www.redbubble.com/shop/2026+graduation+stickers  
Summary: Shows current independent-designer trends for 2026 graduation stickers, including popular styles for laptops, water bottles, and party décor.

2. **Personalized Photo Graduation 2026 Stickers - Voilà Print**  
URL: https://www.voilaprint.com/products/personalized-photo-graduation-2026-stickers?srsltid=AfmBOoqCPU-OcHTJPt70AWBfEI8lBrlfsVyUWmuncLViHoqCEdk7fnDz  
Summary: Highlights the personalization trend, with custom photo, name, and school-based graduation 2026 sticker options for favors and gifts.

3. **2026 Graduation Vinyl Stickers - Etsy**  
URL: https://www.etsy.com/market/2026_graduation_vinyl_stickers?page=2  
Summary: Reflects handmade and small-shop trends, especially custom vinyl sticker designs for 
... [truncated, total 1499 chars]
```

---

## Next steps

If neither path returned real citations, A.1 needs:
1. A native OpenAI key + direct `api.openai.com` endpoint, OR
2. A Tavily / Perplexity API key for a dedicated search provider.

The AIRouter scaffolding (src/services/ai/router.py) already routes
through `web_search()` with a provider list, so adding the winning
provider is a small implementation, not an architecture change.
