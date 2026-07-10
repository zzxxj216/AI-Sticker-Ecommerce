# -*- coding: utf-8 -*-
"""同步草稿体检:draft.json 的 amazon 块还缺什么 → 「问人清单」+「Codex 可做清单」。

用法:python gap_check.py tk_drafts/<id>/draft.json

原则:**事实类**(数量/尺寸/价格/店铺/SKU/类型)缺了必须问人,Codex 不许编;
且已填的事实必须在 amazon.notes 里有**来源留痕**(如 "number_of_items=50 ← 用户确认"),
没有留痕 = 视同没问过人,照样列入问人清单(防 agent 自己猜完自己过)。

退出码:0=完整可转 payload;1=只剩文案 todo;2=还有必须问人的事实;3=文件/JSON 坏。
"""
import json
import sys

# 已填也必须在 notes 里有来源留痕的事实字段
FACT_FIELDS = ["store", "product_type", "sku", "number_of_items", "unit_count", "price_usd"]


def main(path: str):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        print(f"[错误] 文件不存在:{path}"); sys.exit(3)
    except json.JSONDecodeError as e:
        print(f"[错误] draft.json 不是合法 JSON:{e}"); sys.exit(3)
    a = d.get("amazon") or {}
    notes = " ".join(str(n) for n in (a.get("notes") or []))
    ask, todo, ok = [], [], []

    # —— 事实类:缺了问人;填了但 notes 无留痕,也问人 ——
    def fact(key, question):
        v = a.get(key)
        if v in (None, "", []):
            ask.append(question)
        elif key not in notes:
            ask.append(f"{key}={v} 已填但 notes 里没有来源留痕 —— 是人确认过的吗?"
                       f"(确认过就补一条 notes:\"{key}={v} ← 用户确认\";是猜的就去问人)")

    fact("store", "同步到哪个店铺/站点?(--store 写法,如 main / byane@UK;跑 amazon-listing 的 stores.py 看矩阵)")
    fact("product_type", f"product_type 是什么?(TK 类目链:{' > '.join(d.get('category_chain') or []) or '无'};"
                         "贴纸=STICKER_DECAL 标签=LABEL,拿不准用探针 /product-types?keywords=)")
    if not a.get("keyword_csv"):
        ask.append("关键词表在哪?(老板规矩:创建前必须补关键词表——Helium10 Cerebro CSV,存**绝对路径**;"
                   "拿到后跑 amazon-listing 的 keywords.py 分级)")
    fact("number_of_items", f"每包数量(number_of_items)是多少?(TK 标题可参考:{d.get('title_tk','')[:60]}…——但必须人确认)")
    fact("unit_count", "unit_count 是多少?(通常=每包数量)")
    prices = [f"{s.get('price')} (raw {s.get('price_raw')!r})" for s in d.get("skus") or []]
    fact("price_usd", f"Amazon 定价?(TK 价参考:{prices} —— raw 无小数点的值有 100 倍歧义,人裁决;沿用还是重定,人决定)")
    fact("sku", "Amazon 新 SKU 用什么?(全新唯一,建议 INK- 前缀;变体族要父+子一套)")
    has_var = any(s.get("sales_attributes") for s in d.get("skus") or [])
    if has_var and not a.get("variants"):
        ask.append('TK 有变体(sales_attributes)→ 需人确认 COLOR 映射,填 amazon.variants:'
                   '[{"sku":..,"color":..,"main_image":"file:绝对路径"}](变体图已在 sales_attributes[].local)')

    # —— 文案类:Codex 可代写、人审 ——
    t = a.get("title")
    if not t:
        todo.append("写 Amazon 标题(≤75 字符,英文,埋 TITLE 级关键词;带 Item Highlight 时 ≤75 是硬规则)")
    elif len(str(t)) > 75:
        ask.append(f"标题 {len(str(t))} 字 >75(带 Item Highlight 是硬规则),要改")
    h = a.get("title_differentiation")
    if not h:
        todo.append("写 Item Highlight(≤125 字符;创建时必带,漏了只能事后逐 SKU 补)")
    elif len(str(h)) > 125:
        ask.append(f"Item Highlight {len(str(h))} 字 >125,要改")
    if len(a.get("bullets") or []) != 5:
        todo.append(f"写五点(现 {len(a.get('bullets') or [])} 条,要 5 条,埋 BULLET 级关键词)")
    if not a.get("description"):
        todo.append("写 product_description(基于 draft 的 description_text 重写成 Amazon 风格英文纯文本)")
    bk = a.get("backend_keywords")
    if not bk:
        todo.append("拼后端词(keywords.py 的 BACKEND 串,≤249 字节,别与标题重复)")
    elif not isinstance(bk, str):
        ask.append("backend_keywords 应是字符串(空格分隔),现在不是")
    elif len(bk.encode("utf-8")) > 249:
        ask.append(f"后端词 {len(bk.encode('utf-8'))} 字节 >249(超了整条不索引),要裁")

    # —— 图片 ——
    imgs = d.get("images") or []
    if len(imgs) < 7:
        ask.append(f"TK 只有 {len(imgs)} 张图(<7):要补图吗?(1主+6副起步)")
    if not any(im.get("local") for im in imgs):
        todo.append("图片未下载:重跑 tk_pull.py <id> --download(放心,重拉会保留 amazon 块,不丢答案)")

    # ⚠️ TK 图 ≠ Amazon 图:TK 常见营销风(场景图/贴大字/促销角标/拼图),Amazon 会拒或抑制。
    # 人工逐张过图 + notes 留痕 "images_ok ← 用户已过图" 才放行;Pillow 可用时先自动初筛。
    auto_warns = []
    try:
        from PIL import Image
        for i, im in enumerate(imgs, 1):
            loc = im.get("local")
            if not loc:
                continue
            try:
                with Image.open(loc) as pic:
                    w, h = pic.size
                    if max(w, h) < 1000:
                        auto_warns.append(f"图{i} 仅 {w}x{h}(<1000px,Amazon 无法缩放/可能抑制)")
                    if i == 1:                       # 主图:四角采样测白底
                        rgb = pic.convert("RGB")
                        corners = [rgb.getpixel(p) for p in
                                   [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)]]
                        if any(min(c) < 235 for c in corners):
                            auto_warns.append(f"主图四角非纯白(采样 {corners[0]}…)——Amazon 主图必须纯白底(255,255,255)")
            except Exception:
                pass
    except ImportError:
        pass                                          # 没装 Pillow 就只给人工清单
    checklist = ("逐张人工过图(TK 图直接搬常不合规):主图=纯白底/无文字水印logo/无场景道具/产品占85%;"
                 "副图=无水印无网址无促销角标;全部≥1000px(建议1600px)。"
                 "不合规的图要重做/替换后再建。确认没问题后 notes 留痕:\"images_ok ← 用户已过图\"")
    if "images_ok" not in notes:
        ask.append(checklist + ((" 【自动初筛发现:" + ";".join(auto_warns) + "】") if auto_warns else ""))
    elif auto_warns:
        ask.append("notes 已标 images_ok,但自动初筛仍有疑点:" + ";".join(auto_warns) + " —— 请再确认")
    else:
        ok.append("图片已人工过审(notes: images_ok)")

    print(f"=== gap_check: {path} ===")
    print(f"TK 标题: {d.get('title_tk','')[:70]}\n")
    if ask:
        print(f"❓ 必须问人({len(ask)}):")
        for i, q in enumerate(ask, 1):
            print(f"  {i}. {q}")
    if todo:
        print(f"\n🤖 Codex 可代做、人审({len(todo)}):")
        for i, q in enumerate(todo, 1):
            print(f"  {i}. {q}")
    if ok:
        print(f"\n✅ 已就绪:")
        for q in ok:
            print(f"  - {q}")
    if not ask and not todo:
        print("✅ 草稿完整:python to_payload.py <draft> [--family] → amazon-listing validate。")
    sys.exit(2 if ask else (1 if todo else 0))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(3)
    main(sys.argv[1])
