from __future__ import annotations

import os
from urllib.parse import urlencode

import requests

from src.core.config import config
from src.core.logger import get_logger

logger = get_logger("web.feishu")


class FeishuAuthService:
    AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    APP_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
    USER_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(self):
        self.app_id = config.feishu_h5_app_id
        self.app_secret = config.feishu_h5_app_secret
        self.base_url = config.feishu_h5_base_url or os.getenv("FEISHU_H5_BASE_URL", "")
        self.redirect_uri = config.feishu_h5_redirect_uri or os.getenv("FEISHU_H5_REDIRECT_URI", "")
        self._auto_dev = os.getenv("FEISHU_H5_AUTO_DEV", "").lower() in ("1", "true", "yes")

    def is_configured(self) -> bool:
        if self._auto_dev:
            return False
        return bool(self.app_id and self.app_secret and self.redirect_uri)

    def build_login_url(self, state: str) -> str:
        query = urlencode(
            {
                "app_id": self.app_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "contact:user.base:readonly",
                "state": state,
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def _get_app_access_token(self) -> str:
        """Obtain app_access_token from Feishu (internal app)."""
        resp = requests.post(
            self.APP_ACCESS_TOKEN_URL,
            json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ValueError(f"Feishu app_access_token failed: {data}")
        token = data.get("app_access_token", "")
        if not token:
            raise ValueError(f"Feishu app_access_token empty: {data}")
        return token

    def exchange_code(self, code: str) -> dict:
        app_token = self._get_app_access_token()
        resp = requests.post(
            self.TOKEN_URL,
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "grant_type": "authorization_code",
                "code": code,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ValueError(f"Feishu token exchange failed: {data}")
        return data.get("data", {})

    def fetch_user(self, access_token: str) -> dict:
        resp = requests.get(
            self.USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ValueError(f"Feishu user fetch failed: {data}")
        return data.get("data", {})
