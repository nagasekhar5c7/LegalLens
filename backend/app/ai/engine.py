"""The one place the concrete AI engine is chosen.

NFR-14 says the backend reaches the AI layer only through `answer()`,
`summarize()` and `draft()`. Routes therefore import `get_engine()` from here
and receive something typed as `AiEngine` — they never name an implementation.

**This is the M4 swap point, and M4 has landed.** `LL_AI_ENGINE` selects:

    orchestrated  router + ask/summary/draft over bge-m3, Qdrant and Sonnet 5
    fixture       the M3 fixture engine: scripted answers, real event contract
    auto          orchestrated when it can actually run, fixture otherwise

The three-value shape rather than a boolean is the same decision as
`LL_INDEXER` and `LL_OCR_ENGINE`, for the same reason. `uv sync` with no extras
must produce a startable backend, and that backend has no model provider and no
vector store — so `auto` has to be able to degrade. But a demo that silently
answers from scripted fixtures looks exactly like one that works, which is the
worst possible failure for a product whose pitch is that nothing is invented.
So `orchestrated` is honoured **even when unavailable**: the turn fails with the
reason rather than quietly serving a fixture, `/readyz` reports which engine
resolved and why, and the fallback only ever happens where it was asked for.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.ai.ports import AiEngine

log = logging.getLogger("legallens.ai.engine")

ENGINES = ("auto", "orchestrated", "fixture")


@lru_cache
def get_engine() -> AiEngine:
    """The process-wide AI layer.

    Cached because an engine is stateless configuration, not per-request state,
    and because the orchestrated engine holds warmed model clients and a Qdrant
    handle that must not be rebuilt per turn. Construction is cheap and lazy —
    nothing is loaded until the first turn — so this is safe to call from
    `/readyz` on a process that never serves one.
    """
    name = _configured()
    if name not in ENGINES:
        # A typo that silently serves fixtures for every turn is the kind of
        # failure found weeks later by an attorney wondering why every answer
        # cites the same MSA. Raise instead.
        raise ValueError(f"LL_AI_ENGINE must be one of {ENGINES}, not {name!r}")

    if name == "fixture":
        from app.ai.fixture_engine import FixtureEngine

        return FixtureEngine()

    from app.ai.orchestrated_engine import OrchestratedEngine, unavailable_reason

    reason = unavailable_reason()
    if name == "orchestrated":
        # Returned even when unavailable, deliberately. See the module docstring.
        if reason is not None:
            log.warning(
                "LL_AI_ENGINE=orchestrated but the AI layer is not usable (%s) — turns will "
                "fail with that reason rather than falling back to fixtures",
                reason,
            )
        return OrchestratedEngine()

    if reason is None:
        return OrchestratedEngine()

    from app.ai.fixture_engine import FixtureEngine

    fixture = FixtureEngine()
    log.warning(
        "no usable AI layer, falling back to %s — answers will be scripted (%s)",
        fixture.name,
        reason,
    )
    return fixture


def reset_engine() -> None:
    """Drop the cached engine. For tests that swap in a double, and for a
    configuration change in a long-lived process."""
    get_engine.cache_clear()


def _configured() -> str:
    from app.ai.orchestration.settings import get_router_settings

    return get_router_settings().engine.strip().lower()


def describe() -> dict[str, Any]:
    """What `/readyz` reports about generation.

    Reports the *resolved* engine next to the configured one, because those
    differing is exactly the situation an operator needs to see: `auto` plus a
    missing API key is a backend that starts, serves, and answers every question
    from a fixture.
    """
    from app.ai.orchestrated_engine import unavailable_reason

    try:
        configured = _configured()
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        resolved = get_engine()
    except Exception as exc:  # noqa: BLE001
        return {"configured": configured, "error": f"{type(exc).__name__}: {exc}"}

    reason = unavailable_reason()
    return {
        "configured": configured,
        "resolved": resolved.name,
        "promptVersion": resolved.prompt_version,
        "modelVersion": resolved.model_version,
        "indexVersion": resolved.index_version,
        "orchestration": reason or "available",
        # The one line an operator actually needs: are the answers real?
        "grounded": not resolved.name.startswith("fixture"),
    }
