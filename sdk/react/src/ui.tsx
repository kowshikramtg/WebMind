import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChartView, Legend } from "./ChartView.js";
import { columnDecimals, computeChartLayout, fmtCell, prettyTitle } from "./core/chartgeom.js";
import {
  answerToMarkdown,
  buildPrintHtml,
  chartToTable,
  clientExportPolicy,
  downloadBlob,
  MIME,
  printReport,
  safeFileName,
  svgToPng,
  svgToStandalone,
  tableToXlsx,
  toCsv,
  toJson,
  type ClientExportMode,
} from "./core/exports.js";
import { ICONS, type IconName } from "./core/icons.js";
import { menuPlacement } from "./core/placement.js";
import { parseRichText, plainText, safeHttpUrl, type Inline } from "./core/richtext.js";
import type { ChartSpec, ChatResponse, Citation, ExportRef, TableResult } from "./types.js";

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name].map((s, i) => (
        <path key={i} d={s.a.d} />
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------- rich text

function renderInline(items: Inline[], onCite?: (n: number) => void): ReactNode {
  return items.map((t, i) => {
    switch (t.t) {
      case "text":
        return <Fragment key={i}>{t.v}</Fragment>;
      case "bold":
        return <strong key={i}>{t.v}</strong>;
      case "code":
        return <code key={i}>{t.v}</code>;
      case "link": {
        const href = safeHttpUrl(t.href);
        return href ? (
          <a key={i} href={href} target="_blank" rel="noopener noreferrer">
            {t.v}
          </a>
        ) : (
          <Fragment key={i}>{t.v}</Fragment>
        );
      }
      case "cite":
        return onCite ? (
          <button key={i} type="button" className="wm-cite" aria-label={`Show source ${t.n}`} onClick={() => onCite(t.n)}>
            {t.n}
          </button>
        ) : (
          <Fragment key={i}>[{t.n}]</Fragment>
        );
    }
  });
}

/** Renders assistant text as React elements (never HTML): paragraphs, lists, bold, code, links, [n] chips. */
export function Rich({ text, onCite }: { text: string; onCite?: (n: number) => void }) {
  const blocks = useMemo(() => parseRichText(text), [text]);
  return (
    <div className="wm-rich">
      {blocks.map((b, i) =>
        b.t === "p" ? (
          <p key={i}>{renderInline(b.c, onCite)}</p>
        ) : b.t === "ul" ? (
          <ul key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it, onCite)}</li>)}</ul>
        ) : (
          <ol key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it, onCite)}</li>)}</ol>
        ),
      )}
    </div>
  );
}

// ----------------------------------------------------------------- menus

export type MenuEntry = { heading: string } | { separator: true } | { label: string; hint?: string; onSelect: () => void | Promise<void> };

const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

