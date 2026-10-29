"""What all three workflows do identically: build a chat model, read a stream,
count tokens, price a call, and turn a chunk into a citation or an excerpt.

Extracted from `ask.py` when the summary and draft workflows landed. These are
not interesting functions; they are functions that must agree across the three
workflows or the product misreports itself. Three copies of `_usd` is three
chances for one of them to keep billing Sonnet rates after a model swap, and
three copies of `_citations` is three chances for citation `n` to stop meaning
`sources[n-1]` in one workflow only — which is a citation silently pointing at
the wrong clause, the single defect this product cannot ship.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.ai.orchestration.intent import Usage as CallUsage
from app.ai.orchestration.retrieval.ports import IndexedChunk
from app.ai.orchestration.settings import RouterSettings
from app.schemas.common import Citation, Excerpt

#: Characters of surrounding context carried on an excerpt, so the evidence
#: panel shows the passage in situ rather than a bare fragment (FR-12).
EXCERPT_PAD = 180

#: Per-MTok (input, output), by model. `CallUsage.usd` hardcodes Haiku, which is
#: right for the router and wrong for a turn that also ran Sonnet.
#:
#: Checked against `claude-api`'s published rates. A model absent from this
#: table prices at the Sonnet rate rather than at zero: under-reporting spend is
#: worse than over-reporting it, because a cost ceiling nobody notices being
#: crossed is not a ceiling.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

_DEFAULT_PRICE = PRICES["claude-sonnet-5"]


def usd(usage: CallUsage, model: str) -> float:
    """Cost of one call's tokens at `model`'s rate.

    Cache reads bill at ~0.1x input and cache writes at ~1.25x, which is why
    those counters are carried separately all the way from `usage_metadata`
    rather than folded into `input_tokens` at the edge.
    """
    rate_in, rate_out = PRICES.get(model, _DEFAULT_PRICE)
    return (
        usage.input_tokens * rate_in
        + usage.cache_read_input_tokens * rate_in * 0.1
        + usage.cache_creation_input_tokens * rate_in * 1.25
        + usage.output_tokens * rate_out
    ) / 1_000_000


def add(a: CallUsage, b: CallUsage) -> CallUsage:
    """Sum two calls' usage. Latency adds, because the calls are sequential."""
    return CallUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_input_tokens=a.cache_read_input_tokens + b.cache_read_input_tokens,
        cache_creation_input_tokens=a.cache_creation_input_tokens + b.cache_creation_input_tokens,
        latency_ms=a.latency_ms + b.latency_ms,
    )


def text_of(part: Any) -> str:
    """Text out of a LangChain chunk, whose content is a string or a block list."""
    content = getattr(part, "content", "")
    if isinstance(content, str):
        return content
    out: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            out.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            out.append(str(block.get("text", "")))
    return "".join(out)


