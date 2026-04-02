"""Authentication middleware for the Feishu Sticker Workbench.

Intercepts requests to enforce login requirements:
- Public paths are accessible without authentication
- Protected paths redirect to /login (HTML) or return 401 (API)
- When Feishu is not configured, auto-injects a Local Dev user (auto-dev mode)
"""

from __future__ import annotations

from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

PUBLIC_PATH_PREFIXES = (
    "/healthz",
    "/static/",
    "/outputs/",
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
            When False, every request auto-receives a Local Dev session.
    """

    def __init__(self, app, feishu_configured: bool = False):
        super().__init__(app)
        self.feishu_configured = feishu_configured

    async def dispatch(self, request: Request, call_next):
        if not self.feishu_configured:
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
