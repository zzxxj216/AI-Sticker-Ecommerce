---
name: tk2amazon
description: 把 TikTok Shop 上已有的产品**同步/搬运到 Amazon**(拉 TK 产品 → 人工补足 Amazon 的严格要求 → 建 Amazon listing)。当用户说"把 TK/TikTok 的产品同步到亚马逊""TK 上有这个品,帮我上到 Amazon""从 TikTok 搬品"时使用。不要用于:纯 Amazon 上品(用 amazon-listing)、TK 侧上品/改品(本 skill 对 TK 只读)。
---

# TK → Amazon 产品同步(人机协作)

> TK 上已经在卖的产品,信息是**不完整的**(Amazon 要求严得多:product_type 必填集、
> 关键词表、标题≤75、Item Highlight、5 条五点、后端词、白底主图…)。
> 所以这不是全自动搬运,而是**人机协作**:脚本拉事实 → `gap_check` 列出缺口 →
> **事实类问人(答案留痕)、文案类你(agent)代写人审** → `to_payload` → amazon-listing 建品。

## 前置
1. 中间层 multi-channel-api 在跑;**首次使用**:`cp .env.example .env` 改 `AMAZON_MCA_URL`
   (或依赖仓根 .env / 环境变量,三处任一即可);
2. **amazon-listing skill 已安装且与本 skill 平级**(`../amazon-listing/scripts/`,
   Amazon 侧全靠它:stores/keywords/create_listing/create_family/lint/family_status);
3. TK 店铺已在中间层注册(`--shop <名>`,不传走默认店;店名打错会被拒)。

