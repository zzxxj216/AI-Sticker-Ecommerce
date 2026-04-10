"""Generate a self-contained HTML calendar page for Shopify.

Calendar grid and range chips are pre-rendered as static HTML.
Region filtering uses CSS radio-button hack (no JS).
Event detail popup uses minimal inline JS.
"""

from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone, timedelta
from typing import Any
from html import escape

from src.core.logger import get_logger
from src.services.ops.db import OpsDatabase

logger = get_logger("calendar_page_gen")

_CN_TZ = timezone(timedelta(hours=8))

CAT_COLORS = {
    "holiday": ("#f59e0b", "#fef3c7", "#92400e"),
    "event":   ("#3b82f6", "#dbeafe", "#1e40af"),
    "cultural": ("#8b5cf6", "#ede9fe", "#5b21b6"),
    "sports":  ("#10b981", "#d1fae5", "#065f46"),
}
CAT_LABELS = {"holiday": "Holiday", "event": "Event", "cultural": "Cultural", "sports": "Sports"}
REGION_LABELS = {"us": "US", "ca": "Canada", "eu": "Europe"}
REGION_COLORS = {"us": "#3b82f6", "ca": "#ef4444", "eu": "#10b981"}
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def generate_calendar_html(
    db: OpsDatabase | None = None,
    year: int | None = None,
    month: int | None = None,
    regions: list[str] | None = None,
    title: str = "Upcoming Holidays & Events — Sticker Collection Calendar",
) -> str:
    db = db or OpsDatabase()
    now = datetime.now(_CN_TZ)
    year = year or now.year
    month = month or now.month

    events_raw = db.list_planning_events(year=year, month=month)
    if regions:
        events_raw = [e for e in events_raw if e.get("region") in regions]

    events = _clean_events(events_raw)
    return _render_page(title, year, month, events)


# ── helpers ──────────────────────────────────────────────

def _clean_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for e in raw:
        cleaned.append({
            "title": e.get("title", ""),
            "category": e.get("category", ""),
            "region": e.get("region", ""),
            "start_date": e.get("start_date", ""),
            "end_date": e.get("end_date", ""),
            "short_description": e.get("short_description", ""),
            "source": e.get("source", ""),
        })
    return cleaned


def _is_range(e: dict) -> bool:
    return bool(e.get("end_date") and e["end_date"] != e["start_date"])


def _days_between(start: str, end: str) -> int:
    d1 = datetime.strptime(start, "%Y-%m-%d")
    d2 = datetime.strptime(end, "%Y-%m-%d")
    return (d2 - d1).days + 1


def _merge_by_title(events: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for e in events:
        t = e["title"]
        if t in merged:
            if e["region"] not in merged[t]["regions"]:
                merged[t]["regions"].append(e["region"])
            if not merged[t].get("source") and e.get("source"):
                merged[t]["source"] = e["source"]
        else:
            merged[t] = {**e, "regions": [e["region"]]}
    return list(merged.values())


def _hex_to_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}"


def _region_badge_html(region: str) -> str:
    color = REGION_COLORS.get(region, "#6b7280")
    label = REGION_LABELS.get(region, region.upper())
    return (
        f'<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;'
        f'background:rgba({_hex_to_rgb(color)},.1);color:{color};text-transform:uppercase;">'
        f'{label}</span>'
    )


def _cat_style(cat: str) -> str:
    border, bg, fg = CAT_COLORS.get(cat, ("#6b7280", "#f3f4f6", "#374151"))
    return f"background:{bg};color:{fg};border-left:3px solid {border};"


def _evt_data_attr(ev: dict) -> str:
    """Encode event info as a data attribute for JS popup."""
    info = {
        "title": ev.get("title", ""),
        "category": CAT_LABELS.get(ev.get("category", ""), ev.get("category", "")),
        "cat_raw": ev.get("category", ""),
        "regions": ev.get("regions", [ev.get("region", "")]),
        "start_date": ev.get("start_date", ""),
        "end_date": ev.get("end_date", ""),
        "desc": ev.get("short_description", ""),
        "source": ev.get("source", ""),
    }
    return escape(json.dumps(info, ensure_ascii=False), quote=True)


