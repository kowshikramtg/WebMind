/* ---- WebMind widget UI ------------------------------------------------------------------------------
 * Plain-DOM implementation of the same interface as the React components. It runs inside the bundle
 * produced by scripts/build-widget.mjs, after the shared core (rich text, chart layout, exports, icons)
 * and the stylesheet constant `WM_CSS`. Do not edit src/webmind/api/static/webmind-widget.js by hand.
 */
const WM_SCRIPT = document.currentScript;
if (WM_SCRIPT) {
  const attr = (n, d) => (WM_SCRIPT.getAttribute(n) != null ? WM_SCRIPT.getAttribute(n) : d);
  const endpoint = (attr("data-endpoint", "") || new URL(WM_SCRIPT.src).origin).replace(/\/+$/, "");
  const tokenUrl = attr("data-token-url", "");
  const title = attr("data-title", "Ask this site");
  const subtitleAttr = attr("data-subtitle", "");
  const loginUrl = safeHttpUrl(attr("data-login-url", ""));
  const side = attr("data-position", "right") === "left" ? "left" : "right";
  const theme = ["light", "dark"].includes(attr("data-theme", "auto")) ? attr("data-theme", "auto") : "auto";
  const accent = attr("data-accent", "");
  const suggestions = attr("data-suggestions", "").split("|").map((s) => s.trim()).filter(Boolean).slice(0, 4);
  const exportMode = ["off", "all"].includes(attr("data-client-exports", "aggregates")) ? attr("data-client-exports", "aggregates") : "aggregates";
  const streaming = attr("data-streaming", "true") !== "false";
  let conversationId = null;
  let busy = false;

  // ------------------------------------------------------------------ tiny DOM helpers
  const SVG_NS = "http://www.w3.org/2000/svg";
  const h = (tag, props, kids) => {
    const el = document.createElement(tag);
    for (const k in props || {}) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of [].concat(kids || [])) if (kid != null && kid !== false) el.append(kid);
    return el;
  };
  const svgEl = (tag, attrs, kids) => {
    const el = document.createElementNS(SVG_NS, tag);
    for (const k in attrs || {}) el.setAttribute(k, attrs[k]);
    for (const kid of [].concat(kids || [])) if (kid != null) el.append(kid);
    return el;
  };
  const icon = (name, size) => {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", width: size || 16, height: size || 16, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    for (const shape of ICONS[name]) svg.append(svgEl(shape.tag, shape.a));
    return svg;
  };

  // -------------------------------------------------------------------------- shell
  const host = document.createElement("div");
  host.setAttribute("data-webmind", "");
  const root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  root.append(h("style", { text: WM_CSS }));
  const wrap = h("div", { class: "wm wm-float", "data-theme": theme, "data-side": side, "data-open": "false" });
  if (accent) wrap.style.setProperty("--wm-accent", accent);
  const subEl = h("div", { class: "wm-subtitle" });
  const log = h("div", { class: "wm-log", role: "log", "aria-live": "polite" });
  const input = h("textarea", { class: "wm-input", rows: 1, maxlength: 4000, placeholder: "Ask a question", "aria-label": "Your question" });
  const sendBtn = h("button", { class: "wm-send", type: "submit", "aria-label": "Send", disabled: true }, [icon("send", 16)]);
  const newBtn = h("button", { class: "wm-icon-btn", type: "button", "aria-label": "New conversation", title: "New conversation", hidden: true, onclick: () => resetConversation() }, [icon("compose")]);
  const closeBtn = h("button", { class: "wm-icon-btn", type: "button", "aria-label": "Close", title: "Close", onclick: () => setOpen(false) }, [icon("close")]);
  const form = h("form", { class: "wm-composer" }, [h("div", { class: "wm-field" }, [input, sendBtn])]);
  const chat = h("section", { class: "wm-chat", "aria-label": title }, [
    h("header", { class: "wm-header" }, [
      h("div", { class: "wm-title-wrap" }, [
        h("div", { class: "wm-avatar" }, [icon("sparkle", 18)]),
        h("div", { style: "min-width:0" }, [h("div", { class: "wm-title", text: title }), subEl]),
      ]),
      h("div", { class: "wm-header-actions" }, [newBtn, closeBtn]),
    ]),
    log,
    form,
  ]);
  const panel = h("div", { class: "wm-panel", role: "dialog", "aria-label": title, hidden: true }, [chat]);
  const launcher = h("button", { class: "wm-launcher", type: "button", "aria-label": title, title, onclick: () => setOpen(true) }, [icon("chat", 26)]);
  wrap.append(panel, launcher);
  root.append(wrap);
  (document.body || document.documentElement).append(host);

  function setOpen(open) {
    panel.hidden = !open;
    launcher.hidden = open;
    wrap.setAttribute("data-open", String(open));
    if (open) setTimeout(() => input.focus({ preventScroll: true }), 60);
    else launcher.focus({ preventScroll: true });
  }
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) setOpen(false);
  });

  // ------------------------------------------------------------------ menus (shared)
  let openMenu = null;
  const closeMenu = () => {
    if (openMenu) {
      openMenu.pop.hidden = true;
      openMenu.btn.setAttribute("aria-expanded", "false");
      openMenu = null;
    }
  };
  root.addEventListener("pointerdown", (e) => {
    if (openMenu && !openMenu.wrap.contains(e.target)) closeMenu();
  });
  function menu(entries, label, compact) {
    if (!entries.length) return null;
    const btn = h("button", { class: compact ? "wm-act" : "wm-btn-sm", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" }, [icon(compact ? "download" : "share", compact ? 14 : 13), label]);
    const pop = h("div", { class: "wm-menu", role: "menu", hidden: true });
    for (const e of entries) {
      if (e.heading) pop.append(h("div", { class: "wm-menu-label", text: e.heading }));
      else if (e.separator) pop.append(h("div", { class: "wm-menu-sep" }));
      else
        pop.append(
          h("button", { class: "wm-menu-item", type: "button", role: "menuitem", onclick: () => { closeMenu(); Promise.resolve(e.onSelect()).catch(() => {}); } }, [e.label, e.hint ? h("small", { text: e.hint }) : null]),
        );
    }
    const wrapEl = h("div", { class: "wm-menu-wrap" }, [btn, pop]);
    const me = { wrap: wrapEl, btn, pop };
    btn.addEventListener("click", () => {
      const was = openMenu === me;
      closeMenu();
      if (!was) {
        pop.classList.remove("wm-right", "wm-up");
        pop.hidden = false;
        const place = menuPlacement(wrapEl.getBoundingClientRect(), pop.offsetWidth, pop.offsetHeight, (wrapEl.closest(".wm-log") || document.documentElement).getBoundingClientRect());
        if (place.right) pop.classList.add("wm-right");
        if (place.up) pop.classList.add("wm-up");
        btn.setAttribute("aria-expanded", "true");
        openMenu = me;
      }
    });
    return wrapEl;
  }

  // -------------------------------------------------------------------- rich text
  function inlineNodes(items, onCite) {
    const out = [];
    for (const t of items) {
      if (t.t === "text") out.push(document.createTextNode(t.v));
      else if (t.t === "bold") out.push(h("strong", { text: t.v }));
      else if (t.t === "code") out.push(h("code", { text: t.v }));
      else if (t.t === "link") {
        const href = safeHttpUrl(t.href);
        out.push(href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", text: t.v }) : document.createTextNode(t.v));
      } else if (t.t === "cite")
        out.push(onCite ? h("button", { class: "wm-cite", type: "button", "aria-label": "Show source " + t.n, text: String(t.n), onclick: () => onCite(t.n) }) : document.createTextNode("[" + t.n + "]"));
    }
    return out;
  }
  function renderRich(container, text, onCite) {
    container.replaceChildren();
    const rich = h("div", { class: "wm-rich" });
    for (const b of parseRichText(text)) {
      if (b.t === "p") rich.append(h("p", {}, inlineNodes(b.c, onCite)));
      else rich.append(h(b.t, {}, b.items.map((it) => h("li", {}, inlineNodes(it, onCite)))));
    }
    container.append(rich);
  }

  // ----------------------------------------------------------------------- charts
  const tipText = (m) => m.label + " · " + m.series + ": " + m.text;
  function chartView(spec, hideLegend) {
    const L = computeChartLayout(spec);
    const box = h("div", { class: "wm-chart" });
    if (L.empty) {
      box.append(h("p", { class: "wm-empty-chart", text: "No data to chart." }));
      return { box, svg: null };
    }
    const svg = svgEl("svg", { viewBox: "0 0 " + L.width + " " + L.height, role: "img", "aria-label": spec.title, "data-wm-chart": spec.type });
    const pl = L.plot;
    const tip = h("div", { class: "wm-tooltip", role: "status", hidden: true });
    const showTip = (m) => {
      const k = box.clientWidth / L.width;
      tip.replaceChildren(h("b", { text: m.label }), h("span", { text: m.series + ": " + m.text }));
      tip.style.left = m.ax * k + "px";
      tip.style.top = m.ay * k + "px";
      tip.hidden = false;
    };
    if (L.type !== "pie")
      for (const t of L.yTicks) {
        svg.append(svgEl("line", { x1: pl.l, x2: L.width - pl.r, y1: t.y, y2: t.y, stroke: "currentColor", "stroke-opacity": 0.16 }));
        const tx = svgEl("text", { x: pl.l - 7, y: t.y + 3.5, "text-anchor": "end", "font-size": 10, fill: "currentColor" });
        tx.textContent = t.label;
        svg.append(tx);
      }
    for (const ln of L.lines) {
      if (ln.area) svg.append(svgEl("polygon", { points: ln.area, fill: ln.color, "fill-opacity": 0.13 }));
      svg.append(svgEl("polyline", { points: ln.points, fill: "none", stroke: ln.color, "stroke-width": 2.25, "stroke-linejoin": "round", "stroke-linecap": "round", "data-wm-mark": "line" }));
    }
    for (const m of L.marks) {
      const common = { fill: m.color, "data-wm-mark": m.kind, tabindex: 0, role: "img", "aria-label": tipText(m) };
      let node;
      if (m.kind === "bar") node = svgEl("rect", Object.assign({ x: m.x, y: m.y, width: m.w, height: m.h, rx: Math.min(4, m.w / 2) }, common));
      else if (m.kind === "point") node = svgEl("circle", Object.assign({ cx: m.x, cy: m.y, r: m.r, stroke: "#fff", "stroke-opacity": 0.9, "stroke-width": 1.25 }, common));
      else node = svgEl("path", Object.assign({ d: m.d, stroke: "#fff", "stroke-opacity": 0.9, "stroke-width": 1.5 }, common));
      node.append(svgEl("title", {}, [document.createTextNode(tipText(m))]));
      const on = () => showTip(m);
      const off = () => (tip.hidden = true);
      node.addEventListener("mouseenter", on);
      node.addEventListener("focus", on);
      node.addEventListener("mouseleave", off);
      node.addEventListener("blur", off);
      svg.append(node);
    }
    if (L.type !== "pie") {
      svg.append(svgEl("line", { x1: pl.l, x2: L.width - pl.r, y1: L.baseline, y2: L.baseline, stroke: "currentColor", "stroke-opacity": 0.4 }));
      for (const x of L.xLabels) {
        const tx = svgEl("text", { x: x.x, y: L.height - pl.b + 15, "text-anchor": "middle", "font-size": 10, fill: "currentColor" });
        tx.textContent = x.label;
        svg.append(tx);
      }
      if (L.xAxisLabel) {
        const tx = svgEl("text", { x: pl.l + (L.width - pl.l - pl.r) / 2, y: L.height - 3, "text-anchor": "middle", "font-size": 9.5, fill: "currentColor", "fill-opacity": 0.8 });
        tx.textContent = L.xAxisLabel;
        svg.append(tx);
      }
    }
    box.append(svg, tip);
    if (!hideLegend) box.append(legendEl(L));
    return { box, svg, layout: L };
  }
  function legendEl(L) {
    if (!L || L.empty || !L.legend.length || (L.legend.length === 1 && L.type !== "pie")) return document.createDocumentFragment();
    return h("div", { class: "wm-legend" }, L.legend.map((l) => h("span", {}, [h("i", { style: "background:" + l.color }), l.label])));
  }

  // ------------------------------------------------------------------ result cards
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
    const blob = (data, type) => new Blob([data], { type });
  const serverEntries = (ask) => [
    { heading: "Download from server" },
    { label: "Excel (.xlsx)", hint: "permission checked", onSelect: () => ask("Export these results into Excel.") },
    { label: "CSV", onSelect: () => ask("Export these results into CSV.") },
    { label: "PDF", onSelect: () => ask("Export these results as a PDF.") },
    { label: "JSON", onSelect: () => ask("Export these results as a JSON file.") },
  ];

  function tableCard(table, ctx) {
    const pol = clientExportPolicy(ctx.response, exportMode);
    const name = table.dataset || "results";
    const entries = [];
    if (pol.table)
      entries.push(
        { heading: "Download" },
        { label: "Excel (.xlsx)", onSelect: () => downloadBlob(blob(tableToXlsx(table.columns, table.rows, name), MIME.xlsx), safeFileName(name, "xlsx")) },
        { label: "CSV", onSelect: () => downloadBlob(blob("﻿" + toCsv(table.columns, table.rows), MIME.csv), safeFileName(name, "csv")) },
        { label: "JSON", onSelect: () => downloadBlob(blob(toJson(table.columns, table.rows, { dataset: name }), MIME.json), safeFileName(name, "json")) },
      );
    if (pol.serverExport) entries.push(...serverEntries(ctx.ask));
    const numeric = new Set(table.columns.filter((c) => table.rows.length && table.rows.every((r) => r[c] == null || isNum(r[c]))));
    const decimals = Object.fromEntries(table.columns.map((c) => [c, columnDecimals(table.rows, c)]));
    const tbody = h("tbody");
    const draw = (all) => {
      tbody.replaceChildren();
      for (const r of table.rows.slice(0, all ? 100 : 8)) tbody.append(h("tr", {}, table.columns.map((c) => h("td", { class: numeric.has(c) ? "wm-num" : "", text: fmtCell(r[c], decimals[c]) }))));
    };
    draw(false);
    const body = h("div", { class: "wm-card-body" }, [
      h("div", { class: "wm-table-wrap" }, [
        h("table", { class: "wm-table" }, [h("thead", {}, [h("tr", {}, table.columns.map((c) => h("th", { class: numeric.has(c) ? "wm-num" : "", text: c.replace(/_/g, " ") })))]), tbody]),
      ]),
    ]);
    if (table.rows.length > 8) {
      let all = false;
      const more = h("button", { class: "wm-more", type: "button", text: "Show all " + Math.min(table.rows.length, 100) + " rows" });
      more.addEventListener("click", () => {
        all = !all;
        draw(all);
        more.textContent = all ? "Show fewer" : "Show all " + Math.min(table.rows.length, 100) + " rows";
      });
      body.append(more);
    }
    return h("div", { class: "wm-card" }, [
      h("div", { class: "wm-card-head" }, [
        h("div", {}, [
          h("div", { class: "wm-card-title", text: prettyTitle(name) }),
          h("div", { class: "wm-card-sub", text: table.total_rows.toLocaleString("en-US") + (table.total_rows === 1 ? " row" : " rows") + (table.truncated ? " (limit reached)" : "") }),
        ]),
        menu(entries, "Export"),
      ]),
      body,
      table.applied_filters && table.applied_filters.length ? h("div", { class: "wm-card-foot", text: table.applied_filters.join(" · ") }) : null,
    ]);
  }

  function chartCard(spec, ctx) {
    const view = chartView(spec, true);
    const pol = clientExportPolicy(ctx.response, exportMode);
    const data = chartToTable(spec);
    const base = safeFileName(spec.title, "x").replace(/\.x$/, "");
    const entries = [];
    if (pol.chart && view.svg)
      entries.push(
        { heading: "Image" },
        { label: "PNG", hint: "2×", onSelect: async () => downloadBlob(await svgToPng(view.svg, 2), base + ".png") },
        { label: "SVG", onSelect: () => downloadBlob(blob(svgToStandalone(view.svg), MIME.svg), base + ".svg") },
        { separator: true },
        { heading: "Data" },
        { label: "Excel (.xlsx)", onSelect: () => downloadBlob(blob(tableToXlsx(data.columns, data.rows, spec.title), MIME.xlsx), base + ".xlsx") },
        { label: "CSV", onSelect: () => downloadBlob(blob("﻿" + toCsv(data.columns, data.rows), MIME.csv), base + ".csv") },
      );
    const sub = spec.type === "pie" && view.layout ? view.layout.marks.length + " categories" : spec.series.length === 1 ? spec.series[0].label : spec.series.length + " series";
    return h("div", { class: "wm-card" }, [
      h("div", { class: "wm-card-head" }, [h("div", {}, [h("div", { class: "wm-card-title", text: prettyTitle(spec.title) }), h("div", { class: "wm-card-sub", text: sub })]), menu(entries, "Export")]),
      h("div", { class: "wm-card-body" }, [view.box, view.layout ? legendEl(view.layout) : null]),
    ]);
  }

  // ----------------------------------------------------------------------- sources
  function sourcesEl(citations) {
    const list = h("div", { class: "wm-sources", hidden: true });
    const cards = new Map();
    const chev = h("span", { class: "wm-chev", style: "display:inline-flex" }, [icon("chevDown", 13)]);
    const toggle = h("button", { class: "wm-sources-toggle", type: "button", "aria-expanded": "false" }, [icon("doc", 13), citations.length === 1 ? "1 source" : citations.length + " sources", chev]);
    const setList = (open) => {
      list.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => setList(list.hidden));
    for (const c of citations) {
      const href = safeHttpUrl(c.url);
      const when = c.freshness && c.freshness.last_synced_at ? c.freshness.last_synced_at.slice(0, 10) : null;
      const body = h("div", { class: "wm-source-body", hidden: true }, [
        h("blockquote", { class: "wm-quote", text: c.snippet }),
        h("div", { class: "wm-source-meta" }, [when ? h("span", { text: "Indexed " + when }) : null, c.version != null ? h("span", { text: "v" + c.version }) : null, href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", text: "Open page ↗" }) : null]),
      ]);
      const head = h("button", { class: "wm-source-head", type: "button", "aria-expanded": "false" }, [
        h("span", { class: "wm-source-badge", text: String(c.index) }),
        h("span", { class: "wm-source-text" }, [h("div", { class: "wm-source-title", text: c.title || href || "Source" }), c.section && c.section !== c.title ? h("div", { class: "wm-source-section", text: c.section }) : null]),
        icon("chevRight", 14),
      ]);
      head.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        head.setAttribute("aria-expanded", String(open));
      });
      const card = h("div", { class: "wm-source" }, [head, body]);
      cards.set(c.index, { card, head, body });
      list.append(card);
    }
    const focus = (n) => {
      const c = cards.get(n);
      if (!c) return;
      setList(true);
      c.body.hidden = false;
      c.head.setAttribute("aria-expanded", "true");
      c.card.classList.add("wm-flash");
      setTimeout(() => c.card.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60);
      setTimeout(() => c.card.classList.remove("wm-flash"), 1400);
    };
    return { nodes: [toggle, list], focus };
  }

  // ----------------------------------------------------------------------- actions
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const ta = h("textarea", { style: "position:fixed;opacity:0" });
      ta.value = text;
      root.append(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    }
  }
  function actionsEl(ctx, stack) {
    const pol = clientExportPolicy(ctx.response, exportMode);
    const copyBtn = h("button", { class: "wm-act", type: "button" }, [icon("copy", 14), "Copy"]);
    copyBtn.addEventListener("click", async () => {
      if (await copyText(plainText(ctx.response.answer))) {
        copyBtn.replaceChildren(icon("check", 14), "Copied");
        setTimeout(() => copyBtn.replaceChildren(icon("copy", 14), "Copy"), 1500);
      }
    });
    const chartSvg = () => {
      const s = stack.querySelector("svg[data-wm-chart]");
      return s ? svgToStandalone(s) : null;
    };
    const entries = pol.answer
      ? [
          { label: "Markdown (.md)", onSelect: () => downloadBlob(blob(answerToMarkdown(ctx.question, ctx.response, exportMode), MIME.md), safeFileName(ctx.question, "md")) },
          { label: "PDF", hint: "print dialog", onSelect: () => printReport(buildPrintHtml(ctx.question, ctx.response, chartSvg(), exportMode)) },
        ]
      : [];
    return h("div", { class: "wm-actions" }, [copyBtn, menu(entries, "Save", true)]);
  }

  // ---------------------------------------------------------------- who am I
  function showIdentity(me) {
    subEl.replaceChildren();
    if (subtitleAttr) return void subEl.append(h("span", { class: "wm-sub-text", text: subtitleAttr }));
    if (!me) return void subEl.append(h("span", { class: "wm-sub-text", text: "Answers cite their sources" }));
    const label = me.authenticated ? "Signed in as " + me.user_id + (me.roles.length ? " · " + me.roles.join(", ") : "") : "Public visitor";
    subEl.append(h("span", { class: "wm-dot" + (me.authenticated ? " wm-on" : "") }), h("span", { class: "wm-sub-text", text: label }));
    if (!me.authenticated && loginUrl) subEl.append(h("a", { href: loginUrl, text: "Sign in" }));
  }
  async function loadIdentity() {
    try {
      const token = await getToken();
      const r = await fetch(endpoint + "/v1/me", { headers: token ? { Authorization: "Bearer " + token } : {} });
      showIdentity(r.ok ? await r.json() : null);
    } catch (e) {
      showIdentity(null);
    }
  }

  // ------------------------------------------------------------------- networking
  async function getToken() {
    if (!tokenUrl) return null;
    try {
      const r = await fetch(tokenUrl, { credentials: "include" });
      const j = r.ok ? await r.json() : null;
      return j && j.token ? j.token : null;
    } catch (e) {
      return null;
    }
  }
  const authHeaders = (token) => Object.assign({ "Content-Type": "application/json" }, token ? { Authorization: "Bearer " + token } : {});
  const httpError = (r) => new Error(r.status === 429 ? "Too many requests, please wait a minute." : "The assistant is unavailable (" + r.status + ").");

  async function chatOnce(question, token) {
    const r = await fetch(endpoint + "/v1/chat", { method: "POST", headers: authHeaders(token), body: JSON.stringify({ message: question, conversation_id: conversationId }) });
    if (!r.ok) throw httpError(r);
    return r.json();
  }
  async function chatStream(question, token, onToken) {
    const r = await fetch(endpoint + "/v1/chat/stream", { method: "POST", headers: authHeaders(token), body: JSON.stringify({ message: question, conversation_id: conversationId }) });
    if (!r.ok || !r.body) {
      if (r.status === 401 || r.status === 429) throw httpError(r);
      return chatOnce(question, token); // streaming unavailable: fall back to a single response
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let final = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        if (!data) continue;
        const ev = JSON.parse(data);
        if (ev.type === "token") onToken(ev.text);
        else if (ev.type === "final") final = ev.response;
        else if (ev.type === "error") throw new Error("The assistant ran into a problem. Please try again.");
      }
    }
    if (!final) throw new Error("The response was interrupted. Please try again.");
    return final;
  }
  async function downloadExport(ref, note) {
    try {
      const token = await getToken();
      const r = await fetch(endpoint + ref.download_url, { headers: token ? { Authorization: "Bearer " + token } : {} });
      if (!r.ok) throw new Error("x");
      downloadBlob(await r.blob(), ref.filename);
      note.hidden = true;
    } catch (e) {
      note.textContent = "This download has expired or is not available.";
      note.hidden = false;
    }
  }

  // -------------------------------------------------------------------- the chat
  let emptyEl = null;
  function showEmpty() {
    emptyEl = h("div", { class: "wm-empty" }, [
      h("div", { class: "wm-avatar" }, [icon("sparkle", 26)]),
      h("h2", { text: "How can I help?" }),
      h("p", { text: "Ask about this site, look up information, or request a chart or table." }),
      suggestions.length ? h("div", { class: "wm-chips" }, suggestions.map((s) => h("button", { class: "wm-chip", type: "button", text: s, onclick: () => send(s) }))) : null,
    ]);
    log.append(emptyEl);
  }
  function resetConversation() {
    if (busy) return;
    conversationId = null;
    log.replaceChildren();
    newBtn.hidden = true;
    showEmpty();
  }
  const toBottom = () => log.scrollTo({ top: log.scrollHeight });

  async function send(question) {
    const q = question.trim();
    if (!q || busy) return;
    busy = true;
    sendBtn.disabled = true;
    input.value = "";
    input.style.height = "auto";
    if (emptyEl) {
      emptyEl.remove();
      emptyEl = null;
    }
    newBtn.hidden = false;

    const bubbleHost = h("div", { class: "wm-bubble" }, [h("span", { class: "wm-typing", "aria-label": "Assistant is typing" }, [h("i"), h("i"), h("i")])]);
    const stack = h("div", { class: "wm-stack" }, [bubbleHost]);
    log.append(h("article", { class: "wm-turn" }, [h("div", { class: "wm-row wm-user" }, [h("div", { class: "wm-bubble", text: q })]), h("div", { class: "wm-row wm-assistant" }, [stack])]));
    toBottom();

    try {
      const token = await getToken();
      let partial = "";
      const onToken = (t) => {
        partial += t;
        bubbleHost.classList.add("wm-caret");
        renderRich(bubbleHost, partial);
        toBottom();
      };
      const res = streaming ? await chatStream(q, token, onToken) : await chatOnce(q, token);
      conversationId = res.conversation_id || conversationId;
      renderFinal(res, q, stack, bubbleHost);
    } catch (err) {
      bubbleHost.classList.remove("wm-caret");
      bubbleHost.classList.add("wm-error");
      bubbleHost.setAttribute("role", "alert");
      bubbleHost.replaceChildren(
        document.createTextNode(err && err.message ? err.message : "Something went wrong."),
        h("button", { class: "wm-retry", type: "button", text: "Try again", onclick: () => send(q) }),
      );
    } finally {
      busy = false;
      sendBtn.disabled = !input.value.trim();
      toBottom();
    }
  }

  function renderFinal(res, q, stack, bubbleHost) {
    const ctx = { question: q, response: res, ask: (t) => send(t) };
    const src = res.citations && res.citations.length ? sourcesEl(res.citations) : null;
    bubbleHost.classList.remove("wm-caret");
    renderRich(bubbleHost, res.answer, src ? (n) => src.focus(n) : undefined);
    if (res.chart) stack.append(chartCard(res.chart, ctx));
    if (res.table && !res.chart && res.table.rows.length) stack.append(tableCard(res.table, ctx));
    if (res.export) {
      const note = h("div", { class: "wm-note", role: "alert", hidden: true });
      stack.append(
        h("div", { class: "wm-card wm-file" }, [
          h("div", { class: "wm-file-icon", text: res.export.format.toUpperCase().slice(0, 4) }),
          h("div", { class: "wm-file-text" }, [h("div", { class: "wm-file-name", text: res.export.filename }), h("div", { class: "wm-file-meta", text: res.export.row_count.toLocaleString("en-US") + " rows · expires " + res.export.expires_at.slice(0, 16).replace("T", " ") + " UTC" })]),
          h("button", { class: "wm-btn-sm", type: "button", onclick: () => downloadExport(res.export, note) }, [icon("download", 13), "Download"]),
        ]),
        note,
      );
    }
    if (src) stack.append(...src.nodes);
    stack.append(actionsEl(ctx, stack));
  }

  // -------------------------------------------------------------------- composer
  const syncSend = () => {
    sendBtn.disabled = busy || !input.value.trim();
  };
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
    syncSend();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    send(input.value);
  });

  showEmpty();
  showIdentity(null);
  loadIdentity();
  if (attr("data-open", "false") === "true") setOpen(true);
}
