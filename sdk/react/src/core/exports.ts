/**
 * Client-side exports of results the user is ALREADY looking at (nothing is fetched or widened):
 * CSV, JSON, Excel (.xlsx, hand-written OOXML in a stored ZIP, no dependencies), SVG/PNG for charts,
 * Markdown and print-to-PDF for answers.
 *
 * Policy: by default only *aggregate* results (statistics and charts) can be exported client-side.
 * Raw record tables are exported through WebMind's server-side export, which enforces the `export`
 * capability, so a client can never download more than the server allows. See `clientExportPolicy`.
 */
import type { ChartSpecLike } from "./chartgeom.js";
import { plainText } from "./richtext.js";

export type Row = Record<string, unknown>;
export type ClientExportMode = "off" | "aggregates" | "all";

export interface ExportableResponse {
  answer: string;
  intent: string;
  citations: Array<{ index: number; title?: string | null; section?: string | null; url?: string | null }>;
  tools: Array<{ tool: string }>;
  table?: { columns: string[]; rows: Row[]; dataset?: string | null; applied_filters?: string[] } | null;
  chart?: ChartSpecLike | null;
}

const RAW_TOOLS = new Set(["query_records", "export_data", "generate_report"]);

export function isRawRecordResult(r: ExportableResponse): boolean {
  return r.tools.some((t) => RAW_TOOLS.has(t.tool)) || ["records", "export", "report"].includes(r.intent);
}

export function clientExportPolicy(r: ExportableResponse, mode: ClientExportMode = "aggregates") {
  const raw = isRawRecordResult(r);
  return {
    table: !!r.table && r.table.rows.length > 0 && (mode === "all" || (mode === "aggregates" && !raw)),
    chart: !!r.chart && r.chart.data.length > 0 && mode !== "off",
    answer: mode !== "off",
    /** Raw record tables: offer WebMind's server-side export (permission-checked) instead. */
    serverExport: !!r.table && r.table.rows.length > 0 && raw,
  };
}

// ---------------------------------------------------------------- cells, CSV, JSON

const FORMULA = /^[=+\-@\t\r]/;

/** Neutralize spreadsheet formula injection ("=HYPERLINK(...)") in text cells. */
export function safeCell(v: unknown): string | number | boolean {
  if (v === null || v === undefined) return "";
  if (typeof v === "number" || typeof v === "boolean") return v;
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return FORMULA.test(s) ? "'" + s : s;
}

export function toCsv(columns: string[], rows: Row[]): string {
  const esc = (v: unknown) => {
    const s = String(safeCell(v));
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => columns.map((c) => esc(r[c])).join(","))].join("\r\n");
}

export function toJson(columns: string[], rows: Row[], meta: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...meta, columns, row_count: rows.length, rows }, null, 2);
}

export function chartToTable(spec: ChartSpecLike): { columns: string[]; rows: Row[] } {
  const columns = [spec.x_key, ...spec.series.map((s) => s.label)];
  const rows = spec.data.map((d) => {
    const row: Row = { [spec.x_key]: d[spec.x_key] };
    for (const s of spec.series) row[s.label] = d[s.key];
    return row;
  });
  return { columns, rows };
}

// ---------------------------------------------------------------------- XLSX

const enc = new TextEncoder();
let CRC_TABLE: Uint32Array | null = null;

