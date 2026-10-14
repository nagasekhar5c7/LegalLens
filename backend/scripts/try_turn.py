#!/usr/bin/env python
"""Run one turn through the real `OrchestratedEngine` and watch the stream.

`try_ask.py` drives the ask workflow directly. This drives the **engine**, which
is the integration: routing, the turn's envelope events, the empty-scope and
out-of-scope refusals, workflow dispatch, and the `TurnTrace` the route
persists. Everything but the database and the HTTP layer.

It spends money: one Haiku call to route (unless a mode is forced), one Haiku
call to rewrite (only with history), then Sonnet for generation — one call for
ask, one per field for summary, one per section for draft.

    export ANTHROPIC_API_KEY=sk-ant-...            # or put it in backend/.env
    uv run python scripts/index_corpus.py ../docs  # once

    uv run python scripts/try_turn.py "What is the liability cap?"
    uv run python scripts/try_turn.py --mode summary "Summarise it"
    uv run python scripts/try_turn.py --mode draft \
        --docs doc_ab12 "Draft a memo on the cap"

    # let the router decide, and watch it do so
    uv run python scripts/try_turn.py "draft me a memo about termination"

The trace printed at the end is what lands in `traces.timings_json` and the
`usage_transactions` rows — so a cost or latency surprise in production is
reproducible here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.orchestrated_engine import OrchestratedEngine, unavailable_reason  # noqa: E402
from app.ai.orchestration.retrieval import factory  # noqa: E402
from app.ai.orchestration.settings import get_router_settings  # noqa: E402
from app.ai.ports import AiRequest, ScopedDocument  # noqa: E402
from app.schemas.common import ScopeUsed  # noqa: E402
from app.schemas.envelope import DraftParams  # noqa: E402

BOLD, DIM, GREEN, YELLOW, RED, CYAN, MAGENTA, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[36m",
    "\033[35m",
    "\033[0m",
)


def _indexed_documents(store: object) -> list[ScopedDocument]:
    """Every indexed document, as the scope resolver would hand them over.

    Read out of the vector store's payloads rather than the database, because
    this script deliberately has no database: the engine's contract is that it
    receives resolved documents and never queries for them, and a harness that
    quietly read the database would not be testing that contract.
    """
    client = store.client  # type: ignore[attr-defined]
    collection = store.collection  # type: ignore[attr-defined]
    if not client.collection_exists(collection):
        return []

    found: dict[str, dict[str, object]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, limit=512, offset=offset, with_payload=True
        )
        for point in points:
            payload = point.payload or {}
            document_id = payload.get("document_id")
            if not document_id:
                continue
            entry = found.setdefault(
                str(document_id),
                {
                    "name": str(payload.get("document_name") or document_id),
                    "type": str(payload.get("document_type") or "other"),
                    "pages": 0,
                },
            )
            entry["pages"] = max(int(entry["pages"]), int(payload.get("page_to") or 0))
        if offset is None:
            break

    return [
        ScopedDocument(
            id=document_id,
            name=str(entry["name"]),
            type=str(entry["type"]),
            pages=int(entry["pages"]),
            status="ready",
        )
        for document_id, entry in sorted(found.items())
    ]


async def run(args: argparse.Namespace) -> int:
    cfg = get_router_settings()
    reason = unavailable_reason(cfg)
    if reason is not None:
        print(f"{RED}The AI layer is not usable:{RESET} {reason}", file=sys.stderr)
        print(
            f"{DIM}The offline suite needs neither a key nor a model:\n"
            f"  uv run pytest tests/test_orchestrated_engine.py{RESET}",
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

    documents = _indexed_documents(store)
    if args.docs:
        wanted = set(args.docs)
        documents = [d for d in documents if d.id in wanted]
        if not documents:
            print(f"{RED}None of {args.docs} is indexed.{RESET}", file=sys.stderr)
            return 2

    kind = "document" if len(documents) == 1 else "workspace"
    request = AiRequest(
        text=args.question,
        mode=args.mode,
        scope_used=ScopeUsed(
            kind=kind,
            document_ids=[d.id for d in documents],
            document_count=len(documents),
            page_count=sum(d.pages for d in documents),
            excluded=[],
        ),
        documents=documents,
        conversation_id="conv_try",
        trace_id="tr_try_turn",
        user_id="usr_try",
        history=tuple(("user", h) for h in (args.history or [])),
        draft_params=(
            DraftParams(
                draft_type=args.draft_type,
                question_presented=args.question,
                document_ids=[d.id for d in documents],
            )
            if args.mode == "draft"
            else None
        ),
    )

    print(f"{DIM}loading bge-m3 + bge-reranker-v2-m3 (first run downloads ~4.5GB)...{RESET}")
    engine = OrchestratedEngine(cfg)
    retriever = engine._retriever()
    retriever.embedder.warm()  # type: ignore[attr-defined]
    retriever.reranker.warm()  # type: ignore[attr-defined]

    print(f"\n{BOLD}{args.question}{RESET}")
    for turn in request.history:
        print(f"{DIM}  history: {turn[1]}{RESET}")
    print(
        f"{DIM}mode: {args.mode} · scope: {len(documents)} document(s), "
        f"{request.scope_used.page_count} pages · engine: {engine.name}{RESET}\n"
    )

    # The route's own dispatch, reproduced: route first unless a mode forces it.
    if args.mode == "auto":
        intent = await engine.route_intent(request)
        routing = request.trace.detail["routing"]
        print(
            f"  {MAGENTA}routed -> {intent}{RESET}  "
            f"{DIM}({routing['source']}, confidence {routing['confidence']}){RESET}\n"
            f"  {DIM}{routing['rationale']}{RESET}\n"
        )
        port = {"summary": engine.summarize, "draft": engine.draft}.get(intent, engine.answer)
    else:
        port = {"summary": engine.summarize, "draft": engine.draft}.get(args.mode, engine.answer)

    answer_started = False
    async for event in port(request):
        if event.type == "stage":
            if event.stage.status != "done":
                continue
            detail = f"  {DIM}{event.stage.detail}{RESET}" if event.stage.detail else ""
            print(f"  {CYAN}{event.stage.label:<28}{RESET}{event.stage.elapsed_ms:>7.0f}ms{detail}")
        elif event.type == "intent":
            print(f"  {DIM}intent: {event.intent} (mode {event.mode}){RESET}")
        elif event.type == "scope":
            s = event.scope_used
            print(
                f"  {DIM}scope: {s.kind} · {s.document_count} docs · "
                f"{s.page_count} pages · {len(s.excluded)} excluded{RESET}"
            )
        elif event.type == "excerpt":
            e = event.excerpt
            where = f"{e.document_name} p.{e.page}" + (f" {e.section}" if e.section else "")
            print(f"    {DIM}[{e.relevance:.2f}] {where}: {e.text[:80]}...{RESET}")
        elif event.type == "citation":
            print(f"    {GREEN}[[{event.citation.n}]]{RESET} {DIM}{event.citation.label}{RESET}")
        elif event.type == "token":
            if not answer_started:
                print(f"\n{BOLD}Answer{RESET}\n")
                answer_started = True
            print(event.text, end="", flush=True)
        elif event.type == "summary_start":
            print(f"\n{BOLD}{event.summary.template_name}{RESET}")
            print(f"{DIM}{event.summary.document_name} · {event.field_count} fields{RESET}\n")
        elif event.type == "summary_field":
            f = event.field
            if f.value_md is None:
                print(f"  {YELLOW}{f.label}{RESET}  {DIM}not addressed{RESET}")
            else:
                flag = f" {RED}[{f.flag}]{RESET}" if f.flag else ""
                print(f"  {BOLD}{f.label}{RESET}{flag}  {DIM}{f.citations}{RESET}")
                print(f"    {f.value_md.replace(chr(10), chr(10) + '    ')}")
        elif event.type == "draft_start":
            print(f"\n{BOLD}{event.draft.title}{RESET}  {DIM}({event.draft.draft_type}){RESET}\n")
        elif event.type == "draft_section":
            s = event.section
            print(f"  {BOLD}{s.heading}{RESET}  {DIM}{s.citations}{RESET}")
            print(f"    {s.body_md.replace(chr(10), chr(10) + '    ')}\n")
        elif event.type == "coverage":
            c = event.coverage
            print(
                f"\n  {DIM}coverage: {c.pages_covered}/{c.total_pages} pages, "
                f"{c.passes} passes{RESET}"
            )
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
                f"\n\n{BOLD}Cost{RESET}  {u.input_tokens} in / {u.output_tokens} out  ${u.usd:.4f}"
            )
            print(f"{DIM}  versions: {event.versions.model} · {event.versions.prompt}{RESET}")

    # ------------------------------------------------- the trace the route persists
    trace = request.trace
    print(
        f"\n{BOLD}Trace{RESET}  "
        f"{DIM}(this is what lands in `traces` and `usage_transactions`){RESET}"
    )
    print(f"  queries      {trace.queries}")
    kept = sum(1 for c in trace.candidates if c["kept"])
    print(f"  candidates   {len(trace.candidates)} scored, {kept} above the floor")
    for candidate in trace.candidates[:8]:
        mark = f"{GREEN}kept{RESET}" if candidate["kept"] else f"{YELLOW}cut {RESET}"
        print(
            f"    {mark} {candidate['score']:.3f}  {DIM}{candidate['document_id']} "
            f"p.{candidate['page']}{RESET}"
        )
    if trace.calls:
        print(f"  {BOLD}calls{RESET}")
        total = 0.0
        for call in trace.calls:
            total += float(call["usd"])
            print(
                f"    {call['stage']:<22} {call['model']:<18} "
                f"{call['input_tokens']:>7} in / {call['output_tokens']:>5} out  "
                f"${float(call['usd']):.4f}"
            )
        print(f"    {BOLD}{'total':<22} {'':<18} {'':>7}      {'':>5}      ${total:.4f}{RESET}")
    if args.verbose and trace.detail:
        rendered = json.dumps(trace.detail, indent=4, default=str)
        print(f"  {BOLD}detail{RESET}\n{DIM}{rendered}{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "ask", "summary", "draft"],
        help="auto routes with Haiku; anything else skips the router, as a mode chip does",
    )
    parser.add_argument("--draft-type", default="memo", choices=["memo", "case_brief"])
    parser.add_argument("--history", action="append", help="a prior user turn; repeat for several")
    parser.add_argument("--docs", action="append", help="limit scope to these document ids")
    parser.add_argument("--verbose", "-v", action="store_true", help="also dump the trace detail")
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
