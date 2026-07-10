# Amazon Listing 运营技巧 / Playbook

> 接口怎么调见 SKILL.md;**这里是"怎么做好"的经验**。持续补充。
> 标 `[实证]` = 我们项目里踩通/验证过的;标 `[用户]` = 老板补充的技巧。

## 关键词
- `[实证]` 用**真实搜索量**关键词(Helium10 Cerebro CSV),别凭空想。按搜索量排序后**分级**:
  - 最高量 → 进**标题**;次高 → 分散到**五点/Item Highlight**;长尾 → **后端 search_terms**。
- `[实证]` **过滤竞品/品牌词**(teddy、bright star 等)再用,别把别人品牌写进自己 listing(A+ 也会因此被拒)。
- `[实证]` 后端 search_terms 可放**西语词**(mochilas、etiquetas personalizadas)抢西语流量,且不与标题词重复。

## 标题 & Item Highlight
- `[实证]` 标题**简洁 ≤75 字符**;变体族**统一模板,只换主题词**(`...Waterproof {主题} Stickers`)。
- `[实证]` **≤75 不只是建议**:带 `title_differentiation`(Item Highlight)时,标题 >75 直接校验 ERROR
  ("Provide an Item Name that is 75 characters or less to use Item Highlights",2026-07-09 抓到)。
- `[实证]` 标题装不下的关键词 → 放 **Item Highlight**(字段名 `title_differentiation`,后台叫 headline/Item Highlight;**LABEL 和 STICKER_DECAL 都支持**;**≤125 字符**,标题被截断时才显示,写利益短语非整句)。
- `[实证]` **创建 listing 时就要带上 `title_differentiation`**(父体+子体都要)——它不是必填项、漏了校验照样 VALID,事后只能逐 SKU 补 PATCH(2026-07 建 3 个族 12 SKU 时漏配,补了 12 次)。**建 payload 时对照模板逐字段过,别只补必填。**
- `[实证]` `bullet_point` 在后台显示名是 **"Key Product Features"**(≠Item Highlight,别混)。
- `[用户]` **查 Amazon 字段别只按属性键名搜 schema**——界面名和 API 键名经常对不上
  (界面 "Item Highlight" ↔ 键名 `title_differentiation`;按 "highlight" 搜键名会错误得出
  "字段不存在")。正确姿势:**全文搜 schema JSON 的 title/description**(展示名都在 title 里)。
  2026-07 万圣节 listing 排查实证:曾因此弯路误判字段不存在。

## 图片
- `[实证]` 传 COS 一律用**内容哈希 key**(`<sha1>.png`)——固定 key 改图后 Amazon 按 URL 缓存旧图、不重抓。
- `[实证]` 主图要**合规**(白底、像真实产品图)否则会被抑制(suppressed);商品图最多 **9 张**(1 主 + 8 副)。
- `[实证]` 同一变体的多张图若内容重复,Amazon 会按内容去重(显示重复)——确保每张内容不同。

## 变体
- `[实证]` 贴纸/标签系列按**设计款**做变体(`variation_theme=COLOR`,每个设计=一个 color 值);也可按**数量**(size_name)或二维(数量×设计)。
- `[实证]` 父体不可售容器(无 offer/无图);买家在商详页切设计款。
- `[实证]` 新建 listing 的 **ASIN 可能被 Amazon 重新分配**(变体子体常见)→ 绑 A+/做关联前**先 pull_product 确认当前 ASIN**(否则 A+ 贴到旧 ASIN,报 **AC-1022**)。

## 产品类型 & 合规
- `[实证]` 上品前**探针确认 product_type**(姓名标签=`LABEL`,贴纸=`STICKER_DECAL`,字段必填集不同)。
- `[实证]` **品牌必须用已备案的 `Inkelligent`** → 自动 GTIN 豁免;用子品牌会丢豁免、报要 UPC。
- `[实证]` LABEL 必填多 `number_of_labels`/`batteries_required`,不要 `model_name`/compliance_certificate;`unit_count.type.value` 要大写 `Count`。

