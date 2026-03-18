"""Demo: Convert a Markdown blog post → Shopify-ready HTML.

Usage:
    python scripts/demo_md_to_shopify.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.blog.shopify_converter import ShopifyConverter


SAMPLE_MD = "output/blogs/cowboy-western-sticker-ideas-laptop-water-bottle_20260317_154805.md"

STORE_URL = "https://your-store.myshopify.com"
IMAGE_CDN_BASE = "https://cdn.shopify.com/s/files/1/your-store/blogs"


def main():
    converter = ShopifyConverter(store_url=STORE_URL)

    article = converter.convert_file(
        md_path=SAMPLE_MD,
        image_base_url=IMAGE_CDN_BASE,
    )

    # ── 1. Preview metadata ─────────────────────────────────
    print("=" * 70)
    print("SHOPIFY ARTICLE PREVIEW")
    print("=" * 70)
    print(f"Title        : {article.title}")
    print(f"Handle       : {article.handle}")
    print(f"Meta Title   : {article.meta_title}")
    print(f"Meta Desc    : {article.meta_description}")
    print(f"Tags         : {article.tags}")
    print(f"Images ({len(article.image_urls)})   :")
    for url in article.image_urls:
        print(f"  - {url}")

    # ── 2. Preview body_html (first 2000 chars) ─────────────
    print("\n" + "-" * 70)
    print("BODY HTML (first 2000 chars):")
    print("-" * 70)
    print(article.body_html[:2000])
    if len(article.body_html) > 2000:
        print(f"\n... ({len(article.body_html)} chars total)")

    # ── 3. Generate API payload ─────────────────────────────
    payload = article.to_api_payload(
        author="StickerShop Team",
        blog_id=123456789,
    )

    # ── 4. Save outputs ────────────────────────────────────
    output_dir = Path("output/shopify_preview")
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / f"{article.handle}.html"
    html_path.write_text(article.body_html, encoding="utf-8")
    print(f"\n[OK] HTML saved  -> {html_path}")

    json_path = output_dir / f"{article.handle}_api_payload.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] API payload -> {json_path}")

    # ── 5. Show API payload snippet ─────────────────────────
    print("\n" + "-" * 70)
    print("SHOPIFY API PAYLOAD (snippet):")
    print("-" * 70)
    snippet = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(snippet) > 1500:
        print(snippet[:1500])
        print(f"\n... ({len(snippet)} chars total, see full file: {json_path})")
    else:
        print(snippet)


if __name__ == "__main__":
    main()
