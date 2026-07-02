---
name: amazon-listing
description: 在 Amazon(inkelligent 等店/美国站)创建 listing(单品或设计款变体族)、编辑已有 listing 字段(标题/价格/库存/属性)、拉取产品/listing 信息(按 SKU 或 ASIN)、绑定或创建 A+ 内容。当用户要"上品/上架到亚马逊""改/编辑产品信息(标题/价格/库存)""查某个 SKU/ASIN 的产品信息""给 ASIN 绑 A+/做 A+"时使用。
---

# Amazon Listing 工作流(三大功能)

把"创建 listing / 拉取产品信息 / 绑定 A+"固化为可复用流程。脚本在 `scripts/`,从**本仓根目录**运行。

## 前置条件(每次先确认)
**三个功能现在统一只依赖一件事:中间层 multi-channel-api 在跑。**
1. **中间层 multi-channel-api 必须在跑**(create / pull / A+ 全走它的 HTTP)。需带:
   - Amazon 路由 + `VALIDATION_PREVIEW` 透传(`products.py` 的 create 支持 `mode`);
   - **A+ 端点**(`/amazon/aplus/*`,在 `app/platforms/amazon/aplus.py` + `amazon.py`)。
   - 地址 env `AMAZON_MCA_URL`(默认 `http://localhost:8000`;我之前的实例在 `:8001`)。没起会报 NETWORK。
   - 起法:`cd <multi-channel-api> && python -m uvicorn app.main:app --port 8000`
2. **COS 已配**(本仓 `.env` 的 `COS_*`)——create_listing / aplus create 的本地图上传要用(外部对象存储,非要起的服务)。
   - 不依赖本仓 :8888 web 应用。

## 店铺选择与权限
- **指定店铺**:每个脚本支持 `--store <name>`,默认 `main`(=inkelligent)。可用店铺:`main / qifengz / serenorch / bfpeaky`(中间层 `AMAZON_STORES_JSON`)。
  - 例:`python ... pull_product.py --store qifengz <SKU>`
  - 非白名单店铺**直接拒绝**;白名单可用 env `AMAZON_ALLOWED_STORES` 覆盖。
  - 每次运行**醒目打印目标店铺**(`[store = X]`,非默认店额外 `⚠️ 非默认店!`),防误操作。
- **权限模型**:这是本地脚本工具,**无 RBAC**;真正的卖家凭证只在**中间层 `.env`**(`AMAZON_STORES_JSON`),skill 不持有密钥。要控制"谁能操作哪个店",靠:① 谁能跑脚本/起中间层 ② `AMAZON_ALLOWED_STORES` 白名单 ③ 默认锁 main + 非默认店要显式 `--store`。

## 🔴 安全红线(不可破)
- **只用 store=main(=inkelligent)**,美国站 `ATVPDKIKX0DER`。
- **只新增,不删不改现有产品**;create 前对单品/父体先 GET 确认 404(脚本已做)。
- 创建的 listing 默认 `fulfillment_availability.quantity=0`(不可售草稿),确认后再加库存上架。
- 品牌一律 **`Inkelligent`**(已备案;子品牌会丢 GTIN 豁免 → 报 `externally_assigned_product_identifier`)。
- 图片用**内容哈希 key** 传 COS(脚本已做),否则同 URL 被 Amazon 缓存、改图不生效。

---

## 功能一:创建 listing
脚本 `scripts/create_listing.py`。payload JSON = `{sku, product_type, attributes}`;图片字段
`media_location` 可写 `file:<本地路径>`,脚本自动传 COS。

```bash
python .claude/skills/amazon-listing/scripts/create_listing.py validate payload.json   # 零写入校验
python .claude/skills/amazon-listing/scripts/create_listing.py create   payload.json   # 真建/更新
```

**流程**:拼 payload → `validate`(VALIDATION_PREVIEW,看 issues 迭代到 VALID)→ `create`。
**注意 VALIDATION_PREVIEW 比真提交宽松**,真建可能多报字段(如当年 unit_count),所以真建后也要看 issues。

