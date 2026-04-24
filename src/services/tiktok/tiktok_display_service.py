"""TikTok Display API client for video analytics.

Handles OAuth2 authorization flow and video data retrieval
via the official TikTok Display API (v2).

Supports multiple accounts with JSON-file-backed token persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from src.core.logger import get_logger

logger = get_logger("services.tiktok.display")

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

_DEFAULT_TOKEN_FILE = Path("data/tiktok_tokens.json")


class TikTokDisplayService:
    """OAuth2 + Display API wrapper for TikTok analytics (multi-account)."""

    def __init__(self, token_file: Path | None = None) -> None:
        self.client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
        self.client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv(
            "TIKTOK_REDIRECT_URI",
            "http://localhost:8888/auth/tiktok/callback",
        )
        self._token_file = token_file or _DEFAULT_TOKEN_FILE
        self._accounts: dict[str, dict[str, Any]] = {}
        self._load()

    def is_configured(self) -> bool:
        return bool(self.client_key and self.client_secret)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._token_file.exists():
            try:
                data = json.loads(self._token_file.read_text("utf-8"))
                self._accounts = data if isinstance(data, dict) else {}
                logger.info("Loaded %d TikTok account(s) from %s", len(self._accounts), self._token_file)
            except Exception as exc:
                logger.warning("Failed to load token file %s: %s", self._token_file, exc)
                self._accounts = {}
        else:
            self._accounts = {}

    def _save(self) -> None:
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps(self._accounts, ensure_ascii=False, indent=2), "utf-8")

    # ------------------------------------------------------------------
    # OAuth2 flow
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_code_verifier() -> str:
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        # TikTok requires hex-encoded SHA256 (not standard base64url)
        return hashlib.sha256(verifier.encode("utf-8")).hexdigest()

    def build_auth_url(self, state: str, code_verifier: str) -> str:
        """Build the TikTok OAuth2 authorization URL with PKCE."""
        code_challenge = self._generate_code_challenge(code_verifier)
        params = {
            "client_key": self.client_key,
            "scope": "user.info.basic,user.info.profile,user.info.stats,video.list",
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "disable_auto_auth": "1",
        }
        return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange authorization code for access + refresh tokens (PKCE)."""
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            raise ValueError(f"TikTok token error: {data.get('error_description', data['error'])}")

        token_info = {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "open_id": data.get("open_id", ""),
            "expires_at": time.time() + data.get("expires_in", 86400),
            "refresh_expires_at": time.time() + data.get("refresh_expires_in", 86400 * 365),
            "scope": data.get("scope", ""),
        }
        logger.info("TikTok OAuth success for open_id=%s", token_info["open_id"])
        return token_info

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an expired access token."""
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            raise ValueError(f"TikTok refresh error: {data.get('error_description', data['error'])}")

        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "open_id": data.get("open_id", ""),
            "expires_at": time.time() + data.get("expires_in", 86400),
            "refresh_expires_at": time.time() + data.get("refresh_expires_in", 86400 * 365),
            "scope": data.get("scope", ""),
        }

    # ------------------------------------------------------------------
    # Multi-account management
    # ------------------------------------------------------------------

    def save_account(self, token_info: dict[str, Any], user_info: dict[str, Any]) -> None:
        """Persist token + user profile for an account."""
        oid = token_info.get("open_id", "")
        if not oid:
            return
        self._accounts[oid] = {
            "token": token_info,
            "user": user_info,
        }
        self._save()
        logger.info("Saved TikTok account %s (%s)", user_info.get("display_name", ""), oid)

    def remove_account(self, open_id: str) -> bool:
        if open_id in self._accounts:
            del self._accounts[open_id]
            self._save()
            return True
        return False

    def get_account(self, open_id: str) -> dict[str, Any] | None:
        return self._accounts.get(open_id)

    def get_valid_token(self, open_id: str) -> dict[str, Any] | None:
        """Get a valid access token for the account, auto-refreshing if needed."""
        acct = self._accounts.get(open_id)
        if not acct:
            return None
        token = acct.get("token", {})
        if time.time() > token.get("expires_at", 0) - 60:
            try:
                token = self.refresh_access_token(token["refresh_token"])
                acct["token"] = token
                self._save()
            except Exception as exc:
                logger.warning("Token refresh failed for %s: %s", open_id, exc)
                return None
        return token

    def list_accounts(self) -> list[dict[str, Any]]:
        """Return summary of all stored accounts for display."""
        results = []
        for oid, acct in self._accounts.items():
            user = acct.get("user", {})
            token = acct.get("token", {})
            results.append({
                "open_id": oid,
                "display_name": user.get("display_name", ""),
                "avatar_url": user.get("avatar_url", ""),
                "follower_count": user.get("follower_count", 0),
                "video_count": user.get("video_count", 0),
                "expired": time.time() > token.get("expires_at", 0),
            })
        return results

    # ------------------------------------------------------------------
    # Display API
    # ------------------------------------------------------------------

    def _api_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Fetch authorized user's basic profile info."""
        url = f"{TIKTOK_API_BASE}/user/info/?fields=open_id,union_id,avatar_url,display_name,bio_description,profile_deep_link,is_verified,follower_count,following_count,likes_count,video_count"
        resp = requests.get(url, headers=self._api_headers(access_token), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            raise ValueError(f"TikTok API error: {data.get('error', {})}")
        return data.get("data", {}).get("user", {})

    def get_video_list(
        self, access_token: str, max_count: int = 20, cursor: int | None = None,
    ) -> dict[str, Any]:
        """Fetch a paginated list of the user's public videos with engagement metrics."""
        fields = "id,title,video_description,duration,cover_image_url,share_url,embed_link,like_count,comment_count,share_count,view_count,create_time"
        url = f"{TIKTOK_API_BASE}/video/list/?fields={fields}"
        body: dict[str, Any] = {"max_count": min(max_count, 20)}
        if cursor is not None:
            body["cursor"] = cursor

        resp = requests.post(url, headers=self._api_headers(access_token), json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            raise ValueError(f"TikTok API error: {data.get('error', {})}")
        return data.get("data", {})