# ── main render ──────────────────────────────────────────

def _render_page(title: str, year: int, month: int, events: list[dict]) -> str:
    month_name = MONTH_NAMES[month - 1]

    singles = [e for e in events if not _is_range(e)]
    all_regions = sorted(set(e["region"] for e in events))

    parts = [
        f'<div id="ink-cal">',
        f'<style>{_build_css()}</style>',
        _build_header(title, month_name, year),
        _build_region_tabs(all_regions, events),
        _build_legend(),
        _build_calendar_grid(year, month, singles, "all"),
    ]
    for r in all_regions:
        parts.append(_build_calendar_grid(year, month, [e for e in singles if e["region"] == r], r))

    parts.append(_build_range_section(
        _merge_by_title([e for e in events if _is_range(e)]), "all"))
    for r in all_regions:
        parts.append(_build_range_section(
            _merge_by_title([e for e in events if _is_range(e) and e["region"] == r]), r))

    parts.append(
        '<a style="display:block;text-align:center;margin:32px auto 0;padding:12px 32px;'
        'background:#6366f1;color:#fff;border-radius:8px;font-size:14px;font-weight:600;'
        'text-decoration:none;max-width:320px;" href="/collections/all">'
        'Browse Our Sticker Collections &rarr;</a>'
    )
    parts.append(_build_modal_html())
    parts.append(f'<script>{_build_js()}</script>')
    parts.append('</div>')
    return "\n".join(parts)


# ── CSS ──────────────────────────────────────────────────