### 产品类型与必填(已验证,直接用)
先探针确认类型:`GET /product-types?keywords=<词>`(贴纸=`STICKER_DECAL`,姓名标签/标签=`LABEL`)。
完整可用模板见 `docs/amazon_sticker_decal_attributes.example.json`。关键字段事实:
- 通用必填:`brand`(=Inkelligent)、`manufacturer`、`item_name`、`bullet_point`、`product_description`、
  `country_of_origin`(国家码如 CN)、`item_type_keyword`、`supplier_declared_dg_hz_regulation`(=not_applicable)、
  `condition_type`(=new_new)、`supplier_declared_has_product_identifier_exemption`(=true 免 UPC)、
  `number_of_items`、`unit_count`(`type.value` 必须大写 **`Count`**)、`color`、`list_price`、
  `purchasable_offer`、`fulfillment_availability`(`fulfillment_channel_code=DEFAULT`=FBM)。
- **STICKER_DECAL** 额外:`theme`/`subject_character`/`special_feature`/`surface_recommendation`/`material`/
  `generic_keyword`/`model_name`/`model_number`/`part_number`/`required_product_compliance_certificate`(=Does Not Apply)/
  `batteries_required`(=false,**必填**,缺了父子全 INVALID)。
- **LABEL** 额外:`number_of_labels`、`model_number`、`part_number`、`batteries_required`(=false);
  **不要** `model_name`、`required_product_compliance_certificate`(LABEL 不适用)。
- 字段值结构:数组,每项带 `marketplace_id`;文本类再带 `language_tag:"en_US"`。
- 图片:`main_product_image_locator`(主图)+ `other_product_image_locator_1..8`(副图,共 ≤9 张);值 `{media_location,marketplace_id}`。
- 标题建议 ≤75 字符;`title_differentiation`("Item Highlight"/headline,≤125 字符)放标题装不下的关键词。
  **创建时就要带上(父体+子体;LABEL/STICKER_DECAL 都支持)**——非必填、漏了校验照样过,事后只能逐 SKU 补 PATCH。

### 变体族(设计款,无数量维度示例)
- `variation_theme=[{"name":"COLOR","marketplace_id":MP}]`(注意子字段是 **name**)。
- **父体**:`parentage_level=[{value:"parent"}]` + variation_theme;**无 offer/无图/无 exemption**;仍要 brand/item_name/bullets/description/country/item_type_keyword/dg_hz/number_of_labels/model_number/part_number/batteries_required。
- **子体**:`parentage_level=[{value:"child"}]` + `child_parent_sku_relationship=[{child_relationship_type:"variation",parent_sku:"<父SKU>",marketplace_id}]` + `color=[设计名]` + 完整 offer/图片/exemption。
- 逐个 `create`(父先,子后)。SP-API 带图校验/创建偶发 read timeout → 重试。
- **一键建族**:`scripts/create_family.py validate|create family.json` —— 一份 family.json
  (common_attributes + variation_theme + children[{sku,color,main_image,覆盖字段}] + 族级共用 other_images)
  自动展开成父+N子:父体自动剥 offer/图/color,子体自动加关系字段和图;父先子后,父体 GET 防覆盖,
  父体失败即停。格式详见脚本 docstring。

---

## 功能一b:编辑产品信息(PATCH,只改指定字段)
脚本 `scripts/edit_listing.py`。改已有 listing 的部分字段,**不动图片/其它**。product_type 自动探测。

