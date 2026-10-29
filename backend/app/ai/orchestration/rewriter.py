"""Pre-retrieval query rewriting. Claude Haiku 4.5, one call per routed turn.

A follow-up like "and the indemnity?" retrieves nothing useful, because search
sees the question and not the conversation. This resolves the reference before
retrieval runs.

**The two guardrails are enforced twice, deliberately.** The prompt states them
so the model understands the task, and the code enforces the one that is
decidable without a model:

  empty history  ->  short-circuited here, zero tokens spent. A rule the code
                     can guarantee should not be delegated to a model that
                     might not follow it.
  independent /  ->  needs judgement, so it is the model's call. It comes back
  mutually           as `rewrite_needed=false`, and a false there is trusted:
  exclusive          the original question is used verbatim.

**A rewrite that fails is not an error.** The original question is a valid query
— that is what the system did before this stage existed. So every failure path
returns the question unchanged with `source` saying why, rather than failing the
turn. `on_failure="raise"` opts out for eval harnesses.

**Drift guard.** Even when the model says it rewrote, the result is rejected if
it is suspiciously long or has lost the question's scope qualifiers. The prompt
forbids both; this is the check that the prompt was obeyed, because a rewrite
that quietly widens the search is invisible to the attorney.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.orchestration.intent import Usage
from app.ai.orchestration.prompt import IntentPrompt, load_named_prompt
from app.ai.orchestration.router import _usage_from
from app.ai.orchestration.settings import RouterSettings, get_router_settings

log = logging.getLogger("legallens.ai.rewrite")

PROMPT_NAME = "query_rewrite"

#: Why the query that went to retrieval looks the way it does. On the trace, so
#: a bad retrieval can be told apart from a bad rewrite (NFR-11).
RewriteSource = Literal["no_history", "model_kept", "model_rewrote", "rejected", "failed"]

_HUMAN_TEMPLATE = """\
Conversation history (oldest first):
{history_block}

The attorney's latest question:
<question>
{question}
</question>