def _build_css() -> str:
    return '''
#ink-cal{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1a1a2e;width:100%;max-width:100%;margin:0 auto;padding:8px 0;overflow:hidden;}
#ink-cal *{box-sizing:border-box;}
#ink-cal input[name="ik-rg"]{display:none;}
.ik-hdr{text-align:center;margin-bottom:20px;}
.ik-hdr h2{font-size:22px;font-weight:800;margin:0 0 4px;color:#1a1a2e;}
.ik-hdr p{font-size:14px;color:#6b7280;margin:0;}
.ik-tabs{display:flex;justify-content:center;gap:8px;margin-bottom:20px;flex-wrap:wrap;}
.ik-tab{display:inline-flex;align-items:center;gap:5px;padding:6px 16px;border-radius:20px;font-size:12px;font-weight:600;border:1px solid #e5e7eb;color:#6b7280;cursor:pointer;background:#fff;transition:all .15s;}
.ik-tab:hover{border-color:#6366f1;color:#6366f1;}
.ik-tab .ik-dot{width:8px;height:8px;border-radius:50%;display:inline-block;}
.ik-lgd{display:flex;justify-content:center;gap:16px;margin-bottom:16px;flex-wrap:wrap;font-size:12px;color:#6b7280;}
.ik-lgd-i{display:flex;align-items:center;gap:5px;}
.ik-lgd-d{width:10px;height:10px;border-radius:3px;display:inline-block;}
.ik-wk{display:grid;grid-template-columns:repeat(7,1fr);width:100%;text-align:center;font-size:11px;font-weight:700;color:#9ca3af;padding:8px 0;border-bottom:1px solid #e5e7eb;}
.ik-g{display:grid;grid-template-columns:repeat(7,1fr);width:100%;gap:1px;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;table-layout:fixed;}
.ik-c{background:#fff;min-height:80px;padding:4px;overflow:hidden;min-width:0;}
.ik-c-o{opacity:.35;}
.ik-dn{font-size:12px;font-weight:600;color:#6b7280;display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;border-radius:4px;}
.ik-c-today .ik-dn{background:#6366f1;color:#fff;border-radius:50%;}
.ik-evts{display:flex;flex-direction:column;gap:2px;margin-top:2px;overflow:hidden;}
.ik-e{font-size:10px;padding:1px 4px;border-radius:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500;line-height:1.5;cursor:pointer;transition:opacity .15s;max-width:100%;display:block;}
.ik-e:hover{opacity:.75;}
.ik-more{font-size:10px;color:#9ca3af;padding:0 4px;}
.ik-rng{margin-top:24px;}
.ik-rng-t{font-size:14px;font-weight:700;color:#374151;margin-bottom:12px;}
.ik-rng-t span{font-size:11px;background:#f3f4f6;color:#6b7280;padding:1px 8px;border-radius:10px;margin-left:6px;}
.ik-rg{margin-bottom:16px;}
.ik-rg-cat{font-size:12px;font-weight:700;color:#374151;margin-bottom:8px;display:flex;align-items:center;gap:6px;}
.ik-rg-cd{width:8px;height:8px;border-radius:2px;display:inline-block;}
.ik-chips{display:flex;flex-wrap:wrap;gap:8px;}
.ik-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;font-size:12px;cursor:pointer;transition:all .15s;}
.ik-chip:hover{border-color:#6366f1;box-shadow:0 1px 3px rgba(0,0,0,.08);}
.ik-chip-d{font-size:10px;font-weight:600;padding:1px 6px;border-radius:3px;color:#fff;}
.ik-chip-dt{font-weight:600;color:#6b7280;font-size:11px;}
.ik-chip-tt{font-weight:600;color:#1f2937;}
.ik-panel{display:none;}
#ik-rg-all:checked~.ik-p-all{display:block;}
#ik-rg-all:checked~.ik-tabs .ik-tab[for="ik-rg-all"]{background:#6366f1;color:#fff;border-color:#6366f1;}
#ik-rg-us:checked~.ik-p-us{display:block;}
#ik-rg-us:checked~.ik-tabs .ik-tab[for="ik-rg-us"]{background:#6366f1;color:#fff;border-color:#6366f1;}
#ik-rg-ca:checked~.ik-p-ca{display:block;}
#ik-rg-ca:checked~.ik-tabs .ik-tab[for="ik-rg-ca"]{background:#6366f1;color:#fff;border-color:#6366f1;}
#ik-rg-eu:checked~.ik-p-eu{display:block;}
#ik-rg-eu:checked~.ik-tabs .ik-tab[for="ik-rg-eu"]{background:#6366f1;color:#fff;border-color:#6366f1;}
.ik-ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:10000;align-items:center;justify-content:center;padding:20px;}
.ik-ov.ik-show{display:flex;}
.ik-modal{background:#fff;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.2);max-width:440px;width:100%;max-height:80vh;overflow-y:auto;}
.ik-mhd{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid #e5e7eb;}
.ik-mhd h3{margin:0;font-size:16px;font-weight:700;}
.ik-mx{font-size:22px;background:none;border:none;cursor:pointer;color:#9ca3af;padding:4px 8px;line-height:1;}
.ik-mx:hover{color:#374151;}
.ik-mbd{padding:16px 20px;font-size:13px;line-height:1.7;color:#4b5563;}
.ik-mbd .ik-meta{display:flex;gap:6px;align-items:center;margin-bottom:10px;flex-wrap:wrap;}
.ik-mbd .ik-cat-tag{font-size:11px;padding:1px 8px;border-radius:3px;font-weight:500;}
.ik-mbd .ik-desc{margin:8px 0;}
.ik-src{display:inline-flex;align-items:center;gap:5px;margin-top:10px;padding:6px 14px;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:6px;font-size:12px;color:#6366f1;text-decoration:none;font-weight:500;transition:all .15s;}
.ik-src:hover{background:#eef2ff;border-color:#6366f1;}
@media(max-width:768px){
 .ik-c{min-height:50px;padding:2px;}
 .ik-e{font-size:8px;padding:0 2px;line-height:1.4;}
 .ik-dn{font-size:10px;width:18px;height:18px;line-height:18px;}
 .ik-hdr h2{font-size:18px;}
 .ik-tab{padding:4px 10px;font-size:11px;}
 .ik-modal{max-width:95vw;}
 .ik-chip{padding:4px 8px;font-size:11px;}
 .ik-wk span{font-size:10px;}
}
'''


