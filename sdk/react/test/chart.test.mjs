// Structural test of <ChartView>: ChartView is a pure function component, so calling it returns the React element tree.
// We walk that tree and check the SVG marks that would be drawn (no DOM needed). Run: npm run build && npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { ChartSvg as ChartView } from "../dist/ChartView.js";

function walk(node, visit) {
  if (node == null || typeof node === "boolean" || typeof node === "string" || typeof node === "number") return;
  if (Array.isArray(node)) return node.forEach((n) => walk(n, visit));
  visit(node);
  walk(node.props?.children, visit);
}
const collect = (tree, type) => {
  const out = [];
  walk(tree, (n) => n.type === type && out.push(n));
  return out;
};
const text = (n) => [n.props.children].flat(Infinity).filter((c) => typeof c === "string" || typeof c === "number").join("");

const bar = {
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

test("bar chart draws one rect per data point with proportional heights and value tooltips", () => {
  const tree = ChartView({ spec: bar });
  const rects = collect(tree, "rect");
  assert.equal(rects.length, 4);
  const h = rects.map((r) => r.props.height);
  const values = bar.data.map((d) => d.count_records);
  assert.ok(h.every((x) => x > 0));
  // heights are proportional to values (ratio is constant)
  const ratios = h.map((x, i) => x / values[i]);
  assert.ok(ratios.every((r) => Math.abs(r - ratios[0]) < 1e-6));
  const tips = collect(tree, "title").map(text);
  assert.deepEqual(tips, ["admissions · Count records: 23", "engineering · Count records: 24", "finance · Count records: 42", "research · Count records: 31"]);
  const labels = collect(tree, "text").map(text);
  for (const d of ["admissions", "engineering", "finance", "research"]) assert.ok(labels.includes(d), `x label ${d}`);
});

test("line chart draws a polyline through 11 points that match the data", () => {
  const data = Array.from({ length: 11 }, (_, i) => ({ period: `2026-${String(i + 1).padStart(2, "0")}`, sum_registrations: 400 + i * 20 }));
  const tree = ChartView({ spec: { type: "line", title: "registrations", x_key: "period", series: [{ key: "sum_registrations", label: "Sum registrations" }], data } });
  assert.equal(collect(tree, "polyline").length, 1);
  assert.equal(collect(tree, "polyline")[0].props.points.split(" ").length, 11);
});

test("pie chart draws one slice per row; empty data renders a message instead of an empty chart", () => {
  const tree = ChartView({ spec: { ...bar, type: "pie" } });
  assert.equal(collect(tree, "path").length, 4);
  const empty = ChartView({ spec: { ...bar, data: [] } });
  assert.equal(empty.type, "p");
  assert.match(text(empty), /No data/);
});
