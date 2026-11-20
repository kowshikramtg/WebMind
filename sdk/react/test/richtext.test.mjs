import assert from "node:assert/strict";
import { test } from "node:test";
import { parseInline, parseRichText, plainText, safeHttpUrl } from "../dist/core/richtext.js";

test("parses paragraphs, bold, code, links and citation markers", () => {
  const [p] = parseRichText("Tuition is **4,800** per year [1] (see `fees`) at https://example.org/fees.");
  assert.equal(p.t, "p");
  const kinds = p.c.map((i) => i.t);
  assert.deepEqual(kinds, ["text", "bold", "text", "cite", "text", "code", "text", "link", "text"]);
  const link = p.c.find((i) => i.t === "link");
  assert.equal(link.href, "https://example.org/fees"); // trailing "." is punctuation, not part of the URL
  assert.equal(p.c.find((i) => i.t === "cite").n, 1);
});

test("lists: -, *, bullet, + and nested items become one list; numbered lists stay ordered", () => {
  const blocks = parseRichText("Intro:\n- one\n\t+ nested\n* two\n\n1. first\n2) second");
  assert.deepEqual(blocks.map((b) => b.t), ["p", "ul", "ol"]);
  assert.equal(blocks[1].items.length, 3);
  assert.equal(blocks[2].items.length, 2);
});

test("headings render as bold paragraphs, blank lines separate paragraphs", () => {
  const blocks = parseRichText("## Title\n\nOne\nTwo");
  assert.equal(blocks[0].c[0].t, "bold");
  assert.equal(blocks[1].c.map((i) => i.v).join(""), "One\nTwo");
});

test("never produces HTML: markup in text stays literal text tokens", () => {
  const [p] = parseRichText('<img src=x onerror="alert(1)"> and <script>alert(1)</script>');
  assert.ok(p.c.every((i) => i.t === "text"));
  assert.equal(p.c.map((i) => i.v).join(""), '<img src=x onerror="alert(1)"> and <script>alert(1)</script>');
});

test("only http(s) URLs are ever linkable", () => {
  assert.equal(safeHttpUrl("https://a.example/x#y"), "https://a.example/x#y");
  assert.equal(safeHttpUrl("javascript:alert(1)"), undefined);
  assert.equal(safeHttpUrl("data:text/html,hi"), undefined);
  assert.equal(safeHttpUrl("//evil.example/x", "https://site.example/"), "https://evil.example/x");
  assert.equal(safeHttpUrl(null), undefined);
  // a "javascript:" string can't even be tokenised as a link
  assert.ok(parseInline("javascript:alert(1)").every((i) => i.t !== "link"));
});

test("plainText flattens for copy and export", () => {
  assert.equal(plainText("**Hi** there [2]\n\n- a\n- b"), "Hi there [2]\n\n- a\n- b");
});
