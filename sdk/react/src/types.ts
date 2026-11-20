/** Types mirroring the WebMind REST API (see /docs on a running server). */

export interface Freshness {
  mode: "indexed" | "live";
  last_synced_at?: string | null;
  source_changed_at?: string | null;
  sync_method?: string | null;
}

export interface Citation {
  index: number;
  resource_id: string;
  title?: string | null;
  section?: string | null;
  url?: string | null;
  snippet: string;
  version?: number | null;
  freshness: Freshness;
}

export interface NavigationLink {
  title?: string | null;
  section?: string | null;
  url?: string | null;
  snippet: string;
  resource_id: string;
}

export interface ChartSeries {
  key: string;
  label: string;
}

export interface ChartSpec {
  type: "bar" | "line" | "area" | "pie" | "scatter" | "histogram" | "table";
  title: string;
  x_key: string;
  x_label?: string | null;
  y_label?: string | null;
  series: ChartSeries[];
  data: Array<Record<string, unknown>>;
  source?: Record<string, unknown>;
}

export interface TableResult {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total_rows: number;
  truncated: boolean;
  dataset?: string | null;
  applied_filters: string[];
}

export interface ExportRef {
  id: string;
  format: string;
  filename: string;
  row_count: number;
  expires_at: string;
  download_url: string;
}

export interface ToolTrace {
  tool: string;
  arguments: Record<string, unknown>;
  status: "ok" | "denied" | "error";
  message?: string | null;
  duration_ms?: number | null;
}

export interface ChatResponse {
  answer: string;
  intent: string;
  conversation_id: string;
  citations: Citation[];
  navigation: NavigationLink[];
  table?: TableResult | null;
  chart?: ChartSpec | null;
  export?: ExportRef | null;
  statistics?: Record<string, unknown> | null;
  tools: ToolTrace[];
  grounded: boolean;
  llm_used: boolean;
  warnings: string[];
}

/** The caller's verified identity as WebMind sees it (GET /v1/me). */
export interface Me {
  authenticated: boolean;
  user_id: string | null;
  roles: string[];
  tenant_id: string;
  capabilities: string[];
}

export type StreamEvent =
  | { type: "plan"; intent: string; tools: string[]; conversation_id: string }
  | { type: "token"; text: string }
  | { type: "final"; response: ChatResponse }
  | { type: "error"; detail: string };

/** Returns a short-lived WebMind user token from YOUR backend, or null for anonymous use. */
export type TokenProvider = () => Promise<string | null | undefined> | string | null | undefined;
