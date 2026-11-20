// Dev tool: server-render the React components with a sample conversation into a standalone HTML file, so the
// look can be checked in a real browser without a bundler:   node scripts/ssr-snapshot.mjs out.html [light|dark]
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WebMindChat, WebMindProvider } from "../dist/index.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const [out = "snapshot.html", theme = "light"] = process.argv.slice(2);
const chart = {
  type: "bar",
  title: "count_records by department",
  x_key: "department",
  x_label: "Department",
  series: [{ key: "count_records", label: "Count records" }],
  data: [23, 24, 42, 31].map((v, i) => ({ department: ["admissions", "engineering", "finance", "research"][i], count_records: v })),
};
const turns = [
  {
    id: "1",
    question: "Generate a bar chart showing the number of projects by department.",
    pending: false,
    response: {
      answer: "Here are the results:\n\n- Admissions: 23\n- Engineering: 24\n- Finance: 42\n- Research: 31\n\nThe highest number of projects is 42 (**Finance**) [1].",
      intent: "visualization",
      conversation_id: "c",
      citations: [{ index: 1, title: "Projects dataset", section: "Overview", url: "https://example.org/projects", snippet: "Institute projects with department, status, creation date and budget.", freshness: { mode: "indexed", last_synced_at: "2026-11-19T10:00:00Z" }, version: 1 }],
      navigation: [],
      table: null,
      chart,
      export: null,
      statistics: null,
      tools: [{ tool: "generate_chart", status: "ok", arguments: {} }],
      grounded: true,
      llm_used: true,
      warnings: [],
    },
  },
  {
    id: "2",
    question: "What is the average budget per department?",
    pending: false,
    response: {
      answer: "Here is the average budget per department.",
      intent: "statistics",
      conversation_id: "c",
      citations: [],
      navigation: [],
      table: {
        columns: ["department", "avg_budget"],
        rows: [["admissions", 58866.17], ["engineering", 57649.32], ["finance", 70653.17], ["research", 60813.7]].map(([d, v]) => ({ department: d, avg_budget: v })),
        total_rows: 4,
        truncated: false,
        dataset: "projects",
        applied_filters: [],
      },
      chart: null,
      export: null,
      statistics: null,
      tools: [{ tool: "calculate_statistics", status: "ok", arguments: {} }],
      grounded: true,
      llm_used: true,
      warnings: [],
    },
  },
];
const markup = renderToStaticMarkup(h(WebMindProvider, { endpoint: "/x", initialTurns: turns }, h(WebMindChat, { theme, title: "Ask Lumen Valley", suggestions: [] })));
const css = readFileSync(join(root, "dist", "webmind.css"), "utf8");
const bg = theme === "dark" ? "#0b0b0d" : "#e9ebf2";
writeFileSync(out, `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>@webmind/react snapshot</title><style>${css}\nbody{margin:0;background:${bg};display:grid;place-items:center;min-height:100vh;font-family:system-ui}.frame{width:404px;height:960px}*{animation:none!important;transition:none!important}</style></head><body><div class="frame">${markup}</div></body></html>`);
console.log("wrote", out);