Produce the standalone question."""

#: Scope qualifiers a rewrite must not drop. The prompt forbids dropping them;
#: these are the ones cheap enough to verify mechanically.
#
# `§` sits outside the `\b` group on purpose: a word boundary before a
# non-word character requires a word character immediately before it, so
# `\b§` never matches "... § 4.2" and the qualifier was being lost silently.
_QUALIFIER_RE = re.compile(
    r"(?:\b(?:page|pp?\.|section|sec\.|clause|article|art\.|exhibit|schedule|appendix)|§)"
    r"\s*[\w.()\-]+",
    re.I,
)


# The model's docstring is sent as the schema description, so it is one line.
# Field order is load-bearing: `reasoning` is generated before the decision, so
# it reasons rather than justifies.
class QueryRewrite(BaseModel):
    """One rewriting decision for a follow-up question."""

    reasoning: str = Field(
        description="One sentence, under 25 words, naming what was resolved or why nothing was."
    )
    rewrite_needed: bool = Field(description="True only if the question was changed.")
    rewritten_question: str = Field(
        description="The standalone question. Exactly the original when rewrite_needed is false."
    )
    referents_resolved: list[str] = Field(
        default_factory=list,
        description='References replaced, e.g. ["it", "that clause"]. Empty when unchanged.',
    )


@dataclass(frozen=True)
class RewriteResult:
    """What retrieval should search for, and why."""

    #: The query to retrieve with. Never empty — falls back to the original.
    query: str
    original: str
    source: RewriteSource
    rewritten: bool = False
    reasoning: str = ""
    referents: tuple[str, ...] = ()
    usage: Usage = field(default_factory=Usage)
    error: str | None = None
    model: str | None = None
    prompt_version: str | None = None

    @property
    def used_model(self) -> bool:
        return self.source in {"model_kept", "model_rewrote", "rejected"}


class QueryRewriter:
    """Resolves references in a follow-up question against the conversation."""

    def __init__(
        self,
        settings: RouterSettings | None = None,
        *,
        chat_model: Any | None = None,
        prompt: IntentPrompt | None = None,
    ) -> None:
        self.settings = settings or get_router_settings()
        self.prompt = prompt or load_named_prompt(PROMPT_NAME)
        self.model_name = self.settings.rewrite_model
        self.prompt_version = self.prompt.version
        self._chain = self._build_chain(chat_model)

    def _build_chain(self, chat_model: Any | None) -> Any:
        from langchain_core.prompts import ChatPromptTemplate

        model = chat_model if chat_model is not None else self._build_model()
        structured = model.with_structured_output(
            QueryRewrite, method="json_schema", include_raw=True
        )
        template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", _HUMAN_TEMPLATE)]
        )
        return template | structured

    def _build_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic

        from app.ai.orchestration.router import RouterUnavailable

        if not self.settings.has_credentials:
            raise RouterUnavailable(
                "No Anthropic credentials. Set ANTHROPIC_API_KEY (or LL_AI_API_KEY)."
            )
        return ChatAnthropic(
            model=self.settings.rewrite_model,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            timeout=self.settings.timeout_s,
            max_retries=self.settings.max_retries,
            api_key=self.settings.api_key,
        )

    # ------------------------------------------------------------- rewriting

    async def arewrite(
        self, question: str, history: tuple[tuple[str, str], ...] = ()
    ) -> RewriteResult:
        """Guardrail 1 is checked before anything else, and costs nothing."""
        if not self._has_history(history):
            return RewriteResult(
                query=question.strip(),
                original=question.strip(),
                source="no_history",
                reasoning="No conversation history, so there is no reference to resolve.",
                model=self.model_name,
                prompt_version=self.prompt_version,
            )

        started = time.perf_counter()
        try:
            raw = await self._chain.ainvoke(self._variables(question, history))
        except Exception as exc:  # noqa: BLE001 — every failure degrades alike
            return self._on_failure(question, f"{type(exc).__name__}: {exc}")
        return self._decide(question, raw, started)

    def rewrite(self, question: str, history: tuple[tuple[str, str], ...] = ()) -> RewriteResult:
        """Synchronous twin, for the CLI harness and tests."""
        if not self._has_history(history):
            return RewriteResult(
                query=question.strip(),
                original=question.strip(),
                source="no_history",
                reasoning="No conversation history, so there is no reference to resolve.",
                model=self.model_name,
                prompt_version=self.prompt_version,
            )
        started = time.perf_counter()
        try:
            raw = self._chain.invoke(self._variables(question, history))
        except Exception as exc:  # noqa: BLE001
            return self._on_failure(question, f"{type(exc).__name__}: {exc}")
        return self._decide(question, raw, started)

    # ------------------------------------------------------------ internals

    @staticmethod
    def _has_history(history: tuple[tuple[str, str], ...]) -> bool:
        """Whitespace-only turns are not history. A conversation whose only
        prior turn is blank has nothing to resolve against, and sending it would
        spend a call to be told so."""
        return any(text and text.strip() for _, text in history)

    def _variables(self, question: str, history: tuple[tuple[str, str], ...]) -> dict[str, Any]:
        return {
            "system": self.prompt.system,
            "history_block": self._history_block(history),
            "question": question.strip(),
        }

    def _history_block(self, history: tuple[tuple[str, str], ...]) -> str:
        recent = [(r, t) for r, t in history if t and t.strip()][-self.settings.history_turns :]
        lines: list[str] = []
        for role, text in recent:
            clipped = text.strip().replace("\n", " ")
            if len(clipped) > self.settings.history_chars:
                # The prompt is told truncation happens, so a cut-off antecedent
                # is a reason not to rewrite rather than a reason to guess.
                clipped = clipped[: self.settings.history_chars].rstrip() + "…"
            lines.append(f"{role}: {clipped}")
        return "\n".join(lines)

    def _decide(self, question: str, raw: Any, started: float) -> RewriteResult:
        latency_ms = (time.perf_counter() - started) * 1000
        parsed = raw.get("parsed") if isinstance(raw, dict) else None
        message = raw.get("raw") if isinstance(raw, dict) else None
        usage = _usage_from(message, latency_ms)
        original = question.strip()

        if parsed is None or not isinstance(parsed, QueryRewrite):
            detail = raw.get("parsing_error") if isinstance(raw, dict) else None
            return self._on_failure(
                question, f"unparseable rewrite: {detail or 'no parse returned'}", usage
            )

        candidate = parsed.rewritten_question.strip()
        if not parsed.rewrite_needed or not candidate or candidate == original:
            return RewriteResult(
                query=original,
                original=original,
                source="model_kept",
                rewritten=False,
                reasoning=parsed.reasoning.strip(),
                usage=usage,
                model=self.model_name,
                prompt_version=self.prompt_version,
            )

        rejection = self._reject(original, candidate)
        if rejection is not None:
            log.info("rejected rewrite (%s): %r -> %r", rejection, original, candidate)
            return RewriteResult(
                query=original,
                original=original,
                source="rejected",
                rewritten=False,
                reasoning=parsed.reasoning.strip(),
                usage=usage,
                error=f"rewrite rejected: {rejection}",
                model=self.model_name,
                prompt_version=self.prompt_version,
            )

        return RewriteResult(
            query=candidate,
            original=original,
            source="model_rewrote",
            rewritten=True,
            reasoning=parsed.reasoning.strip(),
            referents=tuple(parsed.referents_resolved),
            usage=usage,
            model=self.model_name,
            prompt_version=self.prompt_version,
        )

    def _reject(self, original: str, candidate: str) -> str | None:
        """Checks that the prompt's hard limits were actually obeyed.

        A rewrite the attorney cannot see must not quietly change what is being
        searched for, so a suspicious one is discarded in favour of the original
        rather than trusted.
        """
        if len(candidate.split()) > self.settings.rewrite_max_words:
            return f"{len(candidate.split())} words, over {self.settings.rewrite_max_words}"
        # The prompt forbids dropping a qualifier the attorney used. Losing
        # "section 9" turns a targeted question into a document-wide one.
        lost = {q.lower().replace(" ", "") for q in _QUALIFIER_RE.findall(original)} - {
            q.lower().replace(" ", "") for q in _QUALIFIER_RE.findall(candidate)
        }
        if lost:
            return f"dropped scope qualifier(s): {', '.join(sorted(lost))}"
        return None

    def _on_failure(self, question: str, error: str, usage: Usage | None = None) -> RewriteResult:
        log.warning("query rewrite failed (%s): %s", self.settings.on_failure, error)
        if self.settings.on_failure == "raise":
            from app.ai.orchestration.router import RouterUnavailable

            raise RouterUnavailable(error)
        return RewriteResult(
            query=question.strip(),
            original=question.strip(),
            source="failed",
            rewritten=False,
            reasoning="Rewriter unavailable; retrieving on the original question.",
            usage=usage or Usage(),
            error=error,
            model=self.model_name,
            prompt_version=self.prompt_version,
        )
