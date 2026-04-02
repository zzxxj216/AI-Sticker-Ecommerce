"""Publish a blog article with images to Shopify.

Test script: uploads images to Shopify Files, then creates an article.
"""

import json
import os
import re
import sys
import time
import requests
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT

from src.services.blog.shopify_converter import ShopifyConverter

SHOP_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "inkelligent.myshopify.com")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2024-01"
BLOG_ID = 121522192750  # "Blog" (main blog from test results)

REST_BASE = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}"
GRAPHQL_URL = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json",
}


def graphql(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        print(f"  GraphQL errors: {data['errors']}")
    return data


def upload_image(image_path: Path) -> str | None:
    """Upload a single image to Shopify Files. Returns the CDN URL."""
    filename = image_path.name
    file_size = image_path.stat().st_size
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"

    print(f"  Uploading: {filename} ({file_size / 1024:.0f} KB)...")

    # Step 1: Get presigned upload target
    stage_query = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets {
          url
          resourceUrl
          parameters { name value }
        }
        userErrors { field message }
      }
    }
    """
    stage_vars = {
        "input": [{
            "filename": filename,
            "mimeType": mime,
            "httpMethod": "POST",
            "resource": "FILE",
            "fileSize": str(file_size),
        }]
    }

    result = graphql(stage_query, stage_vars)
    staged = result.get("data", {}).get("stagedUploadsCreate", {})

    if staged.get("userErrors"):
        print(f"  Stage errors: {staged['userErrors']}")
        return None

    targets = staged.get("stagedTargets", [])
    if not targets:
        print("  No staged targets returned")
        return None

    target = targets[0]
    upload_url = target["url"]
    resource_url = target["resourceUrl"]
    params = {p["name"]: p["value"] for p in target["parameters"]}

    # Step 2: Upload file via multipart POST
    form_data = list(params.items())
    with open(image_path, "rb") as f:
        files = {"file": (filename, f, mime)}
        upload_resp = requests.post(
            upload_url,
            data=form_data,
            files=files,
            timeout=60,
        )

    if upload_resp.status_code not in (200, 201, 204):
        print(f"  Upload failed: HTTP {upload_resp.status_code}")
        print(f"  {upload_resp.text[:200]}")
        return None

    # Step 3: Register file in Shopify
    create_query = """
    mutation fileCreate($files: [FileCreateInput!]!) {
      fileCreate(files: $files) {
        files {
          ... on MediaImage {
            id
            image { url }
          }
        }
        userErrors { field message }
      }
    }
    """
    create_vars = {
        "files": [{
            "alt": filename.rsplit(".", 1)[0],
            "contentType": "IMAGE",
            "originalSource": resource_url,
        }]
    }

    create_result = graphql(create_query, create_vars)
    file_create = create_result.get("data", {}).get("fileCreate", {})

    if file_create.get("userErrors"):
        print(f"  File create errors: {file_create['userErrors']}")
        return None

    created_files = file_create.get("files", [])
    if created_files and created_files[0].get("image"):
        cdn_url = created_files[0]["image"]["url"]
        print(f"  OK -> {cdn_url[:80]}...")
        return cdn_url

    # Image URL may not be immediately available; poll for it
    print("  Waiting for image processing...")
    time.sleep(3)

    # Try to get the URL by querying files
    file_id = created_files[0].get("id") if created_files else None
    if file_id:
        poll_query = """
        query getFile($id: ID!) {
          node(id: $id) {
            ... on MediaImage {
              image { url }
              fileStatus
            }
          }
        }
        """
        for attempt in range(5):
            poll_result = graphql(poll_query, {"id": file_id})
            node = poll_result.get("data", {}).get("node", {})
            if node.get("image", {}).get("url"):
                cdn_url = node["image"]["url"]
                print(f"  OK -> {cdn_url[:80]}...")
                return cdn_url
            status = node.get("fileStatus", "UNKNOWN")
            print(f"  File status: {status}, waiting...")
            time.sleep(3)

    print("  WARNING: Could not get CDN URL, using resource URL as fallback")
    return resource_url


def replace_image_urls(html: str, url_map: dict) -> str:
    """Replace local image paths in HTML with Shopify CDN URLs."""
    for local_path, cdn_url in url_map.items():
        html = html.replace(local_path, cdn_url)
    return html


def create_article(article_payload: dict) -> dict:
    """Create a blog article via REST Admin API."""
    url = f"{REST_BASE}/blogs/{BLOG_ID}/articles.json"
    r = requests.post(url, headers=HEADERS, json=article_payload, timeout=30)
    print(f"  Create article status: {r.status_code}")
    if r.status_code == 201:
        return r.json().get("article", {})
    else:
        print(f"  Error: {r.text[:500]}")
        return {}


def main():
    if not ACCESS_TOKEN:
        print("ERROR: Set SHOPIFY_ACCESS_TOKEN environment variable")
        sys.exit(1)

    md_file = PROJECT_ROOT / "output" / "blogs" / "route-66-roadtrip-stickers_20260318_131629.md"
    images_dir = PROJECT_ROOT / "output" / "blogs" / "images" / "route-66-roadtrip-stickers_20260318_131629"

    if not md_file.exists():
        print(f"ERROR: Markdown file not found: {md_file}")
        sys.exit(1)

    print("=" * 50)
    print("Shopify Blog Publisher - Test")
    print("=" * 50)

    # Step 1: Convert markdown to ShopifyArticle
    print("\n[1/3] Converting markdown to HTML...")
    converter = ShopifyConverter(store_url=f"https://{SHOP_DOMAIN}")
    article = converter.convert_file(str(md_file))
    print(f"  Title: {article.title}")
    print(f"  Handle: {article.handle}")
    print(f"  Tags: {article.tags}")
    print(f"  Images found in content: {len(article.image_urls)}")

    # Step 2: Upload images
    print(f"\n[2/3] Uploading images to Shopify Files...")
    url_map = {}

    if images_dir.exists():
        image_files = sorted(images_dir.glob("*.png"))
        print(f"  Found {len(image_files)} image files")

        for img_path in image_files:
            cdn_url = upload_image(img_path)
            if cdn_url:
                local_ref = f"route-66-roadtrip-stickers_20260318_131629/{img_path.name}"
                url_map[local_ref] = cdn_url
    else:
        print(f"  No images directory found at {images_dir}")

    # Step 3: Replace image URLs in HTML and create article
    print(f"\n[3/3] Creating article on Shopify...")

    if url_map:
        article.body_html = replace_image_urls(article.body_html, url_map)
        article.image_urls = list(url_map.values())
        print(f"  Replaced {len(url_map)} image URLs with CDN URLs")

    payload = article.to_api_payload(author="Inkelligent Team", blog_id=BLOG_ID)
    payload["article"]["published"] = False  # Draft mode for safety

    result = create_article(payload)

    if result:
        print(f"\n{'=' * 50}")
        print("SUCCESS! Article created as DRAFT")
        print(f"  ID: {result.get('id')}")
        print(f"  Title: {result.get('title')}")
        print(f"  Handle: {result.get('handle')}")
        print(f"  Preview: https://{SHOP_DOMAIN}/admin/articles/{result.get('id')}")
        print(f"{'=' * 50}")
    else:
        print("\nFAILED to create article")


if __name__ == "__main__":
    main()
