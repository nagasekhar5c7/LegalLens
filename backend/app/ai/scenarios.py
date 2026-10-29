"""Forceable states, ported from `frontend/src/mocks/scenarios.ts`.

The frontend validated every non-happy path through its Demo menu. Moving the
mock server-side would have broken that, so the menu now sends
`X-LegalLens-Scenario` and the engine honours it. Dev only — `deps.scenario`
returns None when ENV=production.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

Scenario = Literal[
    "auto",
    "refusal",
    "llm_down",
    "rate_limited",
    "timeout",
    "interrupted",
    "slow",
]

SCENARIOS: Final[tuple[str, ...]] = get_args(Scenario)


def is_scenario(value: str) -> bool:
    return value in SCENARIOS


#: Errors land *after* retrieval, which is exactly why excerpts survive an
#: outage (NFR-6). Keyed by scenario; mirrors ERRORS in mocks/stream.ts.
SCENARIO_ERRORS: Final[dict[str, dict[str, object]]] = {
    "llm_down": {
        "kind": "llm_unavailable",
        "message": (
            "The language model provider is not responding. Retrieval ran and found "
            "supporting passages, but no answer could be generated."
        ),
        "action": (
            "Retry, or read the retrieved passages directly — they are listed in the "
            "evidence panel."
        ),
        "excerpts_available": True,
    },
    "rate_limited": {
        "kind": "rate_limited",
        "message": "Rate limit reached on the model provider. This request was not charged.",
        "action": "Retry in 24 seconds, or narrow the scope to reduce the request size.",
        "retry_after_sec": 24,
        "excerpts_available": True,
    },
    "timeout": {
        "kind": "timeout",
        "message": (
            "Generation exceeded the 60-second budget and was abandoned. No partial answer "
            "is being shown, because a truncated legal answer is worse than none."
        ),
        "action": "Retry with a narrower scope, or ask about one document at a time.",
        "excerpts_available": True,
    },
}

#: Scenario `slow` multiplies every delay, for checking that the stage trace
#: carries a long wait (NFR-3's three-minute summary budget).
SLOW_FACTOR: Final[float] = 4.0