## 工作目录约定(重要,别混)
**整个流程固定在一个工作目录跑**(建议专门建一个,比如 `D:\tk2amazon-work\`):
`tk_drafts/<product_id>/` 会建在这里,里面是 draft.json、imgs/、payload.json/family.json。
草稿里的图片路径、to_payload 产出的 `file:` 全是**绝对路径**,所以后续调 amazon-listing
的脚本从**任何目录**跑都不断链;但你自己找文件方便起见,别换来换去。

## 🔴 红线
- **TK 侧只读**:代码级闸门(`_tk.api` 只放行 GET 和 POST /products/search),
  中间层的 TK 创建/修改/删除/上下架端点本 skill 永远不碰。
- **人点头才 create**:validate 的结果必须**贴给用户看**,用户明确同意后才允许跑
  create(单品/变体族同规)。没有用户的明确"可以建",停在 validate。
- **事实不许编、答案要留痕**:数量/尺寸/价格/店铺/SKU/类型这类事实必须问用户;
  每个确认过的事实在 `amazon.notes` 里记一条(格式 `"number_of_items=50 ← 用户确认"`)。
  gap_check 会查留痕:**填了值但没留痕 = 视同没问过,照样拦下**(防"自己猜完自己过")。
- Amazon 侧沿用 amazon-listing 红线:新唯一 SKU + **父与全部子体** GET 防覆盖、
  qty=0 草稿(to_payload 写死)、brand 默认 Inkelligent、先 VALIDATION_PREVIEW。

## 工作流(7 步)

```bash
# 1. 找品(--status 真实枚举:ACTIVATE/SELLER_DEACTIVATED/DRAFT/...)
python <skill>/scripts/tk_list.py [--shop main] [--n 50] [--status ACTIVATE]

# 2. 拉草稿:**一开始就带 --download**(图片下到本地,绝对路径)
#    重跑安全:draft 已存在时 amazon 块和已下图自动保留(彻底重来才用 --force)
python <skill>/scripts/tk_pull.py <product_id> --download

# 3. 体检缺口:❓必须问人(exit 2)/ 🤖你可代做(exit 1)/ 完整(exit 0)
python <skill>/scripts/gap_check.py tk_drafts/<id>/draft.json
```
4. **逐条问用户**"必须问人"清单,由**你(agent)直接编辑 draft.json** 把答案写进
   `amazon` 块(字段类型/示例见 tk_pull.py 里 amazon 块的行内注释:store 是
   `"main"`/`"byane@UK"` 字符串、bullets 是 5 条英文字符串数组、price_usd 是数字、
   keyword_csv 存**绝对路径**),并给每个事实补 `notes` 留痕。
   关键词表拿到后跑 `python ../amazon-listing/scripts/keywords.py <csv> --exclude 竞品词`。
5. **你代写文案**(英文):标题≤75、Item Highlight≤125、5 条五点、描述(基于
   `description_text` 重写,别照抄 TK 的 HTML 腔)、后端词≤249 字节 —— 全部埋词,
   写回 `amazon` 块,**整块贴给用户过目**。
6. 机械转换 + 校验(不用手拼 30 个属性,脚本来):
   ```bash
   python <skill>/scripts/to_payload.py tk_drafts/<id>/draft.json            # 单品 → payload.json
   python <skill>/scripts/to_payload.py tk_drafts/<id>/draft.json --family   # 变体族 → family.json
   python ../amazon-listing/scripts/create_listing.py validate <payload> --store <店>
   ```
   to_payload 自带守门:缺关键字段/超长/五点≠5 条直接拒;图片用 draft 里的绝对路径
   自动转 `file:`;qty 写死 0(草稿)。**validate 全 VALID 贴给用户 → 用户点头 → 才 create**。
   变体族:变体映射(amazon.variants)是"必须问人"项;create 前脚本会对父+全部子 SKU 防覆盖。
7. 收尾:`lint_listing.py <SKU...>` 体检 + `family_status.py` 记 ASIN → 向用户汇报
   (SKU/ASIN/状态,以及"qty=0 草稿,确认后再放库存")。

## draft.json 结构
- TK 事实(脚本填):`title_tk` / `description_html`+`description_text`(已洗实体)/
  `images[]`(url + local **绝对路径**)/ `skus[]`(seller_sku、price + **price_raw 原始值**、
  qty_tk、sales_attributes 含变体名/变体图 image_url+local)/ `package` / `category_chain`。
- `amazon` 块(第 4-5 步人机协作填):见 tk_pull.py 行内注释;`variants` 供变体族;
  `notes` 是事实留痕数组。

## TK 数据坑(已在脚本里处理,改脚本时别丢)
- **价格双格式**:`'13.98'`(小数)与 `'1699'`(整数分)并存;`_tk.amount` 有点透传、
  无点除 100;**`'17'` 这类无点整值有 100 倍歧义** → draft 同时存 `price_raw`,
  gap_check 展示两个值让人裁决。
- 描述是 **HTML** → `description_text` 已剥标签 + `html.unescape` 全实体解码。
- 字段新旧双版:`main_images|images`、`urls|url_list`、`inventory|stock_infos`、
  `quantity|available_stock`、`id|sku_id` —— 都做了 fallback。
- 图片扩展名按响应 **Content-Type** 定(PNG/WebP 别存成 .jpg);单图下载失败只跳过不中断,
  draft 先落盘。
- `--status` 过滤:中间层曾把参数包错位置导致静默失效(已修:顶层 `status`);
  tk_list 仍带客户端兜底过滤。
- 单 SKU 产品 `sales_attributes` 为空;多变体时 `name/value_name`,变体图在 `sku_img`。
- **TK 图 ≠ Amazon 图(高频雷区)**:TK 主副图常是营销风——场景图、贴大字标语、促销角标、
  九宫格拼图,Amazon 会拒收或抑制 listing。规则:主图**纯白底**(255,255,255)、无文字/水印/
  logo/道具、产品占比 ~85%;副图无水印无网址;全部 ≥1000px(建议 1600px)。
  gap_check 强制"人工逐张过图 + notes 留痕 images_ok"才放行,装了 Pillow 还会自动初筛
  (尺寸不足/主图四角非白)。不合规的图:让用户提供重做图,或明确同意后用原图承担风险。

## 与其它 skill 的分工
- **amazon-listing**(平级目录 `../amazon-listing/`):Amazon 侧一切读写(第 4/6/7 步在调它);
- **ziniao-premium-aplus**:同步完想做高级 A+ 时再用;
- 本 skill 只负责「TK 事实搬运 + 缺口管理 + 机械转换」这一段。
