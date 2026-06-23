# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI sticker e-commerce content workbench: hot-topic discovery → topic review → AI sticker-pack generation → publish (TikTok videos, TikTok Shop listings, Shopify blogs) → paid TikTok ad experiments. Python + FastAPI + Jinja2, SQLite, served as a server-rendered web app.

## Commands

```bash
# Run the web app (FastAPI). PORT defaults to 8888; auto-reload ON unless ENV=production.
python web_app.py                      # → http://localhost:8888  (RELOAD=0 to disable reload)

# DB migrations — SQLite, idempotent, tracked in schema_migrations table.
python scripts/migrate.py              # apply all pending to data/ops_workbench.db
python scripts/migrate.py --status     # show applied vs pending
python scripts/migrate.py --dry-run

# Background pipeline jobs (interval-gated; each run recorded in scheduled_jobs).
python scripts/scheduler.py --list             # list registered jobs
python scripts/scheduler.py --run-once <JOB>   # run one job immediately

# Tests (pytest, testpaths=tests). Many tests make real AI calls / need API keys.
pytest tests/                          # full suite
pytest tests/test_batch_pipeline.py    # single file
pytest tests/test_batch_pipeline.py::test_name   # single test
```

Config is env-driven via `.env` (loaded with override at import). The single SQLite DB is `data/ops_workbench.db`.

## Architecture (the big picture)

**Two web route layers, one app.** `src/web/app.py` is the FastAPI root holding legacy ("V1") routes + static mounts. The current product lives in `src/web/v2_routes.py` (a large `APIRouter` mounted under `/v2`), backed by `src/services/`. New work goes in V2. Static assets are served from on-disk mounts: `/v2-outputs` → `output/packs/`, plus `/outputs`, `/preview-images`, `/daily-sticker-assets`.

**Service layer is the real codebase.** `src/services/<domain>/` each own a slice and talk to SQLite directly (raw `sqlite3`, WAL, Row factory — no ORM). Route handlers stay thin and call services. Key domains: `sticker`/`batch`/`packs` (pack generation pipeline), `tiktok` (ads + Spark video + display metrics), `tkshop` (TikTok Shop listing), `blog`/`shopify` (blog publish), `hot_topics`/`topic_plans`/`topic_synthesis` (discovery & review), `ai` (model router).

**All LLM calls go through `AIRouter`** (`src/services/ai/router.py`, `get_router()`). Two methods carry almost everything: `text_complete(...)` (free-form) and `extract_json(...)` (structured, with schema + retries). Generation is typically **two-step**: a creative `text_complete` then an `extract_json` to structure it. Default model is `OPENAI_MODEL` (gpt-5.4); web-search uses a separate AiHubMix path. Don't call provider SDKs directly — extend the router.

**Pack generation is a multi-agent pipeline**: Planner → Designer → Prompt Builder → Image Generation (Gemini, concurrency-limited) → QC. Lives across `src/services/batch/` and `src/services/sticker/`. A "pack" (`packs` table) ties to a `pack_series` (style/palette/archetype + `metadata_json`) and `pack_previews`/`pack_stickers`.

**Scheduling** is custom, not Airflow: `scripts/scheduler.py` registers interval jobs and gates each run by comparing `now - last_finished_at` against the interval, recording every run in the `scheduled_jobs` table via `JobContext`. APScheduler is also attached inside the web app for its own daily sync.

### TikTok Ads subsystem (most actively developed)

Paid ad experiments are a **two-repo system**:
- **This repo** (`src/services/tiktok/ads_experiment_service.py`, `tiktok_ads_service.py`) owns orchestration, the audience library, metric snapshots, and UI. It only speaks HTTP to the middle layer.
- **The sibling repo `F:\练习模块\multi-channel-api`** (FastAPI, separate git repo) owns the actual TikTok Marketing API calls, OAuth tokens, and HMAC/Access-Token auth. Reached at `{TKSHOP_SERVER_URL}/api/v1/tiktok-ads/...` (default `http://localhost:8000`). **Both servers must run** for ads features; the middle layer also powers TikTok Shop publishing.

Cross-repo HTTP contract: `docs/tiktok_ads_contract.md` is the source of truth — update it when changing either side. Middle-layer responses use a `{success, message, data}` envelope (not `{code,...}`).

Core ads concepts:
- **The first-class entity is the audience**, not the ad. An "experiment" = 1 campaign + N ad groups, same creative/bid, **varying only the audience**, so each ad group's performance attributes to one audience. Multi-video: N videos × M audiences = N×M ad groups (each `tk_ad_groups` row carries its own `promote_ref_id`).
- **Objective-aware KPI**: PRODUCT_SALES→ROAS↑, VIDEO_VIEWS→CPV↓, ENGAGEMENT→cost-per-follow↓ (see `_OBJECTIVE_KPI` in `ads_experiment_service.py`). Leaderboard ranks per objective.
- **Spark video promotion**: only an ad identity's **ad-authorized** videos are promotable; pull-mode (`identity/video/get/`, `can_pull_video` identity) lists an owned account's full catalog without per-video auth codes. `tt_video/list/` only returns already-pushed videos — different thing.
- Tables: `tk_ad_audiences` (library), `tk_ad_experiments`, `tk_ad_groups`, `tk_ad_metrics_snapshots` (per-adgroup-per-day, **upsert** on `(adgroup_id, stat_date)` because TikTok restates T+1), `tk_ads_accounts`.

**Real-money guardrails (always preserve these):** experiments default `dry_run=True`; real launch is an explicit separate step; ad groups are created **paused** then enabled; env caps `TKADS_MAX_EXPERIMENT_BUDGET` / `TKADS_MAX_CONCURRENT_EXPERIMENTS` and win thresholds `TKADS_WIN_MIN_*` gate promotion (avoiding learning-phase noise). Note: TikTok's own daily budget is the only *hard* spend cap — external spend watchers lag TikTok's pause and can overshoot.

### Verified TikTok Marketing API field facts (hard-won; don't re-guess)

`docs/tiktok_ads_contract.md` and the memory file record these, but in short: report metrics must use the validated names only (`complete_payment`→orders, `total_purchase_value`→gmv, `complete_payment_roas`→roas; `orders`/`gmv`/`gross_revenue` are invalid and 40002 the whole call). adgroup status update = `POST {entity}/status/update/` with field `operation_status`. VIDEO_VIEWS→optimization_goal `ENGAGED_VIEW` (not `VIDEO_VIEW`). Standard product shopping ads are deprecated ("Custom shop ads are no longer available"); product promotion needs GMV Max (account-level enablement). `business-api.tiktok.com` intermittently ConnectErrors — code retries transient `ad/create` and caches promotable-video lists.

## Operational notes

- The web app runs locally; if ads pages error with ConnectError, the middle-layer server on :8000 isn't running. Start it from the sibling repo: `python -m uvicorn app.main:app --port 8000`.
- `.ads-page` (and other V2 pages) apply CSS `transform`, which breaks `position:fixed` descendants — modals must be reparented to `<body>` to fill the viewport.
- Console output is GBK on this Windows host; avoid printing emoji/non-ASCII in quick `python -c` checks or they crash with UnicodeEncodeError.
