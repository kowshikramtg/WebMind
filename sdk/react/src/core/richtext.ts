/**
 * Safe, tiny rich-text parser for assistant answers.
 *
 * It never produces HTML. It returns a token tree that the UI layers render as text nodes / React
 * elements, so server-provided text can never inject markup. Supported: paragraphs, bullet and numbered
 * lists, **bold**, `code`, bare http(s) links and [n] citation markers.
 */
export type Inline =
  | { t: "text"; v: string }
  | { t: "bold"; v: string }
  | { t: "code"; v: string }
  | { t: "link"; v: string; href: string }
  | { t: "cite"; n: number };

export type Block = { t: "p"; c: Inline[] } | { t: "ul" | "ol"; items: Inline[][] };

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d{1,3}\]|https?:\/\/[^\s<>"')\]]+)/g;
const TRAILING = /[.,;:!?]+$/;

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  INLINE.lastIndex = 0;
  for (let m = INLINE.exec(s); m; m = INLINE.exec(s)) {
    if (m.index > last) out.push({ t: "text", v: s.slice(last, m.index) });
    const tok = m[0];
    if (tok.startsWith("**")) out.push({ t: "bold", v: tok.slice(2, -2) });
    else if (tok.startsWith("`")) out.push({ t: "code", v: tok.slice(1, -1) });
    else if (tok.startsWith("[")) out.push({ t: "cite", n: Number(tok.slice(1, -1)) });
    else {
      const trail = TRAILING.exec(tok);
      const url = trail ? tok.slice(0, tok.length - trail[0].length) : tok;
      out.push({ t: "link", v: url, href: url });
      if (trail) out.push({ t: "text", v: trail[0] });
    }
    last = m.index + tok.length;
  }
  if (last < s.length) out.push({ t: "text", v: s.slice(last) });
  return out;
}

const BULLET = /^\s*[-*•+–]\s+(.*)$/; // nested (indented) items are flattened into the same list
const NUMBERED = /^\s*\d{1,2}[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

export function parseRichText(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { t: "ul" | "ol"; items: Inline[][] } | null = null;
  const flushPara = () => {
    if (para.length) blocks.push({ t: "p", c: parseInline(para.join("\n")) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push(list);
    list = null;
  };
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    const b = BULLET.exec(line);
    const n = b ? null : NUMBERED.exec(line);
    const h = b || n ? null : HEADING.exec(line);
    if (b || n) {
      flushPara();
      const kind = b ? "ul" : "ol";
      if (!list || list.t !== kind) {
        flushList();
        list = { t: kind, items: [] };
      }
      list.items.push(parseInline((b ?? n)![1]));
    } else if (h) {
      flushPara();
      flushList();
      blocks.push({ t: "p", c: [{ t: "bold", v: h[1] }] });
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

/** Plain text of a token tree (used for copy / Markdown / print). */
export function plainText(text: string): string {
  return parseRichText(text)
    .map((b) =>
      b.t === "p"
        ? b.c.map(inlinePlain).join("")
        : b.items.map((it, i) => `${b.t === "ol" ? `${i + 1}.` : "-"} ${it.map(inlinePlain).join("")}`).join("\n"),
    )
    .join("\n\n");
}

function inlinePlain(i: Inline): string {
  return i.t === "cite" ? `[${i.n}]` : i.t === "link" ? i.href : i.v;
}

/** Accept only http(s) URLs: indexed content must never become javascript:/data: links. */
export function safeHttpUrl(url: string | null | undefined, base?: string): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url, base ?? (typeof location !== "undefined" ? location.href : "http://localhost/"));
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : undefined;
  } catch {
    return undefined;
  }
}
