import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent } from "react";
import { svgToStandalone, type ClientExportMode } from "./core/exports.js";
import { useWebMind, type ChatTurn } from "./context.js";
import { ChartCard, FileCard, Icon, MessageActions, ResultTable, Rich, Sources, type ExportContext } from "./ui.js";
import { safeHttpUrl } from "./core/richtext.js";
import type { ExportRef, Me } from "./types.js";
import { downloadBlob } from "./core/exports.js";

export type WebMindTheme = "auto" | "light" | "dark";

export interface WebMindChatProps {
  title?: string;
  /** Overrides the identity line ("Signed in as ..." / "Public visitor") under the title. */
  subtitle?: string;
  /** Where anonymous visitors can sign in; shows a "Sign in" link next to "Public visitor". */
  loginUrl?: string;
  placeholder?: string;
  className?: string;
  /** Quick-start prompts shown in the empty state. */
  suggestions?: string[];
  /** Light, dark, or follow the system (default). */
  theme?: WebMindTheme;
  /** Accent color override (any CSS color). */
  accent?: string;
  /**
   * Client-side exports of what the user already sees. "aggregates" (default) allows charts and
   * statistics; raw record tables use WebMind's permission-checked server export. "off" hides them.
   */
  clientExports?: ClientExportMode;
  /** Show the tool trace (intent + tools) under each answer: useful while integrating. */
  debug?: boolean;
  /** Renders a close button in the header (used by the floating variant). */
  onClose?: () => void;
}

