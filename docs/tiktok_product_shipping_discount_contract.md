# TikTok 商品：默认包邮 + 自动折扣 — 跨仓库契约

repo A（AI-Sticker-Ecommerce，本仓库）↔ repo B（multi-channel-api，中间层，默认
`http://localhost:8000`)。repo A 负责传字段 / 调接口，**repo B 负责真正调用 TikTok
Shop Open API**。任一侧改接口都要先改本文件。

目标（运营诉求）：每次制作完产品上传后，**不再手动**去卖家后台开「Default free
shipping」、也不再手动加折扣。希望上传即默认包邮，并自动按 40% 上折扣。

---

## 1. 默认包邮（free shipping）— SHIPPING_DISCOUNT 促销

**结论(2026-06-01)：包邮可以自动化**,机制 = TikTok Promotion API 的
`SHIPPING_DISCOUNT` 活动(与折扣同一套两步:建活动 → 挂商品)。Create Product API
本身没有 free_shipping 字段(这点之前结论正确),但**免邮促销有 API**。

合法 activity_type 实测枚举:`[FIXED_PRICE, DIRECT_DISCOUNT, FLASHSALE,
SHIPPING_DISCOUNT, BUM_MORE_SAVE_MORE]`。

**已实现(best-effort 接线)**:
- repo B `promotions.create_product_free_shipping()` + 路由
  `POST /api/v1/tiktok/products/{id}/free-shipping`(SHIPPING_DISCOUNT,
  product_level=PRODUCT)。
- repo A `tkshop.apply_free_shipping()`,`publish()` 成功后自动调用(env
  `TKSHOP_DEFAULT_FREE_SHIPPING` 默认开;失败 best-effort 不影响上架)。

**唯一未决卡点**:create SHIPPING_DISCOUNT 活动需要 `discount_threshold` 配置,否则
报 `17029268: Discount threshold type not supported`。其**确切 JSON 字段名/结构**在
官方文档(JS 渲染抓不到)里;~28 次盲探未命中(TikTok 静默丢弃未知字段);店里无现成
SHIPPING_DISCOUNT 活动可 introspect。已隔离到 `promotions.py` 的
`_FREE_SHIPPING_THRESHOLD` 常量,确认后填一处即通。

**卖家后台语义(已查官方教程确认)**:Seller Center > Marketing > Promotions >
Shipping fee discount。指定商品(Specific Products)时**门槛(threshold)= None
(无门槛)**;Discount 只有 "Free shipping" 一个选项;Inventory = seller-fulfilled。
即我们要的 body 语义 = 无门槛 + 免邮 + 卖家自配送。

**最快解锁**:在卖家后台手动建一个免邮促销 → `GET /promotion/202309/activities/
{id}?activity_id=` introspect 出真实 threshold 结构 → 填进 `_FREE_SHIPPING_THRESHOLD`。

---

## 2. 自动折扣（默认 40%）

发布成功后，repo A 会**额外调用一个新接口**给商品挂折扣：

### 端点（repo B 实现）
```
POST {TKSHOP_SERVER_URL}/api/v1/tiktok/products/{tiktok_product_id}/discount?shop=<shop>
```

### 请求体
```json
{
  "discount_percent": 40.0,
  "sku_id": "<tiktok_sku_id 可空>",
  "product_title": "<用于活动命名，可空>",
  "activity_type": "DIRECT_DISCOUNT",
  "begin_time": 1730000000,
  "end_time": 1761536000
}
```
- `discount_percent`：折扣力度（百分比，40 表示降价 40% / 即 6 折）。
- `shop`：query 参数，与 sticker_publish 一致的店铺名。
- `activity_type`：促销活动类型（repo A 默认 `DIRECT_DISCOUNT`，env 可调）。
- `begin_time` / `end_time`：活动时间窗（epoch 秒）。repo A 默认给一个很长的窗口
  （`TKSHOP_DISCOUNT_WINDOW_DAYS`，默认 365 天）来模拟常驻折扣。

### 响应（统一 `ok` 风格，也兼容 `{success,data}` 信封）
成功：
```json
{ "ok": true, "activity_id": "1730000000000", "discount_percent": 40.0 }
```
失败：
```json
{ "ok": false, "error_code": "TT_xxxx", "error_message": "可读原因" }
```

**repo B 需要做的（已用 EcomPHP SDK / 官方 Promotion API 调研确认折扣 API 存在）**：

