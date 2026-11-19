import { useState, type FormEvent } from "react";
import { ChartView } from "./ChartView.js";
import { safeHref } from "./client.js";
import { useWebMind, type ChatTurn } from "./context.js";
import type { ChatResponse, ExportRef, TableResult } from "./types.js";

export interface WebMindChatProps {
  title?: string;
  placeholder?: string;
  className?: string;
  /** Show the tool trace (intent + tools) under each answer — useful while integrating. */
  debug?: boolean;
}

/**
 * Ready-made chat UI. All server content is rendered as React text nodes (no
 * dangerouslySetInnerHTML), and links are restricted to http(s).
 */
export function WebMindChat({ title = "Ask this site", placeholder = "Ask a question…", className, debug }: WebMindChatProps) {
  const { turns, ask, pending, reset } = useWebMind();
  const [input, setInput] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = input.trim();
    if (!q || pending) return;
    setInput("");
    void ask(q);
  };

  return (
    <section className={["wm-chat", className].filter(Boolean).join(" ")} aria-label={title}>
      <header className="wm-header">
        <strong>{title}</strong>
        {turns.length > 0 && (
          <button type="button" className="wm-reset" onClick={reset}>
            New conversation
          </button>
        )}
      </header>
      <div className="wm-log" role="log" aria-live="polite">
        {turns.map((t) => (
          <Turn key={t.id} turn={t} debug={debug} />
        ))}
      </div>
      <form className="wm-form" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={placeholder}
          maxLength={4000}
          aria-label="Your question"
        />
        <button type="submit" disabled={pending || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}

function Turn({ turn, debug }: { turn: ChatTurn; debug?: boolean }) {
  const r = turn.response;
  return (
    <article className="wm-turn">
      <p className="wm-question">{turn.question}</p>
      {turn.error && <p className="wm-error">{turn.error}</p>}
      {!r && turn.pending && <p className="wm-answer">{turn.partial || "…"}</p>}
      {r && <Answer response={r} debug={debug} />}
    </article>
  );
}

function Answer({ response: r, debug }: { response: ChatResponse; debug?: boolean }) {
  return (
    <div className="wm-answer">
      <p style={{ whiteSpace: "pre-wrap" }}>{r.answer}</p>
      {r.chart && <ChartView spec={r.chart} />}
      {r.table && !r.chart && <DataTable table={r.table} />}
      {r.export && <ExportButton exportRef={r.export} />}
      {r.citations.length > 0 && (
        <ol className="wm-citations">
          {r.citations.map((c) => {
            const href = safeHref(c.url);
            const label = [c.title, c.section && c.section !== c.title ? c.section : null].filter(Boolean).join(" — ");
            return (
              <li key={c.index} value={c.index}>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {label || href}
                  </a>
                ) : (
                  label
                )}
              </li>
            );
          })}
        </ol>
      )}
      {debug && (
        <p className="wm-debug">
          intent: {r.intent} · tools: {r.tools.map((t) => `${t.tool}(${t.status})`).join(", ") || "none"}
          {r.warnings.length > 0 && ` · warnings: ${r.warnings.join(", ")}`}
        </p>
      )}
    </div>
  );
}

function DataTable({ table }: { table: TableResult }) {
  return (
    <div className="wm-table" style={{ overflowX: "auto" }}>
      <table>
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.slice(0, 100).map((row, i) => (
            <tr key={i}>
              {table.columns.map((c) => (
                <td key={c}>{row[c] == null ? "" : String(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.applied_filters.length > 0 && <p className="wm-filters">Filters: {table.applied_filters.join("; ")}</p>}
    </div>
  );
}

function ExportButton({ exportRef }: { exportRef: ExportRef }) {
  const { client } = useWebMind();
  const [error, setError] = useState<string | null>(null);
  const download = async () => {
    try {
      const blob = await client.download(exportRef);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = exportRef.filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch {
      setError("The export has expired or is not available.");
    }
  };
  return (
    <p className="wm-export">
      <button type="button" onClick={download}>
        Download {exportRef.filename} ({exportRef.row_count} rows)
      </button>
      {error && <span className="wm-error"> {error}</span>}
    </p>
  );
}
