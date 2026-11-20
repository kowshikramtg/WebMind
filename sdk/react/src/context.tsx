import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { WebMindClient } from "./client.js";
import type { ChatResponse, TokenProvider } from "./types.js";

export interface WebMindProviderProps {
  /** WebMind server URL, or a same-origin path your backend proxies to it. */
  endpoint: string;
  /** Returns a short-lived user token from your backend (omit for anonymous/public use). */
  getAccessToken?: TokenProvider;
  /** Use server-sent events for token-by-token answers (default true). */
  streaming?: boolean;
  /** Pre-populated conversation (demos, tests, server rendering). */
  initialTurns?: ChatTurn[];
  children?: ReactNode;
}

export interface ChatTurn {
  id: string;
  question: string;
  response?: ChatResponse;
  partial?: string;
  error?: string;
  pending: boolean;
}

interface WebMindContextValue {
  client: WebMindClient;
  turns: ChatTurn[];
  pending: boolean;
  ask: (question: string) => Promise<ChatResponse | undefined>;
  reset: () => void;
}

const Ctx = createContext<WebMindContextValue | null>(null);

let counter = 0;
const nextId = () => `t${Date.now().toString(36)}${(counter++).toString(36)}`;

export function WebMindProvider({ endpoint, getAccessToken, streaming = true, initialTurns, children }: WebMindProviderProps) {
  const client = useMemo(() => new WebMindClient({ endpoint, getAccessToken }), [endpoint, getAccessToken]);
  const [turns, setTurns] = useState<ChatTurn[]>(initialTurns ?? []);
  const conversation = useRef<string | null>(null);
  const update = (id: string, patch: Partial<ChatTurn>) =>
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const ask = useCallback(
    async (question: string) => {
      const id = nextId();
      setTurns((ts) => [...ts, { id, question, pending: true }]);
      try {
        let response: ChatResponse | undefined;
        if (streaming) {
          let partial = "";
          for await (const ev of client.stream(question, conversation.current)) {
            if (ev.type === "token") {
              partial += ev.text;
              update(id, { partial });
            } else if (ev.type === "final") {
              response = ev.response;
            } else if (ev.type === "error") {
              throw new Error(ev.detail);
            }
          }
        } else {
          response = await client.chat(question, conversation.current);
        }
        if (response) conversation.current = response.conversation_id;
        update(id, { response, pending: false, partial: undefined });
        return response;
      } catch (err) {
        update(id, { error: err instanceof Error ? err.message : String(err), pending: false });
        return undefined;
      }
    },
    [client, streaming],
  );

  const reset = useCallback(() => {
    conversation.current = null;
    setTurns([]);
  }, []);

  const value = useMemo(
    () => ({ client, turns, pending: turns.some((t) => t.pending), ask, reset }),
    [client, turns, ask, reset],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Access the WebMind client and conversation state (build your own UI with this). */
export function useWebMind(): WebMindContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWebMind must be used inside <WebMindProvider>");
  return v;
}
