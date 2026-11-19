"""Document parsers: bytes → normalized :class:`ParsedDocument` with sections.

Sections keep their heading hierarchy and (where the source provides one) an anchor,
which powers navigation answers ("the Eligibility section of /admissions").
"""

from __future__ import annotations

import io
import re
from pathlib import PurePosixPath

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from webmind.core.errors import ProviderNotInstalledError
from webmind.core.types import ParsedDocument, Section

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "canvas",
    "nav",
    "footer",
    "aside",
    "button",
    "select",
    "input",
    "textarea",
    "form",
    "head",
}
_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "header",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "blockquote",
    "pre",
    "dl",
    "dt",
    "dd",
    "figure",
    "figcaption",
    "address",
    "details",
    "summary",
    "hr",
    "br",
}
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n+")

SUPPORTED_EXTENSIONS = {".html", ".htm", ".md", ".markdown", ".txt", ".pdf", ".docx"}


def _clean(text: str) -> str:
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def detect_kind(content_type: str | None, uri: str) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("text/html", "application/xhtml+xml"):
        return "html"
    if ct == "application/pdf":
        return "pdf"
    if ct in ("text/markdown", "text/x-markdown"):
        return "markdown"
    if ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    suffix = PurePosixPath(uri.split("?")[0].split("#")[0]).suffix.lower()
    return {
        ".html": "html",
        ".htm": "html",
        ".md": "markdown",
        ".markdown": "markdown",
        ".txt": "text",
        ".pdf": "pdf",
        ".docx": "docx",
    }.get(suffix) or ("text" if ct.startswith("text/") else ("html" if suffix == "" and not ct else None))


# --------------------------------------------------------------------- HTML


class _HtmlWalker:
    def __init__(self) -> None:
        self.sections: list[Section] = []
        self.stack: list[tuple[int, str]] = []
        self.heading: str | None = None
        self.anchor: str | None = None
        self.buf: list[str] = []

    def flush(self) -> None:
        text = _clean("".join(self.buf))
        if text:
            self.sections.append(
                Section(
                    heading=self.heading,
                    heading_path=[h for _, h in self.stack],
                    anchor=self.anchor,
                    text=text,
                )
            )
        self.buf = []

    @staticmethod
    def _anchor_for(el: Tag) -> str | None:
        if el.get("id"):
            return str(el["id"])
        inner = el.find(lambda t: isinstance(t, Tag) and t.name == "a" and (t.get("id") or t.get("name")))
        if isinstance(inner, Tag):
            return str(inner.get("id") or inner.get("name"))
        parent = el.parent
        if isinstance(parent, Tag) and parent.get("id") and parent.name in ("section", "div", "article"):
            first = parent.find(list(_HEADINGS))
            if first is el:
                return str(parent["id"])
        return None

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                self.buf.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in _SKIP or child.has_attr("data-webmind-ignore") or child.get("aria-hidden") == "true":
                continue
            if name in _HEADINGS:
                self.flush()
                level = _HEADINGS[name]
                title = _clean(child.get_text(" ", strip=True))
                while self.stack and self.stack[-1][0] >= level:
                    self.stack.pop()
                if title:
                    self.stack.append((level, title))
                self.heading = title or None
                self.anchor = self._anchor_for(child)
                continue
            if name == "br":
                self.buf.append("\n")
                continue
            if name == "li":
                self.buf.append("\n- ")
                self.walk(child)
                self.buf.append("\n")
                continue
            if name in ("td", "th"):
                self.walk(child)
                self.buf.append(" | ")
                continue
            block = name in _BLOCK
            if block:
                self.buf.append("\n")
            self.walk(child)
            if block:
                self.buf.append("\n")


def parse_html(content: bytes | str) -> ParsedDocument:
    soup = BeautifulSoup(content, "html.parser")
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = bool(robots and "noindex" in str(robots.get("content", "")).lower())
    title_tag = soup.find("title")
    title = _clean(title_tag.get_text(" ", strip=True)) if title_tag else None
    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = str(desc_tag.get("content")).strip() if desc_tag and desc_tag.get("content") else None
    canon = soup.find("link", rel=lambda v: v and "canonical" in v)
    canonical = str(canon.get("href")) if canon and canon.get("href") else None
    links = [str(a["href"]) for a in soup.find_all("a", href=True)]

    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup.body or soup
    walker = _HtmlWalker()
    walker.walk(root)  # type: ignore[arg-type]
    walker.flush()
    if not title:
        h1 = soup.find("h1")
        title = _clean(h1.get_text(" ", strip=True)) if h1 else None
    return ParsedDocument(
        title=title,
        description=description,
        sections=walker.sections,
        links=links,
        canonical_url=canonical,
        noindex=noindex,
        content_type="text/html",
    )


