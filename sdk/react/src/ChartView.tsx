import type { ChartSpec } from "./types.js";

const PALETTE = ["#3346d3", "#e0663a", "#1f9e74", "#b5487f", "#8a6d1d", "#5577aa", "#c03b3b", "#2b8a9e"];

export interface ChartViewProps {
  spec: ChartSpec;
  width?: number;
  height?: number;
}

/**
 * Dependency-free SVG renderer for WebMind chart specs (bar, histogram, line, area,
 * scatter, pie). The spec is renderer-agnostic, so you can also feed `spec.data` and
 * `spec.series` straight into Recharts, Plotly, Chart.js, etc.
 */
export function ChartView({ spec, width = 520, height = 260 }: ChartViewProps) {
  const pad = { l: 48, r: 12, t: 12, b: 36 };
  const data = spec.data.slice(0, 60);
  const series = spec.series;
  if (!data.length || !series.length) return <p className="wm-empty">No data to chart.</p>;

  if (spec.type === "pie") {
    const key = series[0].key;
    const total = data.reduce((s, d) => s + (Number(d[key]) || 0), 0) || 1;
    const r = Math.min(width, height) / 2 - 10;
    const cx = width / 2;
    const cy = height / 2;
    let angle = -Math.PI / 2;
    return (
      <figure className="wm-chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
          {data.map((d, i) => {
            const frac = (Number(d[key]) || 0) / total;
            const a0 = angle;
            const a1 = (angle += frac * Math.PI * 2);
            const large = a1 - a0 > Math.PI ? 1 : 0;
            const path = `M${cx},${cy} L${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)} Z`;
            return (
              <path key={i} d={path} fill={PALETTE[i % PALETTE.length]}>
                <title>{`${String(d[spec.x_key])}: ${String(d[key])} (${(frac * 100).toFixed(1)}%)`}</title>
              </path>
            );
          })}
        </svg>
        <figcaption>{spec.title}</figcaption>
      </figure>
    );
  }

  const values = data.flatMap((d) => series.map((s) => Number(d[s.key]) || 0));
  const max = Math.max(0, ...values) || 1;
  const min = Math.min(0, ...values);
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const step = innerW / data.length;
  const y = (v: number) => pad.t + innerH - ((v - min) / (max - min || 1)) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + f * (max - min));

  return (
    <figure className="wm-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={spec.title}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="currentColor" strokeOpacity={0.12} />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={10} fill="currentColor">
              {Intl.NumberFormat(undefined, { notation: "compact" }).format(t)}
            </text>
          </g>
        ))}
        {spec.type === "line" || spec.type === "area" || spec.type === "scatter"
          ? series.map((s, si) => {
              const pts = data.map((d, i) => [pad.l + step * i + step / 2, y(Number(d[s.key]) || 0)] as const);
              const color = PALETTE[si % PALETTE.length];
              if (spec.type === "scatter")
                return pts.map(([px, py], i) => <circle key={`${si}-${i}`} cx={px} cy={py} r={3} fill={color} />);
              const line = pts.map((p) => p.join(",")).join(" ");
              return (
                <g key={s.key}>
                  {spec.type === "area" && (
                    <polygon
                      points={`${pts[0][0]},${y(min)} ${line} ${pts[pts.length - 1][0]},${y(min)}`}
                      fill={color}
                      fillOpacity={0.15}
                    />
                  )}
                  <polyline points={line} fill="none" stroke={color} strokeWidth={2} />
                </g>
              );
            })
          : data.map((d, i) =>
              series.map((s, si) => {
                const bw = (step * 0.8) / series.length;
                const v = Number(d[s.key]) || 0;
                return (
                  <rect
                    key={`${i}-${si}`}
                    x={pad.l + step * i + step * 0.1 + bw * si}
                    y={Math.min(y(v), y(0))}
                    width={Math.max(bw - 1, 1)}
                    height={Math.abs(y(0) - y(v))}
                    fill={PALETTE[si % PALETTE.length]}
                  >
                    <title>{`${String(d[spec.x_key])} · ${s.label}: ${v}`}</title>
                  </rect>
                );
              }),
            )}
        {data.map((d, i) =>
          data.length <= 16 || i % Math.ceil(data.length / 16) === 0 ? (
            <text key={i} x={pad.l + step * i + step / 2} y={height - pad.b + 14} textAnchor="middle" fontSize={10} fill="currentColor">
              {String(d[spec.x_key]).slice(0, 10)}
            </text>
          ) : null,
        )}
      </svg>
      <figcaption>
        {spec.title}
        {series.length > 1 && (
          <span className="wm-legend">
            {series.map((s, i) => (
              <span key={s.key} style={{ color: PALETTE[i % PALETTE.length] }}>
                {" "}■ {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>
    </figure>
  );
}