TikTok Promotion API 是**两步活动模型**，不是单接口给商品打折：
1. `POST /promotion/202309/activities` 创建活动 —— 字段：`title`、`activity_type`
   (直降类，如 `DIRECT_DISCOUNT` / `FIXED_PRICE` / `FLASHSALE`，repo B 选支持
   百分比降价的那种)、`begin_time`、`end_time`、`product_level`(=PRODUCT)。
   **注意：活动需要时间窗**，repo B 应设一个足够长的窗口(如 begin=now，
   end=now+N 年)来模拟“常驻 40% 折扣”。
2. `PUT /promotion/202309/activities/{activity_id}/products` —— 把本商品/SKU 加入
   活动，折扣力度(百分比)写在 products[] 元素里。

repo B 把上面两步封装成本契约的单个 `/discount` 端点，对 repo A 暴露简单语义
(`discount_percent`)。幂等建议：同一 product 已在等额活动里就直接返回该 activity。
官方文档(需登录)：Promotion API overview / Create Activity 202309。

### repo A 侧行为（已实现）
- `src/services/tkshop/service.py`：`publish()` 成功后调用
  `apply_discount(product_id)` → `_create_product_discount_remote()`。
  所有发布路径（单个 / 本地 / 批量）都经 `publish()`，故全覆盖。
- **持久化**：结果写入 `tkshop_products` 的 `discount_status`/`discount_percent`/
  `discount_activity_id`/`discount_applied_at`（migration 022）。
- **幂等**：`discount_status == 'applied'` 时自动跳过；手动按钮用 `force=True` 重试。
- **best-effort**：折扣失败**不会**让发布标记为失败（商品已上架）；失败仅记日志 +
  写 `discount_status='failed'`，并在 `publish()` 返回的 `discount` 字段带回结果。
- **手动重试**：产品详情页「🏷 自动折扣」区有「应用折扣」按钮 →
  `POST /v2/products/{id}/apply-discount`（可填百分比，留空用默认）。
- env：见下表。

---

## 护栏 / env（repo A）

| env | 默认 | 说明 |
|-----|------|------|
| `TKSHOP_AUTO_DISCOUNT_PERCENT` | `50` | 发布成功后自动折扣百分比，0=关闭（默认 $13.98×50%=$6.99） |
| `TKSHOP_DEFAULT_SALE_PRICE` | `13.98` | 标价 / 导出表格默认原价 |
| `TKSHOP_DEFAULT_DISCOUNT_PRICE` | `6.99` | 导出表格默认折后价（$13.98 + 50% off） |
| `TKSHOP_DISCOUNT_WINDOW_DAYS` | `365` | 折扣活动时间窗（天） |
| `TKSHOP_DISCOUNT_ACTIVITY_TYPE` | `DIRECT_DISCOUNT` | 传给中间层的促销活动类型 |

> 包邮(free shipping)已从 repo A 撤掉：TikTok Create Product 无此字段，需走运费模板/
> 免邮促销，待 repo B 确认后再做（见上文第 1 节）。

## 状态

- **折扣**：✅✅ **端到端真实验证通过(2026-05-30, 真实店铺 main)**。完整路径
  repo A 详情页/上品 → repo A `apply_discount` → repo B
  `POST /api/v1/tiktok/products/{id}/discount` → TikTok Promotion 两步活动
  (建活动 + 挂商品) → repo A 落库,全部跑通(给真实商品挂上 40% 后又 deactivate 清理)。
- **前置(已解决)**:需 TikTok 应用具备 **Promotion** OAuth scope 并**重新授权**店铺
  (刷新旧 token 不会带新 scope)。否则报 `105005 Access denied`。

### 实测确认的 TikTok Promotion 202309 规则(repo B 已按此实现)
| 错误码 | 规则 | 处理 |
|--------|------|------|
| 17029007 | 活动周期 ≤ 90 天 | 窗口默认 89 天,repo B 硬上限 90 |
| 17029004 | 活动名必须全局唯一 | 标题自动带时间戳 |
| 17029013 | product_level=PRODUCT 时 products[].sku 必须为空 | 商品级折扣不带 sku 明细 |
| 36009004 | PUT body 必须含 activity_id | body 同时带 path 的 activity_id |
| — | 折扣百分比 | 放 products[].discount(字符串,"40"=立减40%)|
- **包邮**：⚠️ TikTok Create Product **没有** free_shipping 字段;靠运费模板/免邮促销。
  repo A 侧只是传 intent 标记;repo B 需确认 TikTok 是否有对应公开 API，否则只能后台手动。
  这是对初版假设(以为是产品字段)的纠正。
- repo B 实现前：包邮标记无效果、折扣调用失败(均已优雅降级，不影响上架)。
