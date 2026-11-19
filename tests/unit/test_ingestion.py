from webmind.core.types import ParsedDocument, Section
from webmind.ingestion.chunking import chunk_document, embedding_text
from webmind.ingestion.parsers import detect_kind, parse_document, parse_html, parse_markdown, parse_text

HTML = b"""<!doctype html><html><head><title>Admissions</title>
<meta name="description" content="How to join">
</head><body>
<nav><a href="/x">Menu item that must not be indexed</a></nav>
<main>
<h1>Admissions</h1><p>Intro text.</p>
<section id="elig"><h2>Eligibility</h2><p>Grade average of 70 percent.</p>
<ul><li>Mathematics</li><li>English B2</li></ul></section>
<h2 id="fees">Fees</h2><table><tr><th>Item</th><th>Cost</th></tr><tr><td>Tuition</td><td>4800</td></tr></table>
<div data-webmind-ignore><p>Hidden cookie banner</p></div>
<script>alert('x')</script>
</main><footer>Footer text</footer></body></html>"""


def test_html_sections_anchors_and_noise_removal():
    doc = parse_html(HTML)
    assert doc.title == "Admissions"
    assert doc.description == "How to join"
    headings = [s.heading for s in doc.sections]
    assert headings == ["Admissions", "Eligibility", "Fees"]
    elig = doc.sections[1]
    assert elig.anchor == "elig"  # parent section id
    assert elig.heading_path == ["Admissions", "Eligibility"]
    assert "- Mathematics" in elig.text
    assert doc.sections[2].anchor == "fees"
    assert "Tuition | 4800" in doc.sections[2].text
    full = doc.text
    for noise in ("Menu item", "Footer text", "alert(", "cookie banner"):
        assert noise not in full
    assert "/x" in doc.links


def test_html_noindex():
    doc = parse_html(b'<html><head><meta name="robots" content="noindex,follow"></head><body><p>x</p></body></html>')
    assert doc.noindex


def test_markdown_headings_and_front_matter():
    md = "---\ntitle: x\n---\n# Guide\nIntro\n## Install **now**\nRun `pip install` [link](http://a)\n```\n# not a heading\n```\n"
    doc = parse_markdown(md)
    assert doc.title == "Guide"
    assert [s.heading for s in doc.sections] == ["Guide", "Install now"]
    assert doc.sections[1].anchor == "install-now"
    assert "# not a heading" in doc.sections[1].text
    assert "link" in doc.sections[1].text and "http://a" not in doc.sections[1].text


def test_detect_kind_and_dispatch():
    assert detect_kind("text/html; charset=utf-8", "x") == "html"
    assert detect_kind(None, "a/b.PDF") == "pdf"
    assert detect_kind(None, "notes.md") == "markdown"
    assert detect_kind("image/png", "logo.png") is None
    assert parse_document(b"\x89PNG", content_type="image/png", uri="logo.png") is None
    assert parse_text(b"Hello\nworld").title == "Hello"


def test_pdf_parsing(tmp_path):
    from reportlab.pdfgen import canvas

    p = tmp_path / "x.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 720, "Exam timetable is published four weeks before exams.")
    c.showPage()
    c.drawString(72, 720, "Library opens at eight.")
    c.save()
    doc = parse_document(p.read_bytes(), content_type="application/pdf", uri="x.pdf")
    assert doc is not None and len(doc.sections) == 2
    assert doc.sections[1].page == 2 and doc.sections[1].anchor == "page=2"
    assert "Library" in doc.sections[1].text


def test_chunking_respects_sections_and_size():
    long_para = " ".join(f"Sentence number {i} talks about topic {i % 7}." for i in range(200))
    doc = ParsedDocument(
        title="T",
        sections=[
            Section(heading="A", heading_path=["A"], anchor="a", text=long_para),
            Section(heading="B", text="short"),
        ],
    )
    chunks = chunk_document(doc, size=500, overlap=80)
    assert all(len(c.text) <= 500 for c in chunks)
    assert {c.heading for c in chunks} == {"A", "B"}
    a_chunks = [c for c in chunks if c.heading == "A"]
    assert len(a_chunks) > 5
    assert all(c.anchor == "a" for c in a_chunks)
    # overlap: consecutive chunks share text
    assert a_chunks[1].text[:20] in a_chunks[0].text
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert embedding_text("T", chunks[-1]).startswith("T")


def test_chunking_empty_document():
    assert chunk_document(ParsedDocument(sections=[]), size=500, overlap=50) == []
