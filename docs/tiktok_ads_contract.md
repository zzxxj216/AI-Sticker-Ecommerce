# TikTok 广告系统 — 跨仓库契约（唯一真相源）

本文件是 multi-channel-api（中间层，repo B）与 AI-Sticker-Ecommerce（本仓库，repo A）之间的 HTTP 接口契约。两侧并行开发，**任何一侧改接口都要先改本文件**。

## 背景

一个人群测试系统：前期小预算投放造数据 → 按 adgroup 拉报表 → 判定优质人群 → 沉淀进人群库复用。核心实体是**人群(audience)**。一个实验 = 1 个 campaign + N 个 adgroup，**同素材、同出价，只变人群**，使 adgroup 表现干净归因到单个人群。

## 关键事实

**TikTok Shop API ≠ TikTok Marketing(Ads) API。**
- Shop：`open-api.tiktokglobalshop.com`，HMAC-SHA256 签名，Shop OAuth（repo B 现有 `app/platforms/tiktok/client.py`，**不要复用**）。
- Marketing：`https://business-api.tiktok.com/open_api/v1.3/`，请求头 `Access-Token`（**无 HMAC**），核心维度 `advertiser_id`，独立 OAuth app（`https://business-api.tiktok.com/portal/auth`）。
- → repo B 新建独立平台模块 `app/platforms/tiktok_ads/`。

## 端点（repo A 调用，repo B 实现）

基址：`{TKSHOP_SERVER_URL}/api/v1/tiktok-ads`（默认 `http://localhost:8000`）。
响应统一包 `ApiResponse`：`{ "success": true, "message": "ok", "data": {...} }`（repo B 实际 schema，见 `app/schemas/common.py`，**无 code 字段**）；`success == false` 表示错误，`message` 为可读原因。无凭证/未授权时返回友好 `success=false`（HTTP 200）而非 500。
> 注：契约初稿写的是 `{code,data,message}`，但 repo B 现有 `ApiResponse` 用的是 `success: bool`。已按 repo B 实际 schema 对齐 —— repo A 的 HTTP client 必须判 `success` 字段，不是 `code`。

| # | 方法 | 路径 | 用途 |
|---|---|---|---|
| 1 | GET | `/auth/url` | 返回 Marketing OAuth 授权链接 |
| 2 | GET | `/auth/callback?auth_code=` | 授权码换 advertiser token，返回可用 advertiser 列表 |
| 3 | GET | `/advertisers` | 列出已授权广告主 |
| 4 | POST | `/experiments` | 建一次人群实验（1 campaign + N adgroup + ad） |
| 5 | POST | `/audiences` | （可选）建平台侧 custom/lookalike 人群 |
| 6 | GET | `/report` | 拉 adgroup 级按天报表 |
| 7 | POST | `/adgroups/{adgroup_id}/status` | 暂停/启用单个 adgroup（kill-switch） |

### 4. POST /experiments — 请求体
```json
{
  "advertiser_id": "7xxxxx",
  "objective": "PRODUCT_SALES",          // 或 TRAFFIC / VIDEO_VIEWS 等
  "promote": { "type": "shop_product", "ref_id": "<tiktok_product_id 或 video id>" },
  "per_adgroup_budget": 20.0,
  "currency": "USD",
  "creative": { "video_id": "", "image_ids": [], "ad_text": "", "call_to_action": "SHOP_NOW" },
  "audiences": [
    { "client_audience_id": 12, "name": "Gen-Z meme lovers",
      "targeting": { "age_groups": ["AGE_18_24"], "genders": ["FEMALE"],
                     "locations": [6252001], "languages": ["en"],
                     "interest_category_ids": [], "action_category_ids": [] } }
  ],
  "dry_run": true
}
```
- `dry_run=true`（默认）：**不真实投放**，只回 `data.preview` 描述将建的 campaign/adgroup 结构。
- `dry_run=false`：真实建 campaign+adgroups+ads。
- `audiences[].client_audience_id` 是 repo A `tk_ad_audiences.id`，B 原样回填到结果里供 A 关联。

### 4. POST /experiments — 响应 data
```json
{
  "dry_run": false,
  "tiktok_campaign_id": "17xxxx",
  "adgroups": [
    { "client_audience_id": 12, "tiktok_adgroup_id": "17yyyy", "status": "ENABLE", "budget": 20.0 }
  ],
  "preview": null
}
```

### 6. GET /report — 入参（query）
`advertiser_id`、`adgroup_ids`（逗号分隔）、`start_date`、`end_date`（`YYYY-MM-DD`）。

### 6. GET /report — 响应 data
按 (adgroup, 天) 一行；repo A 按 `(tiktok_adgroup_id, stat_date)` upsert：
```json
{
  "rows": [
    { "tiktok_adgroup_id": "17yyyy", "stat_date": "2026-05-29",
      "spend": 18.4, "impressions": 5300, "clicks": 210, "ctr": 0.0396,
      "conversions": 12, "orders": 9, "gmv": 71.0, "cpa": 1.53, "roas": 3.86,
      "currency": "USD", "raw": { } }
  ]
}
```
- 缺失指标用 0；`raw` 为该行 Marketing API 原始字段，A 落到 `raw_json`。
- `gmv`/`orders` 在非电商目标下可为 0。

