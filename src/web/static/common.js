/**
 * Shared utilities for the Sticker Workbench frontend.
 */

/** HTML-escape a string to prevent XSS when inserting into innerHTML. */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

/**
 * Minimal Markdown → HTML converter with XSS protection.
 * All text content is escaped; only safe structural tags are emitted.
 */
function mdToHtml(md) {
  if (!md) return '<p style="color:var(--text-muted);">无内容</p>';
  const lines = String(md).split('\n');
  const out = [];
  let inList = false;

  for (const raw of lines) {
    const line = raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

    if (/^### /.test(raw)) { out.push('<h3>' + esc(raw.slice(4)) + '</h3>'); continue; }
    if (/^## /.test(raw))  { out.push('<h2>' + esc(raw.slice(3)) + '</h2>'); continue; }
    if (/^# /.test(raw))   { out.push('<h1>' + esc(raw.slice(2)) + '</h1>'); continue; }
    if (/^> /.test(raw))   { out.push('<blockquote>' + esc(raw.slice(2)) + '</blockquote>'); continue; }

    if (/^- /.test(raw)) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push('<li>' + esc(raw.slice(2)) + '</li>');
      continue;
    }
    if (inList) { out.push('</ul>'); inList = false; }

    if (raw.trim() === '') { out.push('</p><p>'); continue; }

    let s = line
      .replace(/\*\*(.+?)\*\*/g, (_, t) => '<strong>' + t + '</strong>')
      .replace(/\*(.+?)\*/g, (_, t) => '<em>' + t + '</em>')
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, src) => {
        const safeSrc = src.replace(/["'<>]/g, '');
        if (/^(https?:\/\/|\/outputs\/|\/blog-outputs\/|\/static\/|data:image\/)/.test(safeSrc)) {
          return '<img src="' + safeSrc + '" alt="' + esc(alt) + '" style="max-width:100%;cursor:pointer;" onclick="openLightbox(\'' + safeSrc.replace(/'/g,'') + '\')">';
        }
        return esc(raw);
      })
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, href) => {
        const safeHref = href.replace(/["']/g, '');
        if (!/^(https?:\/\/|\/blog-outputs\/|\/outputs\/)/.test(safeHref)) return esc(text);
        return '<a href="' + safeHref + '" target="_blank" rel="noopener">' + text + '</a>';
      });
    out.push(s + '<br>');
  }
  if (inList) out.push('</ul>');
  return '<p>' + out.join('') + '</p>';
}

/** Open a full-screen lightbox overlay for an image. */
function openLightbox(src) {
  let lb = document.getElementById('lightbox-overlay');
  if (!lb) {
    lb = document.createElement('div');
    lb.id = 'lightbox-overlay';
    lb.setAttribute('role', 'dialog');
    lb.setAttribute('aria-label', '图片预览');
    lb.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:9999;cursor:pointer;';
    lb.innerHTML = '<img id="lightbox-img" style="max-width:92vw;max-height:92vh;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.5);" alt="预览图片">'
      + '<button onclick="closeLightbox()" aria-label="关闭预览" style="position:absolute;top:16px;right:24px;font-size:28px;color:#fff;background:none;border:none;cursor:pointer;">✕</button>';
    lb.addEventListener('click', function(e) { if (e.target === lb) closeLightbox(); });
    document.body.appendChild(lb);
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeLightbox(); });
  }
  const safeSrc = (src || '').replace(/["']/g, '');
  document.getElementById('lightbox-img').src = safeSrc;
  lb.style.display = 'flex';
}

/** Close the lightbox overlay. */
function closeLightbox() {
  const lb = document.getElementById('lightbox-overlay');
  if (lb) lb.style.display = 'none';
}


/* ================================================================
   Sortable Table — click column headers to sort
   ================================================================ */
class SortableTable {
  constructor(table) {
    this.table = table;
    this.isServer = table.hasAttribute('data-sort-server');
    this.tbody = table.querySelector('tbody');
    this.headers = Array.from(table.querySelectorAll('th[data-sort-key]'));
    this.sortCol = null;
    this.sortDir = 'asc';

    if (this.isServer) {
      const p = new URLSearchParams(window.location.search);
      this.sortCol = p.get('sort') || null;
      this.sortDir = p.get('dir') || 'desc';
    }
    this._init();
  }

  _init() {
    this.headers.forEach(th => {
      th.style.cursor = 'pointer';
      th.style.userSelect = 'none';
      const arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      arrow.textContent = this._arrow(th);
      th.appendChild(arrow);
      th.addEventListener('click', () => this._sort(th));
    });
  }

  _arrow(th) {
    const k = th.dataset.sortKey;
    if (k === this.sortCol) return this.sortDir === 'asc' ? ' ↑' : ' ↓';
    return ' ↕';
  }

  _sort(th) {
    const key = th.dataset.sortKey;
    if (this.sortCol === key) {
      this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortCol = key;
      this.sortDir = th.dataset.sortDefault || 'asc';
    }
    this.headers.forEach(h => {
      h.querySelector('.sort-arrow').textContent = this._arrow(h);
    });
    if (this.isServer) {
      const url = new URL(window.location.href);
      url.searchParams.set('sort', this.sortCol);
      url.searchParams.set('dir', this.sortDir);
      url.searchParams.set('page', '1');
      window.location.href = url.toString();
    } else {
      this._clientSort();
    }
  }

  _clientSort() {
    const rows = Array.from(this.tbody.querySelectorAll('tr:not(.tbl-log-row)'));
    const allTh = Array.from(this.table.querySelectorAll('thead th'));
    const th = this.headers.find(h => h.dataset.sortKey === this.sortCol);
    const colIdx = allTh.indexOf(th);
    const type = th.dataset.sortType || 'string';

    rows.sort((a, b) => {
      let va = (a.cells[colIdx]?.textContent || '').trim();
      let vb = (b.cells[colIdx]?.textContent || '').trim();
      if (type === 'number') {
        va = parseFloat(va.replace(/[^0-9.\-]/g, '')) || 0;
        vb = parseFloat(vb.replace(/[^0-9.\-]/g, '')) || 0;
      } else if (type === 'date') {
        va = new Date(va.replace(/\s/, 'T')).getTime() || 0;
        vb = new Date(vb.replace(/\s/, 'T')).getTime() || 0;
      } else {
        va = va.toLowerCase(); vb = vb.toLowerCase();
      }
      const c = va < vb ? -1 : va > vb ? 1 : 0;
      return this.sortDir === 'asc' ? c : -c;
    });
    rows.forEach(r => this.tbody.appendChild(r));
  }
}

/* ================================================================
   Date Range Filter — server-side and client-side
   ================================================================ */
function initDateFilter(containerId, opts) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const ss = opts && opts.serverSide !== false;
  const p = new URLSearchParams(window.location.search);
  const df = p.get('date_from') || '';
  const dt = p.get('date_to') || '';

  el.innerHTML =
    '<div class="date-filter-row">' +
    '<label>时间范围</label>' +
    '<input type="date" class="date-input" id="' + containerId + '-from" value="' + df + '">' +
    '<span style="color:var(--text-muted)">至</span>' +
    '<input type="date" class="date-input" id="' + containerId + '-to" value="' + dt + '">' +
    '<button class="btn-secondary" style="padding:6px 12px;font-size:12px;box-shadow:none" ' +
      'onclick="applyDateFilter(\'' + containerId + '\',' + ss + ')">筛选</button>' +
    (df || dt
      ? '<button class="btn-secondary" style="padding:6px 12px;font-size:12px;box-shadow:none" ' +
        'onclick="clearDateFilter(\'' + containerId + '\',' + ss + ')">清除</button>'
      : '') +
    '</div>';
}

function applyDateFilter(cid, server) {
  var from = document.getElementById(cid + '-from').value;
  var to   = document.getElementById(cid + '-to').value;
  if (server) {
    var url = new URL(window.location.href);
    if (from) url.searchParams.set('date_from', from); else url.searchParams.delete('date_from');
    if (to) url.searchParams.set('date_to', to); else url.searchParams.delete('date_to');
    url.searchParams.set('page', '1');
    window.location.href = url.toString();
  } else {
    var tables = document.querySelectorAll('table[data-sortable]');
    tables.forEach(function(t) {
      var dateColIdx = parseInt(t.dataset.dateCol || '0', 10);
      var rows = t.querySelectorAll('tbody tr');
      rows.forEach(function(r) {
        var cell = r.cells[dateColIdx];
        if (!cell) return;
        var txt = cell.textContent.trim().replace(/\s/, 'T');
        var ts = new Date(txt).getTime();
        var show = true;
        if (from && ts < new Date(from).getTime()) show = false;
        if (to && ts > new Date(to + 'T23:59:59').getTime()) show = false;
        r.style.display = show ? '' : 'none';
      });
    });
  }
}

function clearDateFilter(cid, server) {
  if (server) {
    var url = new URL(window.location.href);
    url.searchParams.delete('date_from');
    url.searchParams.delete('date_to');
    url.searchParams.set('page', '1');
    window.location.href = url.toString();
  } else {
    document.getElementById(cid + '-from').value = '';
    document.getElementById(cid + '-to').value = '';
    document.querySelectorAll('table[data-sortable] tbody tr').forEach(function(r) { r.style.display = ''; });
  }
}

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('table[data-sortable]').forEach(function(t) { new SortableTable(t); });
});