## A+
- `[实证]` **标准 A+ 走 API**(单文档 ≤7 模块;设计好的整页图用 970×600 纯图模块堆叠,别加多余 Amazon 文字)。
- `[实证]` **高级/Premium A+ API 建不了也读不了**(premium 模块 Unsupported)→ 只能在 Seller Central 手工建;但**已建好的 Premium 文档可用 API 绑定到 ASIN**。
- `[实证]` A+ 内容里**禁**:价格/运费/保证/#1/最佳、竞品名、注册符号 ™©®、健康功效。

## 流程 / 安全
- `[实证]` 真建前先 **VALIDATION_PREVIEW 干跑**迭代到 VALID;但干跑比真提交宽松,真建后也要看 issues。
- `[实证]` 创建默认 **库存 0 草稿**(不可售)→ 审核过 + 核对无误 → 再 `stock >0` 上架。
- `[实证]` 只在授权店操作(`--store`,默认 main);写操作前 `pull_product` 看一眼现状。

---
## 业界最佳实践(网搜 2025/2026)`[网搜]`

### 标题
- `[网搜]` ≤200 字符;**同一个词不能出现超过 2 次**(介词/冠词/连词除外)——2025-01 新规,违反会被压制。
- `[网搜]` **前 ~120 字符最关键**(移动端/搜索结果截断)——最高量关键词 + 核心卖点放最前。自然可读,别堆词。

### 五点(后台名 Key Product Features)
- `[网搜]` 每条回答"so what" —— 讲**结果/利益 + 可信证据**,不只罗列参数。
- `[网搜]` **第 1 条**框架最重要(常被优先看 / 移动端先显),值得反复 A/B。

### 后端 search_terms
- `[网搜]` 美国站 **249 字节**(是字节不是字符;特殊字符/外文 2-4 字节;**超了整条不被索引**)。
- `[网搜]` **不要重复**标题/五点/描述里已有的词(浪费空间,无额外排名)。
- `[网搜]` **不用逗号/分号**,单空格分隔;放同义词、拼写变体、人群词、**西语翻译**。

### 图片
- `[网搜]` 目标 **6 图 + 1 短视频**:可放大主图(白底)、带比例的场景图、卖点信息图、尺寸/兼容图。
- `[网搜]` 不只放产品本身,要有**使用场景 + 谁在用**(lifestyle)。
- `[网搜]` "前三个交互(标题/主图/五点)"决定成败 —— 相关性/清晰/转化要在这三处一眼到位。

### A+ 模块编排(转化导向)
- `[网搜]` **顺序**:顶部(模块1-2)情感/场景/视频「解决什么问题」→ 中部(3-4)卖点/规格/**对比表**「为何选这款」→ 底部(5-7)Q&A/交叉销售/品牌故事「为何选这品牌」。
- `[网搜]` **对比表**模块很强(多尺寸/款式/套装时帮买家选);Premium 对比表可加 Add-to-Cart/价格/评分。
- `[网搜]` 视频 30-60s,前几秒要有画面(很多人无声看)。
- `[网搜]` 效果数据:Basic A+ 销售 +~8%,**Premium A+ 最高 +~20%**(所以你坚持做高级 A+ 是对的)。

### 2026 趋势
- `[网搜]` **Rufus / AI 搜索**:内容要**语义清晰、自然语言**,覆盖买家真实问法(不只堆精确关键词)。
- `[网搜]` listing 优化是**持续过程**:按表现数据反复测试/迭代,不是一次性。

## 老板补充的技巧

- `[用户]` **创建 listing 前必须先补充"关键词表",不能凭空写文案。**
  - **来源**:Helium10 Cerebro 等导出的关键词表 CSV(带 `Search Volume` 搜索量列),如 amazon4 里的 `US_AMAZON_cerebro_*.csv`。
  - **用法**:① 过滤竞品/品牌词(teddy、bright star…)② 按搜索量降序 ③ **分级**:最高量→标题;次高→五点/Item Highlight;长尾→后端 search_terms ④ 自然埋词,不堆砌。
  - **所以 create_listing 的标准前置 = 先拿到关键词表**;title/bullets/highlight/search_terms 都从它来,不从脑补来。
  - 没有关键词表就别急着上品 —— 先去 Helium10 跑词、导表。
