"""Context assembly — turning retrieved chunks into the prompt's sources block.

Two jobs, and the first is a security control.

**Retrieved text is fenced as data.** Every passage goes inside a `<source>`
element and the ask prompt states that source content is evidence, never
instruction. Documents are untrusted input (TS-4, and BACKEND-TODO band 6 says
so for the ingestion side): an uploaded contract can contain "disregard your
instructions", and a model that reads retrieved text as if the operator wrote it
is one upload away from doing what the document says. Fencing plus the prompt's
rule is the mitigation. `_sanitise` also neutralises a passage that tries to
close the fence early.

**Source numbering is positional and stable.** Source `n` is `chunks[n-1]`, and
the citation gate resolves `[[n]]` the same way. If these two ever disagree, a
citation silently points at the wrong clause — which is why `assemble` returns
the numbering it used rather than letting each side derive it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.orchestration.retrieval.ports import IndexedChunk

#: A passage that contains these would otherwise appear to close its own fence
#: and start speaking as the operator.
_FENCE_BREAKERS = re.compile(r"</?\s*(source|sources|system|instructions?)\b[^>]*>", re.I)


@dataclass
class AssembledContext:
    """The sources block, plus the numbering the citation gate must share."""

    text: str
    #: Position-indexed: source n is `chunks[n-1]`.
    chunks: list[IndexedChunk] = field(default_factory=list)
    token_estimate: int = 0

    @property
    def source_count(self) -> int:
        return len(self.chunks)

    def chunk_for(self, n: int) -> IndexedChunk | None:
        return self.chunks[n - 1] if 1 <= n <= len(self.chunks) else None


def _sanitise(text: str) -> str:
    """Strip anything that could break out of the fence.

    Replaced with a visible marker rather than deleted: if a document really
    does contain `<source>`, the attorney should be able to tell that something
    was removed from what the model read.
    """
    return _FENCE_BREAKERS.sub("[markup removed]", text)


def _escape_attr(value: str) -> str:
    return value.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def assemble(chunks: list[IndexedChunk]) -> AssembledContext:
    """Render retrieved chunks as the numbered `<source>` block.

    Order is retrieval order, which is relevance order — the most relevant
    passage should not be buried at the bottom of the prompt.
    """
    if not chunks:
        return AssembledContext(text="", chunks=[], token_estimate=0)

    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        attrs = [
            f'n="{i}"',
            f'document="{_escape_attr(chunk.document_name)}"',
            f'page="{chunk.page_from}"',
        ]
        if chunk.section_path:
            attrs.append(f'section="{_escape_attr(chunk.section_path)}"')
        parts.append(f"<source {' '.join(attrs)}>\n{_sanitise(chunk.text).strip()}\n</source>")

    text = "\n\n".join(parts)
    return AssembledContext(
        text=text,
        chunks=list(chunks),
        token_estimate=max(1, len(text) // 4),
    )
