"""Shopify Blog Publisher.

Uploads images to Shopify Files and creates blog articles
via the Shopify Admin API.

Supports two authentication modes:
1. Static access token (shpat_...) via SHOPIFY_ACCESS_TOKEN
2. OAuth client_credentials flow via SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET
   — fetches a fresh token automatically before each publish.
"""

import base64
import os
import re
import time
import requests
from pathlib import Path
from typing import Optional

from src.core.logger import get_logger
from src.services.blog.shopify_converter import ShopifyArticle

logger = get_logger("shopify_publisher")


def _obtain_token_via_client_credentials(
    shop_domain: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Exchange client_id + client_secret for an access token."""
    url = f"https://{shop_domain}/admin/oauth/access_token"
    auth_bytes = f"{client_id}:{client_secret}".encode("ascii")
    auth_b64 = base64.b64encode(auth_bytes).decode("ascii")

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=15,
    )

    if resp.status_code != 200:
        raise ValueError(
            f"OAuth token request failed ({resp.status_code}): {resp.text}"
        )

    token = resp.json().get("access_token", "")
    if not token:
        raise ValueError("OAuth response did not contain access_token")

    logger.info("Obtained fresh Shopify access token via client_credentials")
    return token


class ShopifyPublisher:
    """Publishes blog articles with images to a Shopify store."""

    def __init__(
        self,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        api_version: str = "2024-01",
        blog_handle: str = "blog",
    ):
        self.shop_domain = shop_domain or os.getenv("SHOPIFY_STORE_DOMAIN", "")
        self.api_version = api_version
        self.blog_handle = blog_handle

        self._client_id = client_id or os.getenv("SHOPIFY_CLIENT_ID", "")
        self._client_secret = client_secret or os.getenv("SHOPIFY_CLIENT_SECRET", "")

        static_token = access_token or os.getenv("SHOPIFY_ACCESS_TOKEN", "")

        if self._client_id and self._client_secret and self.shop_domain:
            self.access_token = _obtain_token_via_client_credentials(
                self.shop_domain, self._client_id, self._client_secret,
            )
        elif static_token:
            self.access_token = static_token
        else:
            raise ValueError(
                "Shopify credentials not configured. Either set "
                "SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (recommended) "
                "or SHOPIFY_ACCESS_TOKEN in .env"
            )

        if not self.shop_domain:
            raise ValueError("SHOPIFY_STORE_DOMAIN is not set")

        self._rest_base = f"https://{self.shop_domain}/admin/api/{self.api_version}"
        self._graphql_url = f"{self._rest_base}/graphql.json"
        self._headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }
        self._blog_id: Optional[int] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.shop_domain and self.access_token)

    def get_blog_id(self, handle: Optional[str] = None) -> int:
        """Fetch the blog ID by handle. Caches after first call."""
        if self._blog_id:
            return self._blog_id

        target = handle or self.blog_handle
        r = requests.get(
            f"{self._rest_base}/blogs.json",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()

        for blog in r.json().get("blogs", []):
            if blog.get("handle") == target:
                self._blog_id = blog["id"]
                logger.info(f"Found blog '{target}' with id={self._blog_id}")
                return self._blog_id

        blogs = r.json().get("blogs", [])
        if blogs:
            self._blog_id = blogs[0]["id"]
            logger.warning(
                f"Blog handle '{target}' not found, using first blog: "
                f"'{blogs[0].get('title')}' (id={self._blog_id})"
            )
            return self._blog_id

        raise ValueError(f"No blogs found in store {self.shop_domain}")

    def publish(
        self,
        article: ShopifyArticle,
        images_dir: Optional[Path] = None,
        author: str = "Inkelligent Team",
        published: bool = False,
    ) -> dict:
        """Full publish flow: upload images, then create article.

        Args:
            article: The ShopifyArticle to publish.
            images_dir: Directory containing image files to upload.
            author: Author name for the article.
            published: If True, publish immediately. If False, save as draft.

        Returns:
            The created article data dict from Shopify API.
        """
        blog_id = self.get_blog_id()

        # Upload images and replace local paths with CDN URLs
        if images_dir and images_dir.exists():
            url_map = self._upload_all_images(images_dir)
            if url_map:
                article.body_html = self._replace_image_urls(
                    article.body_html, url_map
                )
                article.image_urls = list(url_map.values())
                logger.info(f"Replaced {len(url_map)} image URLs with CDN URLs")

        # Create the article
        payload = article.to_api_payload(author=author, blog_id=blog_id)
        payload["article"]["published"] = published

        r = requests.post(
            f"{self._rest_base}/blogs/{blog_id}/articles.json",
            headers=self._headers,
            json=payload,
            timeout=30,
        )

        if r.status_code == 201:
            result = r.json().get("article", {})
            logger.info(
                f"Article created: id={result.get('id')}, "
                f"handle={result.get('handle')}, "
                f"published={published}"
            )
            return result

        logger.error(f"Failed to create article: {r.status_code} - {r.text[:500]}")
        r.raise_for_status()
        return {}

    def _upload_all_images(self, images_dir: Path) -> dict[str, str]:
        """Upload all images from a directory. Returns {local_ref: cdn_url}."""
        url_map = {}
        image_files = sorted(
            f for f in images_dir.iterdir()
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )

        if not image_files:
            logger.info("No images found to upload")
            return url_map

        logger.info(f"Uploading {len(image_files)} images to Shopify Files...")

        for img_path in image_files:
            cdn_url = self._upload_single_image(img_path)
            if cdn_url:
                local_ref = f"{images_dir.name}/{img_path.name}"
                url_map[local_ref] = cdn_url

        logger.info(f"Uploaded {len(url_map)}/{len(image_files)} images successfully")
        return url_map

    @staticmethod
    def _clean_image_filename(filename: str) -> str:
        """Strip the 'image_NN_' prefix from generated image filenames.

        e.g. 'image_01_cowboy sticker.png' -> 'cowboy sticker.png'
        """
        return re.sub(r"^image_\d+_", "", filename)

    def _upload_single_image(self, image_path: Path) -> Optional[str]:
        """Upload a single image to Shopify Files via GraphQL staged upload."""
        raw_filename = image_path.name
        filename = self._clean_image_filename(raw_filename)
        file_size = image_path.stat().st_size
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime = mime_map.get(image_path.suffix.lower(), "image/png")

        logger.info(f"Uploading {filename} ({file_size / 1024:.0f} KB)...")

        # Step 1: staged upload target
        stage_result = self._graphql(
            """
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
            """,
            {
                "input": [{
                    "filename": filename,
                    "mimeType": mime,
                    "httpMethod": "POST",
                    "resource": "FILE",
                    "fileSize": str(file_size),
                }]
            },
        )

        staged = stage_result.get("data", {}).get("stagedUploadsCreate", {})
        if staged.get("userErrors"):
            logger.error(f"Staged upload errors: {staged['userErrors']}")
            return None

        targets = staged.get("stagedTargets", [])
        if not targets:
            logger.error("No staged targets returned")
            return None

        target = targets[0]
        upload_url = target["url"]
        resource_url = target["resourceUrl"]
        params = {p["name"]: p["value"] for p in target["parameters"]}

        # Step 2: POST file to presigned URL
        with open(image_path, "rb") as f:
            upload_resp = requests.post(
                upload_url,
                data=list(params.items()),
                files={"file": (filename, f, mime)},
                timeout=60,
            )

        if upload_resp.status_code not in (200, 201, 204):
            logger.error(f"Upload failed: HTTP {upload_resp.status_code}")
            return None

        # Step 3: Register file in Shopify
        create_result = self._graphql(
            """
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
            """,
            {
                "files": [{
                    "alt": filename.rsplit(".", 1)[0],
                    "contentType": "IMAGE",
                    "originalSource": resource_url,
                }]
            },
        )

        file_create = create_result.get("data", {}).get("fileCreate", {})
        if file_create.get("userErrors"):
            logger.error(f"File create errors: {file_create['userErrors']}")
            return None

        created_files = file_create.get("files", [])
        first_file = created_files[0] if created_files else None

        # Try to get URL directly
        if first_file and isinstance(first_file, dict):
            url = (first_file.get("image") or {}).get("url")
            if url:
                return url

        # Poll for image processing completion
        file_id = first_file.get("id") if first_file and isinstance(first_file, dict) else None
        if file_id:
            return self._poll_image_url(file_id)

        logger.warning(f"Could not get CDN URL for {filename}, using resource URL")
        return resource_url

    def _poll_image_url(self, file_id: str, max_attempts: int = 8) -> Optional[str]:
        """Poll until the image URL is available."""
        for attempt in range(max_attempts):
            time.sleep(2 + attempt)
            result = self._graphql(
                """
                query getFile($id: ID!) {
                  node(id: $id) {
                    ... on MediaImage {
                      image { url }
                      fileStatus
                    }
                  }
                }
                """,
                {"id": file_id},
            )
            node = result.get("data", {}).get("node", {})
            url = node.get("image", {}).get("url")
            if url:
                return url
            status = node.get("fileStatus", "UNKNOWN")
            logger.debug(f"Image status: {status}, attempt {attempt + 1}/{max_attempts}")

        logger.warning(f"Timed out waiting for image {file_id}")
        return None

    def _graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        """Execute a GraphQL query against the Shopify Admin API."""
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        r = requests.post(
            self._graphql_url,
            headers=self._headers,
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            logger.error(f"GraphQL errors: {data['errors']}")
        return data

    # ------------------------------------------------------------------
    # Article Management API
    # ------------------------------------------------------------------

    def list_articles(
        self,
        status: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """List articles from the blog.

        Args:
            status: Filter by 'published' or 'draft'. None for all.
            limit: Max number of articles to return.

        Returns:
            List of article dicts with id, title, handle, published, created_at.
        """
        blog_id = self.get_blog_id()
        params: dict = {"limit": min(limit, 250)}
        if status == "published":
            params["published_status"] = "published"
        elif status == "draft":
            params["published_status"] = "unpublished"

        r = requests.get(
            f"{self._rest_base}/blogs/{blog_id}/articles.json",
            headers=self._headers,
            params=params,
            timeout=15,
        )
        r.raise_for_status()

        articles = r.json().get("articles", [])
        return [
            {
                "id": a["id"],
                "title": a.get("title", ""),
                "handle": a.get("handle", ""),
                "published": a.get("published_at") is not None,
                "created_at": a.get("created_at", ""),
                "updated_at": a.get("updated_at", ""),
            }
            for a in articles
        ]

    def get_article(self, article_id: int) -> dict:
        """Get a single article by ID."""
        blog_id = self.get_blog_id()
        r = requests.get(
            f"{self._rest_base}/blogs/{blog_id}/articles/{article_id}.json",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("article", {})

    def update_article(
        self,
        article_id: int,
        article: "ShopifyArticle",
        images_dir: Optional[Path] = None,
        published: Optional[bool] = None,
    ) -> dict:
        """Update an existing Shopify article's content, images, and metadata.

        Args:
            article_id: The existing Shopify article ID.
            article: ShopifyArticle with updated content.
            images_dir: Optional directory with new/updated images to upload.
            published: If set, also update publish status.

        Returns:
            Updated article dict from Shopify API.
        """
        blog_id = self.get_blog_id()

        if images_dir and images_dir.exists():
            url_map = self._upload_all_images(images_dir)
            if url_map:
                article.body_html = self._replace_image_urls(
                    article.body_html, url_map
                )
                article.image_urls = list(url_map.values())
                logger.info(f"Replaced {len(url_map)} image URLs with CDN URLs")

        payload: dict = {
            "article": {
                "id": article_id,
                "title": article.title,
                "body_html": article.body_html,
                "handle": article.handle,
                "tags": article.tags,
                "metafields": [
                    {
                        "key": "title_tag",
                        "value": article.meta_title,
                        "type": "single_line_text_field",
                        "namespace": "global",
                    },
                    {
                        "key": "description_tag",
                        "value": article.meta_description,
                        "type": "single_line_text_field",
                        "namespace": "global",
                    },
                ],
            }
        }
        if published is not None:
            payload["article"]["published"] = published

        r = requests.put(
            f"{self._rest_base}/blogs/{blog_id}/articles/{article_id}.json",
            headers=self._headers,
            json=payload,
            timeout=30,
        )

        if r.status_code == 200:
            result = r.json().get("article", {})
            logger.info(f"Article {article_id} updated: {result.get('title', '')}")
            return result

        logger.error(f"Failed to update article: {r.status_code} - {r.text[:500]}")
        r.raise_for_status()
        return {}

    def update_article_status(self, article_id: int, published: bool) -> dict:
        """Publish or unpublish an article.

        Args:
            article_id: The Shopify article ID.
            published: True to publish (make live), False to set as draft.

        Returns:
            Updated article dict.
        """
        blog_id = self.get_blog_id()
        payload = {
            "article": {
                "id": article_id,
                "published": published,
            }
        }
        r = requests.put(
            f"{self._rest_base}/blogs/{blog_id}/articles/{article_id}.json",
            headers=self._headers,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        article = r.json().get("article", {})
        action = "published" if published else "unpublished"
        logger.info(f"Article {article_id} {action}: {article.get('title', '')}")
        return article

    # ── Pages API ─────────────────────────────────────────────

    def create_page(
        self,
        title: str,
        body_html: str,
        handle: str = "",
        published: bool = True,
    ) -> dict:
        """Create a new Shopify Page."""
        payload: dict = {
            "page": {
                "title": title,
                "body_html": body_html,
                "published": published,
            }
        }
        if handle:
            payload["page"]["handle"] = handle

        r = requests.post(
            f"{self._rest_base}/pages.json",
            headers=self._headers,
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            page = r.json().get("page", {})
            logger.info(
                "Created Shopify page: id=%s handle=%s",
                page.get("id"), page.get("handle"),
            )
            return page

        logger.error("Failed to create page: %s - %s", r.status_code, r.text[:500])
        r.raise_for_status()
        return {}

    def update_page(self, page_id: int, body_html: str, title: str = "", published: bool = True) -> dict:
        """Update an existing Shopify Page."""
        payload: dict = {
            "page": {
                "id": page_id,
                "body_html": body_html,
                "published": published,
            }
        }
        if title:
            payload["page"]["title"] = title

        r = requests.put(
            f"{self._rest_base}/pages/{page_id}.json",
            headers=self._headers,
            json=payload,
            timeout=30,
        )
        if r.status_code == 200:
            page = r.json().get("page", {})
            logger.info("Updated Shopify page: id=%s", page.get("id"))
            return page

        logger.error("Failed to update page: %s - %s", r.status_code, r.text[:500])
        r.raise_for_status()
        return {}

    def find_page_by_handle(self, handle: str) -> dict | None:
        """Find a page by its handle."""
        r = requests.get(
            f"{self._rest_base}/pages.json",
            headers=self._headers,
            params={"handle": handle},
            timeout=15,
        )
        r.raise_for_status()
        pages = r.json().get("pages", [])
        for p in pages:
            if p.get("handle") == handle:
                return p
        return None

    def publish_page(
        self,
        title: str,
        body_html: str,
        handle: str = "sticker-calendar",
        published: bool = True,
    ) -> dict:
        """Create or update a Shopify Page by handle.

        Returns the page dict with id, handle, published_at, etc.
        """
        existing = self.find_page_by_handle(handle)
        if existing:
            page = self.update_page(
                existing["id"], body_html, title=title, published=published,
            )
        else:
            page = self.create_page(title, body_html, handle=handle, published=published)

        page_url = f"https://{self.shop_domain}/pages/{page.get('handle', handle)}"
        page["page_url"] = page_url
        return page

    def unpublish_page(self, handle: str = "sticker-calendar") -> dict:
        """Set a page to draft (hidden from storefront) by handle."""
        page = self.find_page_by_handle(handle)
        if not page:
            raise ValueError(f"Page with handle '{handle}' not found")
        payload = {"page": {"id": page["id"], "published": False}}
        r = requests.put(
            f"{self._rest_base}/pages/{page['id']}.json",
            headers=self._headers,
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        result = r.json().get("page", {})
        logger.info("Unpublished Shopify page: id=%s handle=%s", result.get("id"), handle)
        return result

    def delete_page(self, handle: str = "sticker-calendar") -> bool:
        """Permanently delete a page by handle."""
        page = self.find_page_by_handle(handle)
        if not page:
            raise ValueError(f"Page with handle '{handle}' not found")
        r = requests.delete(
            f"{self._rest_base}/pages/{page['id']}.json",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        logger.info("Deleted Shopify page: id=%s handle=%s", page["id"], handle)
        return True

    @staticmethod
    def _replace_image_urls(html: str, url_map: dict[str, str]) -> str:
        """Replace local image references in HTML with Shopify CDN URLs."""
        for local_ref, cdn_url in url_map.items():
            html = html.replace(local_ref, cdn_url)
        return html
