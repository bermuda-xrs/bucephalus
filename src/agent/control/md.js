"use strict";

(function (root) {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const SAFE_URL = /^(https?:\/\/|mailto:)[^\s"'<>]+$/i;

  function inline(text) {
    const codes = [];
    let s = esc(text).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const raw = url.replace(/&amp;/g, "&");
      return SAFE_URL.test(raw)
        ? `<a href="${esc(raw)}" rel="noopener noreferrer" target="_blank">${label}</a>` : m;
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
         .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, "$1<em>$2</em>")
         .replace(/(^|\W)_([^_\s][^_]*)_(?!\w)/g, "$1<em>$2</em>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  }

  function renderMarkdown(src) {
    const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*```/);
      if (fence) {
        const body = [];
        for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) body.push(lines[i]);
        i++;
        out.push(`<pre class="code"><code>${esc(body.join("\n") + (body.length ? "\n" : ""))}</code></pre>`);
        continue;
      }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { const n = Math.min(6, h[1].length + 3); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
      const list = line.match(/^\s*([-*+]|\d+[.)])\s+/);
      if (list) {
        const tag = /\d/.test(list[1]) ? "ol" : "ul";
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]))
          items.push(`<li>${inline(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, ""))}</li>`);
        out.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(inline(lines[i++].replace(/^\s*>\s?/, "")));
        out.push(`<blockquote>${q.join("<br>")}</blockquote>`);
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|[-*+]\s|\d+[.)]\s|>)/.test(lines[i]))
        para.push(inline(lines[i++]));
      if (!para.length) para.push(inline(lines[i++]));
      out.push(`<p>${para.join("<br>")}</p>`);
    }
    return out.join("\n");
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {renderMarkdown};
  else root.renderMarkdown = renderMarkdown;
})(this);