def extract_links(content: bytes) -> list[str]:
    soup = BeautifulSoup(content, "html.parser")
    return [str(a["href"]) for a in soup.find_all("a", href=True)]


# ----------------------------------------------------------------- Markdown

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_EMPH = re.compile(r"(\*\*|__|\*|_|`)")


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\- ]", "", text.lower()).strip()
    return re.sub(r"\s+", "-", s)


def parse_markdown(content: bytes | str) -> ParsedDocument:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    if text.startswith("---\n"):  # strip YAML front matter
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4 :]
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    heading: str | None = None
    buf: list[str] = []
    title: str | None = None
    in_fence = False

    def flush() -> None:
        body = _clean("\n".join(buf))
        if body:
            sections.append(
                Section(
                    heading=heading,
                    heading_path=[h for _, h in stack],
                    anchor=slugify(heading) if heading else None,
                    text=body,
                )
            )
        buf.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _MD_HEADING.match(line)
        if m:
            flush()
            level, htext = len(m.group(1)), _MD_EMPH.sub("", _MD_LINK.sub(r"\1", m.group(2))).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, htext))
            heading = htext
            if level == 1 and title is None:
                title = htext
            continue
        buf.append(line if in_fence else _MD_EMPH.sub("", _MD_LINK.sub(r"\1", line)))
    flush()
    return ParsedDocument(title=title, sections=sections, content_type="text/markdown")


# ---------------------------------------------------------------- PDF / text


def parse_pdf(content: bytes) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ProviderNotInstalledError("pdf parser", "pdf") from exc
    reader = PdfReader(io.BytesIO(content))
    title = None
    try:
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip() or None
    except Exception:  # malformed metadata is common; ignore
        title = None
    sections: list[Section] = []
    for i, page in enumerate(reader.pages, start=1):
        text = _clean(page.extract_text() or "")
        if text:
            sections.append(Section(heading=f"Page {i}", anchor=f"page={i}", text=text, page=i))
    if not title and sections:
        first_line = sections[0].text.split("\n", 1)[0].strip()
        title = first_line[:200] or None
    return ParsedDocument(title=title, sections=sections, content_type="application/pdf")


def parse_docx(content: bytes) -> ParsedDocument:
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise ProviderNotInstalledError("docx parser", "docx") from exc
    d = docx.Document(io.BytesIO(content))
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    heading: str | None = None
    buf: list[str] = []
    title = (d.core_properties.title or "").strip() or None

    def flush() -> None:
        body = _clean("\n".join(buf))
        if body:
            sections.append(Section(heading=heading, heading_path=[h for _, h in stack], text=body))
        buf.clear()

    for para in d.paragraphs:
        style = (para.style.name or "") if para.style is not None else ""
        m = re.match(r"Heading (\d)", style)
        if m and para.text.strip():
            flush()
            level = int(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, para.text.strip()))
            heading = para.text.strip()
            title = title or heading
        else:
            buf.append(para.text)
    flush()
    return ParsedDocument(title=title, sections=sections, content_type="application/docx")


def parse_text(content: bytes | str) -> ParsedDocument:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    body = _clean(text)
    title = body.split("\n", 1)[0][:200] if body else None
    return ParsedDocument(title=title, sections=[Section(text=body)] if body else [], content_type="text/plain")


def parse_document(content: bytes, *, content_type: str | None, uri: str) -> ParsedDocument | None:
    """Parse ``content``; returns None for unsupported content types."""
    kind = detect_kind(content_type, uri)
    if kind == "html":
        return parse_html(content)
    if kind == "markdown":
        return parse_markdown(content)
    if kind == "pdf":
        return parse_pdf(content)
    if kind == "docx":
        return parse_docx(content)
    if kind == "text":
        return parse_text(content)
    return None
