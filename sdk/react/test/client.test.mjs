// Run with: npm run build && npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { WebMindClient, WebMindError, safeHref } from "../dist/client.js";

function sseResponse(chunks) {
  const enc = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

test("stream parses SSE events split across network chunks", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, init });
    return sseResponse([
      'event: plan\ndata: {"type":"plan","intent":"document_qa","tools":[],"conversation_id":"c1"}\n\nevent: tok',
      'en\ndata: {"type":"token","text":"Hel"}\n\n',
      'data: {"type":"token","text":"lo"}\n\ndata: {"type":"final","response":{"answer":"Hello","conversation_id":"c1"}}\n\n',
    ]);
  };
  const client = new WebMindClient({ endpoint: "https://wm.example/", getAccessToken: async () => "tok123", fetch: fakeFetch });
  const events = [];
  for await (const ev of client.stream("hi")) events.push(ev);
  assert.deepEqual(events.map((e) => e.type), ["plan", "token", "token", "final"]);
  assert.equal(events.filter((e) => e.type === "token").map((e) => e.text).join(""), "Hello");
  assert.equal(calls[0].url, "https://wm.example/v1/chat/stream");
  assert.equal(calls[0].init.headers.Authorization, "Bearer tok123");
});

test("anonymous requests send no Authorization header and errors are typed", async () => {
  const fakeFetch = async (_url, init) => {
    assert.equal(init.headers.Authorization, undefined);
    return new Response(JSON.stringify({ detail: "forbidden" }), { status: 403 });
  };
  const client = new WebMindClient({ endpoint: "/webmind-api", fetch: fakeFetch });
  await assert.rejects(client.chat("x"), (err) => err instanceof WebMindError && err.status === 403 && err.message === "forbidden");
});

test("safeHref only allows http(s)", () => {
  assert.equal(safeHref("https://a.example/x#y"), "https://a.example/x#y");
  assert.equal(safeHref("javascript:alert(1)"), undefined);
  assert.equal(safeHref("data:text/html,hi"), undefined);
  assert.equal(safeHref(null), undefined);
});
