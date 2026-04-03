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
        const safeSrc = src.replace(/["']/g, '');
        if (!/^https?:\/\//.test(safeSrc)) return esc(raw);
        return '<img src="' + safeSrc + '" alt="' + esc(alt) + '" style="max-width:100%">';
      })
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, href) => {
        const safeHref = href.replace(/["']/g, '');
        if (!/^https?:\/\//.test(safeHref)) return esc(text);
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
