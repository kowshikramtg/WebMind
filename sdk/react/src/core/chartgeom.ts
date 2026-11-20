/**
 * Renderer-agnostic chart layout.
 *
 * Both the React <ChartView> and the dependency-free widget turn the same layout into SVG, so the two
 * surfaces always draw identical charts. All styling uses SVG presentation attributes (no CSS classes),
 * which keeps exported SVG/PNG files self-contained.
 */
export interface ChartSeriesSpec {
  key: string;
  label: string;
}
export interface ChartSpecLike {
  type: "bar" | "line" | "area" | "pie" | "scatter" | "histogram" | "table";
  title: string;
  x_key: string;
  x_label?: string | null;
  y_label?: string | null;
  series: ChartSeriesSpec[];
  data: Array<Record<string, unknown>>;
}

export interface Mark {
  kind: "bar" | "point" | "slice";
  color: string;
  label: string; // tooltip title (category)
  series: string;
  value: number;
  text: string; // formatted tooltip body
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  r?: number;
  d?: string;
  /** Tooltip anchor in viewBox units. */
  ax: number;
  ay: number;
}
export interface LinePath {
  points: string;
  area?: string;
  color: string;
}
export interface ChartLayout {
  width: number;
  height: number;
  type: ChartSpecLike["type"];
  empty: boolean;
  marks: Mark[];
  lines: LinePath[];
  yTicks: Array<{ y: number; label: string }>;
  xLabels: Array<{ x: number; label: string }>;
  legend: Array<{ label: string; color: string }>;
  plot: { l: number; r: number; t: number; b: number };
  baseline: number;
  xAxisLabel?: string;
}

export const PALETTE = ["#4f5fd9", "#f08c4a", "#2eaa8a", "#b0508f", "#8f9340", "#5b8bc0", "#c25555", "#3fa3b5"];

export function fmtCompact(v: number): string {
  if (!Number.isFinite(v)) return "";
  const a = Math.abs(v);
  if (a >= 1_000_000_000) return trim(v / 1_000_000_000) + "B";
  if (a >= 1_000_000) return trim(v / 1_000_000) + "M";
  if (a >= 10_000) return trim(v / 1_000) + "K";
  return Number.isInteger(v) ? String(v) : trim(v);
}
const trim = (n: number) => String(Math.round(n * 10) / 10);

export function fmtFull(v: number): string {
  return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** Decimals to show for a numeric table column: 0 for whole numbers, otherwise 2 (so "60,813.7" never appears next to "58,866.17"). */
export function columnDecimals(rows: Array<Record<string, unknown>>, col: string): number {
  return rows.some((r) => typeof r[col] === "number" && !Number.isInteger(r[col] as number)) ? 2 : 0;
}

export function fmtCell(v: unknown, decimals: number): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number" && Number.isFinite(v)) return v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return String(v);
}

export function truncateLabel(s: string, n = 12): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

/** Round a maximum up to a "nice" value so axis ticks read cleanly. */
export function niceMax(max: number): number {
  return niceScale(max).top;
}

/** Clean axis scale: the smallest "1/2/5 x 10^n" step that needs at most `maxIntervals` gridlines. */
export function niceScale(max: number, maxIntervals = 5): { step: number; top: number; intervals: number } {
  if (!(max > 0)) return { step: 1, top: 1, intervals: 1 };
  const pow = Math.pow(10, Math.floor(Math.log10(max / maxIntervals)));
  for (const f of [1, 2, 2.5, 5, 10, 20, 25, 50]) {
    const step = f * pow;
    const intervals = Math.ceil(max / step - 1e-9);
    if (intervals <= maxIntervals) return { step, top: step * intervals, intervals };
  }
  return { step: max, top: max, intervals: 1 };
}

