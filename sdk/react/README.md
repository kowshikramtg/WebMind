# @webmind/react

React components and a framework-agnostic client for a self-hosted
[WebMind](../../README.md) server.

```bash
# from a WebMind checkout (not yet published to npm)
cd sdk/react && npm install && npm run build
# in your app
npm install /path/to/WebMind/sdk/react
```

## Drop-in chat

```tsx
import { WebMindProvider, WebMindChat } from "@webmind/react";

async function getSessionToken() {
  // Ask YOUR backend for a short-lived WebMind token for the signed-in user.
  // Return null for anonymous visitors (public content only).
  const r = await fetch("/api/webmind-token", { credentials: "include" });
  return r.ok ? (await r.json()).token : null;
}

export function App() {
  return (
    <WebMindProvider endpoint="/webmind-api" getAccessToken={getSessionToken}>
      <WebMindChat title="Ask Lumen Valley" />
    </WebMindProvider>
  );
}
```

## Build your own UI

```tsx
import { useWebMind, ChartView } from "@webmind/react";

function MyAssistant() {
  const { turns, ask, pending } = useWebMind();
  // turns[i].response has answer, citations, navigation, table, chart, export …
}
```

`ChartView` renders WebMind chart specs as SVG without dependencies. The spec
(`{type, x_key, series, data}`) can be passed to Recharts or Plotly instead.

## Without React

```ts
import { WebMindClient } from "@webmind/react/client";

const wm = new WebMindClient({ endpoint: "https://assistant.example.org", getAccessToken });
const res = await wm.chat("Where can I find the eligibility requirements?");
for await (const ev of wm.stream("Summarize the admissions page")) { /* plan | token | final */ }
```

## Security

- Never put admin keys, database credentials or the JWT secret in browser code.
  `getAccessToken` must call your backend, which signs a short-lived user token.
- Answers and citations are rendered as text nodes, and only `http(s)` links are rendered.
- Class names (`wm-chat`, `wm-answer`, `wm-citations`, …) are stable for styling.

For full integration guides, see [INTEGRATION.md](../../INTEGRATION.md).
