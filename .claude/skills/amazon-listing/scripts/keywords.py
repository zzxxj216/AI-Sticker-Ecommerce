# -*- coding: utf-8 -*-
"""关键词表处理:Helium10 Cerebro CSV → 过滤竞品词 → 按真实搜索量分级埋词清单。

老板规矩:**创建 listing 前必须先补关键词表,不能凭空写文案**(tips.md)。

用法:
  python keywords.py cerebro.csv                              # 输出分级清单
  python keywords.py cerebro.csv --exclude teddy,brand2      # 过滤含这些词的关键词(竞品/品牌词)
  python keywords.py cerebro.csv --title "已定标题"           # 后端词自动去掉标题里已有的词
  python keywords.py cerebro.csv --min-sv 200                # 忽略搜索量低于此的词(默认 100)

输出三级:
  TITLE 级(搜索量最高,进标题) / BULLET 级(次高,进五点/Item Highlight)
  / BACKEND 串(长尾,空格分隔、去与标题重复、≤249 字节,直接贴 generic_keyword)
"""
import csv, sys

KW_COL, SV_COL = "关键词词组", "搜索量"   # Helium10 中文导出列名


def load(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for d in csv.DictReader(fh):
            kw = (d.get(KW_COL) or "").strip().lower()
            try:
                sv = int(float(d.get(SV_COL) or 0))
            except ValueError:
                sv = 0
            if kw and sv > 0:
                rows.append((sv, kw))
    rows.sort(reverse=True)
    return rows


def backend_string(rows, used_words, budget=249):
    """长尾词拼后端串:逐词去重(与标题/已用词、串内),空格分隔,字节数≤249。"""
    out, used = [], set(used_words)
    for _, kw in rows:
        for w in kw.split():
            if w in used or len(w) < 3:
                continue
            cand = (" ".join(out + [w])).encode("utf-8")
            if len(cand) > budget:
                return " ".join(out)
            out.append(w); used.add(w)
    return " ".join(out)


def main(argv):
    path, excl, title, min_sv = argv[0], [], "", 100
    if "--exclude" in argv:
        excl = [w.strip().lower() for w in argv[argv.index("--exclude") + 1].split(",")]
    if "--title" in argv:
        title = argv[argv.index("--title") + 1].lower()
    if "--min-sv" in argv:
        min_sv = int(argv[argv.index("--min-sv") + 1])

    rows = [(sv, kw) for sv, kw in load(path)
            if sv >= min_sv and not any(x in kw for x in excl)]
    if not rows:
        print("没有可用关键词(检查列名/过滤条件)"); return
    n = len(rows)
    t_end, b_end = max(1, n // 5), max(2, n // 2)   # 前20%=标题级,20-50%=五点级,其余=长尾

    print(f"共 {n} 个有效词(过滤:{','.join(excl) or '无'};min_sv={min_sv})\n")
    print("== TITLE 级(进标题)==")
    for sv, kw in rows[:t_end]:
        print(f"  {sv:>8}  {kw}")
    print("\n== BULLET 级(进五点/Item Highlight)==")
    for sv, kw in rows[t_end:b_end][:15]:
        print(f"  {sv:>8}  {kw}")
    used = set(title.split()) if title else set()
    for _, kw in rows[:t_end]:
        used.update(kw.split())
    bs = backend_string(rows[b_end:], used)
    print(f"\n== BACKEND 串(贴 generic_keyword,{len(bs.encode('utf-8'))} 字节/249)==")
    print(f"  {bs}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1:])
