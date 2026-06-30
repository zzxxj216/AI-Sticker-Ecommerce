"""Helium10 CSV 导入 + 与 autocomplete 挖词合流。

普通付费套餐没有 API,但 Cerebro(反查竞品 ASIN)/ Magnet(种子词扩展)都能导出 CSV。
本模块:
  1. 容错解析 Cerebro / Magnet 的 CSV 导出(列名大小写/别名不敏感),拿到带**真实搜索量**的词;
  2. 和 keyword_probe.probe_keywords() 的 autocomplete 结果合流——
     autocomplete 给广度+意图,Helium10 给搜索量+竞争度,交叉对齐后按真实量排序。

纯数据处理,不调 LLM。分级(主词/次词/长尾/后端)可上层用 AIRouter 做,或用本模块的启发式 tier。
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable

# 标准字段 → 可能的 CSV 表头别名(全部小写比较)。Helium10 不同版本/站点列名略有差异,
# 故做模糊匹配而非写死。命中第一个即采用。
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "keyword": ("keyword phrase", "keyword", "phrase"),
    "search_volume": ("search volume", "search vol", "volume"),
    "volume_trend": ("search volume trend", "search volume trend (%)", "volume trend"),
    "competing_products": ("competing products", "competing", "number of competing products"),
    "sponsored_asins": ("sponsored asins", "sponsored"),
    "cpr": ("cpr", "cpr 8-day giveaways", "cerebro product rank"),
    "title_density": ("title density", "title density exact"),
    "iq_score": ("cerebro iq score", "magnet iq score", "iq score"),
    "keyword_sales": ("keyword sales", "kw sales"),
    "organic_rank": ("organic rank", "amazon recommended rank", "position (organic)"),
    "sponsored_rank": ("sponsored rank", "position (sponsored)"),
    "word_count": ("word count", "words"),
}

# 数字字段(解析时去掉千分位逗号 / 百分号 / 区间符号)。
_NUMERIC_FIELDS = frozenset({
    "search_volume", "competing_products", "sponsored_asins", "cpr",
    "title_density", "iq_score", "keyword_sales", "organic_rank",
    "sponsored_rank", "word_count",
})


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _build_header_map(fieldnames: Iterable[str]) -> dict[str, str]:
    """CSV 实际表头 → 标准字段名。未识别的列丢进 raw(不在此 map)。"""
    norm_to_real = {_norm_header(f): f for f in fieldnames if f}
    out: dict[str, str] = {}
    for std, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in norm_to_real:
                out[norm_to_real[alias]] = std
                break
    return out


def _to_number(val: str) -> float | int | None:
    s = (val or "").strip()
    if not s or s in {"-", "–", "—", "n/a", "na", ">", "<"}:
        return None
    # ">1000" / "< 50" / "1,234" / "12.5%" / "10 - 20" → 取第一个数字
    s = s.replace(",", "").replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    num = float(m.group())
    return int(num) if num.is_integer() else num


def parse_export(
    csv_text: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """解析一份 Helium10 CSV 文本。

    source: 'cerebro' | 'magnet' | None(自动判定:有 organic/sponsored rank 列→cerebro)。
    返回 {source, count, columns_recognized, rows:[{keyword, search_volume, ...}]}。
    每行未识别的列保留在该行的 'raw' 子 dict 里,不丢信息。
    """
    # 去 BOM,Excel 导出常带。
    text = csv_text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {"source": source or "unknown", "count": 0,
                "columns_recognized": [], "rows": []}

    header_map = _build_header_map(reader.fieldnames)
    std_present = set(header_map.values())

    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        rec: dict[str, Any] = {"raw": {}}
        for real_col, value in raw_row.items():
            std = header_map.get(real_col)
            if std is None:
                if real_col:
                    rec["raw"][real_col] = value
                continue
            if std in _NUMERIC_FIELDS:
                rec[std] = _to_number(value)
            else:
                rec[std] = (value or "").strip()
        kw = (rec.get("keyword") or "").strip()
        if not kw:
            continue
        rec["keyword"] = kw
        rows.append(rec)

    if source is None:
        source = "cerebro" if {"organic_rank", "sponsored_rank"} & std_present else "magnet"

    return {
        "source": source,
        "count": len(rows),
        "columns_recognized": sorted(std_present),
        "rows": rows,
    }


def parse_export_file(path: str, *, source: str | None = None) -> dict[str, Any]:
    """从磁盘读 CSV(utf-8-sig 兜底 latin-1)。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as f:
            text = f.read()
    return parse_export(text, source=source)


