# TikTok 视频投放（Spark / Pull 模式）说明

## 核心结论：自有账号视频可直接投，无需逐条授权码

经真机验证（2026-05-31，advertiser 7488243015711408146）：

- 广告身份 **InkelligentSticker**（`identity_type=BC_AUTH_TT`，`identity_authorized_bc_id=7507153807659368466`）的能力标志是 **`can_pull_video=true`**。
- 凭此能力，可用 **pull 模式** 直接拉取该账号**全部已发视频**投广告（实测 171 条），**不需要每条视频在 App 里生成 Spark 授权码**。
- 关键接口区别（之前踩坑点）：
  - ❌ `tt_video/list/` —— 只返回**已 push 授权**的视频（实测只 1 条），不是全部。
  - ✅ `identity/video/get/` —— **pull 模式**，返回账号全部可投视频（翻页 cursor+has_more）。**这才是正确数据源。**

## auth_code 什么时候才需要

只有投**别人的**视频（达人/第三方创作者，`identity_type=AUTH_CODE`）才需要 Spark 授权码——由对方在 TikTok App 里（视频 → 三个点 → 广告设置 → 生成，可选 7/30/60/365 天，可批量 20 条）生成后给你。投**自己 BC 账号**的视频走 pull，不需要。

## 系统里怎么投（UI 流程）

1. 广告实验页 → 新建实验 → 推广类型选「视频(Spark)」
2. 选广告身份（InkelligentSticker）→ 「已发布视频」下拉自动列出该身份**全部可投视频**（来自 `/promotable-videos`）
3. 选一条视频（选中会自动带出其所属身份，防止身份/视频不匹配）→ 选目标（VIDEO_VIEWS / ENGAGEMENT）→ 设预算 → 生成人群并预演(dry_run，不花钱)
4. 进实验详情 → 🚀 真实投放（建广告，暂停态）→ ▶️ 开始投放（真实花钱）→ 停投全部（随时熔断）

## 代码落点
- 中间层 `app/platforms/tiktok_ads/client.py::get_promotable_videos`（翻页 `identity/video/get/`）+ 路由 `GET /api/v1/tiktok-ads/promotable-videos`
- 本仓库 `src/services/tiktok/tiktok_ads_service.py::list_promotable_videos`（调中间层）
- 本仓库 `src/services/tiktok/ads_experiment_service.py::list_promotable_videos`（按身份返回真实可投视频，**不再读 tk_videos**）
- UI `src/web/templates/v2_ads_experiments.html`（视频下拉 + 选视频自动同步身份 + 空状态提示）

## 注意
- 视频下拉为空时，检查广告身份是否 `can_pull_video=true`、middle-channel-api 是否在跑。
- 投放最低预算：视频单组约 $20/天（TikTok 规则），UI 上点「开始投放」后用「停投全部」控制花费。