export function crc32(bytes: Uint8Array): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** Minimal ZIP writer ("stored", no compression): enough for OOXML packages. */
export function zipStore(files: Array<{ name: string; data: Uint8Array }>): Uint8Array {
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true); // UTF-8 names
    lv.setUint16(8, 0, true); // stored
    lv.setUint16(10, 0, true);
    lv.setUint16(12, 0x21, true); // 1980-01-01
    lv.setUint32(14, crc, true);
    lv.setUint32(18, f.data.length, true);
    lv.setUint32(22, f.data.length, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    parts.push(local, f.data);
    const cd = new Uint8Array(46 + name.length);
    const cv = new DataView(cd.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0x21, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, f.data.length, true);
    cv.setUint32(24, f.data.length, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(42, offset, true);
    cd.set(name, 46);
    central.push(cd);
    offset += local.length + f.data.length;
  }
  const cdSize = central.reduce((s, c) => s + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, offset, true);
  const all = [...parts, ...central, end];
  const out = new Uint8Array(all.reduce((s, p) => s + p.length, 0));
  let pos = 0;
  for (const p of all) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

const xmlEsc = (s: string) =>
  s
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

function colName(i: number): string {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

export function sheetName(name: string): string {
  return (name.replace(/[[\]:*?/\\]/g, " ").trim() || "data").slice(0, 31);
}

export function tableToXlsx(columns: string[], rows: Row[], name = "data"): Uint8Array {
  const cell = (ref: string, v: unknown, header = false) => {
    const val = safeCell(v);
    const style = header ? ' s="1"' : "";
    if (typeof val === "number" && Number.isFinite(val)) return `<c r="${ref}"${style}><v>${val}</v></c>`;
    if (typeof val === "boolean") return `<c r="${ref}"${style} t="b"><v>${val ? 1 : 0}</v></c>`;
    return `<c r="${ref}"${style} t="inlineStr"><is><t xml:space="preserve">${xmlEsc(String(val))}</t></is></c>`;
  };
  const head = `<row r="1">${columns.map((c, i) => cell(colName(i) + "1", c, true)).join("")}</row>`;
  const body = rows
    .map(
      (r, ri) =>
        `<row r="${ri + 2}">${columns
          .map((c, ci) => (r[c] === null || r[c] === undefined || r[c] === "" ? "" : cell(colName(ci) + (ri + 2), r[c])))
          .join("")}</row>`,
    )
    .join("");
  const widths = columns
    .map((c, i) => {
      const longest = Math.max(c.length, ...rows.slice(0, 200).map((r) => String(r[c] ?? "").length));
      return `<col min="${i + 1}" max="${i + 1}" width="${Math.min(Math.max(longest + 2, 8), 48)}" customWidth="1"/>`;
    })
    .join("");
  const X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
  const NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const PKG = "http://schemas.openxmlformats.org/package/2006/relationships";
  const files = [
    {
      name: "[Content_Types].xml",
      text: `${X}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`,
    },
    {
      name: "_rels/.rels",
      text: `${X}<Relationships xmlns="${PKG}"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    },
    {
      name: "xl/workbook.xml",
      text: `${X}<workbook xmlns="${NS}" xmlns:r="${REL}"><sheets><sheet name="${xmlEsc(sheetName(name))}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    },
    {
      name: "xl/_rels/workbook.xml.rels",
      text: `${X}<Relationships xmlns="${PKG}"><Relationship Id="rId1" Type="${REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${REL}/styles" Target="styles.xml"/></Relationships>`,
    },
    {
      name: "xl/styles.xml",
      text: `${X}<styleSheet xmlns="${NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`,
    },
    {
      name: "xl/worksheets/sheet1.xml",
      text: `${X}<worksheet xmlns="${NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${widths}</cols><sheetData>${head}${body}</sheetData></worksheet>`,
    },
  ];
  return zipStore(files.map((f) => ({ name: f.name, data: enc.encode(f.text) })));
}

// ------------------------------------------------------------- Markdown / print

const mdEsc = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");

export function answerToMarkdown(
  question: string,
  r: ExportableResponse,
  mode: ClientExportMode = "aggregates",
): string {
  const lines = [`# ${question.replace(/\n/g, " ")}`, "", plainText(r.answer)];
  const pol = clientExportPolicy(r, mode);
  if (pol.table && r.table) {
    const cols = r.table.columns;
    lines.push("", `| ${cols.map(mdEsc).join(" | ")} |`, `| ${cols.map(() => "---").join(" | ")} |`);
    for (const row of r.table.rows.slice(0, 200)) lines.push(`| ${cols.map((c) => mdEsc(String(row[c] ?? ""))).join(" | ")} |`);
  }
  if (r.citations.length) {
    lines.push("", "## Sources");
    for (const c of r.citations) {
      const label = [c.title, c.section && c.section !== c.title ? c.section : null].filter(Boolean).join(" - ") || c.url || "Source";
      lines.push(`${c.index}. ${c.url ? `[${label}](${c.url})` : label}`);
    }
  }
  lines.push("", `_Exported from WebMind on ${new Date().toISOString().slice(0, 10)}._`);
  return lines.join("\n");
}

const htmlEsc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Self-contained, fully escaped HTML report used for "Save as PDF" (browser print dialog). */
export function buildPrintHtml(
  question: string,
  r: ExportableResponse,
  svgMarkup: string | null,
  mode: ClientExportMode = "aggregates",
): string {
  const pol = clientExportPolicy(r, mode);
  const paras = plainText(r.answer)
    .split(/\n{2,}/)
    .map((p) => `<p>${htmlEsc(p).replace(/\n/g, "<br>")}</p>`)
    .join("");
  let table = "";
  if (pol.table && r.table) {
    const cols = r.table.columns;
    table = `<table><thead><tr>${cols.map((c) => `<th>${htmlEsc(c)}</th>`).join("")}</tr></thead><tbody>${r.table.rows
      .slice(0, 200)
      .map((row) => `<tr>${cols.map((c) => `<td>${htmlEsc(String(row[c] ?? ""))}</td>`).join("")}</tr>`)
      .join("")}</tbody></table>`;
  }
  const sources = r.citations.length
    ? `<h2>Sources</h2><ol>${r.citations
        .map((c) => `<li>${htmlEsc([c.title, c.section].filter(Boolean).join(" - ") || "Source")}${c.url ? ` <small>${htmlEsc(c.url)}</small>` : ""}</li>`)
        .join("")}</ol>`
    : "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>${htmlEsc(question)}</title><style>
  body{font:14px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#1c1c1e;max-width:720px;margin:32px auto;padding:0 20px}
  h1{font-size:20px;margin:0 0 16px}h2{font-size:15px;margin:24px 0 8px}p{margin:0 0 10px}
  table{border-collapse:collapse;width:100%;font-size:12px;margin:12px 0}th,td{border:1px solid #d1d1d6;padding:4px 8px;text-align:left}th{background:#f2f2f7}
  svg{max-width:100%;height:auto;margin:8px 0;color:#1c1c1e}small{color:#6c6c70}footer{margin-top:28px;color:#8e8e93;font-size:11px}
  </style></head><body><h1>${htmlEsc(question)}</h1>${paras}${pol.chart && svgMarkup ? svgMarkup : ""}${table}${sources}<footer>Exported from WebMind on ${new Date().toISOString().slice(0, 10)}</footer></body></html>`;
}

// --------------------------------------------------------------- browser helpers

export function safeFileName(s: string, ext: string): string {
  const base =
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "webmind";
  return `${base}.${ext}`;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const MIME = {
  csv: "text/csv;charset=utf-8",
  json: "application/json",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  md: "text/markdown;charset=utf-8",
  svg: "image/svg+xml;charset=utf-8",
  png: "image/png",
};

/** Standalone SVG text for a rendered chart: light background and explicit text color so it reads anywhere. */
export function svgToStandalone(svg: SVGSVGElement, background = "#ffffff", color = "#1c1c1e"): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("style", `color:${color};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif`);
  const vb = (clone.getAttribute("viewBox") ?? "0 0 372 224").split(/\s+/).map(Number);
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("x", "0");
  bg.setAttribute("y", "0");
  bg.setAttribute("width", String(vb[2]));
  bg.setAttribute("height", String(vb[3]));
  bg.setAttribute("fill", background);
  clone.insertBefore(bg, clone.firstChild);
  clone.querySelectorAll("[data-wm-tip]").forEach((n) => n.removeAttribute("data-wm-tip"));
  return new XMLSerializer().serializeToString(clone);
}

export function svgToPng(svg: SVGSVGElement, scale = 2): Promise<Blob> {
  const markup = svgToStandalone(svg);
  const vb = (svg.getAttribute("viewBox") ?? "0 0 372 224").split(/\s+/).map(Number);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(vb[2] * scale);
      canvas.height = Math.round(vb[3] * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("canvas unavailable"));
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encoding failed"))), "image/png");
    };
    img.onerror = () => reject(new Error("could not rasterize chart"));
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
  });
}

/** Open the browser's print dialog (Save as PDF) for an escaped report, using a hidden same-origin iframe. */
export function printReport(html: string): void {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  if (!doc || !frame.contentWindow) {
    frame.remove();
    return;
  }
  doc.open();
  doc.write(html);
  doc.close();
  const win = frame.contentWindow;
  setTimeout(() => {
    win.focus();
    win.print();
    setTimeout(() => frame.remove(), 2000);
  }, 150);
}
