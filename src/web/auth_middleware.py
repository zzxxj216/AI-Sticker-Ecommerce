"""Authentication middleware for the Feishu Sticker Workbench.

Intercepts requests to enforce login requirements:
- Public paths are accessible without authentication
- Protected paths redirect to /login (HTML) or return 401 (API)
- When Feishu is not configured AND ENV != production, auto-injects a Local Dev user
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

PUBLIC_PATH_PREFIXES = (
    "/healthz",
    "/static/",
    "/auth/",
    "/login",
)

PUBLIC_EXACT_PATHS = {
    "/",
    "/trends",
}


def _is_public(path: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on non-public routes.

    Constructor args:
        feishu_configured: whether Feishu OIDC login is configured.
            When False AND not in production, auto-injects a Local Dev session.
            When False AND in production, rejects all non-public requests.
    """

    def __init__(self, app, feishu_configured: bool = False):
        super().__init__(app)
        self.feishu_configured = feishu_configured
        self._is_production = os.getenv("ENV", "development") == "production"
        if not feishu_configured and self._is_production:
            logger.warning(
                "Feishu auth is NOT configured in production! "
                "All non-public routes will require authentication."
            )

    async def dispatch(self, request: Request, call_next):
        if not self.feishu_configured:
            if self._is_production:
                path = request.url.path
                if _is_public(path):
                    return await call_next(request)
                if path.startswith("/api/"):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "认证服务未配置，请联系管理员"},
                    )
                return RedirectResponse(url="/login", status_code=303)

            if not request.session.get("user"):
                request.session["user"] = {
                    "name": "Local Dev",
                    "en_name": "local-dev",
                    "open_id": "local-dev",
                }
            return await call_next(request)

        user = request.session.get("user")
        path = request.url.path

        if _is_public(path) or user:
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "未登录，请先完成飞书登录"},
            )

        next_url = quote(str(request.url.path), safe="")
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)
