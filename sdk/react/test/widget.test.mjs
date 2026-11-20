// Guards for the dependency-free widget bundle that the Python server ships.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const committed = join(root, "..", "..", "src", "webmind", "api", "static", "webmind-widget.js");

test("the committed widget bundle is exactly what the current sources build (run `npm run build`)", () => {
  const dir = mkdtempSync(join(tmpdir(), "wm-widget-"));
  try {
    const out = join(dir, "widget.js");
    execFileSync(process.execPath, [join(root, "scripts", "postbuild.mjs")], { env: { ...process.env, WIDGET_OUT: out }, stdio: "pipe" });
    assert.equal(readFileSync(out, "utf8"), readFileSync(committed, "utf8"), "stale bundle: run `npm run build` in sdk/react and commit the result");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the bundle compiles, is dependency-free, and never uses innerHTML or eval", () => {
  const code = readFileSync(committed, "utf8");
  new vm.Script(code); // throws on a syntax error
  const body = code.replace(/^\/\*![\s\S]*?\*\//, ""); // ignore the documented header
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML/.test(body), "no HTML string injection");
  assert.ok(!/\beval\(|new Function\(/.test(body));
  assert.ok(!/^\s*import\s|\brequire\(/m.test(body), "single self-contained file");
  assert.ok(code.includes("never innerHTML"));
});

test("the documented attributes are implemented", () => {
  const code = readFileSync(committed, "utf8");
  for (const a of ["data-endpoint", "data-token-url", "data-title", "data-subtitle", "data-position", "data-theme", "data-accent", "data-suggestions", "data-client-exports", "data-login-url", "data-streaming", "data-open"]) {
    assert.ok(code.includes(`"${a}"`), a);
  }
  assert.ok(code.includes("/v1/chat/stream") && code.includes("/v1/chat"), "streaming with a non-streaming fallback");
});

test("the stylesheet is shared: the widget embeds exactly the CSS the React package ships", () => {
  const css = readFileSync(join(root, "src", "webmind.css"), "utf8");
  const code = readFileSync(committed, "utf8");
  // a few load-bearing rules must be present in both
  for (const rule of [".wm-bubble", ".wm-card", ".wm-menu", ".wm-sources", ".wm-composer", "prefers-color-scheme:dark", "max-width:520px"]) {
    assert.ok(css.replace(/\s+/g, "").includes(rule.replace(/\s+/g, "")), `css has ${rule}`);
    assert.ok(code.replace(/\s+/g, "").includes(rule.replace(/\s+/g, "")), `widget has ${rule}`);
  }
});
