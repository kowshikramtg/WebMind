// Server-renders the REAL React components (react-dom/server) with a pre-populated conversation and checks
// the markup: chart marks, table, sources, citation chips, export menus and XSS safety. Run: npm run build && npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WebMindChat, WebMindFloating, WebMindProvider } from "../dist/index.js";

const chart = {
  type: "bar",
  title: "count_records by department",
  x_key: "department",
  series: [{ key: "count_records", label: "Count records" }],
  data: [
    { department: "admissions", count_records: 23 },
    { department: "engineering", count_records: 24 },
    { department: "finance", count_records: 42 },
    { department: "research", count_records: 31 },
  ],
};
const response = (over = {}) => ({
  answer: "The highest is **Finance** (42) [1]. Visit https://example.org/a.\n\n- first\n- second",
  intent: "visualization",
  conversation_id: "c1",
  citations: [{ index: 1, title: "Projects", section: "Overview", url: "https://example.org/p", snippet: "Finance has 42 projects.", freshness: { mode: "indexed", last_synced_at: "2026-11-19T10:00:00Z" }, version: 1 }],
  navigation: [],
  table: { columns: ["department", "count_records"], rows: chart.data, total_rows: 4, truncated: false, dataset: "projects", applied_filters: ["tenant = default"] },
  chart,
  export: null,
  statistics: null,
  tools: [{ tool: "generate_chart", status: "ok", arguments: {} }],
  grounded: true,
  llm_used: true,
  warnings: [],
  ...over,
});
const render = (turns, props = {}) =>
  renderToStaticMarkup(h(WebMindProvider, { endpoint: "/x", initialTurns: turns }, h(WebMindChat, props)));
const turn = (r, q = "chart please") => ({ id: "t1", question: q, response: r, pending: false });
const count = (html, re) => (html.match(re) ?? []).length;

test("empty state shows the greeting and suggestion chips", () => {
  const html = render([], { suggestions: ["One?", "Two?"] });
  assert.match(html, /How can I help\?/);
  assert.equal(count(html, /class="wm-chip"/g), 2);
  assert.match(html, /data-theme="auto"/);
});

test("a chart answer renders bubble, SVG marks with values, export menu, sources and actions", () => {
  const html = render([turn(response())]);
  assert.match(html, /class="wm-bubble">chart please/); // user bubble
  assert.match(html, /<strong>Finance<\/strong>/);
  assert.equal(count(html, /<li>/g), 2);
  assert.equal(count(html, /data-wm-chart="bar"/g), 1);
  assert.equal(count(html, /data-wm-mark="bar"/g), 4);
  assert.match(html, /finance · Count records: 42/);
  assert.match(html, />Count records by department</); // pretty card title
  assert.match(html, /class="wm-btn-sm"[^>]*>.*Export/); // export menu trigger
  assert.match(html, /1 source/); // sources toggle
  assert.match(html, /class="wm-cite"[^>]*>1</); // inline [1] became a chip
  assert.match(html, /class="wm-act"[^>]*>.*Copy/);
  assert.doesNotMatch(html, /<script/i);
});

test("an aggregate table renders formatted rows; raw records offer the server export instead of client downloads", () => {
  const stats = response({ chart: null, intent: "statistics", tools: [{ tool: "calculate_statistics", status: "ok", arguments: {} }], table: { columns: ["department", "avg_budget"], rows: [{ department: "a", avg_budget: 58866.17 }, { department: "b", avg_budget: 60813.7 }], total_rows: 2, truncated: false, dataset: "projects", applied_filters: [] } });
  const html = render([turn(stats)]);
  assert.match(html, /58,866\.17/);
  assert.match(html, /60,813\.70/); // consistent decimals within a column
  assert.match(html, /class="wm-num"/);
  const rawHtml = render([turn(response({ chart: null, intent: "records", tools: [{ tool: "query_records", status: "ok", arguments: {} }] }))]);
  assert.match(rawHtml, /Export/); // the menu exists; its items are server-side only (policy tested in exports.test)
  assert.equal(render([turn(response({ chart: null }))], { clientExports: "off" }).includes("wm-card-head") && render([turn(response())], { clientExports: "off" }).includes("Export"), false);
});

test("server-provided text can never inject markup or javascript: links", () => {
  const evil = response({ answer: '<img src=x onerror="alert(1)"> [link](javascript:alert(1)) javascript:alert(2) https://ok.example/', citations: [{ index: 1, title: '<script>alert(3)</script>', section: null, url: "javascript:alert(4)", snippet: "<b>x</b>", freshness: { mode: "indexed" } }], chart: null, table: null });
  const html = render([turn(evil, '<script>alert("q")</script>')]);
  assert.doesNotMatch(html, /<script/i);
  assert.doesNotMatch(html, /<img/i);
  assert.doesNotMatch(html, /href="javascript:/i);
  assert.match(html, /&lt;img src=x onerror=/);
  assert.match(html, /href="https:\/\/ok\.example\/"/); // legitimate links still work
});

test("an errored turn offers retry; a pending turn shows the typing indicator", () => {
  assert.match(render([{ id: "e", question: "q", error: "The assistant is unavailable (503).", pending: false }]), /wm-error[\s\S]*Try again/);
  assert.match(render([{ id: "p", question: "q", pending: true }]), /wm-typing/);
  assert.match(render([{ id: "s", question: "q", partial: "Hello **wor", pending: true }]), /wm-caret/);
});

test("theme and accent are applied; the floating variant renders a launcher", () => {
  const html = render([], { theme: "dark", accent: "#0a7d5a" });
  assert.match(html, /data-theme="dark"/);
  assert.match(html, /--wm-accent:#0a7d5a/);
  const floating = renderToStaticMarkup(h(WebMindProvider, { endpoint: "/x" }, h(WebMindFloating, { side: "left" })));
  assert.match(floating, /class="wm wm-float"[^>]*data-side="left"/);
  assert.match(floating, /class="wm-launcher"/);
});
