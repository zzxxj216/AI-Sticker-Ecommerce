"""TikTok Marketing (Ads) API HTTP client → the multi-channel-api middle layer.

Thin wrapper over ``{TKSHOP_SERVER_URL}/api/v1/tiktok-ads/...`` (the SAME env
var tkshop uses — both repo-A services talk to the same middle-layer process).
The middle layer (repo B) owns the real TikTok Marketing API calls, tokens and
HMAC-free ``Access-Token`` auth; this client speaks the middle layer's actual
``ApiResponse`` envelope ``{success: bool, message, data}`` (see repo B
``app/schemas/common.py``) and raises on ``success == false``. A legacy
``{code, data, message}`` shape is still tolerated as a fallback.

Contract: docs/tiktok_ads_contract.md (endpoints #3/#4/#6/#7).

This service also owns the ``tk_ads_accounts`` upsert from ``list_advertisers``.

Style mirrors src/services/tkshop/service.py (env config, requests w/ timeout,
``_open_db`` WAL + Row helper, get_logger logging).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.exceptions import APIError
from src.core.logger import get_logger

logger = get_logger("service.tiktok.ads")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# Same middle-layer base URL tkshop uses. The TikTok Ads endpoints live under a
# distinct ``/api/v1/tiktok-ads`` prefix (Shop vs Marketing API are different).
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
TKADS_SERVER_TIMEOUT = int(
    os.getenv("TKADS_SERVER_TIMEOUT", os.getenv("TKSHOP_SERVER_TIMEOUT", "120"))
)

_ADS_BASE = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok-ads"


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class TikTokAdsService:
    """HTTP client for the middle-layer TikTok Marketing API endpoints."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.base = _ADS_BASE

    # ------------------------------------------------------------------
    # Envelope helpers
    # ------------------------------------------------------------------

    def _parse_envelope(self, resp: requests.Response, *, action: str) -> Any:
        """Validate the middle layer's ``ApiResponse`` envelope.

        Repo B uses ``{success: bool, message, data}`` (see its
        ``app/schemas/common.py``) — ``success == false`` is an error and
        carries a human ``message``. A legacy ``{code, data, message}`` shape
        (``code != 0`` == error) is still accepted as a fallback.

        Raises ``APIError`` on transport non-2xx, unparseable body, or a
        failure envelope. Returns ``data`` on success.
        """
        try:
            body = resp.json() if resp.text else {}
        except Exception:
            raise APIError(
                f"tiktok-ads {action}: non-JSON response "
                f"(HTTP {resp.status_code}): {resp.text[:300]}",
                service="tiktok_ads",
            )
        if not isinstance(body, dict):
            raise APIError(
                f"tiktok-ads {action}: unexpected response shape: {str(body)[:300]}",
                service="tiktok_ads",
            )
        message = body.get("message") or ""
        # Repo B puts the upstream TikTok error payload in ``detail`` — without
        # it the message is just "POST ad/create/ failed", which is undebuggable.
        detail = str(body.get("detail") or "").strip()
        detail_suffix = f" — {detail[:400]}" if detail else ""
        # Primary: repo B's success-flag envelope.
        if "success" in body:
            if not body.get("success"):
                raise APIError(
                    f"tiktok-ads {action} failed: {message or 'unknown error'}"
                    f"{detail_suffix}",
                    service="tiktok_ads",
                )
            return body.get("data")
        # Fallback: legacy code-based envelope.
        code = body.get("code", None)
        if code is not None:
            if int(code) != 0:
                raise APIError(
                    f"tiktok-ads {action} failed (code={code}): "
                    f"{message or 'unknown error'}{detail_suffix}",
                    service="tiktok_ads",
                )
            return body.get("data")
        # No envelope markers → surface HTTP status.
        if not resp.ok:
            raise APIError(
                f"tiktok-ads {action}: HTTP {resp.status_code}: {resp.text[:300]}",
                service="tiktok_ads",
            )
        return body.get("data")

    def _get(self, path: str, *, params: Optional[dict] = None, action: str) -> Any:
        url = f"{self.base}{path}"
        try:
            resp = requests.get(url, params=params, timeout=TKADS_SERVER_TIMEOUT)
        except requests.RequestException as e:
            raise APIError(
                f"tiktok-ads {action}: request failed: {type(e).__name__}: {e}",
                service="tiktok_ads",
            )
        return self._parse_envelope(resp, action=action)

    def _post(self, path: str, *, json_body: dict, action: str) -> Any:
        url = f"{self.base}{path}"
        try:
            resp = requests.post(url, json=json_body, timeout=TKADS_SERVER_TIMEOUT)
        except requests.RequestException as e:
            raise APIError(
                f"tiktok-ads {action}: request failed: {type(e).__name__}: {e}",
                service="tiktok_ads",
            )
        return self._parse_envelope(resp, action=action)

    # ------------------------------------------------------------------
    # #1 GET /auth/url   #2 GET /auth/callback
    # ------------------------------------------------------------------

    def get_auth_url(self) -> str:
        """Ask the middle layer for the Marketing OAuth authorize URL.

        Tolerant of the data shape: a bare string, or a dict keyed by
        ``auth_url`` / ``url`` / ``authorization_url``.
        """
        data = self._get("/auth/url", action="get_auth_url")
        if isinstance(data, str) and data.strip():
            return data.strip()
        if isinstance(data, dict):
            url = (
                data.get("auth_url")
                or data.get("url")
                or data.get("authorization_url")
                or ""
            )
            if url:
                return str(url)
        raise APIError(
            "get_auth_url: middle layer returned no authorize URL",
            service="tiktok_ads",
        )

    def handle_auth_callback(self, auth_code: str) -> list[dict[str, Any]]:
        """Forward the OAuth ``auth_code`` to the middle layer, which exchanges
        it for an advertiser token and returns the now-authorized advertisers.

        The returned list is upserted into ``tk_ads_accounts`` (same shape as
        ``list_advertisers``).
        """
        code = (auth_code or "").strip()
        if not code:
            raise APIError("handle_auth_callback: empty auth_code",
                           service="tiktok_ads")
        data = self._get(
            "/auth/callback", params={"auth_code": code}, action="auth_callback",
        )
        if isinstance(data, dict):
            advertisers = data.get("advertisers") or data.get("list") or []
        elif isinstance(data, list):
            advertisers = data
        else:
            advertisers = []
        self._upsert_accounts(advertisers)
        return advertisers

    # ------------------------------------------------------------------
    # #3 GET /advertisers  (+ tk_ads_accounts upsert)
    # ------------------------------------------------------------------

    def list_advertisers(self) -> list[dict[str, Any]]:
        """List authorized advertisers and upsert them into ``tk_ads_accounts``.

        Returns the list of advertiser dicts as returned by the middle layer
        (keys: advertiser_id, name, currency, ... — tolerant of extras).
        """
        data = self._get("/advertisers", action="list_advertisers")
        if isinstance(data, dict):
            advertisers = data.get("advertisers") or data.get("list") or []
        elif isinstance(data, list):
            advertisers = data
        else:
            advertisers = []
        self._upsert_accounts(advertisers)
        return advertisers

    def _upsert_accounts(self, advertisers: list[dict[str, Any]]) -> int:
        now = int(time.time())
        n = 0
        with _open_db(self.db_path) as conn:
            for a in advertisers:
                adv_id = str(a.get("advertiser_id") or a.get("id") or "").strip()
                if not adv_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO tk_ads_accounts
                        (advertiser_id, name, shop, currency, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(advertiser_id) DO UPDATE SET
                        name     = excluded.name,
                        currency = excluded.currency,
                        status   = excluded.status
                    """,
                    (
                        adv_id,
                        str(a.get("name") or a.get("advertiser_name") or ""),
                        str(a.get("shop") or ""),
                        str(a.get("currency") or ""),
                        str(a.get("status") or "active"),
                        now,
                    ),
                )
                n += 1
            conn.commit()
        logger.info("tk_ads_accounts upsert: %d advertiser(s)", n)
        return n

    # ------------------------------------------------------------------
    # Phase 2: GET /identities  (Spark ad identities)
    # ------------------------------------------------------------------

    def list_identities(self) -> list[dict[str, Any]]:
        """List ad identities usable for Spark ads (promoting an already-posted
        TikTok video). Source: Marketing API ``identity/get/`` (middle layer).

        Returns a list of dicts, each tolerant of extras but normalized to:
        ``identity_id, identity_type, display_name, can_pull_video``.
        """
        data = self._get("/identities", action="list_identities")
        if isinstance(data, dict):
            raw = data.get("identities") or data.get("list") or []
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        out: list[dict[str, Any]] = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            ident_id = str(it.get("identity_id") or it.get("id") or "").strip()
            if not ident_id:
                continue
            out.append({
                "identity_id": ident_id,
                "identity_type": str(it.get("identity_type") or "").strip(),
                "display_name": str(
                    it.get("display_name") or it.get("name") or ""
                ).strip(),
                "can_pull_video": bool(it.get("can_pull_video")),
                "identity_authorized_bc_id": str(
                    it.get("identity_authorized_bc_id") or ""
                ).strip(),
            })
        return out

    def list_promotable_videos(
        self,
        identity_id: str,
        identity_type: str = "BC_AUTH_TT",
        identity_authorized_bc_id: str = "",
    ) -> list[dict[str, Any]]:
        """List videos directly promotable (pull mode) under an identity.

        Calls middle-layer ``/promotable-videos`` (→ ``identity/video/get/``):
        for a ``can_pull_video`` identity this returns the account's ENTIRE
        posted-video catalog with no per-video Spark auth code needed. Each row
        normalized to ``tiktok_video_id`` (= item_id), ``one_liner`` (= text),
        ``cover_url``, ``status`` — keys the UI already consumes.
        """
        params: dict[str, Any] = {
            "identity_id": identity_id,
            "identity_type": identity_type,
        }
        if identity_authorized_bc_id:
            params["identity_authorized_bc_id"] = identity_authorized_bc_id
        data = self._get("/promotable-videos", params=params,
                         action="list_promotable_videos")
        if isinstance(data, dict):
            raw = data.get("videos") or data.get("list") or []
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        out: list[dict[str, Any]] = []
        for v in raw:
            if not isinstance(v, dict):
                continue
            item_id = str(v.get("item_id") or v.get("tiktok_video_id") or "").strip()
            if not item_id:
                continue
            out.append({
                "tiktok_video_id": item_id,
                "one_liner": str(v.get("text") or v.get("one_liner") or "").strip(),
                "cover_url": str(v.get("cover_url") or "").strip(),
                "play_url": str(v.get("play_url") or "").strip(),
                "duration": v.get("duration"),
                "status": str(v.get("status") or "").strip(),
            })
        return out

    # ------------------------------------------------------------------
    # #4 POST /experiments
    # ------------------------------------------------------------------

    def create_experiment_remote(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create (or dry-run preview) a campaign+adgroups experiment.

        ``payload`` must match the contract POST /experiments body, including the
        Phase 2 fields ``identity_id`` / ``identity_type`` / ``objective`` (passed
        straight through — this client only forwards the payload). Returns the
        ``data`` object (tiktok_campaign_id, adgroups[], preview, dry_run).
        """
        data = self._post("/experiments", json_body=payload,
                          action="create_experiment")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # #6 GET /report
    # ------------------------------------------------------------------

    def get_report(
        self,
        advertiser_id: str,
        adgroup_ids: list[str] | str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Pull adgroup-level per-day report rows for ``[start, end]``.

        ``adgroup_ids`` may be a list or a pre-joined comma string. Returns the
        ``rows`` list (each row keyed by tiktok_adgroup_id + stat_date).
        """
        if isinstance(adgroup_ids, (list, tuple)):
            ids = ",".join(str(i) for i in adgroup_ids if str(i).strip())
        else:
            ids = str(adgroup_ids or "")
        params = {
            "advertiser_id": str(advertiser_id),
            "adgroup_ids": ids,
            "start_date": start,
            "end_date": end,
        }
        data = self._get("/report", params=params, action="get_report")
        if isinstance(data, dict):
            return data.get("rows") or []
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # #7 POST /adgroups/{id}/status
    # ------------------------------------------------------------------

    def set_adgroup_status(
        self, adgroup_id: str, advertiser_id: str, status: str,
    ) -> dict[str, Any]:
        """Enable / disable a single ad group (kill-switch).

        ``status`` must be ``ENABLE`` or ``DISABLE``.
        """
        s = (status or "").strip().upper()
        if s not in ("ENABLE", "DISABLE"):
            raise APIError(
                f"set_adgroup_status: invalid status {status!r} "
                "(expected ENABLE / DISABLE)",
                service="tiktok_ads",
            )
        data = self._post(
            f"/adgroups/{adgroup_id}/status",
            json_body={"advertiser_id": str(advertiser_id), "status": s},
            action="set_adgroup_status",
        )
        return data if isinstance(data, dict) else {"status": s}


_svc: Optional[TikTokAdsService] = None


def get_tiktok_ads_service() -> TikTokAdsService:
    """Return the process-wide TikTokAdsService singleton."""
    global _svc
    if _svc is None:
        _svc = TikTokAdsService()
    return _svc