/** Sentence-case a metric/dataset alias for display: "count_records by department" -> "Count records by department". */
export function prettyTitle(s: string): string {
  const t = s.replace(/_/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

export function computeChartLayout(spec: ChartSpecLike, width = 372, height = 224): ChartLayout {
  const plot = { l: 44, r: 10, t: 12, b: 36 };
  const base: ChartLayout = {
    width,
    height,
    type: spec.type,
    empty: true,
    marks: [],
    lines: [],
    yTicks: [],
    xLabels: [],
    legend: [],
    plot,
    baseline: height - plot.b,
    xAxisLabel: spec.x_label ?? undefined,
  };
  const data = spec.data.slice(0, 60);
  const series = spec.series;
  if (!data.length || !series.length || spec.type === "table") return base;
  const cat = (d: Record<string, unknown>) => String(d[spec.x_key] ?? "");
  const num = (d: Record<string, unknown>, k: string) => {
    const n = Number(d[k]);
    return Number.isFinite(n) ? n : 0;
  };

  if (spec.type === "pie") {
    const key = series[0].key;
    const total = data.reduce((s, d) => s + Math.max(num(d, key), 0), 0) || 1;
    const cx = width / 2;
    const cy = height / 2;
    const ro = Math.min(width, height) / 2 - 14;
    const ri = ro * 0.58;
    let a = -Math.PI / 2;
    data.forEach((d, i) => {
      const v = Math.max(num(d, key), 0);
      const frac = v / total;
      const a0 = a;
      a += frac * Math.PI * 2;
      const a1 = frac >= 0.9999 ? a0 + Math.PI * 2 - 0.0001 : a;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const pt = (r: number, ang: number) => `${(cx + r * Math.cos(ang)).toFixed(2)},${(cy + r * Math.sin(ang)).toFixed(2)}`;
      const dPath = `M${pt(ro, a0)} A${ro},${ro} 0 ${large} 1 ${pt(ro, a1)} L${pt(ri, a1)} A${ri},${ri} 0 ${large} 0 ${pt(ri, a0)} Z`;
      const mid = (a0 + a1) / 2;
      const rm = (ro + ri) / 2;
      base.marks.push({
        kind: "slice",
        d: dPath,
        ax: cx + rm * Math.cos(mid),
        ay: cy + rm * Math.sin(mid),
        color: PALETTE[i % PALETTE.length],
        label: cat(d),
        series: series[0].label,
        value: v,
        text: `${fmtFull(v)} (${(frac * 100).toFixed(1)}%)`,
      });
      base.legend.push({ label: `${cat(d)} (${(frac * 100).toFixed(0)}%)`, color: PALETTE[i % PALETTE.length] });
    });
    return { ...base, empty: false };
  }

  let max = 0;
  let min = 0;
  for (const d of data) for (const s of series) {
    const v = num(d, s.key);
    if (v > max) max = v;
    if (v < min) min = v;
  }
  const scale = niceScale(max);
  const top = scale.top;
  const bottom = min < 0 ? -niceScale(-min).top : 0;
  const iw = width - plot.l - plot.r;
  const ih = height - plot.t - plot.b;
  const step = iw / data.length;
  const y = (v: number) => plot.t + ih - ((v - bottom) / (top - bottom || 1)) * ih;
  const baseline = y(0);
  base.baseline = baseline;

  const ticks = Math.round((top - bottom) / scale.step);
  for (let i = 0; i <= ticks; i++) {
    const v = bottom + scale.step * i;
    base.yTicks.push({ y: y(v), label: fmtCompact(v) });
  }

  const asLine = spec.type === "line" || spec.type === "area" || spec.type === "scatter";
  if (asLine) {
    series.forEach((s, si) => {
      const color = PALETTE[si % PALETTE.length];
      const pts = data.map((d, j) => ({ x: plot.l + step * j + step / 2, y: y(num(d, s.key)), d }));
      if (spec.type !== "scatter") {
        const points = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
        const line: LinePath = { points, color };
        if (spec.type === "area") {
          line.area = `${pts[0].x.toFixed(1)},${baseline.toFixed(1)} ${points} ${pts[pts.length - 1].x.toFixed(1)},${baseline.toFixed(1)}`;
        }
        base.lines.push(line);
      }
      for (const p of pts) {
        base.marks.push({
          kind: "point",
          x: p.x,
          y: p.y,
          ax: p.x,
          ay: p.y,
          r: spec.type === "scatter" ? 4 : 3,
          color,
          label: cat(p.d),
          series: s.label,
          value: num(p.d, s.key),
          text: fmtFull(num(p.d, s.key)),
        });
      }
    });
  } else {
    const hist = spec.type === "histogram";
    const group = step * (hist ? 0.96 : 0.72);
    const bw = group / series.length;
    data.forEach((d, j) => {
      series.forEach((s, si) => {
        const v = num(d, s.key);
        const x0 = plot.l + step * j + (step - group) / 2 + bw * si;
        const yy = y(Math.max(v, 0));
        base.marks.push({
          kind: "bar",
          ax: x0 + Math.max(bw - (hist ? 1 : 2), 1) / 2,
          ay: Math.min(yy, baseline),
          x: x0,
          y: Math.min(yy, baseline),
          w: Math.max(bw - (hist ? 1 : 2), 1),
          h: Math.max(Math.abs(baseline - y(v)), v === 0 ? 0 : 1),
          color: PALETTE[si % PALETTE.length],
          label: cat(d),
          series: s.label,
          value: v,
          text: fmtFull(v),
        });
      });
    });
  }

  const every = Math.max(1, Math.ceil(data.length / 7));
  data.forEach((d, j) => {
    if (j % every === 0) base.xLabels.push({ x: plot.l + step * j + step / 2, label: truncateLabel(cat(d)) });
  });
  series.forEach((s, si) => base.legend.push({ label: s.label, color: PALETTE[si % PALETTE.length] }));
  return { ...base, empty: false };
}
