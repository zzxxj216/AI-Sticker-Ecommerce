"""Per-host HTTP proxy routing for outbound AI API calls.

Some providers (jiekou.ai, Google Gemini) need a local proxy; others
(highwayapi.ai image endpoints) must connect directly. Shell-wide
HTTPS_PROXY would send everything through the proxy and break the
direct hosts, so callers use these helpers with trust_env=False.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse


def host_from_url(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    return (urlparse(url).hostname or "").lower()


@lru_cache(maxsize=1)
def _direct_hosts() -> frozenset[str]:
    raw = os.getenv(
        "HTTP_DIRECT_HOSTS",
        "api.highwayapi.ai,highwayapi.ai,127.0.0.1,localhost",
    )
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


@lru_cache(maxsize=1)
def _proxy_hosts() -> frozenset[str]:
    raw = os.getenv(
        "HTTP_PROXY_HOSTS",
        "api.jiekou.ai,jiekou.ai,generativelanguage.googleapis.com,googleapis.com",
    )
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _host_matches(host: str, patterns: frozenset[str]) -> bool:
    host = (host or "").lower()
    if not host:
        return False
    for pattern in patterns:
        if host == pattern or host.endswith(f".{pattern}"):
            return True
    return False


def proxy_url() -> str:
    return (
        os.getenv("HTTP_PROXY_URL")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or ""
    ).strip()


def proxy_url_for(url: str) -> str | None:
    """Return proxy URL for this request, or None for direct connect."""
    host = host_from_url(url)
    if not host:
        return None
    if _host_matches(host, _direct_hosts()):
        return None
    if _host_matches(host, _proxy_hosts()):
        px = proxy_url()
        return px or None
    return None


def httpx_request_kwargs(url: str, **extra: Any) -> dict[str, Any]:
    """Kwargs for httpx.get/post/Client — never inherit shell proxy env."""
    out: dict[str, Any] = {"trust_env": False, **extra}
    px = proxy_url_for(url)
    if px:
        out["proxy"] = px
    return out


def httpx_client_kwargs(url: str, **extra: Any) -> dict[str, Any]:
    return httpx_request_kwargs(url, **extra)


def openai_http_client(base_url: str, *, timeout: float | int = 300) -> "httpx.Client":
    import httpx

    return httpx.Client(
        **httpx_client_kwargs(base_url or "https://api.openai.com", timeout=timeout),
    )
