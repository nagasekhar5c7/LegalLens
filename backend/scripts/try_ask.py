#!/usr/bin/env python
"""Ask a question against the indexed corpus and watch the stream.

The live counterpart to `tests/test_ask_workflow.py`, which runs offline
against fakes. This one spends money: one Haiku call for the rewrite (only when
history is supplied) and one Sonnet call for the answer.

    export ANTHROPIC_API_KEY=sk-ant-...            # or put it in backend/.env
    uv run python scripts/index_corpus.py ../docs  # once

    uv run python scripts/try_ask.py "What is the liability cap?"

    # a follow-up, to exercise the rewriter's guardrails
    uv run python scripts/try_ask.py --history "What does the MSA say about termination?" \
        "Does it require written notice?"

    # scope to specific documents
    uv run python scripts/try_ask.py --docs doc_ab12 "What is the cap?"

Prints every event as it arrives, then the trace: what was retrieved, what was
cited, what the gate stripped or refused, and what it cost.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.orchestration import AskOutcome, AskWorkflow, QueryRewriter  # noqa: E402
from app.ai.orchestration.retrieval import factory  # noqa: E402
from app.ai.orchestration.settings import get_router_settings  # noqa: E402

BOLD, DIM, GREEN, YELLOW, RED, CYAN, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[36m",
    "\033[0m",
)


def _all_document_ids(store: object) -> list[str]:
    """Every indexed document. The default scope is workspace-wide (FR-9)."""
    client = store.client  # type: ignore[attr-defined]
    collection = store.collection  # type: ignore[attr-defined]
    if not client.collection_exists(collection):
        return []
    found: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, limit=512, offset=offset, with_payload=True
        )
        for point in points:
            doc = (point.payload or {}).get("document_id")
            if doc:
                found.add(str(doc))
        if offset is None:
            break
    return sorted(found)


async def run(args: argparse.Namespace) -> int:
    cfg = get_router_settings()
    if not cfg.has_credentials:
        print(
            f"{RED}No Anthropic credentials.{RESET} This script calls the real API.\n"
            "  Put ANTHROPIC_API_KEY in backend/.env, or export it.\n"
            f"{DIM}The offline suite needs no key:  "
            f"uv run pytest tests/test_ask_workflow.py{RESET}",
            file=sys.stderr,
        )
        return 2

    store = factory.get_store(cfg)
    if store.count() == 0:
        print(
            f"{RED}The index is empty.{RESET}\n  uv run python scripts/index_corpus.py <folder>",
            file=sys.stderr,
        )
        return 2

    document_ids = args.docs or _all_document_ids(store)
    history = tuple(("user", h) for h in (args.history or []))

    print(f"{DIM}loading bge-m3 + bge-reranker-v2-m3 (first run downloads ~4.5GB)...{RESET}")
    retriever = factory.build_retriever(cfg)
    retriever.embedder.warm()  # type: ignore[attr-defined]
    retriever.reranker.warm()  # type: ignore[attr-defined]

    workflow = AskWorkflow(
        retriever=retriever,
        rewriter=QueryRewriter(cfg),
        settings=cfg,
    )

    print(f"\n{BOLD}{args.question}{RESET}")
    if history:
        for _, turn in history:
            print(f"{DIM}  history: {turn}{RESET}")
    print(
        f"{DIM}scope: {len(document_ids)} document(s) · "
        f"{cfg.ask_model} over {cfg.embed_model}{RESET}\n"
    )

    outcome = AskOutcome()
    answer_started = False

    async for event in workflow.run(
        args.question,
        document_ids=document_ids,
        history=history,
        trace_id="try-ask",
        outcome=outcome,
    ):
        if event.type == "stage":
            if event.stage.status != "done":
                continue
            detail = f"  {DIM}{event.stage.detail}{RESET}" if event.stage.detail else ""
            print(f"  {CYAN}{event.stage.label:<28}{RESET}{event.stage.elapsed_ms:>7.0f}ms{detail}")
        elif event.type == "excerpt":
            e = event.excerpt
            where = f"{e.document_name} p.{e.page}" + (f" {e.section}" if e.section else "")
            print(f"    {DIM}[{e.relevance:.2f}] {where}: {e.text[:88]}...{RESET}")
        elif event.type == "citation":
            print(f"    {GREEN}[[{event.citation.n}]]{RESET} {DIM}{event.citation.label}{RESET}")
        elif event.type == "token":
            if not answer_started:
                print(f"\n{BOLD}Answer{RESET}\n")
                answer_started = True
            print(event.text, end="", flush=True)
        elif event.type == "refusal":
            r = event.refusal
            print(f"\n{YELLOW}REFUSAL ({r.kind}){RESET}\n  {r.message}")
            if r.best_relevance is not None:
                print(f"  {DIM}best relevance {r.best_relevance:.3f} vs floor {r.threshold}{RESET}")
            for s in r.suggestions:
                print(f"  {DIM}- {s.label}{RESET}")
        elif event.type == "error":
            err = event.error
            print(f"\n{RED}ERROR ({err.kind}){RESET}\n  {err.message}\n  {DIM}{err.action}{RESET}")
        elif event.type == "usage":
            u = event.usage
            print(
                f"\n\n{BOLD}Cost{RESET}  {u.input_tokens} in / {u.output_tokens} out  "
                f"${u.usd:.4f}  {DIM}({u.usd / 0.05:.0%} of the $0.05 NFR-8 Ask budget){RESET}"
            )
            print(f"{DIM}  versions: {event.versions.model} · {event.versions.prompt}{RESET}")

    # ------------------------------------------------------------- the trace
    print(f"\n{BOLD}Trace{RESET}")
    if outcome.rewrite is not None:
        rw = outcome.rewrite
        mark = f"{GREEN}rewritten{RESET}" if rw.rewritten else f"{DIM}unchanged{RESET}"
        print(f"  rewrite      {mark} ({rw.source}) {DIM}{rw.reasoning}{RESET}")
        if rw.rewritten:
            print(f"               {DIM}{rw.original!r} -> {rw.query!r}{RESET}")
        if rw.error:
            print(f"               {YELLOW}{rw.error}{RESET}")
    if outcome.retrieval is not None:
        r = outcome.retrieval
        print(
            f"  retrieval    {r.candidate_count} candidates -> {len(r.hits)} above floor "
            f"-> {len(r.context)} parents, ~{r.context_tokens} tokens"
            + (f" ({r.dropped_parents} dropped)" if r.dropped_parents else "")
        )
        print(f"               {DIM}{ {k: round(v) for k, v in r.timings_ms.items()} }{RESET}")
    print(f"  citations    {[c.n for c in outcome.citations]}")
    if outcome.stripped_citations:
        print(f"  {YELLOW}stripped     {outcome.stripped_citations} (unresolvable){RESET}")
    if outcome.fabricated:
        print(f"  {RED}FR-22        {outcome.fabricated}{RESET}")
    if outcome.uncited_claims:
        print(f"  {YELLOW}uncited      {outcome.uncited_claims} claim(s) with no citation{RESET}")
    if outcome.groundedness:
        print(f"  groundedness {DIM}{outcome.groundedness}{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument(
        "--history",
        action="append",
        help="a prior user turn; repeat for several. Exercises the rewriter.",
    )
    parser.add_argument("--docs", action="append", help="limit scope to these document ids")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    try:
        code = main()
    finally:
        factory.reset()
    raise SystemExit(code)