def merge_with_probe(
    probe_result: dict[str, Any],
    *helium10_results: dict[str, Any],
) -> dict[str, Any]:
    """合流 autocomplete 挖词 + 一份或多份 Helium10 解析结果。

    - autocomplete 词:标记 in_autocomplete=True,有匹配则带上 search_volume;
    - Helium10 词(autocomplete 没挖到的)也并入,带搜索量;
    - 按 search_volume 降序(无量的排最后,保留 autocomplete 顺序作次序)。
    返回 {total, with_volume, keywords:[{keyword, search_volume, competing_products,
           iq_score, in_autocomplete, in_helium10, ...}]}。
    """
    # Helium10:keyword.lower() → 最优记录(取搜索量更大的那条,跨多份去重)。
    h10: dict[str, dict[str, Any]] = {}
    for res in helium10_results:
        for row in res.get("rows") or []:
            key = row["keyword"].lower()
            prev = h10.get(key)
            if prev is None or (row.get("search_volume") or 0) > (prev.get("search_volume") or 0):
                h10[key] = row

    merged: dict[str, dict[str, Any]] = {}

    def _ensure(kw: str) -> dict[str, Any]:
        key = kw.lower()
        if key not in merged:
            merged[key] = {
                "keyword": kw, "search_volume": None, "competing_products": None,
                "iq_score": None, "in_autocomplete": False, "in_helium10": False,
            }
        return merged[key]

    # 先铺 autocomplete(保留其挖掘顺序)。
    auto_order: dict[str, int] = {}
    for i, kw in enumerate(probe_result.get("keywords") or []):
        rec = _ensure(kw)
        rec["in_autocomplete"] = True
        auto_order[kw.lower()] = i

    # 叠加 Helium10 量化字段。
    for key, row in h10.items():
        rec = _ensure(row["keyword"])
        rec["in_helium10"] = True
        for f in ("search_volume", "competing_products", "iq_score",
                  "sponsored_asins", "cpr", "title_density", "keyword_sales"):
            if row.get(f) is not None:
                rec[f] = row[f]

    def _sort_key(rec: dict[str, Any]) -> tuple:
        vol = rec.get("search_volume")
        has_vol = vol is not None
        # 有量的按量降序;无量的按 autocomplete 原序。
        return (
            0 if has_vol else 1,
            -(vol or 0),
            auto_order.get(rec["keyword"].lower(), 1_000_000),
        )

    keywords = sorted(merged.values(), key=_sort_key)
    with_volume = sum(1 for r in keywords if r.get("search_volume") is not None)
    return {"total": len(keywords), "with_volume": with_volume, "keywords": keywords}


def tier_keywords(
    merged: dict[str, Any],
    *,
    title_n: int = 5,
    bullets_n: int = 15,
) -> dict[str, list[str]]:
    """启发式分级(无 LLM):按真实搜索量切桶,供下游文案直接用。

    title  : 量最大的 title_n 个(进标题)
    bullets: 接下来的 bullets_n 个(进五点)
    long_tail: ≥3 词的剩余词(进描述)
    backend: 其余(后端 search_terms,250 byte 由文案层拼)
    """
    kws = merged.get("keywords") or []
    title = [k["keyword"] for k in kws[:title_n]]
    bullets = [k["keyword"] for k in kws[title_n:title_n + bullets_n]]
    rest = kws[title_n + bullets_n:]
    long_tail = [k["keyword"] for k in rest if len(k["keyword"].split()) >= 3]
    backend = [k["keyword"] for k in rest if len(k["keyword"].split()) < 3]
    return {"title": title, "bullets": bullets,
            "long_tail": long_tail, "backend": backend}
