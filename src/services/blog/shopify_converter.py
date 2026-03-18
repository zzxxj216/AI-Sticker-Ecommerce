"""Markdown -> Shopify-ready HTML converter.

Two main workflows:
1. convert_file() / convert_draft()  -> ShopifyArticle (structured data)
2. generate_paste_ready_html()       -> standalone .html you open in a browser,
                                        select the content, and paste into
                                        Shopify's rich-text editor.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List

import markdown
from markdown.extensions.toc import TocExtension


@dataclass
class BlogMetadata:
    """Metadata parsed from the HTML comment header."""
    title: str = ""
    description: str = ""
    url_slug: str = ""
    keywords: list[str] = field(default_factory=list)
    generated: str = ""
    iteration: int = 1


@dataclass
class ShopifyArticle:
    """Structured output ready for Shopify Admin API."""
    title: str
    body_html: str
    handle: str
    meta_title: str
    meta_description: str
    tags: str
    image_urls: list[str] = field(default_factory=list)

    def to_api_payload(self, author: str = "Store Team", blog_id: Optional[int] = None) -> dict:
        """Generate the JSON payload for POST /admin/api/.../articles.json"""
        payload: dict = {
            "article": {
                "title": self.title,
                "author": author,
                "body_html": self.body_html,
                "handle": self.handle,
                "tags": self.tags,
                "metafields": [
                    {
                        "key": "title_tag",
                        "value": self.meta_title,
                        "type": "single_line_text_field",
                        "namespace": "global",
                    },
                    {
                        "key": "description_tag",
                        "value": self.meta_description,
                        "type": "single_line_text_field",
                        "namespace": "global",
                    },
                ],
            }
        }
        if blog_id:
            payload["article"]["blog_id"] = blog_id
        if self.image_urls:
            payload["article"]["image"] = {
                "src": self.image_urls[0],
                "alt": self.meta_title,
            }
        return payload


class ShopifyConverter:
    """Converts markdown blog content to Shopify-ready HTML."""

    METADATA_PATTERN = re.compile(
        r"<!--\s*(.*?)\s*-->", re.DOTALL
    )
    META_LINE_PATTERN = re.compile(r"^(\w[\w\s]*?):\s*(.+)$", re.MULTILINE)
    IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def __init__(self, store_url: str = "https://your-store.myshopify.com"):
        self.store_url = store_url.rstrip("/")
        self.md = markdown.Markdown(
            extensions=[
                "extra",
                "smarty",
                "sane_lists",
                TocExtension(permalink=False),
            ],
            output_format="html",
        )

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def convert_file(
        self,
        md_path: str,
        image_base_url: Optional[str] = None,
    ) -> ShopifyArticle:
        """Convert a markdown blog file to a ShopifyArticle."""
        path = Path(md_path)
        raw = path.read_text(encoding="utf-8")

        meta = self._parse_metadata(raw)
        body_md = self._strip_metadata(raw)

        if image_base_url:
            body_md = self._rewrite_image_urls(body_md, image_base_url)

        body_md = self._rewrite_internal_links(body_md)
        image_urls = self._extract_image_urls(body_md)

        body_md = self._fix_nested_lists(body_md)
        self.md.reset()
        body_html = self.md.convert(body_md)
        body_html = self._post_process_html(body_html)

        handle = meta.url_slug.strip("/").split("/")[-1] if meta.url_slug else path.stem

        return ShopifyArticle(
            title=meta.title or self._extract_h1(body_md),
            body_html=body_html,
            handle=handle,
            meta_title=meta.title,
            meta_description=meta.description,
            tags=", ".join(meta.keywords),
            image_urls=image_urls,
        )

    def convert_draft(
        self,
        content_md: str,
        meta_title: str,
        meta_description: str,
        url_slug: str,
        keywords: List[str],
    ) -> ShopifyArticle:
        """Convert an in-memory markdown draft to a ShopifyArticle.

        Called by the orchestrator right after finalization so we don't
        need to re-read the .md file from disk.
        """
        body_md = self._rewrite_internal_links(content_md)
        image_urls = self._extract_image_urls(body_md)

        body_md = self._fix_nested_lists(body_md)
        self.md.reset()
        body_html = self.md.convert(body_md)
        body_html = self._post_process_html(body_html)

        handle = url_slug.strip("/").split("/")[-1] if url_slug else "untitled"

        return ShopifyArticle(
            title=meta_title or self._extract_h1(body_md),
            body_html=body_html,
            handle=handle,
            meta_title=meta_title,
            meta_description=meta_description,
            tags=", ".join(keywords),
            image_urls=image_urls,
        )

    def generate_paste_ready_html(
        self,
        article: ShopifyArticle,
        output_path: str,
        images_dir: Optional[str] = None,
    ) -> str:
        """Generate a browser-previewable HTML file.

        Open this file in the browser, select the blog content between the
        red lines, Ctrl+C, then Ctrl+V into Shopify's rich-text editor.
        Formatting is preserved as rich text — no raw HTML tags.

        Args:
            article: The ShopifyArticle to render.
            output_path: Where to save the .html file.
            images_dir: If set, copy images here for local preview.

        Returns:
            The absolute path to the saved .html file.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        featured_image_info = ""
        if article.image_urls:
            featured_image_info = f"""
    <tr>
      <td>Featured image</td>
      <td>Upload the first image from the images folder</td>
    </tr>
    <tr>
      <td>Featured image alt</td>
      <td>{self._escape_html(article.meta_title)}</td>
    </tr>"""

        tags_rows = "".join(
            f"<li><code>{t.strip()}</code></li>"
            for t in article.tags.split(",") if t.strip()
        )

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{self._escape_html(article.title)} - Shopify Paste Ready</title>
<style>
  body {{
    max-width: 780px;
    margin: 40px auto;
    padding: 0 20px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.7;
    color: #1a1a1a;
  }}
  .instructions {{
    background: #fff3cd;
    border: 2px dashed #e6a800;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 40px;
    font-size: 15px;
  }}
  .instructions h2 {{ margin-top: 0; color: #856404; }}
  .instructions ol li {{ margin-bottom: 8px; }}
  .instructions code {{ background: #ffeeba; padding: 2px 6px; border-radius: 3px; }}
  .seo-box {{
    background: #e8f4fd;
    border: 2px dashed #0288d1;
    border-radius: 8px;
    padding: 20px 24px;
    margin-top: 40px;
    font-size: 15px;
  }}
  .seo-box h2 {{ margin-top: 0; color: #01579b; }}
  .seo-box table {{ border-collapse: collapse; width: 100%; }}
  .seo-box td {{ padding: 8px 12px; border-bottom: 1px solid #b3d9f2; vertical-align: top; }}
  .seo-box td:first-child {{ font-weight: bold; white-space: nowrap; width: 160px; }}
  .separator {{ border: 3px solid #e53935; margin: 40px 0 20px; }}
  .separator-label {{
    text-align: center;
    color: #e53935;
    font-weight: bold;
    font-size: 18px;
    margin-bottom: 20px;
  }}
  #blog-content img {{ max-width: 100%; height: auto; border-radius: 6px; }}
  #blog-content hr {{ border: none; border-top: 1px solid #ddd; margin: 30px 0; }}
</style>
</head>
<body>

<div class="instructions">
  <h2>How to paste into Shopify</h2>
  <ol>
    <li>Select <strong>ONLY the blog content</strong> between the two red lines below</li>
    <li>Press <code>Ctrl+C</code> (or Cmd+C on Mac) to copy</li>
    <li>Go to <strong>Shopify Admin &rarr; Online Store &rarr; Blog posts &rarr; Add blog post</strong></li>
    <li>Click in the content editor (<strong>stay in rich-text mode</strong>, do NOT click &lt;&gt;)</li>
    <li>Press <code>Ctrl+V</code> to paste &mdash; formatting is preserved automatically</li>
    <li>Upload images: go to each image placeholder, click it, and upload the matching file from the images folder</li>
    <li>Fill in SEO fields using the blue box at the bottom of this page</li>
  </ol>
</div>

<hr class="separator">
<div class="separator-label">&#11015;&#11015;&#11015; SELECT FROM HERE &#11015;&#11015;&#11015;</div>

<div id="blog-content">
{article.body_html}
</div>

<div class="separator-label">&#11014;&#11014;&#11014; SELECT TO HERE &#11014;&#11014;&#11014;</div>
<hr class="separator">

<div class="seo-box">
  <h2>SEO &amp; Meta Info (fill in Shopify manually)</h2>
  <table>
    <tr>
      <td>Page title</td>
      <td>{self._escape_html(article.meta_title)}</td>
    </tr>
    <tr>
      <td>Meta description</td>
      <td>{self._escape_html(article.meta_description)}</td>
    </tr>
    <tr>
      <td>URL handle</td>
      <td><code>{self._escape_html(article.handle)}</code></td>
    </tr>
    <tr>
      <td>Tags</td>
      <td><ul style="margin:0;padding-left:18px;">{tags_rows}</ul></td>
    </tr>{featured_image_info}
  </table>
</div>

</body>
</html>"""

        out.write_text(html_doc, encoding="utf-8")
        return str(out.resolve())

    # ----------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------

    def _parse_metadata(self, raw: str) -> BlogMetadata:
        match = self.METADATA_PATTERN.search(raw)
        if not match:
            return BlogMetadata()

        block = match.group(1)
        meta = BlogMetadata()
        for m in self.META_LINE_PATTERN.finditer(block):
            key, value = m.group(1).strip().lower(), m.group(2).strip()
            if key == "title":
                meta.title = value
            elif key == "description":
                meta.description = value
            elif key == "url slug":
                meta.url_slug = value
            elif key == "keywords":
                meta.keywords = [k.strip() for k in value.split(",")]
            elif key == "generated":
                meta.generated = value
            elif key == "iteration":
                meta.iteration = int(value)
        return meta

    def _strip_metadata(self, raw: str) -> str:
        return self.METADATA_PATTERN.sub("", raw, count=1).strip()

    def _rewrite_image_urls(self, md_text: str, base_url: str) -> str:
        """Replace relative image paths with absolute CDN URLs."""
        base = base_url.rstrip("/")

        def replacer(m: re.Match) -> str:
            alt, src = m.group(1), m.group(2)
            if src.startswith(("http://", "https://")):
                return m.group(0)
            clean_src = src.lstrip("./")
            return f"![{alt}]({base}/{clean_src})"

        return self.IMAGE_PATTERN.sub(replacer, md_text)

    def _rewrite_internal_links(self, md_text: str) -> str:
        """Convert relative links like (/collections/...) to full store URLs."""
        def replacer(m: re.Match) -> str:
            text, href = m.group(1), m.group(2)
            if href.startswith("/"):
                return f"[{text}]({self.store_url}{href})"
            return m.group(0)

        return re.sub(r"\[([^\]]+)\]\((/[^)]+)\)", replacer, md_text)

    def _extract_image_urls(self, md_text: str) -> list[str]:
        return [m.group(2) for m in self.IMAGE_PATTERN.finditer(md_text)]

    def _extract_h1(self, md_text: str) -> str:
        m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
        return m.group(1).strip() if m else "Untitled"

    @staticmethod
    def _fix_nested_lists(md_text: str) -> str:
        """Convert 2-space nested list indentation to 4-space.

        LLMs often generate nested markdown lists with 2-space indent,
        but Python's ``markdown`` library requires 4 spaces to
        recognize nested ``<ul>`` / ``<ol>`` structures.
        """
        lines = md_text.split("\n")
        result = []
        for line in lines:
            match = re.match(r"^( +)([-*+]|\d+\.)\s", line)
            if match:
                spaces = len(match.group(1))
                level = spaces // 2
                line = "    " * level + line.lstrip()
            result.append(line)
        return "\n".join(result)

    def _post_process_html(self, html: str) -> str:
        """Apply Shopify-friendly tweaks to the generated HTML."""
        html = html.replace("<hr />", "<hr>")
        html = re.sub(
            r'<img ([^>]*?)(/?)>',
            lambda m: f'<img {m.group(1).strip()} loading="lazy" style="max-width:100%;height:auto;">',
            html,
        )
        return html

    @staticmethod
    def _escape_html(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
