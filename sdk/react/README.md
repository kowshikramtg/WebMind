# @webmind/react

React components and a framework-agnostic client for a self-hosted [WebMind](../../README.md) server.
The same interface also ships as a dependency-free `<script>` widget (`/v1/widget.js`); both are built from one
shared core and one stylesheet, so they look and behave identically.

```bash
# from a WebMind checkout (not yet published to npm)
cd sdk/react && npm install && npm run build
# in your app
npm install /path/to/WebMind/sdk/react
```

## Drop-in chat

```tsx
import { WebMindProvider, WebMindChat, WebMindFloating } from "@webmind/react";
import "@webmind/react/styles.css";                       // once, anywhere in your app

async function getSessionToken() {
  // Ask YOUR backend for a short-lived WebMind token for the signed-in user (null = anonymous visitor).
  const r = await fetch("/api/webmind-token", { credentials: "include" });
  return r.ok ? (await r.json()).token : null;
}

export function App() {
  return (
    <WebMindProvider endpoint="/webmind-api" getAccessToken={getSessionToken}>
      <div style={{ height: 640 }}>
        <WebMindChat title="Ask Lumen Valley" suggestions={["Where is the admissions page?"]} />
      </div>
      {/* or a floating launcher, like the script widget: */}
      <WebMindFloating side="right" suggestions={["What does tuition cost?"]} />
    </WebMindProvider>
  );
}
```

| Prop | Meaning |
| --- | --- |
| `title`, `placeholder` | Copy |
| `subtitle` | Replaces the identity line (default: "Signed in as ... / Public visitor", from `GET /v1/me`) |
| `loginUrl` | Shows a "Sign in" link to anonymous visitors |
| `suggestions` | Up to 4 quick-start prompts in the empty state |
| `theme` | `"auto"` (default, follows the system), `"light"`, `"dark"` |
| `accent` | Accent color (any CSS color); or set `--wm-accent` in CSS |
| `clientExports` | `"aggregates"` (default), `"all"`, `"off"`: see *Exports* |
| `debug` | Show intent and tool trace under answers while integrating |

## What the interface does

- Messages in iOS-style bubbles; **streamed** answers with a typing indicator and caret.
- **Citations**: inline `[n]` chips open an expandable *Sources* list with a document preview (quote, index date,
  link to the exact section).
- **Charts**: interactive SVG (bar, line, area, pie/donut, scatter, histogram) with hover/keyboard tooltips.
- **Tables**: compact, sticky header, numeric alignment, "Show all N rows", applied access limits shown underneath.
- **Exports** (below), copy, and a download card for server-generated files.
- Light/dark, responsive (bottom sheet on phones), keyboard accessible, `prefers-reduced-motion` respected.

## Exports

| What | Options | Where it is produced |
| --- | --- | --- |
| Chart | PNG (2×), SVG, data as CSV or Excel | in the browser |
| Aggregate table (statistics) | Excel (.xlsx), CSV, JSON | in the browser |
| Answer | Copy, Markdown, PDF (print dialog) | in the browser |
| **Raw record table** | Excel, CSV, PDF, JSON | **WebMind server** (`export` capability checked) |

Client-side exports contain only what is already on screen. Raw record tables are **not** downloadable
client-side by default, so a user can never export more than the server's `export` permission allows; the menu
asks the server instead. Set `clientExports="all"` to let your users download any visible table, or `"off"` to
hide client-side exports. Cells are protected against spreadsheet formula injection, and the Excel writer is
dependency-free.

## Build your own UI

```tsx
import { useWebMind, ChartView, ExportMenu, Sources, Rich, tableToXlsx, toCsv, computeChartLayout } from "@webmind/react";

function MyAssistant() {
  const { turns, ask, pending } = useWebMind();   // turns[i].response: answer, citations, table, chart, export …
}
```

Pure helpers without React (`parseRichText`, `computeChartLayout`, `toCsv`, `tableToXlsx`, `answerToMarkdown`,
`clientExportPolicy`) are exported too. `ChartView` renders `ChartSpec`s as SVG; the spec
(`{type, x_key, series, data}`) can equally be passed to Recharts or Plotly.

## Without React

```ts
import { WebMindClient } from "@webmind/react/client";

const wm = new WebMindClient({ endpoint: "https://assistant.example.org", getAccessToken });
const res = await wm.chat("Where can I find the eligibility requirements?");
for await (const ev of wm.stream("Summarize the admissions page")) { /* plan | token | final */ }
```

## Theming

All colors, radii and shadows are CSS variables on `.wm` (`--wm-accent`, `--wm-bg`, `--wm-surface`, `--wm-text`,
`--wm-radius`, …); see `src/webmind.css`.

## Security

- Never put admin keys, database credentials or the JWT secret in browser code. `getAccessToken` must call your
  backend, which signs a short-lived user token.
- Answers, citations and table cells are rendered as React elements (never `dangerouslySetInnerHTML`); only
  `http(s)` links are rendered. A server-rendering test asserts hostile text stays inert.

## Development

```bash
npm run build      # tsc + stylesheet + regenerate the script widget (src/webmind/api/static/webmind-widget.js)
npm test           # 30 tests: rich text, charts, every export format, React server rendering, widget bundle
```

The committed widget is generated; a test fails if it is stale. See [docs/frontend.md](../../docs/frontend.md).
For integration guides see [INTEGRATION.md](../../INTEGRATION.md).
