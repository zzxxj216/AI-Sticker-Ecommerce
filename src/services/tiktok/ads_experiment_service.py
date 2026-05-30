"""TikTok Ads audience-experiment orchestration (the core service).

Implements the public method signatures fixed in docs/tiktok_ads_contract.md
("repo A service 对外方法签名"). The web track (Track C) calls THIS module.

Flow:
  1. ``generate_audience_candidates`` — gather pack context (theme + sample
     sticker briefs), two-step AI (text_complete → extract_json), insert
     ``tk_ad_audiences`` rows (source='ai_generated', status='candidate').
  2. ``create_experiment`` — guardrail checks, insert ``tk_ad_experiments`` +
     ``tk_ad_groups``, build the middle-layer payload (audiences[].
     client_audience_id = tk_ad_audiences.id), call create_experiment_remote,
     persist returned tiktok_campaign_id / tiktok_adgroup_ids.
  3. ``refresh_metrics`` — pull report, UPSERT ``tk_ad_metrics_snapshots`` on
     (tiktok_adgroup_id, stat_date).
  4. ``evaluate_experiment`` — per-audience aggregate vs env thresholds, flip
     winning audiences, AI writes ``decision_summary``.
  5. ``kill_experiment`` — DISABLE every adgroup, mark experiment killed.
  6. ``audience_leaderboard`` — read-time aggregation across snapshots.

DB / HTTP / AI patterns copied from src/services/tkshop/service.py and
src/services/tiktok/display_metrics_service.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from src.core.exceptions import APIError
from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.tiktok.ads_prompts import (
    AUDIENCE_EXTRACT_INSTRUCTIONS,
    AUDIENCE_EXTRACT_SCHEMA,
    AUDIENCE_MAIN_SYSTEM_PROMPT,
    DECISION_SUMMARY_SYSTEM_PROMPT,
    build_audience_main_prompt,
    build_decision_summary_prompt,
)
from src.services.tiktok.tiktok_ads_service import (
    TikTokAdsService,
    get_tiktok_ads_service,
)

logger = get_logger("service.tiktok.ads_experiment")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")


# ---------------------------------------------------------------------------
# Phase 2: objective → primary-KPI rules (single source of truth, mirrors the
# contract table "目标 → optimization_goal / 主 KPI / 门槛").
# ---------------------------------------------------------------------------

OBJ_PRODUCT_SALES = "PRODUCT_SALES"
OBJ_VIDEO_VIEWS = "VIDEO_VIEWS"
OBJ_ENGAGEMENT = "ENGAGEMENT"
DEFAULT_OBJECTIVE = OBJ_PRODUCT_SALES

# Per-objective KPI metadata:
#   name      — human label returned to the UI (Track C).
#   direction — "higher" => bigger KPI is better; "lower" => smaller is better.
# The leaderboard sorts by ``primary_kpi_value`` in this direction.
_OBJECTIVE_KPI = {
    OBJ_PRODUCT_SALES: {"name": "ROAS", "direction": "higher"},
    OBJ_VIDEO_VIEWS: {"name": "CPV", "direction": "lower"},
    OBJ_ENGAGEMENT: {"name": "单粉成本", "direction": "lower"},
}


def _normalize_objective(objective: Optional[str]) -> str:
    """Map an arbitrary objective string to one of the three supported ones,
    defaulting to PRODUCT_SALES (Phase-1 behaviour) when unknown/empty."""
    o = (objective or "").strip().upper()
    return o if o in _OBJECTIVE_KPI else DEFAULT_OBJECTIVE


def _primary_kpi(objective: str, agg: dict[str, Any]) -> dict[str, Any]:
    """Compute the objective's primary KPI value + label + sort direction from a
    per-audience aggregate dict (keys: spend, gmv, video_views, follows, ...).

    PRODUCT_SALES → ROAS = gmv/spend (higher better).
    VIDEO_VIEWS   → CPV  = spend/video_views (lower better).
    ENGAGEMENT    → 单粉成本 = spend/follows (lower better).
    """
    obj = _normalize_objective(objective)
    meta = _OBJECTIVE_KPI[obj]
    spend = float(agg.get("spend") or 0)
    if obj == OBJ_VIDEO_VIEWS:
        views = int(agg.get("video_views") or 0)
        value = (spend / views) if views else 0.0
    elif obj == OBJ_ENGAGEMENT:
        follows = int(agg.get("follows") or 0)
        value = (spend / follows) if follows else 0.0
    else:  # PRODUCT_SALES
        gmv = float(agg.get("gmv") or 0)
        value = (gmv / spend) if spend else 0.0
    return {
        "objective": obj,
        "primary_kpi_name": meta["name"],
        "primary_kpi_value": value,
        "primary_kpi_direction": meta["direction"],
    }


# ---------------------------------------------------------------------------
# Guardrail / threshold env reads (single source of truth)
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "").strip() or default))
    except (TypeError, ValueError):
        return default


def _guardrails() -> dict[str, float]:
    """Hard caps applied before any spend is committed."""
    return {
        # 0 / unset => no cap.
        "max_experiment_budget": _env_float("TKADS_MAX_EXPERIMENT_BUDGET", 0.0),
        "max_concurrent_experiments": _env_int("TKADS_MAX_CONCURRENT_EXPERIMENTS", 0),
    }


def _win_thresholds() -> dict[str, float]:
    """Promotion thresholds — an audience must meet the volume/spend/days floor
    for its experiment's objective to be 'winning'.

    Phase 2 winning rule (documented here, applied in evaluate_experiment): a
    lower-is-better KPI (CPV / 单粉成本) is hard to gate on an absolute target
    early (learning-phase noise + no calibrated target), so we gate winning on
    *sufficient volume + spend + days* for the objective's threshold dimension:
      PRODUCT_SALES → orders(complete_payment) >= min_conv
      VIDEO_VIEWS   → video_views(video_play_actions) >= min_views
      ENGAGEMENT    → follows >= min_follows
    plus spend >= min_spend and days >= min_days for all objectives. The primary
    KPI is then used only for *ranking* within the qualifying set.
    """
    return {
        "min_spend": _env_float("TKADS_WIN_MIN_SPEND", 50.0),
        "min_conv": _env_float("TKADS_WIN_MIN_CONV", 50.0),
        "min_days": _env_int("TKADS_WIN_MIN_DAYS", 7),
        "min_views": _env_float("TKADS_WIN_MIN_VIEWS", 1000.0),
        "min_follows": _env_float("TKADS_WIN_MIN_FOLLOWS", 50.0),
    }


def _meets_volume(objective: str, agg: dict[str, Any], th: dict[str, float]) -> bool:
    """Objective-specific minimum-volume gate (the threshold dimension that
    varies by objective). Spend + days are checked by the caller."""
    obj = _normalize_objective(objective)
    if obj == OBJ_VIDEO_VIEWS:
        return int(agg.get("video_views") or 0) >= th["min_views"]
    if obj == OBJ_ENGAGEMENT:
        return int(agg.get("follows") or 0) >= th["min_follows"]
    return int(agg.get("conversions") or 0) >= th["min_conv"]


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _clean(s: str, *, max_len: int = 400) -> str:
    return (s or "").replace("\r", " ").replace("\n", " ").strip()[:max_len]


class AdsExperimentService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        ads: Optional[TikTokAdsService] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.ads = ads or get_tiktok_ads_service()
        self.db_path = db_path

    # ------------------------------------------------------------------
    # 1. generate_audience_candidates
    # ------------------------------------------------------------------

    def _collect_pack_context(self, pack_id: int) -> dict[str, Any]:
        """Gather pack theme + a clean sample of sticker briefs (mirrors the
        tkshop ``collect_pack_design_context`` approach)."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT p.id, p.display_name, p.total_stickers, p.series_id,
                       s.style_anchor, s.palette, s.pack_archetype, s.metadata_json
                  FROM packs p
             LEFT JOIN pack_series s ON s.id = p.series_id
                 WHERE p.id = ?
                """,
                (pack_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"pack #{pack_id} not found")
            sticker_rows = conn.execute(
                """
                SELECT ps.name
                  FROM pack_stickers ps
                  JOIN pack_previews pv ON pv.id = ps.preview_id
                 WHERE pv.series_id = ?
                   AND COALESCE(ps.name, '') != ''
                 ORDER BY ps.is_selected DESC, ps.id
                 LIMIT 40
                """,
                (row["series_id"],),
            ).fetchall() if row["series_id"] else []

        briefs: list[str] = []
        seen: set[str] = set()
        for r in sticker_rows:
            b = _clean(str(r["name"]), max_len=120)
            key = b.lower()
            if b and key not in seen:
                briefs.append(b)
                seen.add(key)
        # Fall back to the series metadata preview_briefs if no split stickers.
        if not briefs:
            try:
                md = json.loads(row["metadata_json"] or "{}")
                for pb in md.get("preview_briefs", [])[:6]:
                    for raw in (pb.get("stickers", []) or [])[:8]:
                        b = _clean(str(raw), max_len=120)
                        key = b.lower()
                        if b and key not in seen:
                            briefs.append(b)
                            seen.add(key)
                        if len(briefs) >= 18:
                            break
                    if len(briefs) >= 18:
                        break
            except Exception:
                pass

        return {
            "pack_id": pack_id,
            "display_name": _clean(row["display_name"] or "", max_len=120),
            "pack_archetype": _clean(row["pack_archetype"] or "", max_len=80),
            "style_anchor": _clean(row["style_anchor"] or "", max_len=420),
            "palette": _clean(row["palette"] or "", max_len=140),
            "total_stickers": row["total_stickers"] or 0,
            "briefs_sample": briefs[:18],
        }

    def generate_audience_candidates(
        self, pack_id: int, *, n: int = 3,
    ) -> list[dict[str, Any]]:
        """AI-generate ``n`` distinct audience hypotheses for a pack and insert
        them into ``tk_ad_audiences`` (source='ai_generated',
        status='candidate'). Returns the newly created rows.
        """
        ctx = self._collect_pack_context(pack_id)

        main_model = os.getenv("TKADS_AUDIENCE_MODEL") or None
        extract_model = os.getenv("TKADS_EXTRACT_MODEL") or None

        prompt = build_audience_main_prompt(
            pack_display_name=ctx["display_name"],
            pack_archetype=ctx["pack_archetype"],
            style_anchor=ctx["style_anchor"],
            palette=ctx["palette"],
            total_stickers=ctx["total_stickers"],
            sticker_briefs_sample=ctx["briefs_sample"],
            n=n,
        )
        main_text = self.router.text_complete(
            prompt,
            model=main_model,
            system=AUDIENCE_MAIN_SYSTEM_PROMPT,
            temperature=0.8,
            task="tkads_audience:main",
            related_table="packs",
            related_id=pack_id,
        )
        payload = self.router.extract_json(
            main_text,
            schema=AUDIENCE_EXTRACT_SCHEMA,
            instructions=AUDIENCE_EXTRACT_INSTRUCTIONS,
            model=extract_model,
            max_retries=1,
            task="tkads_audience:extract",
            related_table="packs",
            related_id=pack_id,
        )
        candidates = payload.get("audiences") or []
        if not candidates:
            raise APIError(
                "generate_audience_candidates: AI returned no audiences",
                service="tiktok_ads",
            )
        return self._insert_audiences(pack_id, candidates[:n])

    def _insert_audiences(
        self, pack_id: int, candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        created: list[dict[str, Any]] = []
        with _open_db(self.db_path) as conn:
            for c in candidates:
                name = _clean(str(c.get("name") or ""), max_len=80) or "Untitled audience"
                hypothesis = _clean(str(c.get("hypothesis") or ""), max_len=600)
                targeting = c.get("targeting") or {}
                if not isinstance(targeting, dict):
                    targeting = {}
                targeting_json = json.dumps(targeting, ensure_ascii=False)
                cur = conn.execute(
                    """
                    INSERT INTO tk_ad_audiences
                        (name, kind, targeting_json, tiktok_audience_id,
                         hypothesis, source, status, pack_id, created_at)
                    VALUES (?, 'targeting', ?, '', ?, 'ai_generated',
                            'candidate', ?, ?)
                    """,
                    (name, targeting_json, hypothesis, pack_id, now),
                )
                created.append({
                    "id": cur.lastrowid,
                    "name": name,
                    "hypothesis": hypothesis,
                    "targeting": targeting,
                    "source": "ai_generated",
                    "status": "candidate",
                    "pack_id": pack_id,
                    "created_at": now,
                })
            conn.commit()
        logger.info("generate_audience_candidates: pack=%d inserted=%d",
                    pack_id, len(created))
        return created

    # ------------------------------------------------------------------
    # 2. create_experiment
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        *,
        advertiser_id: str,
        promote_type: str,
        promote_ref_id: str,
        pack_id: int,
        audience_ids: list[int],
        per_adgroup_budget: float,
        objective: str,
        creative: dict[str, Any],
        identity_id: str = "",
        identity_type: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create one campaign + N adgroups (one per audience), varying only the
        audience. Guardrails enforced before any remote call. Persists local
        rows, calls the middle layer, then backfills platform IDs.

        ``objective`` is required (one of PRODUCT_SALES / VIDEO_VIEWS /
        ENGAGEMENT) and is persisted to drive objective-aware evaluation.

        When ``promote_type == "video"`` the promotion is a **Spark** ad: the
        middle-layer ``promote`` becomes ``{type:"video", ref_id:<tiktok_video_id>}``
        and ``identity_id`` (+ ``identity_type``) are required and forwarded so
        the ad reuses the already-posted video under that identity (no upload).
        The ``shop_product`` path is unchanged from Phase 1.

        On ``dry_run=True`` the rows are persisted with preview statuses and the
        experiment is NOT marked running; the middle-layer preview is returned.
        """
        if not audience_ids:
            raise APIError("create_experiment: audience_ids is empty",
                           service="tiktok_ads")
        objective = _normalize_objective(objective)
        identity_id = (identity_id or "").strip()
        identity_type = (identity_type or "").strip()
        is_spark = str(promote_type).strip().lower() == "video"
        if is_spark and not identity_id:
            raise APIError(
                "create_experiment: identity_id is required for Spark "
                "(promote_type='video') experiments",
                service="tiktok_ads",
            )
        per_adgroup_budget = float(per_adgroup_budget or 0)
        n_adgroups = len(audience_ids)
        total_budget = per_adgroup_budget * n_adgroups

        guard = _guardrails()
        # Guardrail: total experiment budget cap.
        if guard["max_experiment_budget"] > 0 and total_budget > guard["max_experiment_budget"]:
            raise APIError(
                f"create_experiment: total budget {total_budget:.2f} exceeds "
                f"TKADS_MAX_EXPERIMENT_BUDGET={guard['max_experiment_budget']:.2f}",
                service="tiktok_ads",
            )
        # Guardrail: max concurrent running experiments (only for real launches).
        if not dry_run and guard["max_concurrent_experiments"] > 0:
            with _open_db(self.db_path) as conn:
                running = conn.execute(
                    "SELECT COUNT(*) AS n FROM tk_ad_experiments WHERE status = 'running'",
                ).fetchone()["n"]
            if running >= guard["max_concurrent_experiments"]:
                raise APIError(
                    f"create_experiment: {running} experiment(s) already running, "
                    f"TKADS_MAX_CONCURRENT_EXPERIMENTS={guard['max_concurrent_experiments']}",
                    service="tiktok_ads",
                )

        # Load the selected audiences (name + targeting) for the payload.
        with _open_db(self.db_path) as conn:
            ph = ",".join("?" * n_adgroups)
            arows = conn.execute(
                f"""
                SELECT id, name, targeting_json
                  FROM tk_ad_audiences
                 WHERE id IN ({ph})
                """,
                tuple(audience_ids),
            ).fetchall()
        aud_by_id = {r["id"]: r for r in arows}
        missing = [a for a in audience_ids if a not in aud_by_id]
        if missing:
            raise APIError(
                f"create_experiment: audience ids not found: {missing}",
                service="tiktok_ads",
            )

        currency = (creative or {}).get("currency") or os.getenv("TKADS_CURRENCY", "USD")
        now = int(time.time())

        # Persist the experiment + adgroup rows first so we always have local
        # state even if the remote call fails. Initial statuses reflect intent.
        exp_status = "draft" if dry_run else "running"
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO tk_ad_experiments
                    (advertiser_id, promote_type, promote_ref_id, pack_id,
                     tiktok_campaign_id, objective, identity_id, identity_type,
                     per_adgroup_budget, currency, status, decision_summary,
                     created_at, started_at)
                VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    str(advertiser_id), str(promote_type), str(promote_ref_id),
                    pack_id, str(objective), identity_id, identity_type,
                    per_adgroup_budget, currency,
                    exp_status, now, (None if dry_run else now),
                ),
            )
            experiment_id = cur.lastrowid
            adgroup_row_ids: dict[int, int] = {}  # audience_id -> tk_ad_groups.id
            for aid in audience_ids:
                ag_status = "preview" if dry_run else "pending"
                agcur = conn.execute(
                    """
                    INSERT INTO tk_ad_groups
                        (experiment_id, audience_id, tiktok_adgroup_id,
                         budget, status, created_at)
                    VALUES (?, ?, '', ?, ?, ?)
                    """,
                    (experiment_id, aid, per_adgroup_budget, ag_status, now),
                )
                adgroup_row_ids[aid] = agcur.lastrowid
            conn.commit()

        # Build the middle-layer payload per the contract.
        audiences_payload = []
        for aid in audience_ids:
            r = aud_by_id[aid]
            try:
                targeting = json.loads(r["targeting_json"] or "{}")
            except Exception:
                targeting = {}
            audiences_payload.append({
                "client_audience_id": aid,
                "name": r["name"],
                "targeting": targeting,
            })
        # Spark video → promote {type:"video", ref_id:<tiktok_video_id>}; the ad
        # is built from identity_id + identity_type + the posted item, no upload.
        promote = {
            "type": ("video" if is_spark else str(promote_type)),
            "ref_id": str(promote_ref_id),
        }
        payload = {
            "advertiser_id": str(advertiser_id),
            "objective": str(objective),
            "identity_id": identity_id,
            "identity_type": identity_type,
            "promote": promote,
            "per_adgroup_budget": per_adgroup_budget,
            "currency": currency,
            "creative": creative or {},
            "audiences": audiences_payload,
            "dry_run": bool(dry_run),
        }

        # Call the middle layer. On failure mark experiment failed but keep rows.
        try:
            data = self.ads.create_experiment_remote(payload)
        except Exception as e:
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE tk_ad_experiments SET status = 'failed', "
                    "decision_summary = ? WHERE id = ?",
                    (f"remote create failed: {e}"[:1000], experiment_id),
                )
                conn.execute(
                    "UPDATE tk_ad_groups SET status = 'failed' WHERE experiment_id = ?",
                    (experiment_id,),
                )
                conn.commit()
            raise

        # Backfill platform IDs from the response.
        campaign_id = str(data.get("tiktok_campaign_id") or "")
        adgroups_resp = data.get("adgroups") or []
        with _open_db(self.db_path) as conn:
            if campaign_id:
                conn.execute(
                    "UPDATE tk_ad_experiments SET tiktok_campaign_id = ? WHERE id = ?",
                    (campaign_id, experiment_id),
                )
            for ag in adgroups_resp:
                client_aid = ag.get("client_audience_id")
                tt_adgroup_id = str(ag.get("tiktok_adgroup_id") or "")
                ag_status_raw = (ag.get("status") or "").upper()
                # Local status: dry_run keeps 'preview'; real ENABLE → 'running'.
                if dry_run:
                    local_ag_status = "preview"
                elif ag_status_raw == "ENABLE":
                    local_ag_status = "running"
                elif ag_status_raw == "DISABLE":
                    local_ag_status = "paused"
                else:
                    local_ag_status = "pending"
                row_id = adgroup_row_ids.get(client_aid)
                if row_id is not None:
                    conn.execute(
                        "UPDATE tk_ad_groups SET tiktok_adgroup_id = ?, "
                        "status = ?, budget = ? WHERE id = ?",
                        (
                            tt_adgroup_id, local_ag_status,
                            float(ag.get("budget") or per_adgroup_budget),
                            row_id,
                        ),
                    )
            # Promote audiences from 'candidate' to 'testing' on a real launch.
            if not dry_run:
                ph = ",".join("?" * n_adgroups)
                conn.execute(
                    f"UPDATE tk_ad_audiences SET status = 'testing' "
                    f"WHERE id IN ({ph}) AND status = 'candidate'",
                    tuple(audience_ids),
                )
            conn.commit()

        return {
            "experiment_id": experiment_id,
            "dry_run": bool(dry_run),
            "tiktok_campaign_id": campaign_id,
            "status": exp_status if not dry_run else "draft",
            "adgroups": adgroups_resp,
            "preview": data.get("preview"),
            "per_adgroup_budget": per_adgroup_budget,
            "total_budget": total_budget,
        }

    # ------------------------------------------------------------------
    # 3. refresh_metrics
    # ------------------------------------------------------------------

    def _experiment_adgroups(
        self, conn: sqlite3.Connection, experiment_id: int,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT id, audience_id, tiktok_adgroup_id, status
              FROM tk_ad_groups
             WHERE experiment_id = ?
            """,
            (experiment_id,),
        ).fetchall()

    def refresh_metrics(self, experiment_id: int) -> dict[str, Any]:
        """Fetch the per-day report for this experiment's adgroups and UPSERT
        into ``tk_ad_metrics_snapshots`` on (tiktok_adgroup_id, stat_date).
        """
        with _open_db(self.db_path) as conn:
            exp = conn.execute(
                "SELECT id, advertiser_id, created_at, started_at, ended_at "
                "FROM tk_ad_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if not exp:
                raise ValueError(f"experiment #{experiment_id} not found")
            adgroups = self._experiment_adgroups(conn, experiment_id)

        adgroup_ids = [
            r["tiktok_adgroup_id"] for r in adgroups
            if (r["tiktok_adgroup_id"] or "").strip()
        ]
        if not adgroup_ids:
            return {"experiment_id": experiment_id, "upserted": 0,
                    "rows": 0, "note": "no platform adgroup ids yet"}

        # Window: from the experiment start (fallback created_at) to today.
        start_epoch = exp["started_at"] or exp["created_at"]
        start = datetime.fromtimestamp(start_epoch).date()
        end = exp["ended_at"]
        end_date = datetime.fromtimestamp(end).date() if end else date.today()
        # Guard against clock skew.
        if end_date < start:
            end_date = start

        rows = self.ads.get_report(
            advertiser_id=str(exp["advertiser_id"]),
            adgroup_ids=adgroup_ids,
            start=start.isoformat(),
            end=end_date.isoformat(),
        )

        now = int(time.time())
        upserted = 0
        with _open_db(self.db_path) as conn:
            for r in rows:
                ag_id = str(r.get("tiktok_adgroup_id") or "").strip()
                stat_date = str(r.get("stat_date") or "").strip()
                if not ag_id or not stat_date:
                    continue
                # Phase 2 metrics. The middle layer may emit either the local
                # column name or the raw Marketing-API metric name — accept both.
                def _pick(*keys: str) -> Any:
                    for k in keys:
                        if r.get(k) is not None:
                            return r.get(k)
                    return None

                conn.execute(
                    """
                    INSERT INTO tk_ad_metrics_snapshots
                        (tiktok_adgroup_id, stat_date, spend, impressions,
                         clicks, conversions, orders, gmv, ctr, cpa, roas,
                         video_views, video_2s, video_6s, video_p100,
                         avg_video_play, profile_visits, follows, engagements,
                         reach, frequency,
                         currency, raw_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?)
                    ON CONFLICT(tiktok_adgroup_id, stat_date) DO UPDATE SET
                        spend          = excluded.spend,
                        impressions    = excluded.impressions,
                        clicks         = excluded.clicks,
                        conversions    = excluded.conversions,
                        orders         = excluded.orders,
                        gmv            = excluded.gmv,
                        ctr            = excluded.ctr,
                        cpa            = excluded.cpa,
                        roas           = excluded.roas,
                        video_views    = excluded.video_views,
                        video_2s       = excluded.video_2s,
                        video_6s       = excluded.video_6s,
                        video_p100     = excluded.video_p100,
                        avg_video_play = excluded.avg_video_play,
                        profile_visits = excluded.profile_visits,
                        follows        = excluded.follows,
                        engagements    = excluded.engagements,
                        reach          = excluded.reach,
                        frequency      = excluded.frequency,
                        currency       = excluded.currency,
                        raw_json       = excluded.raw_json,
                        fetched_at     = excluded.fetched_at
                    """,
                    (
                        ag_id, stat_date,
                        float(r.get("spend") or 0),
                        int(r.get("impressions") or 0),
                        int(r.get("clicks") or 0),
                        int(r.get("conversions") or 0),
                        int(r.get("orders") or 0),
                        float(r.get("gmv") or 0),
                        float(r.get("ctr") or 0),
                        float(r.get("cpa") or 0),
                        float(r.get("roas") or 0),
                        int(_pick("video_views", "video_play_actions") or 0),
                        int(_pick("video_2s", "video_watched_2s") or 0),
                        int(_pick("video_6s", "video_watched_6s") or 0),
                        int(_pick("video_p100", "video_views_p100") or 0),
                        float(_pick("avg_video_play", "average_video_play") or 0),
                        int(_pick("profile_visits") or 0),
                        int(_pick("follows") or 0),
                        int(_pick("engagements") or 0),
                        int(_pick("reach") or 0),
                        float(_pick("frequency") or 0),
                        str(r.get("currency") or ""),
                        json.dumps(r.get("raw") or {}, ensure_ascii=False),
                        now,
                    ),
                )
                upserted += 1
            conn.commit()

        logger.info("refresh_metrics: experiment=%d adgroups=%d upserted=%d",
                    experiment_id, len(adgroup_ids), upserted)
        return {
            "experiment_id": experiment_id,
            "adgroups": len(adgroup_ids),
            "rows": len(rows),
            "upserted": upserted,
            "window": {"start": start.isoformat(), "end": end_date.isoformat()},
        }

    # ------------------------------------------------------------------
    # 4. evaluate_experiment
    # ------------------------------------------------------------------

    def _aggregate_by_audience(
        self, conn: sqlite3.Connection, experiment_id: int,
        *, objective: str = DEFAULT_OBJECTIVE,
    ) -> list[dict[str, Any]]:
        """Per-audience aggregate across this experiment's adgroup snapshots,
        annotated with the objective-aware primary KPI."""
        rows = conn.execute(
            """
            SELECT a.id            AS audience_id,
                   a.name          AS name,
                   a.status        AS status,
                   COALESCE(SUM(m.spend), 0)        AS spend,
                   COALESCE(SUM(m.conversions), 0)  AS conversions,
                   COALESCE(SUM(m.orders), 0)       AS orders,
                   COALESCE(SUM(m.gmv), 0)          AS gmv,
                   COALESCE(SUM(m.video_views), 0)  AS video_views,
                   COALESCE(SUM(m.follows), 0)      AS follows,
                   COALESCE(SUM(m.engagements), 0)  AS engagements,
                   COUNT(DISTINCT m.stat_date)      AS days
              FROM tk_ad_groups g
              JOIN tk_ad_audiences a ON a.id = g.audience_id
         LEFT JOIN tk_ad_metrics_snapshots m
                ON m.tiktok_adgroup_id = g.tiktok_adgroup_id
                   AND m.tiktok_adgroup_id != ''
             WHERE g.experiment_id = ?
             GROUP BY a.id, a.name, a.status
            """,
            (experiment_id,),
        ).fetchall()
        out = []
        for r in rows:
            spend = float(r["spend"] or 0)
            gmv = float(r["gmv"] or 0)
            agg = {
                "audience_id": r["audience_id"],
                "name": r["name"],
                "status": r["status"],
                "spend": spend,
                "conversions": int(r["conversions"] or 0),
                "orders": int(r["orders"] or 0),
                "gmv": gmv,
                "roas": (gmv / spend) if spend else 0.0,
                "video_views": int(r["video_views"] or 0),
                "follows": int(r["follows"] or 0),
                "engagements": int(r["engagements"] or 0),
                "days": int(r["days"] or 0),
            }
            agg.update(_primary_kpi(objective, agg))
            out.append(agg)
        return out

    def evaluate_experiment(self, experiment_id: int) -> dict[str, Any]:
        """Apply promotion thresholds per audience. If an audience meets ALL of
        min_spend / min_conv / min_days → flip its ``tk_ad_audiences.status`` to
        'winning'. Otherwise leave 'candidate'/'testing' — unless the experiment
        has ended, in which case non-winners become 'inconclusive'. Then have
        the AI write a human ``decision_summary`` onto the experiment.
        """
        th = _win_thresholds()
        with _open_db(self.db_path) as conn:
            exp = conn.execute(
                """
                SELECT e.id, e.status, e.ended_at, e.pack_id, e.objective,
                       COALESCE(p.display_name, '') AS pack_name
                  FROM tk_ad_experiments e
             LEFT JOIN packs p ON p.id = e.pack_id
                 WHERE e.id = ?
                """,
                (experiment_id,),
            ).fetchone()
            if not exp:
                raise ValueError(f"experiment #{experiment_id} not found")
            objective = _normalize_objective(exp["objective"])
            ended = exp["ended_at"] is not None or exp["status"] in ("killed", "ended")
            aggs = self._aggregate_by_audience(
                conn, experiment_id, objective=objective,
            )

            results: list[dict[str, Any]] = []
            for a in aggs:
                # Objective-aware winning gate: enough spend + days + the
                # objective's volume floor (orders / views / follows).
                meets = (
                    a["spend"] >= th["min_spend"]
                    and a["days"] >= th["min_days"]
                    and _meets_volume(objective, a, th)
                )
                if meets:
                    verdict = "winning"
                elif ended:
                    verdict = "inconclusive"
                else:
                    verdict = "candidate"
                # Persist the audience status. Never demote an already-winning
                # audience back to candidate.
                cur_status = a["status"]
                if verdict == "winning":
                    new_status = "winning"
                elif cur_status == "winning":
                    new_status = "winning"  # keep
                elif verdict == "inconclusive":
                    new_status = "inconclusive"
                else:
                    # leave testing/candidate as-is (don't clobber 'testing')
                    new_status = cur_status if cur_status in ("testing", "candidate") else "candidate"
                if new_status != cur_status:
                    conn.execute(
                        "UPDATE tk_ad_audiences SET status = ? WHERE id = ?",
                        (new_status, a["audience_id"]),
                    )
                a2 = dict(a)
                a2["verdict"] = verdict
                a2["new_status"] = new_status
                results.append(a2)
            conn.commit()

        # AI decision summary (best-effort — never block the verdicts on it).
        summary = ""
        try:
            prompt = build_decision_summary_prompt(
                pack_display_name=exp["pack_name"] or f"pack#{exp['pack_id']}",
                thresholds=th,
                experiment_status=exp["status"],
                audience_rows=results,
            )
            summary = self.router.text_complete(
                prompt,
                system=DECISION_SUMMARY_SYSTEM_PROMPT,
                temperature=0.5,
                max_tokens=2000,
                task="tkads_experiment:decision_summary",
                related_table="tk_ad_experiments",
                related_id=experiment_id,
            ).strip()
        except Exception as e:
            logger.warning("evaluate_experiment: decision summary AI failed: %s", e)
            summary = ""

        if summary:
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE tk_ad_experiments SET decision_summary = ? WHERE id = ?",
                    (summary[:4000], experiment_id),
                )
                conn.commit()

        winners = [r for r in results if r["verdict"] == "winning"]
        logger.info("evaluate_experiment: experiment=%d audiences=%d winners=%d",
                    experiment_id, len(results), len(winners))
        return {
            "experiment_id": experiment_id,
            "objective": objective,
            "ended": ended,
            "thresholds": th,
            "audiences": results,
            "winners": [r["audience_id"] for r in winners],
            "decision_summary": summary,
        }

    # ------------------------------------------------------------------
    # 5. kill_experiment
    # ------------------------------------------------------------------

    def kill_experiment(self, experiment_id: int) -> dict[str, Any]:
        """DISABLE every adgroup on the platform and mark the experiment
        'killed'. Best-effort per adgroup — collects errors but always sets
        the local terminal state.
        """
        with _open_db(self.db_path) as conn:
            exp = conn.execute(
                "SELECT id, advertiser_id, status FROM tk_ad_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if not exp:
                raise ValueError(f"experiment #{experiment_id} not found")
            adgroups = self._experiment_adgroups(conn, experiment_id)

        advertiser_id = str(exp["advertiser_id"])
        disabled = 0
        errors: list[str] = []
        for ag in adgroups:
            tt_id = (ag["tiktok_adgroup_id"] or "").strip()
            if not tt_id:
                continue
            try:
                self.ads.set_adgroup_status(tt_id, advertiser_id, "DISABLE")
                disabled += 1
            except Exception as e:
                errors.append(f"adgroup {tt_id}: {e}")
                logger.warning("kill_experiment: disable %s failed: %s", tt_id, e)

        now = int(time.time())
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE tk_ad_experiments SET status = 'killed', ended_at = ? WHERE id = ?",
                (now, experiment_id),
            )
            conn.execute(
                "UPDATE tk_ad_groups SET status = 'paused' "
                "WHERE experiment_id = ? AND tiktok_adgroup_id != ''",
                (experiment_id,),
            )
            conn.commit()

        return {
            "experiment_id": experiment_id,
            "status": "killed",
            "adgroups_disabled": disabled,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # 6. audience_leaderboard
    # ------------------------------------------------------------------

    def audience_leaderboard(
        self, *, pack_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Read-time aggregation across ``tk_ad_metrics_snapshots`` joined via
        tk_ad_groups → tk_ad_audiences, grouped by audience.

        Objective-aware (Phase 2): each audience's objective is taken from its
        highest-spend experiment (fallback: most recent). The row carries the
        objective's primary KPI as ``primary_kpi_value`` + ``primary_kpi_name``
        (ROAS / CPV / 单粉成本) plus ``primary_kpi_direction``. Raw aggregates
        (spend/orders/gmv/roas/video_views/follows/...) are kept so the UI can
        show objective-specific columns. Rows are sorted so the best performer
        for its own objective comes first (descending for higher-is-better,
        ascending for lower-is-better KPIs).
        """
        sql = """
            SELECT
                a.id                              AS audience_id,
                a.name                            AS name,
                a.status                          AS status,
                a.source                          AS source,
                a.pack_id                         AS pack_id,
                a.hypothesis                      AS hypothesis,
                COALESCE(SUM(m.spend), 0)         AS spend,
                COALESCE(SUM(m.impressions), 0)   AS impressions,
                COALESCE(SUM(m.clicks), 0)        AS clicks,
                COALESCE(SUM(m.conversions), 0)   AS conversions,
                COALESCE(SUM(m.orders), 0)        AS orders,
                COALESCE(SUM(m.gmv), 0)           AS gmv,
                COALESCE(SUM(m.video_views), 0)   AS video_views,
                COALESCE(SUM(m.video_2s), 0)      AS video_2s,
                COALESCE(SUM(m.video_6s), 0)      AS video_6s,
                COALESCE(SUM(m.video_p100), 0)    AS video_p100,
                COALESCE(SUM(m.profile_visits), 0) AS profile_visits,
                COALESCE(SUM(m.follows), 0)       AS follows,
                COALESCE(SUM(m.engagements), 0)   AS engagements,
                COALESCE(SUM(m.reach), 0)         AS reach,
                COUNT(DISTINCT m.stat_date)       AS days_with_data,
                COUNT(DISTINCT g.experiment_id)   AS experiments,
                -- Objective of this audience's highest-spend experiment (ties
                -- broken by most recent). Drives the primary KPI selection.
                (
                    SELECT e.objective
                      FROM tk_ad_groups g2
                      JOIN tk_ad_experiments e ON e.id = g2.experiment_id
                 LEFT JOIN tk_ad_metrics_snapshots m2
                        ON m2.tiktok_adgroup_id = g2.tiktok_adgroup_id
                       AND m2.tiktok_adgroup_id != ''
                     WHERE g2.audience_id = a.id
                     GROUP BY e.id
                     ORDER BY COALESCE(SUM(m2.spend), 0) DESC,
                              e.created_at DESC
                     LIMIT 1
                )                                 AS objective
            FROM tk_ad_audiences a
            LEFT JOIN tk_ad_groups g ON g.audience_id = a.id
            LEFT JOIN tk_ad_metrics_snapshots m
                   ON m.tiktok_adgroup_id = g.tiktok_adgroup_id
                  AND m.tiktok_adgroup_id != ''
        """
        params: list[Any] = []
        if pack_id is not None:
            sql += " WHERE a.pack_id = ?"
            params.append(pack_id)
        sql += """
            GROUP BY a.id, a.name, a.status, a.source, a.pack_id, a.hypothesis
        """
        with _open_db(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["id"] = d["audience_id"]  # alias for UI consumers expecting `id`
            spend = float(d["spend"] or 0)
            gmv = float(d["gmv"] or 0)
            clicks = int(d["clicks"] or 0)
            conversions = int(d["conversions"] or 0)
            impressions = int(d["impressions"] or 0)
            d["roas"] = (gmv / spend) if spend else 0.0
            d["cpa"] = (spend / conversions) if conversions else 0.0
            d["ctr"] = (clicks / impressions) if impressions else 0.0
            d["objective"] = _normalize_objective(d.get("objective"))
            d.update(_primary_kpi(d["objective"], d))
            out.append(d)

        # Sort objective-aware: best-for-its-objective first. A lower-is-better
        # KPI of 0 means "no volume yet" (e.g. CPV with 0 views) — treat as worst
        # so populated audiences outrank empty ones; same for higher-is-better.
        def _rank_key(d: dict[str, Any]):
            val = float(d.get("primary_kpi_value") or 0)
            if d.get("primary_kpi_direction") == "lower":
                # ascending: smaller (but > 0) is best; 0 => worst (push to end).
                eff = val if val > 0 else float("inf")
                return (0 if val > 0 else 1, eff, -float(d["spend"]), d["id"])
            # higher-is-better: bigger is best (descending).
            return (0, -val, -float(d["spend"]), d["id"])

        out.sort(key=_rank_key)
        return out

    # ------------------------------------------------------------------
    # 7. Phase 2: identities + promotable videos (Spark inputs)
    # ------------------------------------------------------------------

    def list_identities(self) -> list[dict[str, Any]]:
        """Pass-through to the middle layer's ad-identity list (Spark ads).
        Returns ``[{identity_id, identity_type, display_name, can_pull_video}]``.
        """
        return self.ads.list_identities()

    def list_promotable_videos(
        self, *, pack_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """List already-posted TikTok videos usable as Spark ad sources, read
        from ``tk_videos`` where ``tiktok_video_id != ''`` (the real TikTok
        item_id). Optionally filtered by ``pack_id``.

        Each row: ``video_id`` (local tk_videos.id), ``tiktok_video_id``,
        ``pack_id``, ``one_liner``, ``caption``, ``publish_status``,
        ``published_at``.
        """
        sql = """
            SELECT id, pack_id, tiktok_video_id, video_one_liner, caption,
                   publish_status, published_at, local_video_path
              FROM tk_videos
             WHERE COALESCE(tiktok_video_id, '') != ''
        """
        params: list[Any] = []
        if pack_id is not None:
            sql += " AND pack_id = ?"
            params.append(pack_id)
        sql += " ORDER BY COALESCE(published_at, 0) DESC, id DESC"
        with _open_db(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "video_id": r["id"],
                "tiktok_video_id": str(r["tiktok_video_id"] or ""),
                "pack_id": r["pack_id"],
                "one_liner": str(r["video_one_liner"] or ""),
                "caption": str(r["caption"] or ""),
                "publish_status": str(r["publish_status"] or ""),
                "published_at": r["published_at"],
                "local_video_path": str(r["local_video_path"] or ""),
            })
        return out


_svc: Optional[AdsExperimentService] = None


def get_ads_experiment_service() -> AdsExperimentService:
    """Return the process-wide AdsExperimentService singleton."""
    global _svc
    if _svc is None:
        _svc = AdsExperimentService()
    return _svc
