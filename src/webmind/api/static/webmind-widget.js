/*!
 * WebMind embeddable widget — dependency-free, Apache-2.0.
 *
 * <script src="https://your-webmind-host/v1/widget.js"
 *         data-endpoint="https://your-webmind-host"
 *         data-token-url="/webmind-token"      (optional: your backend returns {"token": "..."} for signed-in users)
 *         data-title="Ask our assistant"       (optional)
 *         data-position="right"                (optional: right | left)
 *         defer></script>
 *
 * Security notes: all server-provided text is rendered with textContent (never innerHTML);
 * only http(s) links are rendered; the widget never stores tokens.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var endpoint = (script.getAttribute("data-endpoint") || new URL(script.src).origin).replace(/\/+$/, "");
  var tokenUrl = script.getAttribute("data-token-url");
  var title = script.getAttribute("data-title") || "Ask this site";
  var position = script.getAttribute("data-position") === "left" ? "left" : "right";
  var conversationId = null;

  var css = [
    ":host{all:initial}",
    ".wm{position:fixed;bottom:20px;" + position + ":20px;z-index:2147483000;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1d2330}",
    ".btn{background:#3346d3;color:#fff;border:0;border-radius:999px;padding:12px 18px;font-weight:600;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.18)}",
    ".panel{display:none;flex-direction:column;width:min(400px,calc(100vw - 32px));height:min(560px,calc(100vh - 100px));background:#fff;border:1px solid #d9dce5;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.2);overflow:hidden;margin-bottom:10px}",
    ".open .panel{display:flex}",
    ".head{padding:12px 14px;background:#f4f5fa;border-bottom:1px solid #e3e5ec;font-weight:600;display:flex;justify-content:space-between;align-items:center}",
    ".x{background:none;border:0;font-size:18px;cursor:pointer;color:#555}",
    ".log{flex:1;overflow:auto;padding:12px 14px}",
    ".msg{margin:0 0 12px;white-space:pre-wrap;word-wrap:break-word}",
    ".me{background:#eef0ff;padding:8px 10px;border-radius:10px;margin-left:40px}",
    ".bot{padding:2px 0}",
    ".cites{margin:6px 0 0;padding:0;list-style:none;font-size:12px;color:#4a5263}",
    ".cites a,.bot a{color:#3346d3}",
    ".meta{font-size:11px;color:#7a8191;margin-top:4px}",
    "table{border-collapse:collapse;font-size:12px;margin-top:6px;display:block;overflow:auto;max-height:200px}",
    "th,td{border:1px solid #e3e5ec;padding:3px 6px;text-align:left;white-space:nowrap}",
    "form{display:flex;gap:8px;border-top:1px solid #e3e5ec;padding:10px}",
    "input{flex:1;border:1px solid #cfd3de;border-radius:8px;padding:9px 10px;font:inherit}",
    "button.send{background:#3346d3;color:#fff;border:0;border-radius:8px;padding:0 14px;cursor:pointer}",
    "svg{max-width:100%;margin-top:6px}",
    "@media (prefers-color-scheme:dark){.panel{background:#171a22;border-color:#2c313d;color:#e6e8ee}.head{background:#1e222c;border-color:#2c313d}.me{background:#262c45}input{background:#11141a;color:#e6e8ee;border-color:#2c313d}th,td{border-color:#2c313d}.cites{color:#aab0bf}.x{color:#aab0bf}}"
  ].join("");

  var host = document.createElement("div");
  var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  var style = document.createElement("style");
  style.textContent = css;
  root.appendChild(style);
  var wrap = el("div", "wm");
  var panel = el("div", "panel");
  var head = el("div", "head");
  head.appendChild(text(title));
  var close = el("button", "x");
  close.setAttribute("aria-label", "Close");
  close.textContent = "×";
  head.appendChild(close);
  var log = el("div", "log");
  log.setAttribute("role", "log");
  log.setAttribute("aria-live", "polite");
  var form = document.createElement("form");
  var input = document.createElement("input");
  input.placeholder = "Ask a question…";
  input.maxLength = 2000;
  input.setAttribute("aria-label", "Your question");
  var send = el("button", "send");
  send.type = "submit";
  send.textContent = "Send";
  form.appendChild(input);
  form.appendChild(send);
  panel.appendChild(head);
  panel.appendChild(log);
  panel.appendChild(form);
  var toggle = el("button", "btn");
  toggle.textContent = title;
  wrap.appendChild(panel);
  wrap.appendChild(toggle);
  root.appendChild(wrap);
  (document.body || document.documentElement).appendChild(host);

  toggle.addEventListener("click", function () { wrap.classList.toggle("open"); if (wrap.classList.contains("open")) input.focus(); });
  close.addEventListener("click", function () { wrap.classList.remove("open"); });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) return;
    input.value = "";
    addMessage(q, "me");
    ask(q);
  });

  function el(tag, cls) { var n = document.createElement(tag); if (cls) n.className = cls; return n; }
  function text(t) { return document.createTextNode(t == null ? "" : String(t)); }
  function safeUrl(u) {
    try { var url = new URL(u, endpoint); return url.protocol === "http:" || url.protocol === "https:" ? url.href : null; }
    catch (e) { return null; }
  }
  function addMessage(t, cls) { var m = el("div", "msg " + cls); m.appendChild(text(t)); log.appendChild(m); log.scrollTop = log.scrollHeight; return m; }

  // Minimal, safe rendering: **bold** and bare http(s) URLs become nodes; everything else is text.
  function renderRich(container, s) {
    var re = /(\*\*[^*]+\*\*|https?:\/\/[^\s)]+)/g, last = 0, m;
    while ((m = re.exec(s))) {
      container.appendChild(text(s.slice(last, m.index)));
      if (m[0].indexOf("**") === 0) { var b = el("strong"); b.textContent = m[0].slice(2, -2); container.appendChild(b); }
      else { var href = safeUrl(m[0]); if (href) { var a = el("a"); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = m[0]; container.appendChild(a); } else container.appendChild(text(m[0])); }
      last = m.index + m[0].length;
    }
    container.appendChild(text(s.slice(last)));
  }

  function getToken() {
    if (!tokenUrl) return Promise.resolve(null);
    return fetch(tokenUrl, { credentials: "include" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { return j && j.token ? j.token : null; })
      .catch(function () { return null; });
  }

  function ask(q) {
    var bubble = addMessage("…", "bot");
    getToken().then(function (token) {
      var headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = "Bearer " + token;
      return fetch(endpoint + "/v1/chat", {
        method: "POST", headers: headers,
        body: JSON.stringify({ message: q, conversation_id: conversationId })
      }).then(function (r) {
        if (r.status === 429) throw new Error("Too many requests — please wait a minute.");
        if (!r.ok) throw new Error("The assistant is unavailable (" + r.status + ").");
        return r.json();
      }).then(function (res) { render(bubble, res, token); });
    }).catch(function (err) { bubble.textContent = err.message || "Something went wrong."; });
  }

  function render(bubble, res, token) {
    conversationId = res.conversation_id || conversationId;
    bubble.textContent = "";
    renderRich(bubble, res.answer || "");
    if (res.citations && res.citations.length) {
      var ul = el("ul", "cites");
      res.citations.slice(0, 6).forEach(function (c) {
        var li = el("li");
        li.appendChild(text("[" + c.index + "] "));
        var href = c.url && safeUrl(c.url);
        var label = (c.title || "Source") + (c.section && c.section !== c.title ? " — " + c.section : "");
        if (href) { var a = el("a"); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = label; li.appendChild(a); }
        else li.appendChild(text(label));
        ul.appendChild(li);
      });
      bubble.appendChild(ul);
    }
    if (res.chart && res.chart.data && res.chart.data.length) bubble.appendChild(chart(res.chart));
    if (res.table && res.table.rows && res.table.rows.length && !res.chart) bubble.appendChild(table(res.table));
    if (res.export && token) {
      var btn = el("button", "send");
      btn.type = "button";
      btn.textContent = "Download " + res.export.filename;
      btn.style.marginTop = "6px";
      btn.style.padding = "6px 10px";
      btn.addEventListener("click", function () { download(res.export, token); });
      bubble.appendChild(btn);
    }
    log.scrollTop = log.scrollHeight;
  }

  function table(t) {
    var tbl = el("table"), tr = el("tr");
    t.columns.forEach(function (c) { var th = el("th"); th.textContent = c; tr.appendChild(th); });
    tbl.appendChild(tr);
    t.rows.slice(0, 50).forEach(function (r) {
      var row = el("tr");
      t.columns.forEach(function (c) { var td = el("td"); td.textContent = r[c] == null ? "" : String(r[c]); row.appendChild(td); });
      tbl.appendChild(row);
    });
    return tbl;
  }

  function chart(spec) {
    var NS = "http://www.w3.org/2000/svg", W = 360, H = 200, P = 30;
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", spec.title);
    var palette = ["#3346d3", "#e0663a", "#1f9e74", "#b5487f", "#8a6d1d", "#5577aa"];
    var series = spec.series || [];
    var data = spec.data.slice(0, 40);
    var max = 0;
    data.forEach(function (d) { series.forEach(function (s) { var v = Number(d[s.key]) || 0; if (v > max) max = v; }); });
    max = max || 1;
    var step = (W - P * 2) / Math.max(data.length, 1);
    function y(v) { return H - P - (Number(v) || 0) / max * (H - P * 2); }
    if (spec.type === "line" || spec.type === "area") {
      series.forEach(function (s, i) {
        var pts = data.map(function (d, j) { return (P + step * j + step / 2) + "," + y(d[s.key]); }).join(" ");
        var pl = document.createElementNS(NS, "polyline");
        pl.setAttribute("points", pts); pl.setAttribute("fill", "none");
        pl.setAttribute("stroke", palette[i % palette.length]); pl.setAttribute("stroke-width", "2");
        svg.appendChild(pl);
      });
    } else {
      var bw = step / Math.max(series.length, 1) * 0.85;
      data.forEach(function (d, j) {
        series.forEach(function (s, i) {
          var r = document.createElementNS(NS, "rect"), v = Number(d[s.key]) || 0;
          r.setAttribute("x", P + step * j + bw * i); r.setAttribute("y", y(v));
          r.setAttribute("width", Math.max(bw - 1, 1)); r.setAttribute("height", H - P - y(v));
          r.setAttribute("fill", palette[i % palette.length]);
          var tip = document.createElementNS(NS, "title"); tip.textContent = d[spec.x_key] + " · " + s.label + ": " + v; r.appendChild(tip);
          svg.appendChild(r);
        });
      });
    }
    var axis = document.createElementNS(NS, "line");
    axis.setAttribute("x1", P); axis.setAttribute("x2", W - P); axis.setAttribute("y1", H - P); axis.setAttribute("y2", H - P);
    axis.setAttribute("stroke", "#9aa0ad"); svg.appendChild(axis);
    var cap = el("div", "meta");
    cap.textContent = spec.title + " — " + series.map(function (s) { return s.label; }).join(", ");
    var box = el("div"); box.appendChild(svg); box.appendChild(cap);
    return box;
  }

  function download(ref, token) {
    fetch(endpoint + ref.download_url, { headers: { Authorization: "Bearer " + token } })
      .then(function (r) { if (!r.ok) throw new Error("download failed"); return r.blob(); })
      .then(function (b) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(b); a.download = ref.filename;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
      })
      .catch(function () { addMessage("The download link has expired or is not available.", "bot"); });
  }
})();