```bash
python .claude/skills/amazon-listing/scripts/edit_listing.py <SKU> set item_name="新标题" style=Cartoon finish_type=Matte
python .claude/skills/amazon-listing/scripts/edit_listing.py <SKU> price 12.99 [--list 16.99]   # 改价
python .claude/skills/amazon-listing/scripts/edit_listing.py <SKU> stock 50                      # 改库存(0=草稿,>0=可售)
python .claude/skills/amazon-listing/scripts/edit_listing.py <SKU> patches patches.json          # 任意 patch,完全控制
```
- `set` 的文本类属性(item_name/style/pattern/finish_type/color/title_differentiation/...)自动带 `language_tag`;枚举/标识类(brand/model_number/...)只给 value。
- 加 `--dry` 只打印 patch 不提交;`--type` 在探测失败时手动指定;`--store` 选店。
- **这是修改现有 listing**:只改自己上的;改前先 `pull_product` 看一眼;红线仍是只在授权店操作。

## 功能二:拉取产品信息
脚本 `scripts/pull_product.py`。

```bash
python .claude/skills/amazon-listing/scripts/pull_product.py <SKU> [<SKU> ...]   # 摘要(状态/ASIN/标题/属性/图数/价)
python .claude/skills/amazon-listing/scripts/pull_product.py <SKU> --json        # 完整 attributes
python .claude/skills/amazon-listing/scripts/pull_product.py --asin <ASIN> [<ASIN>..]   # 按 ASIN 查 catalog 摘要(标题/品牌/style/color/型号/类目/主图/排名);加 --json 看完整
```
**注意**:新建 listing 的 ASIN 可能被 Amazon 后续**重新分配**(变体子体常见)。绑 A+ 前务必先 pull 确认**当前 ASIN**。

---

## 功能三:绑定 / 创建 A+
脚本 `scripts/aplus.py`(**走中间层 HTTP** `/amazon/aplus/*`,不再 import SDK)。

```bash
python .claude/skills/amazon-listing/scripts/aplus.py list                                 # 列出所有 A+(名/状态/badge/key)
python .claude/skills/amazon-listing/scripts/aplus.py bind <KEY> <ASIN> [<ASIN>...]   # 把 A+ 关联到 ASIN(默认提审;加 --no-submit 只关联不提审)
python .claude/skills/amazon-listing/scripts/aplus.py create "<名称>" <ASIN> <img1.png> [img2...]  # 用图建标准A+→关联→提审
```
- **绑定**用 `list` 拿 key(Premium A+ 也能绑,只是读不了内容)。绑前先 `pull_product` 确认 ASIN 没变。
- **创建**只支持**标准 A+**(STANDARD_*);Premium/高级 A+ 只能在 Seller Central 手工建(API 既读不了也建不了 premium 模块)。
- A+ 硬坑(**中间层 `app/platforms/amazon/aplus.py` 已内置**):图走 **S3 POST 表单上传**(非 PUT,SDK 的 upload_document 只建目标不传字节);`locale=en-US`(连字符);`contentType=EBC`;图缩到 970×600;单文档 ≤7 模块。

---

## 典型完整流程
1. 探针确认 product_type → 按上面字段拼 payload(图用 `file:`)。
2. `create_listing.py validate` 迭代到 VALID → `create`(父→子)。
3. `pull_product.py` 确认状态/当前 ASIN/字段齐全。
4. `aplus.py create`(或 `bind` 已有文档)把 A+ 贴到**当前** ASIN → 提审。
5. 确认无误后在 Seller Central 给子 SKU 加库存上架。

## 运营技巧 / Playbook(怎么把 listing 做好)
**做内容(标题/关键词/图片/变体/A+ 策略)前,先读 `references/tips.md`** —— 那里是踩出来的实战经验
(关键词分级埋词、标题≤75+Item Highlight、图片内容哈希、变体按设计、ASIN 会变要先确认、A+ 标准 vs Premium、合规禁词…),
并持续收录老板补充的技巧。接口怎么调看本文;**内容怎么做好看 tips.md**。

## 相关记忆/文档(更细的坑)
`docs/amazon_custom_listing_design.md`、`docs/amazon_sticker_decal_attributes.example.json`,
以及记忆:amazon-field-facts / amazon-aplus-api / amazon-cdn / amazon-test-safety / amazon-variant-pattern。
