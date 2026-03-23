"""Test Shopify API connectivity and permissions.

Quick diagnostic script to verify:
1. Network connection to the Shopify store
2. Token authentication
3. Available API permissions (blogs, articles, files)
"""

import requests
import sys
import os

SHOP_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "inkelligent.myshopify.com")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2024-01"

BASE_URL = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}"

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json",
}


def test_connection():
    """Test basic connectivity to the store."""
    print(f"[1/4] Testing connection to {SHOP_DOMAIN}...")
    try:
        r = requests.get(f"https://{SHOP_DOMAIN}", timeout=10)
        print(f"  Store reachable: HTTP {r.status_code}")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_shop_info():
    """Test Admin API authentication by fetching shop info."""
    print(f"\n[2/4] Testing Admin API auth (GET /shop.json)...")
    try:
        r = requests.get(f"{BASE_URL}/shop.json", headers=HEADERS, timeout=10)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            shop = r.json().get("shop", {})
            print(f"  Shop name: {shop.get('name')}")
            print(f"  Shop email: {shop.get('email')}")
            print(f"  Plan: {shop.get('plan_display_name')}")
            return True
        elif r.status_code == 401:
            print("  AUTH FAILED - Token is invalid or is a Storefront API token")
            print("  Need: Admin API access token (starts with shpat_)")
            print(f"  Your token starts with: {ACCESS_TOKEN[:6]}...")
            return False
        elif r.status_code == 403:
            print("  FORBIDDEN - Token valid but missing permissions")
            return False
        else:
            print(f"  Response: {r.text[:300]}")
            return False
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_blogs():
    """Test blog read access."""
    print(f"\n[3/4] Testing blog access (GET /blogs.json)...")
    try:
        r = requests.get(f"{BASE_URL}/blogs.json", headers=HEADERS, timeout=10)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            blogs = r.json().get("blogs", [])
            print(f"  Found {len(blogs)} blog(s):")
            for blog in blogs:
                print(f"    - id={blog['id']}, handle=\"{blog.get('handle')}\", title=\"{blog.get('title')}\"")
            return True
        else:
            print(f"  Response: {r.text[:300]}")
            return False
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def test_articles():
    """Test article read access."""
    print(f"\n[4/4] Testing article access...")
    try:
        r = requests.get(f"{BASE_URL}/blogs.json", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"  Skipped (no blog access)")
            return False

        blogs = r.json().get("blogs", [])
        if not blogs:
            print("  No blogs found. You may need to create one in Shopify Admin.")
            return False

        blog_id = blogs[0]["id"]
        r2 = requests.get(
            f"{BASE_URL}/blogs/{blog_id}/articles.json",
            headers=HEADERS,
            params={"limit": 3},
            timeout=10,
        )
        print(f"  Status: {r2.status_code}")
        if r2.status_code == 200:
            articles = r2.json().get("articles", [])
            print(f"  Found {len(articles)} article(s) in blog '{blogs[0].get('title')}':")
            for a in articles[:3]:
                print(f"    - \"{a.get('title')}\" (handle: {a.get('handle')})")
            return True
        else:
            print(f"  Response: {r2.text[:300]}")
            return False
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def main():
    if not ACCESS_TOKEN:
        print("ERROR: No token provided.")
        print("Usage: set SHOPIFY_ACCESS_TOKEN=shpat_xxx then run this script")
        sys.exit(1)

    print("=" * 50)
    print("Shopify API Connection Test")
    print("=" * 50)
    print(f"Store:   {SHOP_DOMAIN}")
    print(f"Token:   {ACCESS_TOKEN[:8]}...{ACCESS_TOKEN[-4:]}")
    print(f"API:     {API_VERSION}")
    print("=" * 50)

    results = {}
    results["connection"] = test_connection()
    results["auth"] = test_shop_info()
    results["blogs"] = test_blogs()
    results["articles"] = test_articles()

    print("\n" + "=" * 50)
    print("Summary:")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name:15s} [{status}]")
    print("=" * 50)

    if not results["auth"]:
        print("\nNext steps:")
        print("1. Go to Shopify Admin > Settings > Apps > Your Custom App")
        print("2. Click 'API credentials' tab")
        print("3. Copy the 'Admin API access token' (starts with shpat_)")
        print("4. Set it: $env:SHOPIFY_ACCESS_TOKEN='shpat_xxx'")


if __name__ == "__main__":
    main()
