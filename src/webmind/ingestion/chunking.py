"""Section-aware chunking.

Chunks never cross section boundaries, so every chunk maps to exactly one heading /
anchor — which is what makes precise citations and navigation links possible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from webmind.core.types import ParsedDocument

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass
class ChunkDraft:
    ordinal: int
    text: str
    heading: str | None
    heading_path: list[str] = field(default_factory=list)
    anchor: str | None = None
    page: int | None = None


def _pieces(text: str, size: int) -> list[str]:
    """Split text into units no longer than ``size``: paragraphs → sentences → hard cuts."""
    out: list[str] = []
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if len(para) <= size:
            out.append(para)
            continue
        for sent in _SENT.split(para):
            sent = sent.strip()
            while len(sent) > size:
                cut = sent.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                out.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if sent:
                out.append(sent)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def chunk_document(doc: ParsedDocument, *, size: int, overlap: int) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for section in doc.sections:
        pieces = _pieces(section.text, size)
        current = ""
        carried = False
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= size or not current:
                current = candidate
                carried = False
                continue
            chunks.append(_draft(len(chunks), current, section))
            prefix = _tail(current, overlap)
            current = f"{prefix}\n\n{piece}" if prefix and len(prefix) + len(piece) + 2 <= size else piece
            carried = bool(prefix)
        if current and not (carried and chunks and current == _tail(chunks[-1].text, overlap)):
            chunks.append(_draft(len(chunks), current, section))
    return chunks


def _draft(ordinal: int, text: str, section) -> ChunkDraft:  # noqa: ANN001
    return ChunkDraft(
        ordinal=ordinal,
        text=text.strip(),
        heading=section.heading,
        heading_path=list(section.heading_path),
        anchor=section.anchor,
        page=section.page,
    )


def embedding_text(title: str | None, chunk: ChunkDraft) -> str:
    """Contextual chunk header: prepend title and heading path so short chunks embed well."""
    context = " > ".join([p for p in [title, *chunk.heading_path] if p])
    return f"{context}\n{chunk.text}" if context else chunk.text
