#!/usr/bin/env python
"""Exercise the intent router against the real Claude Haiku 4.5 endpoint.

This is the paid, live counterpart to `tests/test_intent_router.py`, which runs
offline against a fake model. It is a script and not a test so that CI never
needs a key and nobody is billed by a `pytest` run.

    export ANTHROPIC_API_KEY=sk-ant-...

    # the whole gold set from the prompt file, scored
    uv run python scripts/try_intent_router.py

    # one request
    uv run python scripts/try_intent_router.py "draft me a memo on the renewal option"

    # one request, as if the attorney had pressed a mode chip
    uv run python scripts/try_intent_router.py --mode summary "what is the cap?"

Accuracy is reported separately for cases the prompt states outright and cases
it does not. Averaging the two flatters the router: only the held-out number
says anything about generalisation.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.orchestration import (  # noqa: E402
    INTENTS,
    IntentDecision,
    IntentRouter,
    RouterInput,
    ScopeFacts,
    get_router_settings,
    load_prompt,
)

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[31m",
    "\033[32m",
    "\033[33m",
    "\033[0m",
)


def _show(decision: IntentDecision, *, expected: str | None = None) -> bool:
    ok = expected is None or decision.intent == expected
    mark = f"{GREEN}pass{RESET}" if ok else f"{RED}FAIL{RESET}"
    target = "" if expected is None else f" expected={expected}"
    colour = GREEN if decision.is_confident else YELLOW
    print(
        f"  {mark} {BOLD}{decision.intent:<13}{RESET}"
        f"{colour}conf={decision.confidence:.2f}{RESET}"
        f" {DIM}src={decision.source} {decision.usage.latency_ms:.0f}ms"
        f" ${decision.usage.usd:.5f}{RESET}{target}"
    )
    print(f"       {DIM}{decision.rationale}{RESET}")
    if decision.error:
        print(f"       {RED}error: {decision.error}{RESET}")
    return ok


async def _one(router: IntentRouter, text: str, mode: str) -> None:
    print(f"\n{BOLD}{text}{RESET}  {DIM}(mode={mode}){RESET}")
    decision = await router.aclassify(
        RouterInput(
            text=text,
            mode=mode,  # type: ignore[arg-type]
            scope=ScopeFacts(
                document_count=3,
                document_types=("contract", "opinion"),
                single_document_in_focus=False,
            ),
        )
    )
    _show(decision)


async def _gold_set(router: IntentRouter, *, concurrency: int) -> int:
    prompt = load_prompt()
    cases = prompt.examples
    print(
        f"{BOLD}{len(cases)} cases{RESET} "
        f"({len(prompt.taught_examples)} stated in the prompt, "
        f"{len(prompt.held_out_examples)} held out) "
        f"· model={router.model_name} · prompt={router.prompt_version}\n"
    )

    gate = asyncio.Semaphore(concurrency)

    async def run(case: object) -> tuple[object, IntentDecision]:
        async with gate:
            decision = await router.aclassify(
                RouterInput(
                    text=case.question,  # type: ignore[attr-defined]
                    mode=case.mode,  # type: ignore[attr-defined]
                    scope=ScopeFacts(
                        document_count=1,
                        document_types=("contract",),
                        single_document_in_focus=True,
                    ),
                )
            )
            return case, decision

    results = await asyncio.gather(*(run(c) for c in cases))

    confusion: Counter[tuple[str, str]] = Counter()
    scores = {"taught": [0, 0], "held out": [0, 0]}
    total_usd = 0.0
    latencies: list[float] = []
    failures: list[str] = []

    for case, decision in results:
        group = "taught" if case.taught else "held out"  # type: ignore[attr-defined]
        ok = _show(decision, expected=case.expected)  # type: ignore[attr-defined]
        print(f"       {DIM}{case.question}{RESET}")  # type: ignore[attr-defined]
        scores[group][1] += 1
        if ok:
            scores[group][0] += 1
        else:
            failures.append(
                f"{case.question!r}: got {decision.intent}, "  # type: ignore[attr-defined]
                f"expected {case.expected}"  # type: ignore[attr-defined]
            )
        confusion[(case.expected, decision.intent)] += 1  # type: ignore[attr-defined]
        total_usd += decision.usage.usd
        if decision.used_model:
            latencies.append(decision.usage.latency_ms)

    print(f"\n{BOLD}Accuracy{RESET}")
    for group, (hits, n) in scores.items():
        if n:
            print(f"  {group:<10} {hits}/{n}  ({hits / n:.0%})")
    hits = sum(v[0] for v in scores.values())
    n = sum(v[1] for v in scores.values())

    print(f"\n{BOLD}Confusion (expected -> predicted){RESET}")
    header = "  " + " ".join(f"{label:>12}" for label in INTENTS)
    print(f"  {'':<13}{header}")
    for expected in INTENTS:
        row = " ".join(f"{confusion[(expected, got)]:>12}" for got in INTENTS)
        print(f"  {expected:<13}  {row}")

    if latencies:
        latencies.sort()
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(
            f"\n{BOLD}Cost and latency{RESET}\n"
            f"  model calls   {len(latencies)}\n"
            f"  p50 latency   {latencies[len(latencies) // 2]:.0f}ms\n"
            f"  p95 latency   {p95:.0f}ms  (NFR-1 budgets 2000ms to first token, "
            f"and retrieval has not started yet)\n"
            f"  total         ${total_usd:.4f}\n"
            f"  per call      ${total_usd / len(latencies):.5f}  "
            f"({total_usd / len(latencies) / 0.05:.1%} of the $0.05 NFR-8 Ask budget)"
        )

    if failures:
        print(f"\n{BOLD}{RED}Misroutes{RESET}")
        for line in failures:
            print(f"  - {line}")

    print(f"\n{BOLD}{hits}/{n} correct{RESET}")
    return 0 if hits == n else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="*", help="one request; omit to run the gold set")
    parser.add_argument("--mode", default="auto", choices=["auto", "ask", "summary", "draft"])
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    settings = get_router_settings()
    if not settings.has_credentials:
        print(
            f"{RED}No Anthropic credentials.{RESET} This script calls the real API.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "The offline suite needs no key:  uv run pytest tests/test_intent_router.py",
            file=sys.stderr,
        )
        return 2

    router = IntentRouter(settings)
    if args.text:
        asyncio.run(_one(router, " ".join(args.text), args.mode))
        return 0
    return asyncio.run(_gold_set(router, concurrency=args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