def usage_from_meta(meta: dict[str, Any]) -> CallUsage:
    """Token counts out of LangChain's `usage_metadata`.

    Read defensively: a fake model in a test has no usage, and the cache
    counters live in a nested `input_token_details` that is absent unless a
    cache breakpoint was actually honoured.
    """
    details = meta.get("input_token_details") or {}
    return CallUsage(
        input_tokens=int(meta.get("input_tokens") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
        cache_read_input_tokens=int(details.get("cache_read") or 0),
        cache_creation_input_tokens=int(details.get("cache_creation") or 0),
    )


def build_chat_model(
    settings: RouterSettings, *, model: str, max_tokens: int, timeout: float
) -> Any:
    """A `ChatAnthropic` for one of the generation workflows.

    Imported inside the function so that importing a workflow module does not
    import LangChain — `tests/test_layering.py` has no rule about it, but
    `scripts/` and the fixture path must stay runnable without the `ai` extra.

    `temperature` is sent **only when explicitly configured**. Sampling
    parameters were removed on the 4.6-and-later models, so passing
    `temperature` to claude-sonnet-5 returns 400 "temperature is deprecated for
    this model" — and it fails at generation time, after retrieval has already
    been paid for.

    All three workflows read `settings.ask_temperature`, despite the name. One
    knob rather than three, because the three defaults are the same model and
    the setting exists only for a deployment that pins an older one that still
    accepts sampling — a case where wanting ask pinned but summary not is not a
    real configuration. Naming it per workflow would imply an independence that
    does not exist. It stays `ask_temperature` because renaming it would break
    an `LL_AI_ASK_TEMPERATURE` already set somewhere.
    """
    from langchain_anthropic import ChatAnthropic

    from app.ai.orchestration.router import RouterUnavailable

    if not settings.has_credentials:
        raise RouterUnavailable(
            "No Anthropic credentials. Set ANTHROPIC_API_KEY (or LL_AI_API_KEY)."
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "max_retries": settings.max_retries,
        "api_key": settings.api_key,
    }
    if settings.ask_temperature is not None:
        kwargs["temperature"] = settings.ask_temperature
    return ChatAnthropic(**kwargs)


async def stream_text(
    model: Any, messages: list[Any]
) -> AsyncIterator[tuple[str, CallUsage | None]]:
    """Yields `(text, None)` per chunk, then `("", usage)` once at the end.

    One generator rather than two so a caller cannot forget to collect usage,
    which NFR-8 needs on every call.
    """
    total = CallUsage()
    async for part in model.astream(messages):
        text = text_of(part)
        if text:
            yield text, None
        meta = getattr(part, "usage_metadata", None)
        if meta:
            total = usage_from_meta(meta)
    yield "", total


async def complete(model: Any, messages: list[Any]) -> tuple[str, CallUsage]:
    """A whole response, for a call whose output is not streamed to the user.

    Summary fields and draft sections arrive as units — a half-written field is
    not showable — so they are generated with this rather than with
    `stream_text`. The per-unit event is what gives the UI its progress, which
    is what NFR-3's three-minute summary actually needs.
    """
    parts: list[str] = []
    usage = CallUsage()
    async for text, maybe_usage in stream_text(model, messages):
        if maybe_usage is not None:
            usage = maybe_usage
        elif text:
            parts.append(text)
    return "".join(parts), usage


# ------------------------------------------------------- chunks → wire shapes


def citations_for(chunks: list[IndexedChunk]) -> list[Citation]:
    """The citation list, numbered positionally.

    Source `n` is `chunks[n-1]`, matching `context.assemble` and
    `CitationGate`. All three derive it the same way from the same list rather
    than each computing its own, because a disagreement between them is a
    citation chip that opens the wrong page.
    """
    return [
        Citation(
            n=i,
            span_id=c.span_id,
            document_id=c.document_id,
            document_name=c.document_name,
            page=c.page_from,
            section=c.section_path,
            label=c.citation_label,
        )
        for i, c in enumerate(chunks, start=1)
    ]


def excerpt_for(
    chunk: IndexedChunk, relevance: float, *, parent: IndexedChunk | None = None
) -> Excerpt:
    """FR-12's excerpt: the retrieved text, with context either side of it.

    `before` and `after` come from the chunk's **parent section** when one was
    retrieved. The child is the span that matched and the span the citation
    resolves to, so it has to stay `text` exactly — but a child is a ~500-token
    fragment, and shown alone it reads as a sentence torn out of a clause. The
    parent is the surrounding section and is already in memory from expansion,
    so the context costs nothing beyond locating the child inside it.

    Located by substring rather than by the stored offsets: the child's offsets
    are page-local and the parent may span pages, so subtracting one from the
    other is only correct when both start on the same page. A substring search
    is right in both cases and degrades to no context instead of to wrong
    context.
    """
    text = chunk.text.strip()
    before = after = ""
    if parent is not None and parent.chunk_id != chunk.chunk_id:
        at = parent.text.find(text)
        if at >= 0:
            before = parent.text[max(0, at - EXCERPT_PAD) : at]
            after = parent.text[at + len(text) : at + len(text) + EXCERPT_PAD]
    return Excerpt(
        span_id=chunk.span_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        page=chunk.page_from,
        section=chunk.section_path,
        before=before,
        text=text[: EXCERPT_PAD * 4],
        after=after,
        relevance=round(relevance, 3),
    )
