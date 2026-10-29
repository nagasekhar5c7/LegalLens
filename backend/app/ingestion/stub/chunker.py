"""Section-aware chunking. Real in M3 — `chunk_meta` rows and span offsets are
backend data, not model data, and FR-4 requires them regardless of whether a
vector store exists yet.

Two rules carried over from the architecture:

  * **A numbered clause is never split.** A chunk that ends mid-obligation
    produces an answer that is wrong in the most expensive way: it looks
    complete.
  * **Child chunks for retrieval, parent sections for generation.** The parent
    is recorded now so parent-document expansion at M4 has something to expand
    to.
"""

from __future__ import annotations

import re
from typing import Any

from app.ingestion.ports import Chunk, ParsedPage

CHILD_TARGET_CHARS = 2000  # ≈512 tokens
CHILD_OVERLAP_CHARS = 256  # ≈64 tokens
MAX_PARENT_CHARS = 12_000

#: `12.3`, `§ 9.2`, `ARTICLE IV`, `COUNT III` — the headings legal documents
#: actually use, in the order they are most reliable.
CLAUSE_RE = re.compile(
    r"^(?:§\s*)?(\d+(?:\.\d+)+(?:\([a-z]\))?)\s+(?=\S)"
    r"|^(ARTICLE\s+[IVXLC]+|SECTION\s+\d+|COUNT\s+[IVXLC]+|EXHIBIT\s+[A-Z])\b",
    re.IGNORECASE,
)


def _estimate_tokens(text: str) -> int:
    # ~4 chars per token for English prose; good enough for a budget field.
    return max(1, len(text) // 4)


class SectionAwareChunker:
    name = "chunker/section-aware-v1"

    def chunk(self, pages: list[ParsedPage], doc_type: str) -> list[Chunk]:
        sections = self._segment(pages)
        chunks: list[Chunk] = []

        for section in sections:
            parent_key = f"p{len(chunks)}"
            text = section["text"]
            parent = Chunk(
                page_from=section["page_from"],
                page_to=section["page_to"],
                char_start=section["char_start"],
                char_end=section["char_start"] + len(text),
                text=text[:MAX_PARENT_CHARS],
                section_path=section["section_path"],
                clause_no=section["clause_no"],
                token_count=_estimate_tokens(text),
                is_parent=True,
                parent_key=parent_key,
            )
            chunks.append(parent)

            # A section that fits is its own child — splitting a two-paragraph
            # clause into two chunks only loses context.
            if len(text) <= CHILD_TARGET_CHARS:
                child = Chunk(
                    page_from=section["page_from"],
                    page_to=section["page_to"],
                    char_start=section["char_start"],
                    char_end=section["char_start"] + len(text),
                    text=text,
                    section_path=section["section_path"],
                    clause_no=section["clause_no"],
                    token_count=_estimate_tokens(text),
                    parent_key=parent_key,
                )
                chunks.append(child)
                continue

            for start, end in _windows(len(text)):
                chunks.append(
                    Chunk(
                        page_from=section["page_from"],
                        page_to=section["page_to"],
                        char_start=section["char_start"] + start,
                        char_end=section["char_start"] + end,
                        text=text[start:end],
                        section_path=section["section_path"],
                        clause_no=section["clause_no"],
                        token_count=_estimate_tokens(text[start:end]),
                        parent_key=parent_key,
                    )
                )

        return chunks

    def _segment(self, pages: list[ParsedPage]) -> list[dict[str, Any]]:
        """Split into sections at clause boundaries. `char_start` is page-local
        to `page_from`, matching how spans are stored."""
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for page in pages:
            if not page.text.strip():
                continue
            offset = 0
            for para in page.text.split("\n\n"):
                match = CLAUSE_RE.match(para.strip())
                if match or current is None:
                    if current is not None:
                        sections.append(current)
                    clause = (match.group(1) or match.group(2)) if match else None
                    current = {
                        "page_from": page.page_no,
                        "page_to": page.page_no,
                        "char_start": offset,
                        "text": para,
                        "clause_no": clause,
                        "section_path": f"§ {clause}" if clause else None,
                    }
                else:
                    current["text"] = f"{current['text']}\n\n{para}"
                    current["page_to"] = page.page_no
                offset += len(para) + 2

        if current is not None:
            sections.append(current)
        return sections


def _windows(length: int) -> list[tuple[int, int]]:
    step = CHILD_TARGET_CHARS - CHILD_OVERLAP_CHARS
    out: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(length, start + CHILD_TARGET_CHARS)
        out.append((start, end))
        if end >= length:
            break
        start += step
    return out
