# TikTok 商品广告（GMV Max）开通申请 — 给 TikTok 商务/客服

> 用途：转发给你的 TikTok 客户经理 / Marketing API 工单。目的是开通"用 API 自动投 TikTok Shop 商品广告"的能力。

## 账户信息
- Marketing API App ID: `7644446803990937601`
- Advertiser ID: `7488243015711408146`（Inkelligent-US-TK-Shop）
- Business Center ID: `7507153807659368466`（Inkelligent-TK）
- TikTok Shop Store ID: `7496158611896240643`（InkelligentSticker，store_role=AD_PROMOTION，status=ACTIVE，is_gmv_max_available=true）

## 我们遇到的问题（已自查确认）
1. 传统商品/视频购物广告（Product Shopping Ads / Video Shopping Ads）通过 `adgroup/create/` 创建时返回 **`Custom shop ads are no longer available.`** —— 该广告类型似乎已被下线。
2. GMV Max 是当前唯一可投 TikTok Shop 商品的方式，且 `gmv_max/store/list/` 显示我们的店铺 **is_gmv_max_available=true**；但：
   - 所有 GMV Max 创建类端点（`gmv_max/campaign/create/`、`gmv_max/ad/create/` 等）通过 Marketing API 返回 **空响应/404**，疑似 App 未获 GMV Max API 权限。
   - 广告后台 UI 创建 GMV Max 时只显示 **Promote LIVE**，没有 **Promote products（Product GMV Max）** 选项。
3. 手动 `catalog/product/upload/` 可返回 feed_log_id，但商品始终 **0 入库**（overview 长期 0/0/0），且无 store→catalog 绑定端点。

## 请求开通（二选一或都开）
1. **为 App `7644446803990937601` 开通 GMV Max（Product GMV Max）的 Marketing API 权限/Scope**，使 `gmv_max/*` 商品广告创建端点可用。
2. **为 Advertiser `7488243015711408146` 在广告后台启用 Product GMV Max 功能**（使 Create GMV Max ads 出现 "Promote products" 选项）。

## 期望结果
能够通过 API 对 Store `7496158611896240643` 的商品创建并投放 GMV Max 商品广告（接受 GMV Max 的最低日预算要求）。

---
（参考：视频 Spark 广告已通过 Marketing API 正常投放并产生真实花费，证明账户的视频广告链路正常；仅商品广告受上述限制。）