# ── HTML builders ────────────────────────────────────────

def _build_header(title: str, month_name: str, year: int) -> str:
    return f'<div class="ik-hdr"><h2>{escape(title)}</h2><p>{month_name} {year}</p></div>'


def _build_region_tabs(all_regions: list[str], events: list[dict]) -> str:
    radios = '<input type="radio" name="ik-rg" id="ik-rg-all" checked>'
    for r in ["us", "ca", "eu"]:
        if r in all_regions:
            radios += f'<input type="radio" name="ik-rg" id="ik-rg-{r}">'

    tabs = '<div class="ik-tabs">'
    total = len(set(e["title"] for e in events))
    tabs += f'<label class="ik-tab" for="ik-rg-all">All ({total})</label>'
    for r in ["us", "ca", "eu"]:
        if r in all_regions:
            cnt = len(set(e["title"] for e in events if e["region"] == r))
            color = REGION_COLORS.get(r, "#6b7280")
            label = REGION_LABELS.get(r, r.upper())
            tabs += (
                f'<label class="ik-tab" for="ik-rg-{r}">'
                f'<span class="ik-dot" style="background:{color};"></span> {label} ({cnt})</label>'
            )
    tabs += '</div>'
    return radios + tabs


def _build_legend() -> str:
    items = ""
    for cat, label in CAT_LABELS.items():
        border, _, _ = CAT_COLORS.get(cat, ("#6b7280", "#f3f4f6", "#374151"))
        items += f'<span class="ik-lgd-i"><span class="ik-lgd-d" style="background:{border};"></span> {label}</span>'
    return f'<div class="ik-lgd">{items}</div>'


def _build_calendar_grid(year: int, month: int, singles: list[dict], panel_id: str) -> str:
    merged = _merge_by_title(singles)
    today_str = datetime.now(_CN_TZ).strftime("%Y-%m-%d")
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    date_events: dict[str, list[dict]] = {}
    for e in merged:
        date_events.setdefault(e["start_date"], []).append(e)

    html = f'<div class="ik-panel ik-p-{panel_id}">'
    html += '<div class="ik-wk">' + "".join(f'<span>{d}</span>' for d in WEEKDAYS) + '</div>'
    html += '<div class="ik-g">'

    for week in weeks:
        for day in week:
            ds = day.strftime("%Y-%m-%d")
            is_other = day.month != month
            is_today = ds == today_str
            cls = "ik-c"
            if is_other:
                cls += " ik-c-o"
            if is_today:
                cls += " ik-c-today"

            html += f'<div class="{cls}"><span class="ik-dn">{day.day}</span>'

            if not is_other and ds in date_events:
                evts = date_events[ds]
                html += '<div class="ik-evts">'
                for ev in evts[:3]:
                    style = _cat_style(ev.get("category", ""))
                    t = escape(ev["title"])
                    data = _evt_data_attr(ev)
                    html += f'<div class="ik-e" style="{style}" title="{t}" data-evt="{data}" onclick="ikShow(this)">{t}</div>'
                if len(evts) > 3:
                    html += f'<div class="ik-more">+{len(evts) - 3} more</div>'
                html += '</div>'

            html += '</div>'

    html += '</div></div>'
    return html