> **Marketing API metric 名（已对真实广告主 7488243015711408146 实测确认 2026-05）**
> report/integrated/get/ 只要 metrics 含任一非法名就整体 40002，故只能用已验证名：
> - 合法：`spend / impressions / clicks / ctr / conversion / cost_per_conversion / complete_payment / complete_payment_roas / total_purchase_value`
> - 非法（勿用）：`orders / gross_revenue / cost_per_order / gmv / total_complete_payment / total_complete_payment_value / complete_payment_amount`
> - 映射：`complete_payment`→orders（成交次数），`total_purchase_value`→gmv，`complete_payment_roas`→roas
>
> **adgroup 定向字段（实测确认）**：`gender`（单值 GENDER_*）/ `age_groups`（AGE_18_24…）/ `location_ids` / `languages` / `interest_category_ids`（字符串id）。campaign 用 `objective_type`（非 `objective`）。
> **仍未验证**：shop_product 电商广告的 `promotion_type` 取值与商品挂载字段——测试账户无 Shop 广告系列可读，首次真投前需校验。

### 7. POST /adgroups/{id}/status — 请求体
`{ "advertiser_id": "...", "status": "DISABLE" }`（`ENABLE` / `DISABLE`）。

## repo A 侧落库映射

- 账户 → `tk_ads_accounts`
- 人群（AI 生成，A 自己造）→ `tk_ad_audiences`（A 把 `id` 作为 `client_audience_id` 传给 B）
- 实验 → `tk_ad_experiments`（存 `tiktok_campaign_id`）
- adgroup → `tk_ad_groups`（存 `tiktok_adgroup_id` + `audience_id`）
- 报表 → `tk_ad_metrics_snapshots`（upsert）

详见 `scripts/migrations/020_tiktok_ads.sql`。

## repo A service 对外方法签名（Track C 依赖，先固定）

`src/services/tiktok/ads_experiment_service.py`：
- `generate_audience_candidates(pack_id: int, *, n: int = 3) -> list[dict]` — AI 生成人群，写 `tk_ad_audiences`，返回新建行。
- `create_experiment(*, advertiser_id, promote_type, promote_ref_id, pack_id, audience_ids, per_adgroup_budget, objective, creative, dry_run=True) -> dict`
- `refresh_metrics(experiment_id: int) -> dict` — 拉报表 upsert。
- `evaluate_experiment(experiment_id: int) -> dict` — 过晋级门槛判赢 + AI 写 `decision_summary`。
- `kill_experiment(experiment_id: int) -> dict` — 暂停全部 adgroup。
- `audience_leaderboard(*, pack_id: int | None = None) -> list[dict]` — 人群库排行榜（按 roas 聚合）。

## 护栏 env（repo A）

- `TKADS_MAX_EXPERIMENT_BUDGET`、`TKADS_MAX_CONCURRENT_EXPERIMENTS`
- `TKADS_WIN_MIN_SPEND`、`TKADS_WIN_MIN_CONV`（默认 50）、`TKADS_WIN_MIN_DAYS`（默认 7）
- 实投 `dry_run=false` 需人工显式开启。

---

# Phase 2 增补 — 目标驱动 + Spark 视频 + 19-metric

> 见 `scripts/migrations/021_tiktok_ads_objective.sql`。背景：把"推广类型/目标"提为一等输入，指标随目标变。

## 新增/变更端点（repo B 实现）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/identities` | 列广告身份（Spark 用），返回 `[{identity_id, identity_type, display_name, can_pull_video}]`。源 `identity/get/`，已验证可用。 |

`POST /experiments` 请求体**新增**：`identity_id`、`identity_type`、`objective`（三选一：`VIDEO_VIEWS`/`PRODUCT_SALES`/`ENGAGEMENT`）。
- `promote.type=video` → **Spark**：`promote.ref_id` = 已发帖的 `tiktok_video_id`（= TikTok item_id），ad 用 identity_id+identity_type+item_id，**不上传素材**。
- `promote.type=shop_product` → 同 Phase 1（商品 id；promotion_type 真投前仍需校验）。

## 目标 → optimization_goal / 主 KPI / 门槛

| objective | optimization_goal | 主 KPI（排名/判赢，方向） | 门槛维度 |
|---|---|---|---|
| PRODUCT_SALES | VALUE / CONVERT | ROAS = `complete_payment_roas`（越高越好） | `complete_payment` 数 + 花费 + 天数 |
| VIDEO_VIEWS | VIDEO_VIEW | CPV = spend/`video_play_actions`（越低越好） | `video_play_actions` + 花费 + 天数 |
| ENGAGEMENT | FOLLOWERS | 单粉成本 = spend/`follows`（越低越好） | `follows` + 花费 + 天数 |

## report metric 全集（19，一次拉，已实测同调合法）
`spend, impressions, clicks, ctr, conversion, cost_per_conversion, complete_payment, complete_payment_roas, total_purchase_value, video_play_actions, video_watched_2s, video_watched_6s, video_views_p100, average_video_play, profile_visits, follows, engagements, reach, frequency`
→ `/report` rows 增字段；repo A 落 `tk_ad_metrics_snapshots` 新列（video_views/video_2s/video_6s/video_p100/avg_video_play/profile_visits/follows/engagements/reach/frequency）。CPV 不存（派生）。

## repo A service 签名变更（Track C 依赖）
- `create_experiment(...)` 增必填 `objective` + `identity_id`（video 时）。
- `audience_leaderboard(*, pack_id=None)` 行内含目标感知的 `primary_kpi_value` + `primary_kpi_name`；排序方向按目标。
- 新增 `list_identities() -> list[dict]`、`list_promotable_videos(*, pack_id=None) -> list[dict]`（读 `tk_videos` 且 `tiktok_video_id != ''`）。
- 门槛 env 增 `TKADS_WIN_MIN_VIEWS`、`TKADS_WIN_MIN_FOLLOWS`。
