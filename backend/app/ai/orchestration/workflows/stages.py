"""Stage labels and the stage-event helper.

Duplicated from `FixtureEngine` rather than imported from it. The fixture engine
is scheduled for deletion once this layer is wired in, and importing it here
would make the thing that replaces it depend on the thing it replaces.
`tests/test_ask_workflow.py` asserts the two label maps still agree, so the
duplication cannot drift while both exist.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Literal

from app.schemas.common import Stage, StageKey
from app.schemas.events import StageEvent, StreamEvent

STAGE_LABEL: dict[StageKey, str] = {
    "routing": "Routing the question",
    "classifying": "Resolving document type",
    "retrieving": "Hybrid retrieval",
    "field_retrieval": "Retrieving per template field",
    "reranking": "Cross-encoder rerank",
    "expanding": "Parent-document expansion",
    "assembling": "Assembling context",
    "mapping": "Extracting fields",
    "composing": "Composing sections",
    "writing": "Writing",
    "verifying": "Verifying citations",
}


class StageClock:
    """Emits the `active` / `done` pair every stage needs, timed from one t0.

    `elapsed_ms` is measured from the start of the turn rather than the start of
    the stage, because NFR-11 wants latency per stage *within* a request and the
    frontend renders a cumulative timeline.
    """

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.timings: dict[str, float] = {}

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def event(
        self, key: StageKey, status: Literal["active", "done"], detail: str | None = None
    ) -> StageEvent:
        return StageEvent(
            stage=Stage(
                key=key,
                label=STAGE_LABEL[key],
                detail=detail,
                status=status,
                elapsed_ms=self.elapsed_ms,
            )
        )

    async def run(self, key: StageKey, detail: str | None = None) -> AsyncIterator[StreamEvent]:
        """A stage that has already happened: both events, back to back."""
        started = self.elapsed_ms
        yield self.event(key, "active", detail)
        yield self.event(key, "done", detail)
        self.timings[f"{key}_ms"] = self.elapsed_ms - started

    def open(self, key: StageKey, detail: str | None = None) -> StageEvent:
        """For a stage that spans streaming — `writing` opens, then closes."""
        self.timings[f"{key}_start_ms"] = self.elapsed_ms
        return self.event(key, "active", detail)

    def close(self, key: StageKey, detail: str | None = None) -> StageEvent:
        start = self.timings.pop(f"{key}_start_ms", self.elapsed_ms)
        self.timings[f"{key}_ms"] = self.elapsed_ms - start
        return self.event(key, "done", detail)
