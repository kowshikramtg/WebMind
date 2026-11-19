/**
 * Framework-agnostic WebMind client (works in browsers, Node 18+, Deno, Bun).
 *
 * Never put administrator keys, database credentials or JWT secrets in browser code:
 * `getAccessToken` should call your own backend, which signs a short-lived user token.
 */
import type { ChatResponse, ExportRef, StreamEvent, TableResult, TokenProvider } from "./types.js";

export class WebMindError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "WebMindError";
  }
}

export interface WebMindClientOptions {
  /** Base URL of the WebMind server, or a same-origin path that your server proxies (e.g. "/webmind-api"). */
  endpoint: string;
  getAccessToken?: TokenProvider;
  fetch?: typeof fetch;
}

export class WebMindClient {
  private readonly endpoint: string;
  private readonly getAccessToken?: TokenProvider;
  private readonly fetchImpl: typeof fetch;

  constructor(options: WebMindClientOptions) {
    this.endpoint = options.endpoint.replace(/\/+$/, "");
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  private async headers(json = true): Promise<Record<string, string>> {
    const h: Record<string, string> = json ? { "Content-Type": "application/json" } : {};
    const token = this.getAccessToken ? await this.getAccessToken() : null;
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.fetchImpl(this.endpoint + path, {
      ...init,
      headers: { ...(await this.headers(init.body !== undefined)), ...(init.headers as Record<string, string>) },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch {
        /* non-JSON error body */
      }
      throw new WebMindError(res.status, detail);
    }
    return (await res.json()) as T;
  }

  chat(message: string, conversationId?: string | null): Promise<ChatResponse> {
    return this.request("/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId ?? null }),
    });
  }

  /** Stream a response as server-sent events (plan → token* → final). */
  async *stream(message: string, conversationId?: string | null, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
    const res = await this.fetchImpl(this.endpoint + "/v1/chat/stream", {
      method: "POST",
      headers: await this.headers(),
      body: JSON.stringify({ message, conversation_id: conversationId ?? null }),
      signal,
    });
    if (!res.ok || !res.body) throw new WebMindError(res.status, res.statusText);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = block
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim())
          .join("");
        if (data) yield JSON.parse(data) as StreamEvent;
      }
    }
  }

  search(query: string, topK?: number): Promise<{ results: unknown[] }> {
    return this.request("/v1/search", { method: "POST", body: JSON.stringify({ query, top_k: topK ?? null }) });
  }

  analytics(kind: "records" | "aggregate", query: Record<string, unknown>): Promise<{ table: TableResult }> {
    return this.request("/v1/analytics/query", { method: "POST", body: JSON.stringify({ kind, query }) });
  }

  createExport(query: Record<string, unknown>, format: "csv" | "json" | "xlsx" | "pdf" = "csv"): Promise<ExportRef> {
    return this.request("/v1/exports", { method: "POST", body: JSON.stringify({ query, format }) });
  }

  /** Download an export with the user's token and return it as a Blob. */
  async download(ref: ExportRef): Promise<Blob> {
    const res = await this.fetchImpl(this.endpoint + ref.download_url, { headers: await this.headers(false) });
    if (!res.ok) throw new WebMindError(res.status, "download failed");
    return res.blob();
  }
}

/** Accept only http(s) URLs (citations come from indexed content and must not become javascript: links). */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url, typeof window !== "undefined" ? window.location.href : "http://localhost/");
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}
