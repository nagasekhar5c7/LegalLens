"""Router configuration, separate from `app.config`.

Its own `BaseSettings` rather than new fields on `Settings`: this module is not
wired into the app yet, and adding `LL_` fields to the backend's settings would
make an unintegrated module part of the backend's startup contract. The prefix
is `LL_AI_`, so merging it later is a copy of this class into `app/config.py`
with no renaming.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: `backend/`, so the router finds `backend/.env` whichever directory it is run
#: from. `env_file=".env"` alone resolves against the current working
#: directory, which silently loads nothing when a script is run from the repo
#: root — and a missing key then looks like a code problem rather than a path
#: problem.
BACKEND_ROOT = Path(__file__).resolve().parents[3]


class RouterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LL_AI_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `api_key` is reached by alias, and an alias *replaces* the field name
        # unless this is set — so `RouterSettings(api_key=...)` silently left
        # the key empty. Tests only passed because they inject a fake model and
        # never build a real client.
        populate_by_name=True,
    )

    #: Claude Haiku 4.5 — chosen because intent classification is a single
    #: short-output call where the cheapest current model is enough, and
    #: routing sits on the NFR-1 critical path (p95 TTFT <= 2s) before any
    #: retrieval has started.
    model: str = "claude-haiku-4-5"

    #: ANTHROPIC_API_KEY first, because that is what the Anthropic SDK and
    #: every CI runner already set; LL_AI_API_KEY second, for consistency with
    #: the other fields here.
    #:
    #: `AliasChoices` and not a bare `validation_alias`: a single alias
    #: *replaces* the `LL_AI_` prefix rather than adding to it, so
    #: LL_AI_API_KEY was being read as unset while appearing to be supported.
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "LL_AI_API_KEY"),
    )

    #: 0.0 — a classifier that returns different labels for the same question
    #: cannot be evaluated, and NFR-12's promise of a reproducible answer would
    #: be false. Haiku 4.5 accepts sampling parameters; the 4.6+ models do not.
    temperature: float = 0.0

    #: Enough for a 30-word rationale, a label and a float, with headroom. A
    #: truncated response is a parse failure, not a short answer, so this is
    #: deliberately not trimmed to the minimum.
    max_tokens: int = 512

    #: Routing happens before the user sees anything, so it gets a small slice
    #: of LL_GENERATION_BUDGET_S. Past this the heuristic is better than a wait.
    timeout_s: float = 8.0

    #: The SDK retries 429 and 5xx with backoff. Two retries inside an 8s
    #: timeout is the useful range; more just burns the budget.
    max_retries: int = 2

    #: Below this the caller should offer a mode chip rather than route
    #: silently. Mirrors `IntentDecision.is_confident`.
    confidence_floor: float = 0.70

    #: Turns of history sent to the classifier. Four covers "now as a memo"
    #: style follow-ups; more adds tokens and, with them, latency on the
    #: critical path.
    history_turns: int = 4

    #: Per-turn character cap on that history. A pasted contract in a previous
    #: turn would otherwise dominate the prompt and the bill.
    history_chars: int = 300

    #: What to do when the model cannot be reached or understood.
    #: `heuristic` — keyword rules, then `ask`. `ask` — go straight to `ask`.
    #: `raise` — propagate, for an eval harness that must not score a fallback
    #: as a prediction.
    on_failure: Literal["heuristic", "ask", "raise"] = "heuristic"

    # --- pre-retrieval: query rewrite ------------------------------------
    #: Haiku again. Resolving "and the indemnity?" against the previous turn is
    #: a reference-substitution task, not a reasoning one.
    rewrite_model: str = "claude-haiku-4-5"

    #: A rewrite longer than this is discarded in favour of the original. The
    #: prompt caps it at ~40 words; this is the check that it complied, because
    #: a rewrite that quietly widens the search is invisible to the attorney.
    rewrite_max_words: int = 60

    # --- retrieval -------------------------------------------------------
    #: bge-m3: dense + learned sparse from one pass, 8192-token context, MIT.
    #: The context length is why it beat all-MiniLM-L6-v2, which truncates at
    #: 256 and would silently drop half of every child chunk.
    embed_model: str = "BAAI/bge-m3"
    #: Same family, same context window, Apache-2.0.
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    #: fp16 is a CUDA optimisation; on CPU several kernels fall back or raise.
    model_fp16: bool = False
    #: None lets the library choose (mps on Apple Silicon, else cpu).
    model_device: str | None = None

    #: Local embedded Qdrant. Takes an exclusive lock on the directory, so the
    #: indexing script and a running engine cannot share one path at once.
    qdrant_path: str = "data/qdrant"
    #: Set to run against a Qdrant server instead; `qdrant_path` is then ignored.
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "legallens_chunks"

    #: Candidates per hybrid query, fused by RRF before reranking.
    candidate_k: int = 25
    #: Survivors of the cross-encoder, expanded to parents.
    rerank_k: int = 10

    #: FR-14's refusal threshold, applied to the reranker's sigmoid-normalised
    #: score — never to an RRF rank, which is a position and not a relevance.
    #:
    #: **This number is not yet principled.** It carries over from the M3
    #: fixture's scale and needs calibrating against a labelled set, which is
    #: M5. Until then it is a knob with a defensible default.
    relevance_floor: float = 0.25

    #: Ceiling on assembled context. Parent expansion, not top-K, is what
    #: actually drives context size: ten children can expand to six parents of
    #: up to 12,000 characters each. Lowest-ranked parents are dropped first and
    #: the trace reports how many.
    context_token_budget: int = 18_000

    # --- generation: ask -------------------------------------------------
    #: Sonnet 5 for grounded answering.
    ask_model: str = "claude-sonnet-5"
    #: None, and None is the only safe default on Sonnet 5.
    #:
    #: Sampling parameters were **removed** on the 4.6-and-later models:
    #: sending `temperature` to claude-sonnet-5 returns
    #: `400 "temperature is deprecated for this model"`. Only models that still
    #: accept sampling (Haiku 4.5 and earlier) may set this, which is why it is
    #: opt-in rather than a number with a default.
    #:
    #: Consequence worth knowing for NFR-12: answer generation is not
    #: temperature-pinnable on Sonnet 5, so "reproducible" means the recorded
    #: prompt/model/index versions, not a byte-identical answer.
    ask_temperature: float | None = None
    ask_max_tokens: int = 4096
    #: Generous next to the router's 8s — this one streams, so the budget is
    #: for the whole answer rather than one short classification.
    ask_timeout_s: float = 120.0

    # --- the citation gate (FR-21, FR-22) --------------------------------
    #: Minimum lexical overlap between a cited sentence and its source.
    #: Reported on the trace by default rather than enforced: it is a proxy for
    #: entailment, and a proxy strict enough to catch real problems also strips
    #: correct citations whose wording was paraphrased.
    groundedness_floor: float = 0.25
    #: Turn the proxy into an enforcement rule. Off until M5 calibrates it.
    strip_ungrounded_citations: bool = False

    #: Ask for a cache breakpoint on the system prompt. Claude Haiku 4.5 will
    #: not cache a prefix below 4096 tokens and reports no error when it
    #: declines, so `router.py` checks the estimate and logs what happened
    #: rather than letting a dead marker look like a working optimisation.
    cache_system_prompt: bool = True

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key.strip())


@lru_cache
def get_router_settings() -> RouterSettings:
    return RouterSettings()
