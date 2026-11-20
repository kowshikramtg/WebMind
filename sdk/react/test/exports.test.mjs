import assert from "node:assert/strict";
import { test } from "node:test";
import {
  answerToMarkdown,
  buildPrintHtml,
  chartToTable,
  clientExportPolicy,
  crc32,
  safeCell,
  safeFileName,
  sheetName,
  tableToXlsx,
  toCsv,
  toJson,
} from "../dist/core/exports.js";

const aggregate = {
  answer: "Finance has the most projects [1].",
  intent: "statistics",
  citations: [{ index: 1, title: "Projects", section: null, url: "https://x.example/p" }],
  tools: [{ tool: "calculate_statistics" }],
  table: { columns: ["department", "count_records"], rows: [{ department: "finance", count_records: 42 }], dataset: "projects", applied_filters: [] },
  chart: { type: "bar", title: "count_records by department", x_key: "department", series: [{ key: "count_records", label: "Count records" }], data: [{ department: "finance", count_records: 42 }] },
};
const raw = { ...aggregate, intent: "records", tools: [{ tool: "query_records" }], chart: null };

test("CSV quotes properly and neutralizes spreadsheet formulas", () => {
  const csv = toCsv(["name", "note"], [{ name: 'He said "hi", ok', note: "=HYPERLINK(\"http://evil\")" }, { name: "+1", note: "line1\nline2" }, { name: -5, note: null }]);
  const lines = csv.split("\r\n");
  assert.equal(lines[0], "name,note");
  assert.equal(lines[1], '"He said ""hi"", ok","\'=HYPERLINK(""http://evil"")"');
  assert.ok(lines[2].startsWith("'+1,")); // text cell starting with + is neutralized
  assert.ok(csv.includes('"line1\nline2"'));
  assert.ok(csv.endsWith("-5,")); // real numbers are untouched
  assert.equal(safeCell("@SUM(A1)"), "'@SUM(A1)");
  assert.equal(safeCell(-5), -5);
});

test("JSON export carries rows and metadata", () => {
  const j = JSON.parse(toJson(["a"], [{ a: 1 }], { dataset: "d" }));
  assert.deepEqual(j, { dataset: "d", columns: ["a"], row_count: 1, rows: [{ a: 1 }] });
});

function readZip(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  assert.ok(eocd >= 0, "end-of-central-directory record");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const files = {};
  for (let n = 0; n < count; n++) {
    assert.equal(dv.getUint32(p, true), 0x02014b50);
    const crc = dv.getUint32(p + 16, true);
    const size = dv.getUint32(p + 24, true);
    const nameLen = dv.getUint16(p + 28, true);
    const offset = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(bytes.slice(p + 46, p + 46 + nameLen));
    assert.equal(dv.getUint32(offset, true), 0x04034b50);
    const start = offset + 30 + dv.getUint16(offset + 26, true) + dv.getUint16(offset + 28, true);
    const data = bytes.slice(start, start + size);
    assert.equal(crc32(data), crc, `CRC of ${name}`);
    files[name] = new TextDecoder().decode(data);
    p += 46 + nameLen;
  }
  return files;
}

test("XLSX is a structurally valid OOXML package with correct CRCs and values", () => {
  const files = readZip(tableToXlsx(["department", "avg_budget", "note"], [{ department: "a<b & \"c\"", avg_budget: 58866.17, note: "=1+1" }, { department: "d", avg_budget: 3, note: "x" }], "projects"));
  for (const need of ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"]) assert.ok(need in files, need);
  const sheet = files["xl/worksheets/sheet1.xml"];
  assert.ok(sheet.includes("<v>58866.17</v>")); // numbers stay numeric
  assert.ok(sheet.includes("a&lt;b &amp; &quot;c&quot;")); // XML-escaped text
  assert.ok(sheet.includes("'=1+1")); // formula injection neutralized in Excel too
  assert.ok(files["xl/styles.xml"].includes('<cellStyle name="Normal"'));
  assert.ok(files["xl/workbook.xml"].includes('name="projects"'));
  assert.equal(sheetName("a/b:c*d?e[f]"), "a b c d e f");
  assert.equal(sheetName("x".repeat(50)).length, 31);
});

test("chart data converts to a table with series labels as headers", () => {
  const t = chartToTable(aggregate.chart);
  assert.deepEqual(t.columns, ["department", "Count records"]);
  assert.deepEqual(t.rows, [{ department: "finance", "Count records": 42 }]);
});

test("client export policy: aggregates yes, raw records only through the server", () => {
  assert.deepEqual(clientExportPolicy(aggregate, "aggregates"), { table: true, chart: true, answer: true, serverExport: false });
  const r = clientExportPolicy(raw, "aggregates");
  assert.equal(r.table, false); // raw rows are never downloadable client-side by default
  assert.equal(r.serverExport, true); // the permission-checked server export is offered instead
  assert.equal(clientExportPolicy(raw, "all").table, true); // an integrator can opt in
  const off = clientExportPolicy(aggregate, "off");
  assert.deepEqual([off.table, off.chart, off.answer], [false, false, false]);
  for (const intent of ["export", "report"]) assert.equal(clientExportPolicy({ ...aggregate, intent, tools: [] }, "aggregates").table, false);
});

test("Markdown export includes the table only when policy allows, and always the sources", () => {
  const md = answerToMarkdown("What is the max?", aggregate);
  assert.ok(md.startsWith("# What is the max?"));
  assert.ok(md.includes("| department | count_records |") && md.includes("| finance | 42 |"));
  assert.ok(md.includes("## Sources") && md.includes("[Projects](https://x.example/p)"));
  assert.ok(!answerToMarkdown("q", raw).includes("| finance |"));
});

test("print/PDF report escapes everything", () => {
  const evil = { ...aggregate, answer: "<script>alert(1)</script> & <img src=x onerror=1>", citations: [{ index: 1, title: "<b>t</b>", url: "https://x.example/?a=1&b=2" }] };
  const html = buildPrintHtml("<q>", evil, "<svg></svg>");
  assert.ok(!html.includes("<script>alert(1)</script>"));
  assert.ok(!html.includes("<img src=x"));
  assert.ok(html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
  assert.ok(html.includes("&lt;b&gt;t&lt;/b&gt;"));
  assert.ok(html.includes("<svg></svg>")); // chart markup is our own serialized SVG, passed in separately
});

test("file names are safe", () => {
  assert.equal(safeFileName("Count records / by department!", "csv"), "count-records-by-department.csv");
  assert.equal(safeFileName("../../etc/passwd", "md"), "etc-passwd.md");
  assert.equal(safeFileName("", "png"), "webmind.png");
});