export function ExportMenu({ entries, label = "Export", compact = false }: { entries: MenuEntry[]; label?: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState({ up: false, right: false });
  const ref = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  useIsoLayoutEffect(() => {
    if (!open || !ref.current || !pop.current) return;
    const bounds = (ref.current.closest(".wm-log") ?? document.documentElement).getBoundingClientRect();
    setPlace(menuPlacement(ref.current.getBoundingClientRect(), pop.current.offsetWidth, pop.current.offsetHeight, bounds));
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  if (entries.length === 0) return null;
  return (
    <div className="wm-menu-wrap" ref={ref}>
      <button type="button" className={compact ? "wm-act" : "wm-btn-sm"} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name={compact ? "download" : "share"} size={compact ? 14 : 13} />
        {label}
      </button>
      {open && (
        <div className={"wm-menu" + (place.right ? " wm-right" : "") + (place.up ? " wm-up" : "")} role="menu" ref={pop}>
          {entries.map((e, i) =>
            "heading" in e ? (
              <div key={i} className="wm-menu-label">
                {e.heading}
              </div>
            ) : "separator" in e ? (
              <div key={i} className="wm-menu-sep" />
            ) : (
              <button
                key={i}
                type="button"
                role="menuitem"
                className="wm-menu-item"
                onClick={() => {
                  setOpen(false);
                  void e.onSelect();
                }}
              >
                {e.label}
                {e.hint && <small>{e.hint}</small>}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- sources

const dateOnly = (iso?: string | null) => (iso ? iso.slice(0, 10) : null);

export function Sources({ citations, focus }: { citations: Citation[]; focus: { n: number; tick: number } | null }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const refs = useRef(new Map<number, HTMLDivElement>());
  useEffect(() => {
    if (!focus) return;
    setOpen(true);
    setExpanded(focus.n);
    setFlash(focus.n);
    const t1 = setTimeout(() => refs.current.get(focus.n)?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60);
    const t2 = setTimeout(() => setFlash(null), 1400);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [focus]);
  if (citations.length === 0) return null;
  return (
    <>
      <button type="button" className="wm-sources-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="doc" size={13} />
        {citations.length === 1 ? "1 source" : `${citations.length} sources`}
        <span className="wm-chev" style={{ display: "inline-flex" }}>
          <Icon name="chevDown" size={13} />
        </span>
      </button>
      {open && (
        <div className="wm-sources">
          {citations.map((c) => {
            const href = safeHttpUrl(c.url);
            const isOpen = expanded === c.index;
            const when = dateOnly(c.freshness?.last_synced_at);
            return (
              <div key={c.index} className={"wm-source" + (flash === c.index ? " wm-flash" : "")} ref={(el) => void (el ? refs.current.set(c.index, el) : refs.current.delete(c.index))}>
                <button type="button" className="wm-source-head" aria-expanded={isOpen} onClick={() => setExpanded(isOpen ? null : c.index)}>
                  <span className="wm-source-badge">{c.index}</span>
                  <span className="wm-source-text">
                    <div className="wm-source-title">{c.title || href || "Source"}</div>
                    {c.section && c.section !== c.title && <div className="wm-source-section">{c.section}</div>}
                  </span>
                  <Icon name="chevRight" size={14} />
                </button>
                {isOpen && (
                  <div className="wm-source-body">
                    <blockquote className="wm-quote">{c.snippet}</blockquote>
                    <div className="wm-source-meta">
                      {when && <span>Indexed {when}</span>}
                      {c.version != null && <span>v{c.version}</span>}
                      {href && (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          Open page ↗
                        </a>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

// -------------------------------------------------------------- result cards

const isNum = (v: unknown) => typeof v === "number" && Number.isFinite(v);

export interface ExportContext {
  question: string;
  response: ChatResponse;
  mode: ClientExportMode;
  /** Sends a follow-up message (used for permission-checked server-side exports). */
  onAsk: (text: string) => void;
}

function serverEntries(onAsk: (t: string) => void): MenuEntry[] {
  return [
    { heading: "Download from server" },
    { label: "Excel (.xlsx)", hint: "permission checked", onSelect: () => onAsk("Export these results into Excel.") },
    { label: "CSV", onSelect: () => onAsk("Export these results into CSV.") },
    { label: "PDF", onSelect: () => onAsk("Export these results as a PDF.") },
    { label: "JSON", onSelect: () => onAsk("Export these results as a JSON file.") },
  ];
}

export function ResultTable({ table, ctx }: { table: TableResult; ctx: ExportContext }) {
  const [all, setAll] = useState(false);
  const pol = clientExportPolicy(ctx.response, ctx.mode);
  const name = table.dataset ?? "results";
  const rows = all ? table.rows.slice(0, 100) : table.rows.slice(0, 8);
  const numeric = useMemo(() => new Set(table.columns.filter((c) => table.rows.length > 0 && table.rows.every((r) => r[c] == null || isNum(r[c])))), [table]);
  const decimals = useMemo(() => Object.fromEntries(table.columns.map((c) => [c, columnDecimals(table.rows, c)])), [table]);
  const entries: MenuEntry[] = [];
  if (pol.table) {
    entries.push(
      { heading: "Download" },
      { label: "Excel (.xlsx)", onSelect: () => downloadBlob(new Blob([tableToXlsx(table.columns, table.rows, name) as BlobPart], { type: MIME.xlsx }), safeFileName(name, "xlsx")) },
      { label: "CSV", onSelect: () => downloadBlob(new Blob(["﻿" + toCsv(table.columns, table.rows)], { type: MIME.csv }), safeFileName(name, "csv")) },
      { label: "JSON", onSelect: () => downloadBlob(new Blob([toJson(table.columns, table.rows, { dataset: name })], { type: MIME.json }), safeFileName(name, "json")) },
    );
  }
  if (pol.serverExport) entries.push(...serverEntries(ctx.onAsk));
  return (
    <div className="wm-card">
      <div className="wm-card-head">
        <div>
          <div className="wm-card-title">{prettyTitle(name)}</div>
          <div className="wm-card-sub">
            {table.total_rows.toLocaleString("en-US")} {table.total_rows === 1 ? "row" : "rows"}
            {table.truncated ? " (limit reached)" : ""}
          </div>
        </div>
        <ExportMenu entries={entries} />
      </div>
      <div className="wm-card-body">
        <div className="wm-table-wrap">
          <table className="wm-table">
            <thead>
              <tr>
                {table.columns.map((c) => (
                  <th key={c} className={numeric.has(c) ? "wm-num" : undefined}>
                    {c.replace(/_/g, " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {table.columns.map((c) => (
                    <td key={c} className={numeric.has(c) ? "wm-num" : undefined}>
                      {fmtCell(r[c], decimals[c] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {table.rows.length > 8 && (
          <button type="button" className="wm-more" onClick={() => setAll((a) => !a)}>
            {all ? "Show fewer" : `Show all ${Math.min(table.rows.length, 100)} rows`}
          </button>
        )}
      </div>
      {table.applied_filters.length > 0 && <div className="wm-card-foot">{table.applied_filters.join(" · ")}</div>}
    </div>
  );
}

export function ChartCard({ spec, ctx }: { spec: ChartSpec; ctx: ExportContext }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const pol = clientExportPolicy(ctx.response, ctx.mode);
  const table = chartToTable(spec);
  const base = safeFileName(spec.title, "x").replace(/\.x$/, "");
  const entries: MenuEntry[] = [];
  if (pol.chart) {
    entries.push(
      { heading: "Image" },
      {
        label: "PNG",
        hint: "2×",
        onSelect: async () => {
          if (svgRef.current) downloadBlob(await svgToPng(svgRef.current, 2), `${base}.png`);
        },
      },
      {
        label: "SVG",
        onSelect: () => {
          if (svgRef.current) downloadBlob(new Blob([svgToStandalone(svgRef.current)], { type: MIME.svg }), `${base}.svg`);
        },
      },
      { separator: true },
      { heading: "Data" },
      { label: "Excel (.xlsx)", onSelect: () => downloadBlob(new Blob([tableToXlsx(table.columns, table.rows, spec.title) as BlobPart], { type: MIME.xlsx }), `${base}.xlsx`) },
      { label: "CSV", onSelect: () => downloadBlob(new Blob(["﻿" + toCsv(table.columns, table.rows)], { type: MIME.csv }), `${base}.csv`) },
    );
  }
  const L = computeChartLayout(spec);
  const sub = spec.series.length === 1 ? spec.series[0].label : `${spec.series.length} series`;
  return (
    <div className="wm-card">
      <div className="wm-card-head">
        <div>
          <div className="wm-card-title">{prettyTitle(spec.title)}</div>
          <div className="wm-card-sub">{spec.type === "pie" ? `${L.marks.length} categories` : sub}</div>
        </div>
        <ExportMenu entries={entries} />
      </div>
      <div className="wm-card-body">
        <ChartView spec={spec} svgRef={svgRef} hideLegend />
        <Legend spec={spec} />
      </div>
    </div>
  );
}

export function FileCard({ file, onDownload }: { file: ExportRef; onDownload: (f: ExportRef) => void }) {
  const ext = file.format.toUpperCase();
  return (
    <div className="wm-card wm-file">
      <div className="wm-file-icon">{ext.slice(0, 4)}</div>
      <div className="wm-file-text">
        <div className="wm-file-name">{file.filename}</div>
        <div className="wm-file-meta">
          {file.row_count.toLocaleString("en-US")} rows · expires {file.expires_at.slice(0, 16).replace("T", " ")} UTC
        </div>
      </div>
      <button type="button" className="wm-btn-sm" onClick={() => onDownload(file)}>
        <Icon name="download" size={13} />
        Download
      </button>
    </div>
  );
}

// ----------------------------------------------------------------- actions

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function MessageActions({ ctx, chartSvg }: { ctx: ExportContext; chartSvg: () => string | null }) {
  const [copied, setCopied] = useState(false);
  const pol = clientExportPolicy(ctx.response, ctx.mode);
  const entries: MenuEntry[] = pol.answer
    ? [
        { label: "Markdown (.md)", onSelect: () => downloadBlob(new Blob([answerToMarkdown(ctx.question, ctx.response, ctx.mode)], { type: MIME.md }), safeFileName(ctx.question, "md")) },
        { label: "PDF", hint: "print dialog", onSelect: () => printReport(buildPrintHtml(ctx.question, ctx.response, chartSvg(), ctx.mode)) },
      ]
    : [];
  return (
    <div className="wm-actions">
      <button
        type="button"
        className="wm-act"
        onClick={async () => {
          if (await copyText(plainText(ctx.response.answer))) {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }
        }}
      >
        <Icon name={copied ? "check" : "copy"} size={14} />
        {copied ? "Copied" : "Copy"}
      </button>
      {entries.length > 0 && <ExportMenu entries={entries} label="Save" compact />}
    </div>
  );
}