def _build_range_section(ranges: list[dict], panel_id: str) -> str:
    if not ranges:
        return ""

    cat_order = ["holiday", "event", "cultural", "sports"]
    groups: dict[str, list[dict]] = {}
    for e in ranges:
        groups.setdefault(e.get("category", "event"), []).append(e)

    html = f'<div class="ik-panel ik-p-{panel_id}"><div class="ik-rng">'
    html += f'<div class="ik-rng-t">Multi-Day Events<span>{len(ranges)}</span></div>'

    for cat in cat_order:
        if cat not in groups:
            continue
        border, _, _ = CAT_COLORS.get(cat, ("#6b7280", "#f3f4f6", "#374151"))
        label = CAT_LABELS.get(cat, cat)
        items = groups[cat]
        html += '<div class="ik-rg">'
        html += (
            f'<div class="ik-rg-cat"><span class="ik-rg-cd" style="background:{border};"></span> '
            f'{label} <span style="font-size:11px;background:#f3f4f6;color:#6b7280;padding:1px 8px;'
            f'border-radius:10px;">{len(items)}</span></div>'
        )
        html += '<div class="ik-chips">'

        for e in items:
            days = _days_between(e["start_date"], e.get("end_date") or e["start_date"])
            badges = " ".join(_region_badge_html(r) for r in e.get("regions", [e.get("region", "")]))
            dr = f'{e["start_date"][5:]} ~ {(e.get("end_date") or e["start_date"])[5:]}'
            t = escape(e["title"])
            data = _evt_data_attr(e)
            html += (
                f'<div class="ik-chip" data-evt="{data}" onclick="ikShow(this)">'
                f'<span class="ik-chip-d" style="background:{border};">{days}d</span> '
                f'{badges} <span class="ik-chip-dt">{dr}</span> '
                f'<span class="ik-chip-tt">{t}</span></div>'
            )

        html += '</div></div>'

    html += '</div></div>'
    return html


def _build_modal_html() -> str:
    return '''<div class="ik-ov" id="ikOv" onclick="if(event.target===this)ikHide()">
<div class="ik-modal">
<div class="ik-mhd"><h3 id="ikMT"></h3><button class="ik-mx" onclick="ikHide()">&times;</button></div>
<div class="ik-mbd" id="ikMB"></div>
</div></div>'''


def _build_js() -> str:
    cat_styles = json.dumps({
        k: f"background:{bg};color:{fg};" for k, (_, bg, fg) in CAT_COLORS.items()
    }, ensure_ascii=False)
    region_colors = json.dumps(REGION_COLORS, ensure_ascii=False)
    region_labels = json.dumps(REGION_LABELS, ensure_ascii=False)

    return f'''
var _CS={cat_styles},_RC={region_colors},_RL={region_labels};
function ikShow(el){{
  var d=JSON.parse(el.getAttribute("data-evt"));
  document.getElementById("ikMT").textContent=d.title;
  var dt=d.end_date&&d.end_date!==d.start_date?d.start_date+" ~ "+d.end_date:d.start_date;
  var cs=_CS[d.cat_raw]||"background:#f3f4f6;color:#374151;";
  var h='<div class="ik-meta"><span class="ik-cat-tag" style="'+cs+'">'+d.category+'</span> ';
  (d.regions||[]).forEach(function(r){{
    var c=_RC[r]||"#6b7280",l=_RL[r]||r.toUpperCase();
    h+='<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba('+hexRgb(c)+',.1);color:'+c+';text-transform:uppercase;">'+l+'</span> ';
  }});
  h+='<span style="color:#9ca3af;font-size:12px;">'+dt+'</span></div>';
  if(d.desc)h+='<div class="ik-desc">'+d.desc+'</div>';
  if(d.source)h+='<a class="ik-src" href="'+d.source+'" target="_blank" rel="noopener">'
    +'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
    +' Learn More</a>';
  document.getElementById("ikMB").innerHTML=h;
  document.getElementById("ikOv").classList.add("ik-show");
}}
function ikHide(){{document.getElementById("ikOv").classList.remove("ik-show");}}
function hexRgb(h){{h=h.replace("#","");return parseInt(h.substring(0,2),16)+","+parseInt(h.substring(2,4),16)+","+parseInt(h.substring(4,6),16);}}
'''