/** The chat surface itself (no outer container). */
function ChatPanel({ title = "Ask this site", subtitle, loginUrl, placeholder = "Ask a question", suggestions, clientExports = "aggregates", debug, onClose }: WebMindChatProps) {
  const { turns, ask, pending, reset, client } = useWebMind();
  const [input, setInput] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    let alive = true;
    client.me().then((m) => alive && setMe(m)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [client]);
  const identity = me ? (me.authenticated ? `Signed in as ${me.user_id}${me.roles.length ? " · " + me.roles.join(", ") : ""}` : "Public visitor") : "Answers cite their sources";
  const login = me && !me.authenticated ? safeHttpUrl(loginUrl) : undefined;
  const log = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [turns]);

  const send = (text: string) => {
    const q = text.trim();
    if (!q || pending) return;
    setInput("");
    if (field.current) field.current.style.height = "auto";
    void ask(q);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <section className="wm-chat" aria-label={title}>
      <header className="wm-header">
        <div className="wm-title-wrap">
          <div className="wm-avatar">
            <Icon name="sparkle" size={18} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="wm-title">{title}</div>
            <div className="wm-subtitle">
              <span className={"wm-dot" + (me?.authenticated ? " wm-on" : "")} />
              <span className="wm-sub-text">{subtitle ?? identity}</span>
              {login && !subtitle && <a href={login}>Sign in</a>}
            </div>
          </div>
        </div>
        <div className="wm-header-actions">
          {turns.length > 0 && (
            <button type="button" className="wm-icon-btn" aria-label="New conversation" title="New conversation" onClick={reset}>
              <Icon name="compose" />
            </button>
          )}
          {onClose && (
            <button type="button" className="wm-icon-btn" aria-label="Close" title="Close" onClick={onClose}>
              <Icon name="close" />
            </button>
          )}
        </div>
      </header>

      <div className="wm-log" role="log" aria-live="polite" ref={log}>
        {turns.length === 0 ? (
          <div className="wm-empty">
            <div className="wm-avatar">
              <Icon name="sparkle" size={26} />
            </div>
            <h2>How can I help?</h2>
            <p>Ask about this site, look up information, or request a chart or table.</p>
            {suggestions && suggestions.length > 0 && (
              <div className="wm-chips">
                {suggestions.slice(0, 4).map((s) => (
                  <button key={s} type="button" className="wm-chip" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          turns.map((t, i) => <Turn key={t.id} turn={t} last={i === turns.length - 1} mode={clientExports} debug={debug} onAsk={send} />)
        )}
      </div>

      <form className="wm-composer" onSubmit={submit}>
        <div className="wm-field">
          <textarea
            ref={field}
            className="wm-input"
            rows={1}
            value={input}
            placeholder={placeholder}
            maxLength={4000}
            aria-label="Your question"
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
            }}
            onKeyDown={onKey}
          />
          <button type="submit" className="wm-send" aria-label="Send" disabled={pending || !input.trim()}>
            <Icon name="send" size={16} />
          </button>
        </div>
      </form>
    </section>
  );
}

function Turn({ turn, last, mode, debug, onAsk }: { turn: ChatTurn; last: boolean; mode: ClientExportMode; debug?: boolean; onAsk: (q: string) => void }) {
  const { client } = useWebMind();
  const r = turn.response;
  const stack = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState<{ n: number; tick: number } | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);
  const ctx: ExportContext | null = r ? { question: turn.question, response: r, mode, onAsk } : null;

  const download = async (f: ExportRef) => {
    try {
      downloadBlob(await client.download(f), f.filename);
      setDlError(null);
    } catch {
      setDlError("This download has expired or is not available.");
    }
  };
  const chartSvg = () => {
    const s = stack.current?.querySelector("svg[data-wm-chart]");
    return s ? svgToStandalone(s as SVGSVGElement) : null;
  };

  return (
    <article className="wm-turn">
      <div className="wm-row wm-user">
        <div className="wm-bubble">{turn.question}</div>
      </div>
      <div className="wm-row wm-assistant">
        <div className="wm-stack" ref={stack}>
          {turn.error && (
            <div className="wm-bubble wm-error" role="alert">
              {turn.error}
              <button type="button" className="wm-retry" onClick={() => onAsk(turn.question)}>
                Try again
              </button>
            </div>
          )}
          {turn.pending && !turn.partial && !r && (
            <div className="wm-bubble" aria-label="Assistant is typing">
              <span className="wm-typing"><i /><i /><i /></span>
            </div>
          )}
          {!r && turn.partial && (
            <div className="wm-bubble wm-caret">
              <Rich text={turn.partial} />
            </div>
          )}
          {r && (
            <div className="wm-bubble">
              <Rich text={r.answer} onCite={r.citations.length ? (n) => setFocus({ n, tick: Date.now() }) : undefined} />
            </div>
          )}
          {r && ctx && r.chart && <ChartCard spec={r.chart} ctx={ctx} />}
          {r && ctx && r.table && !r.chart && r.table.rows.length > 0 && <ResultTable table={r.table} ctx={ctx} />}
          {r?.export && <FileCard file={r.export} onDownload={download} />}
          {dlError && <div className="wm-note" role="alert">{dlError}</div>}
          {r && <Sources citations={r.citations} focus={focus} />}
          {r && ctx && (last || true) && <MessageActions ctx={ctx} chartSvg={chartSvg} />}
          {debug && r && (
            <div className="wm-debug">
              intent: {r.intent} · tools: {r.tools.map((t) => `${t.tool}(${t.status})`).join(", ") || "none"}
              {r.warnings.length > 0 && ` · warnings: ${r.warnings.join(", ")}`}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function rootProps(theme: WebMindTheme, accent?: string): { "data-theme": WebMindTheme; style?: CSSProperties } {
  return { "data-theme": theme, style: accent ? ({ "--wm-accent": accent } as CSSProperties) : undefined };
}

/**
 * Ready-made inline chat (give its container a height). All server content is rendered as React
 * elements (no dangerouslySetInnerHTML) and links are restricted to http(s).
 * Import the styles once: `import "@webmind/react/styles.css"`.
 */
export function WebMindChat({ theme = "auto", accent, className, ...rest }: WebMindChatProps) {
  return (
    <div className={["wm", "wm-inline", className].filter(Boolean).join(" ")} {...rootProps(theme, accent)}>
      <ChatPanel {...rest} />
    </div>
  );
}

export interface WebMindFloatingProps extends WebMindChatProps {
  side?: "left" | "right";
  defaultOpen?: boolean;
}

/** Floating launcher + panel, the React equivalent of the `<script>` widget. */
export function WebMindFloating({ theme = "auto", accent, side = "right", defaultOpen = false, className, onClose: _onClose, ...rest }: WebMindFloatingProps) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (!open) return;
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  return (
    <div className={["wm", "wm-float", className].filter(Boolean).join(" ")} data-side={side} data-open={open} {...rootProps(theme, accent)}>
      {open && (
        <div className="wm-panel" role="dialog" aria-label={rest.title ?? "Ask this site"}>
          <ChatPanel {...rest} onClose={() => setOpen(false)} />
        </div>
      )}
      {!open && (
        <button type="button" className="wm-launcher" aria-label={rest.title ?? "Ask this site"} onClick={() => setOpen(true)}>
          <Icon name="chat" size={26} />
        </button>
      )}
    </div>
  );
}
