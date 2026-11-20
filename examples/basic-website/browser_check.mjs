// Real-browser check of the WebMind widget. Drives the actual UI in headless Chrome/Edge over the DevTools
// Protocol (Node 22's built-in WebSocket, throwaway profile, nothing downloaded): opens the assistant, types a
// question, waits for the answer, reads back what was RENDERED (marks, axes, tables, sources), optionally
// compares drawn values with ground truth, can click an export and verify the downloaded bytes, and saves a
// screenshot in light/dark and desktop/mobile.
//
//   node examples/basic-website/browser_check.mjs --url http://localhost:8000/ \
//        --question "Generate a bar chart showing the number of projects by department." \
//        --expectChart bar --expect '{"engineering":24,...}' --shot out.png
//
// Options: --pre <url,url>  --scheme light|dark  --viewport 390x844  --expandSources  --export "Export>CSV"
//          --exportOut file  --expectText a|b  --forbidText a|b  --expectStream  --timeout 240
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith("--")) {
    const next = process.argv[i + 1];
    args[process.argv[i].slice(2)] = next && !next.startsWith("--") ? next : true;
  }
}
if (!args.url || !args.question) {
  console.error("usage: --url <page> --question <text> [--shot f.png] [--pre u] [--scheme dark] [--viewport WxH] [--export 'Export>CSV'] ...");
  process.exit(2);
}
const CHROME = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => p && existsSync(p));
if (!CHROME) {
  console.error("no Chrome/Edge found; set CHROME_PATH");
  process.exit(2);
}
const [vw, vh] = String(args.viewport ?? "1100x860").split("x").map(Number);
const mobile = vw <= 520;
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "wm-browser-"));
const proc = spawn(
  CHROME,
  [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-extensions", `--window-size=${vw},${vh}`, "about:blank"],
  { stdio: "ignore" },
);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = false;
const fail = (m) => {
  failed = true;
  console.log("FAIL  " + m);
};
const ok = (m) => console.log("ok    " + m);

async function main() {
  let targets;
  for (let i = 0; i < 60; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      if (targets.find((t) => t.type === "page")) break;
    } catch {
      /* browser still starting */
    }
    await sleep(250);
  }
  const page = targets?.find((t) => t.type === "page");
  if (!page) throw new Error("could not attach to the browser");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = rej;
  });
  let id = 0;
  const pending = new Map();
  const consoleErrors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    } else if (m.method === "Runtime.exceptionThrown") {
      consoleErrors.push(m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description ?? ""));
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      consoleErrors.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
    }
  };
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const i = ++id;
      pending.set(i, { res, rej });
      ws.send(JSON.stringify({ id: i, method, params }));
    });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
    return r.result.value;
  };
  const goto = async (url) => {
    await send("Page.navigate", { url });
    for (let i = 0; i < 80; i++) {
      if ((await evalJs("document.readyState")) === "complete") return;
      await sleep(250);
    }
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Browser.setDownloadBehavior", { behavior: "deny" }).catch(() => {});
  await send("Emulation.setDeviceMetricsOverride", { width: vw, height: vh, deviceScaleFactor: mobile ? 2 : 1, mobile });
  if (args.scheme) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: String(args.scheme) }] });

  if (args.pre) for (const u of String(args.pre).split(",")) await goto(u); // e.g. sign in as a persona (sets the host cookie)
  await goto(String(args.url));

  const ROOT = "([...document.body.children].find(e=>e.shadowRoot)?.shadowRoot)";
  for (let i = 0; i < 60 && !(await evalJs(`!!${ROOT}`)); i++) await sleep(250);
  if (!(await evalJs(`!!${ROOT}`))) throw new Error("widget did not mount (script blocked or not loaded?)");
  ok("widget mounted in the page");

  // Capture every Blob that is turned into a download, so exports can be verified byte-for-byte.
  await evalJs(`(()=>{window.__blobs=[];const o=URL.createObjectURL.bind(URL);URL.createObjectURL=b=>{window.__blobs.push(b);return o(b)};return true})()`);

  // Open the panel with a real mouse click on the launcher.
  const rect = await evalJs(`(()=>{const b=${ROOT}.querySelector('.wm-launcher').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()`);
  for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: rect.x, y: rect.y, button: "left", clickCount: 1 });
  await sleep(500);
  const empty = await evalJs(`(()=>{const r=${ROOT};return {chips:[...r.querySelectorAll('.wm-chip')].map(c=>c.textContent),heading:r.querySelector('.wm-empty h2')?.textContent}})()`);
  await sleep(900); // identity (GET /v1/me) arrives asynchronously
  const who = await evalJs(`(()=>{const r=${ROOT};const el=r.querySelector('.wm-subtitle');return {text:el.innerText.trim().replace(/\\s+/g,' '),dot:r.querySelector('.wm-dot')?.className||'',link:r.querySelector('.wm-subtitle a')?.getAttribute('href')||null}})()`);
  console.log(`identity : "${who.text}"${who.link ? ` (sign-in link -> ${who.link})` : ""}`);
  if (args.expectIdentity) who.text.includes(String(args.expectIdentity)) ? ok(`identity line says "${who.text}"`) : fail(`identity line "${who.text}" lacks "${args.expectIdentity}"`);
  console.log(`empty state: "${empty.heading}", ${empty.chips.length} suggestion chip(s)`);
  if (args.shotEmpty) writeFileSync(String(args.shotEmpty), Buffer.from((await send("Page.captureScreenshot", { format: "png" })).data, "base64"));

  await evalJs(`${ROOT}.querySelector('.wm-input').focus(); true`);
  await send("Input.insertText", { text: String(args.question) });
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: type === "keyDown" ? "\r" : undefined });
  ok(`typed question and pressed Enter: "${args.question}"`);

  const LAST = `${ROOT}.querySelector('.wm-turn:last-of-type')`;
  const collect = `(()=>{
    const root=${ROOT}; const turns=[...root.querySelectorAll('.wm-turn')]; const last=turns[turns.length-1];
    if(!last) return {done:false};
    const bubble=last.querySelector('.wm-assistant .wm-bubble'); const svg=last.querySelector('svg[data-wm-chart]'); const marks={};
    last.querySelectorAll('[data-wm-mark]').forEach(m=>{marks[m.dataset.wmMark]=(marks[m.dataset.wmMark]||0)+1});
    const box=svg?svg.getBoundingClientRect():null;
    return {done: !!last.querySelector('.wm-actions') || !!last.querySelector('.wm-error'), streaming: !!last.querySelector('.wm-caret'), partialLen: bubble?bubble.innerText.length:0,
      answer: bubble?bubble.innerText.slice(0,420):'', chartType: svg?svg.dataset.wmChart:null, marks,
      tips:[...last.querySelectorAll('svg title')].map(t=>t.textContent),
      barHeights:[...last.querySelectorAll('rect[data-wm-mark=bar]')].map(r=>+r.getAttribute('height')),
      svgText:[...last.querySelectorAll('svg text')].map(t=>t.textContent), legend:[...last.querySelectorAll('.wm-legend span')].map(l=>l.textContent),
      cardTitles:[...last.querySelectorAll('.wm-card-title')].map(t=>t.textContent),
      tableRows:last.querySelectorAll('.wm-table tbody tr').length, tableHead:[...last.querySelectorAll('.wm-table th')].map(t=>t.textContent),
      sources:[...last.querySelectorAll('.wm-source-title')].map(t=>t.textContent), sourcesToggle:last.querySelector('.wm-sources-toggle')?.textContent.trim()||null,
      citeChips:last.querySelectorAll('.wm-cite').length, exportButtons:[...last.querySelectorAll('.wm-btn-sm,.wm-act')].map(b=>b.textContent.trim()),
      fileCard:last.querySelector('.wm-file-name')?.textContent||null, errorText:last.querySelector('.wm-error')?.textContent||null,
      svgBox:box?{w:Math.round(box.width),h:Math.round(box.height)}:null};
  })()`;
  const timeout = Number(args.timeout ?? 240);
  let ev;
  const partials = new Set();
  for (let t = 0; t < timeout * 4; t++) {
    ev = await evalJs(collect);
    if (ev.streaming) partials.add(ev.partialLen);
    if (ev.done) break;
    await sleep(250);
  }
  if (!ev?.done) throw new Error("no answer rendered before the timeout");
  await sleep(500);
  ev = await evalJs(collect);

  console.log("\n--- what the browser rendered ---");
  console.log("answer   :", ev.answer.replace(/\s+/g, " ").slice(0, 320));
  if (ev.errorText) console.log("error    :", ev.errorText);
  console.log("cards    :", JSON.stringify(ev.cardTitles));
  console.log("chart    :", ev.chartType, "| drawn marks:", JSON.stringify(ev.marks), "| svg size:", JSON.stringify(ev.svgBox));
  if (ev.svgText.length) console.log("axis text:", JSON.stringify(ev.svgText));
  if (ev.legend.length) console.log("legend   :", JSON.stringify(ev.legend));
  if (ev.tableRows) console.log("table    :", `${ev.tableRows} data rows drawn; columns ${JSON.stringify(ev.tableHead)}`);
  if (ev.sources.length) console.log("sources  :", `${ev.sourcesToggle}; ${JSON.stringify(ev.sources)}; ${ev.citeChips} inline [n] chip(s)`);
  if (ev.fileCard) console.log("file card:", ev.fileCard);
  console.log("buttons  :", JSON.stringify(ev.exportButtons));
  if (partials.size) console.log(`streaming: ${partials.size} distinct partial render(s) observed while tokens arrived`);

  if (args.then) {
    const before = await evalJs(`${ROOT}.querySelectorAll('.wm-turn').length`);
    await evalJs(`${ROOT}.querySelector('.wm-input').focus(); true`);
    await send("Input.insertText", { text: String(args.then) });
    for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: type === "keyDown" ? "\r" : undefined });
    for (let i = 0; i < timeout * 4; i++) {
      const done = await evalJs(`(()=>{const t=[...${ROOT}.querySelectorAll('.wm-turn')];return t.length>${before}&&!!(t[t.length-1].querySelector('.wm-actions')||t[t.length-1].querySelector('.wm-error'))})()`);
      if (done) break;
      await sleep(250);
    }
    ok(`asked a follow-up: "${args.then}"`);
  }
  if (args.expectStream) partials.size >= 2 ? ok(`tokens streamed into the bubble (${partials.size} partial states)`) : fail("no token streaming observed");
  if (args.expectChart) {
    ev.chartType === args.expectChart ? ok(`SVG chart of type '${ev.chartType}' is drawn`) : fail(`expected ${args.expectChart} chart, got ${ev.chartType}`);
    const drawn = (ev.marks.bar ?? 0) + (ev.marks.point ?? 0) + (ev.marks.slice ?? 0);
    drawn > 0 ? ok(`${drawn} data marks are present in the DOM`) : fail("no data marks drawn");
  }
  if (args.expect) {
    const expected = JSON.parse(String(args.expect));
    const drawn = {};
    for (const tip of ev.tips) {
      const m = /^(.*?) · .*?: (-?[\d,.]+)/.exec(tip);
      if (m) drawn[m[1]] = Number(m[2].replace(/,/g, ""));
    }
    const keys = Object.keys(expected);
    const same = keys.length === Object.keys(drawn).length && keys.every((k) => Math.abs((drawn[k] ?? NaN) - expected[k]) < 0.5);
    same ? ok(`drawn values equal the ground truth from the database (${keys.length} marks)`) : fail(`drawn ${JSON.stringify(drawn)} != expected ${JSON.stringify(expected)}`);
    if (ev.barHeights.length === keys.length) {
      const byValue = keys.map((k, i) => [expected[k], ev.barHeights[i]]).sort((a, b) => a[0] - b[0]);
      byValue.every((p, i) => i === 0 || p[1] >= byValue[i - 1][1] - 0.01) ? ok("bar heights are monotonic in the data values (rendered proportionally)") : fail("bar heights do not follow the values");
    }
  }
  if (args.expectTableRows !== undefined) ev.tableRows === Number(args.expectTableRows) ? ok(`table shows ${ev.tableRows} rows`) : fail(`table rows ${ev.tableRows} != ${args.expectTableRows}`);
  for (const needle of String(args.expectText ?? "").split("|").filter(Boolean)) ev.answer.toLowerCase().includes(needle.toLowerCase()) ? ok(`answer text contains "${needle}"`) : fail(`answer text lacks "${needle}"`);
  for (const needle of String(args.forbidText ?? "").split("|").filter(Boolean)) ev.answer.toLowerCase().includes(needle.toLowerCase()) ? fail(`answer text must not contain "${needle}"`) : ok(`answer text correctly lacks "${needle}"`);

  if (args.expandSources) {
    const r = await evalJs(`(()=>{const last=[...${ROOT}.querySelectorAll('.wm-turn')].pop();const t=last.querySelector('.wm-sources-toggle'); if(!t) return {error:'no sources toggle'};
      t.click(); const head=last.querySelector('.wm-source-head'); head.click();
      const body=last.querySelector('.wm-source-body'); return {open:!last.querySelector('.wm-sources').hidden, quote:body.querySelector('.wm-quote')?.textContent.slice(0,120), link:body.querySelector('a')?.href||null, meta:body.querySelector('.wm-source-meta').innerText}})()`);
    r.open && r.quote ? ok(`sources expand with a document preview: "${r.quote}…" (${r.meta.replace(/\s+/g, " ")}) link=${r.link}`) : fail("sources did not expand: " + JSON.stringify(r));
  }

  if (args.export || args.menu) {
    const [opener, item] = String(args.export ?? args.menu).split(">");
    const TURN = args.menuTurn === "first" ? `[...${ROOT}.querySelectorAll('.wm-turn')][0]` : `[...${ROOT}.querySelectorAll('.wm-turn')].pop()`;
    await evalJs(`(()=>{window.__blobs.length=0;return true})()`);
    const menu = await evalJs(`(()=>{const last=${TURN};
      const btn=[...last.querySelectorAll('.wm-btn-sm,.wm-act')].find(b=>b.textContent.trim().startsWith(${JSON.stringify(opener)})); if(!btn) return {error:'no "${opener}" button'};
      btn.click(); btn.scrollIntoView({block:'center',behavior:'instant'}); const pop=btn.parentElement.querySelector('.wm-menu');
      return {headings:[...pop.querySelectorAll('.wm-menu-label')].map(x=>x.textContent), items:[...pop.querySelectorAll('.wm-menu-item')].map(x=>x.textContent.trim())}})()`);
    if (menu.error) fail(`export: ${menu.error}`);
    else {
      console.log(`menu     : ${JSON.stringify(menu.headings)} -> ${JSON.stringify(menu.items)}`);
      await sleep(450); // let the popover finish its fade-in
      const vis = await evalJs(`(()=>{const r=${ROOT};const pop=[...r.querySelectorAll('.wm-menu')].find(m=>!m.hidden); if(!pop) return {error:'no open menu'};
        const log=r.querySelector('.wm-log').getBoundingClientRect(); const b=pop.getBoundingClientRect();
        const inside=b.left>=log.left-1&&b.right<=log.right+1&&b.top>=log.top-1&&b.bottom<=log.bottom+1;
        const pts=[...pop.querySelectorAll('.wm-menu-item')].map(it=>{const q=it.getBoundingClientRect();const el=r.elementFromPoint(q.left+q.width/2,q.top+q.height/2);return !!el&&(el===it||it.contains(el))});
        return {inside, allTopmost: pts.length>0&&pts.every(Boolean), placement: pop.className.replace('wm-menu','').trim()||'below, left-aligned', rect:[Math.round(b.left),Math.round(b.top),Math.round(b.width),Math.round(b.height)], log:[Math.round(log.left),Math.round(log.top),Math.round(log.width),Math.round(log.height)]}})()`);
      if (vis.error) fail("menu visibility: " + vis.error);
      else {
        console.log(`placement: ${vis.placement}; menu ${JSON.stringify(vis.rect)} inside log ${JSON.stringify(vis.log)}`);
        vis.inside ? ok("open menu lies fully inside the chat (not clipped)") : fail("open menu is clipped by the chat edge");
        vis.allTopmost ? ok("every menu item is the topmost element at its position (nothing overlaps it)") : fail("something overlaps the open menu");
      }
      for (const n of String(args.expectMenu ?? "").split("|").filter(Boolean)) menu.items.some((i) => i.startsWith(n)) ? ok(`menu offers "${n}"`) : fail(`menu lacks "${n}"`);
      for (const n of String(args.forbidMenu ?? "").split("|").filter(Boolean)) (menu.headings.includes(n) || menu.items.some((i) => i.startsWith(n))) ? fail(`menu must not offer "${n}"`) : ok(`menu correctly lacks "${n}"`);
      if (args.shotMenu) await sleep(450); // let the popover finish its fade-in
      if (args.shotMenu) writeFileSync(String(args.shotMenu), Buffer.from((await send("Page.captureScreenshot", { format: "png" })).data, "base64"));
    }
    if (!menu.error && item) {
      const turnsBefore = await evalJs(`${ROOT}.querySelectorAll('.wm-turn').length`);
      const clicked = await evalJs(`(()=>{const last=${TURN};
        const btn=[...last.querySelectorAll('.wm-btn-sm,.wm-act')].find(b=>b.textContent.trim().startsWith(${JSON.stringify(opener)}));
        const it=[...btn.parentElement.querySelectorAll('.wm-menu-item')].find(b=>b.textContent.trim().startsWith(${JSON.stringify(item)})); if(!it) return 'no menu item "${item}"';
        it.click(); return 'clicked'})()`);
      if (clicked !== "clicked") fail(`export: ${clicked}`);
      else {
        if (args.followFile) {
          // A server-side export is a follow-up message: wait for the new turn, then use its download card.
          for (let i = 0; i < timeout * 4; i++) {
            const st = await evalJs(`(()=>{const t=[...${ROOT}.querySelectorAll('.wm-turn')]; const last=t[t.length-1]; return {n:t.length, done:!!last.querySelector('.wm-actions')||!!last.querySelector('.wm-error'), file:last.querySelector('.wm-file-name')?.textContent||null, answer:last.querySelector('.wm-assistant .wm-bubble')?.innerText.slice(0,200)}})()`);
            if (st.n > turnsBefore && st.done) {
              console.log("follow-up:", st.answer.replace(/\s+/g, " "));
              st.file ? ok(`server export produced a file card: ${st.file}`) : fail("server export produced no file card");
              if (st.file) await evalJs(`(()=>{[...${ROOT}.querySelectorAll('.wm-file .wm-btn-sm')].pop().click(); return true})()`);
              break;
            }
            await sleep(250);
          }
        }
        let info = null;
        for (let i = 0; i < 60 && !info; i++) {
          await sleep(250);
          info = await evalJs(`(async()=>{const b=window.__blobs[0]; if(!b) return null; const buf=new Uint8Array(await b.arrayBuffer()); return {type:b.type,size:b.size,head:Array.from(buf.slice(0,8)),text:b.type.startsWith('text')||b.type.includes('json')||b.type.includes('svg')?new TextDecoder().decode(buf).slice(0,${Number(args.textLimit ?? 600)}):null,b64:btoa(String.fromCharCode(...buf.slice(0,200000)))}})()`);
        }
        if (!info) fail("export produced no download");
        else {
          ok(`export "${args.export}" produced a ${info.type} download of ${info.size} bytes`);
          const sig = info.head.join(",");
          if (info.type.includes("spreadsheet")) sig.startsWith("80,75,3,4") ? ok("xlsx starts with the ZIP signature (PK 03 04)") : fail("xlsx is not a ZIP");
          if (info.type === "image/png") sig.startsWith("137,80,78,71") ? ok("PNG signature is valid") : fail("not a PNG");
          if (info.text) console.log("content  :", info.text.replace(/\r?\n/g, " | ").slice(0, 400));
          if (args.exportOut) {
            writeFileSync(String(args.exportOut), Buffer.from(info.b64, "base64"));
            ok(`saved download to ${args.exportOut}`);
          }
          for (const n of String(args.expectExportText ?? "").split("|").filter(Boolean)) (info.text ?? "").includes(n) ? ok(`export contains "${n}"`) : fail(`export lacks "${n}"`);
          for (const n of String(args.forbidExportText ?? "").split("|").filter(Boolean)) (info.text ?? "").includes(n) ? fail(`export must not contain "${n}"`) : ok(`export correctly lacks "${n}"`);
        }
      }
    }
  }

  consoleErrors.length ? fail("browser console errors: " + consoleErrors.slice(0, 3).join(" | ")) : ok("no browser console errors");
  if (args.shot) {
    await evalJs(`(()=>{const l=${ROOT}.querySelector('.wm-log'); l.style.scrollBehavior='auto'; l.scrollTop=l.scrollHeight; return true})()`);
    await sleep(300);
    writeFileSync(String(args.shot), Buffer.from((await send("Page.captureScreenshot", { format: "png" })).data, "base64"));
    ok(`screenshot saved: ${args.shot}`);
  }
  ws.close();
}

try {
  await main();
} catch (e) {
  console.log("FAIL  " + e.message);
  failed = true;
} finally {
  proc.kill();
  await sleep(500);
  try {
    rmSync(profile, { recursive: true, force: true });
  } catch {
    /* profile may still be locked briefly on Windows; it lives in the OS temp dir */
  }
}
process.exit(failed ? 1 : 0);
