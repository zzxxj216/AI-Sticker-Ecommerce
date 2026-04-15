"""Blotato API client for TikTok video publishing.

Wraps Blotato's REST API v2 endpoints for:
- Listing connected TikTok accounts
- Obtaining presigned upload URLs for local files
- Publishing posts to TikTok
- Polling post submission status
"""

from __future__ import annotations

import os
from typing import Any

import requests

from src.core.logger import get_logger

logger = get_logger("services.tiktok.blotato")

BLOTATO_BASE_URL = "https://backend.blotato.com/v2"


class BlotaToService:
    """Thin wrapper around the Blotato publish API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("BLOTATO_API_KEY", "")
        self._timeout = 30
        raw_ids = os.getenv("BLOTATO_TK_ACCOUNT_IDS", "")
        self._allowed_account_ids: set[str] = {
            aid.strip() for aid in raw_ids.split(",") if aid.strip()
        } if raw_ids.strip() else set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "blotato-api-key": self.api_key,
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BLOTATO_BASE_URL}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_body: dict | None = None) -> dict:
        url = f"{BLOTATO_BASE_URL}{path}"
        resp = requests.post(url, headers=self._headers(), json=json_body, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tiktok_accounts(self) -> list[dict[str, Any]]:
        """Return connected TikTok accounts from Blotato.

        When ``BLOTATO_TK_ACCOUNT_IDS`` is set (comma-separated), only
        accounts whose id is in that list are returned.
        """
        data = self._get("/users/me/accounts", params={"platform": "tiktok"})
        items = data.get("items", [])
        if self._allowed_account_ids:
            items = [a for a in items if a.get("id") in self._allowed_account_ids]
        return items

    def get_presigned_url(self, filename: str) -> dict[str, str]:
        """Get a presigned upload URL for a local file.

        Returns ``{"presignedUrl": "...", "publicUrl": "..."}``.
        The caller should HTTP PUT the raw file bytes to ``presignedUrl``,
        then use ``publicUrl`` as a ``mediaUrls`` entry when publishing.
        """
        return self._post("/media/uploads", {"filename": filename})

    def publish_tiktok_video(
        self,
        account_id: str,
        text: str,
        media_urls: list[str],
        target_options: dict[str, Any],
        scheduled_time: str | None = None,
        use_next_free_slot: bool = False,
    ) -> dict[str, Any]:
        """Publish a video post to TikTok via Blotato.

        ``target_options`` must include TikTok-required fields such as
        ``privacyLevel``, ``disabledComments``, etc.

        Returns ``{"postSubmissionId": "..."}``.
        """
        target = {"targetType": "tiktok", **target_options}

        body: dict[str, Any] = {
            "post": {
                "accountId": account_id,
                "content": {
                    "text": text,
                    "mediaUrls": media_urls,
                    "platform": "tiktok",
                },
                "target": target,
            },
        }

        if scheduled_time:
            body["scheduledTime"] = scheduled_time
        elif use_next_free_slot:
            body["useNextFreeSlot"] = True

        logger.info("Publishing TikTok video to account %s (%d media)", account_id, len(media_urls))
        return self._post("/posts", body)

    def get_post_status(self, post_submission_id: str) -> dict[str, Any]:
        """Poll Blotato for the current status of a submitted post.

        Possible statuses: ``in-progress``, ``published``, ``failed``.
        """
        return self._get(f"/posts/{post_submission_id}")
