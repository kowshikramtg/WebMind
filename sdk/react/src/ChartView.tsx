import { useRef, useState, type Ref } from "react";
import { computeChartLayout, type ChartLayout, type ChartSpecLike, type Mark } from "./core/chartgeom.js";

export interface ChartViewProps {
  spec: ChartSpecLike;
  width?: number;
  height?: number;
  /** Receives the rendered <svg> (used for PNG/SVG export). */
  svgRef?: Ref<SVGSVGElement>;
  /** Hide the legend (the card renders its own). */
  hideLegend?: boolean;
}

interface SvgProps extends ChartViewProps {
  onMark?: (mark: Mark | null, layout: ChartLayout) => void;
}

const tipText = (m: Mark) => `${m.label} · ${m.series}: ${m.text}`;

/**
 * Pure SVG chart (no hooks, no DOM access) drawn from the shared layout, so the widget and the React
 * SDK always render identical charts. Use <ChartView> for the interactive version with tooltips.
 */
export function ChartSvg({ spec, width, height, svgRef, onMark }: SvgProps) {
  const L = computeChartLayout(spec, width, height);
  if (L.empty) return <p className="wm-empty-chart">No data to chart.</p>;
  const { plot } = L;
  const markProps = (m: Mark) => ({
    "data-wm-mark": m.kind,
    tabIndex: 0,
    role: "img" as const,
    "aria-label": tipText(m),
    onMouseEnter: () => onMark?.(m, L),
    onFocus: () => onMark?.(m, L),
    onMouseLeave: () => onMark?.(null, L),
    onBlur: () => onMark?.(null, L),
  });
  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${L.width} ${L.height}`}
      role="img"
      aria-label={spec.title}
      data-wm-chart={spec.type}
      fontFamily="inherit"
    >
      {L.type !== "pie" &&
        L.yTicks.map((t, i) => (
          <g key={i}>
            <line x1={plot.l} x2={L.width - plot.r} y1={t.y} y2={t.y} stroke="currentColor" strokeOpacity={0.16} />
            <text x={plot.l - 7} y={t.y + 3.5} textAnchor="end" fontSize={10} fill="currentColor">
              {t.label}
            </text>
          </g>
        ))}
      {L.lines.map((ln, i) => (
        <g key={i}>
          {ln.area && <polygon points={ln.area} fill={ln.color} fillOpacity={0.13} />}
          <polyline points={ln.points} fill="none" stroke={ln.color} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" data-wm-mark="line" />
        </g>
      ))}
      {L.marks.map((m, i) => {
        const common = markProps(m);
        if (m.kind === "bar")
          return (
            <rect key={i} x={m.x} y={m.y} width={m.w} height={m.h} rx={Math.min(4, (m.w ?? 0) / 2)} fill={m.color} {...common}>
              <title>{tipText(m)}</title>
            </rect>
          );
        if (m.kind === "point")
          return (
            <circle key={i} cx={m.x} cy={m.y} r={m.r} fill={m.color} stroke="#fff" strokeOpacity={0.9} strokeWidth={1.25} {...common}>
              <title>{tipText(m)}</title>
            </circle>
          );
        return (
          <path key={i} d={m.d} fill={m.color} stroke="#fff" strokeOpacity={0.9} strokeWidth={1.5} {...common}>
            <title>{tipText(m)}</title>
          </path>
        );
      })}
      {L.type !== "pie" && (
        <>
          <line x1={plot.l} x2={L.width - plot.r} y1={L.baseline} y2={L.baseline} stroke="currentColor" strokeOpacity={0.4} />
          {L.xLabels.map((x, i) => (
            <text key={i} x={x.x} y={L.height - plot.b + 15} textAnchor="middle" fontSize={10} fill="currentColor">
              {x.label}
            </text>
          ))}
          {L.xAxisLabel && (
            <text x={plot.l + (L.width - plot.l - plot.r) / 2} y={L.height - 3} textAnchor="middle" fontSize={9.5} fill="currentColor" fillOpacity={0.8}>
              {L.xAxisLabel}
            </text>
          )}
        </>
      )}
    </svg>
  );
}

export function Legend({ spec }: { spec: ChartSpecLike }) {
  const L = computeChartLayout(spec);
  if (L.empty || L.legend.length === 0 || (L.legend.length === 1 && L.type !== "pie")) return null;
  return (
    <div className="wm-legend">
      {L.legend.map((l) => (
        <span key={l.label}>
          <i style={{ background: l.color }} />
          {l.label}
        </span>
      ))}
    </div>
  );
}

/** Interactive chart: hover or focus a mark to see its value. */
export function ChartView(props: ChartViewProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; m: Mark } | null>(null);
  const onMark = (m: Mark | null, L: ChartLayout) => {
    const w = wrap.current;
    if (!m || !w) return setTip(null);
    const scale = w.clientWidth / L.width;
    setTip({ x: m.ax * scale, y: m.ay * scale, m });
  };
  return (
    <div className="wm-chart" ref={wrap}>
      <ChartSvg {...props} onMark={onMark} />
      {tip && (
        <div className="wm-tooltip" style={{ left: tip.x, top: tip.y }} role="status">
          <b>{tip.m.label}</b>
          <span>
            {tip.m.series}: {tip.m.text}
          </span>
        </div>
      )}
      {!props.hideLegend && <Legend spec={props.spec} />}
    </div>
  );
}
