"""Pre-retrieval query rewriting, offline against a fake model.

The two guardrails are the point of this suite. "Empty history means no
rewrite" is enforced in code, so it is tested as an absolute — the model is
asserted never to have been called. "Independent or mutually exclusive means no
rewrite" needs judgement, so what is tested here is that a `false` from the
model is honoured and that a rewrite which breaks the prompt's rules is
discarded.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.ai.orchestration import (
    QueryRewrite,
    QueryRewriter,
    RouterSettings,
    RouterUnavailable,
    load_named_prompt,
)

PROMPT = load_named_prompt("query_rewrite")


class _FakeModel:
    def __init__(
        self,
        result: QueryRewrite | None = None,
        *,
        error: Exception | None = None,
        usage: dict[str, object] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.usage = usage
        self.calls: list[list[object]] = []

    def with_structured_output(self, schema: object, **kwargs: object) -> Runnable:
        self.kwargs = kwargs

        def respond(messages: object) -> dict[str, object]:
            self.calls.append(list(getattr(messages, "messages", []) or []))
            if self.error is not None:
                raise self.error
            return {
                "raw": AIMessage(content="", usage_metadata=self.usage) if self.usage else None,
                "parsed": self.result,
                "parsing_error": None,
            }

        return RunnableLambda(respond)


def _settings(**over: object) -> RouterSettings:
    base: dict[str, object] = {"api_key": "sk-ant-test"}
    base.update(over)
    return RouterSettings(**base)  # type: ignore[arg-type]


def _rewriter(model: _FakeModel, **over: object) -> QueryRewriter:
    return QueryRewriter(_settings(**over), chat_model=model)


def _ok(question: str, *, needed: bool = True, referents: list[str] | None = None) -> _FakeModel:
    return _FakeModel(
        QueryRewrite(
            reasoning="resolved the pronoun",
            rewrite_needed=needed,
            rewritten_question=question,
            referents_resolved=referents or ["it"],
        )
    )


HISTORY = (("user", "What does the MSA say about termination?"),)


# ------------------------------------------------------- guardrail 1: history


async def test_empty_history_never_reaches_the_model() -> None:
    """Enforced in code, not delegated to the prompt: a rule the code can
    guarantee should not depend on a model following an instruction."""
    model = _ok("rewritten!")
    result = await _rewriter(model).arewrite("What is the notice period?")

    assert result.query == "What is the notice period?"
    assert result.rewritten is False
    assert result.source == "no_history"
    assert result.usage.input_tokens == 0
    assert model.calls == [], "the model was called with no history"


@pytest.mark.parametrize("history", [(), (("user", ""),), (("user", "   \n "),)])
async def test_blank_history_counts_as_no_history(history: tuple) -> None:
    """A conversation whose only prior turn is whitespace has nothing to
    resolve against, and sending it would spend a call to be told so."""
    model = _ok("rewritten!")
    result = await _rewriter(model).arewrite("What is the cap?", history)

    assert result.source == "no_history"
    assert model.calls == []


# ------------------------------------- guardrail 2: independent / exclusive


async def test_the_model_declining_to_rewrite_is_honoured() -> None:
    model = _FakeModel(
        QueryRewrite(
            reasoning="new topic, unrelated to the history",
            rewrite_needed=False,
            rewritten_question="What governing law applies?",
            referents_resolved=[],
        )
    )
    result = await _rewriter(model).arewrite("What governing law applies?", HISTORY)

    assert result.query == "What governing law applies?"
    assert result.rewritten is False
    assert result.source == "model_kept"
    assert model.calls, "the model should be consulted when history exists"


async def test_an_echoed_question_is_treated_as_kept_even_if_flagged_rewritten() -> None:
    """A model that sets the flag but returns the same text has not rewritten
    anything; trusting the flag would mislabel the trace."""
    model = _ok("What is the cap?", needed=True)
    result = await _rewriter(model).arewrite("What is the cap?", HISTORY)

    assert result.source == "model_kept"
    assert result.rewritten is False


async def test_an_empty_rewrite_falls_back_to_the_original() -> None:
    model = _ok("   ", needed=True)
    result = await _rewriter(model).arewrite("And the indemnity?", HISTORY)

    assert result.query == "And the indemnity?"
    assert result.rewritten is False


# ------------------------------------------------------------ happy rewrite


async def test_a_genuine_rewrite_is_used_for_retrieval() -> None:
    model = _ok("Does the MSA's termination clause require written notice?")
    result = await _rewriter(model).arewrite("Does it require written notice?", HISTORY)

    assert result.query == "Does the MSA's termination clause require written notice?"
    assert result.rewritten is True
    assert result.source == "model_rewrote"
    assert result.referents == ("it",)
    assert result.original == "Does it require written notice?"


async def test_the_result_is_stamped_for_reproducibility() -> None:
    result = await _rewriter(_ok("Does the MSA renew?")).arewrite("Does it renew?", HISTORY)

    assert result.model == "claude-haiku-4-5"
    assert result.prompt_version == PROMPT.version


# ------------------------------------------------------------- drift guard


async def test_an_over_long_rewrite_is_rejected() -> None:
    """The prompt caps a rewrite at ~40 words. A rewrite that grows past the
    limit is adding rather than resolving, and the attorney cannot see it."""
    model = _ok(" ".join(["word"] * 80))
    result = await _rewriter(model, rewrite_max_words=60).arewrite("And the cap?", HISTORY)

    assert result.query == "And the cap?"
    assert result.rewritten is False
    assert result.source == "rejected"
    assert result.error is not None and "over 60" in result.error


async def test_a_rewrite_that_drops_a_scope_qualifier_is_rejected() -> None:
    """Losing "section 9" turns a targeted question into a document-wide one —
    a silent change to what was asked."""
    model = _ok("What is the liability cap in the agreement?")
    result = await _rewriter(model).arewrite("What is the cap in section 9?", HISTORY)

    assert result.source == "rejected"
    assert result.error is not None and "scope qualifier" in result.error
    assert result.query == "What is the cap in section 9?"


async def test_a_rewrite_that_keeps_the_qualifier_is_accepted() -> None:
    model = _ok("What is the liability cap in section 9 of the MSA?")
    result = await _rewriter(model).arewrite("What is the cap in section 9?", HISTORY)

    assert result.source == "model_rewrote"


@pytest.mark.parametrize(
    "original",
    ["What is on page 12?", "What does § 4.2 say?", "What is in exhibit B?"],
)
async def test_every_qualifier_form_is_protected(original: str) -> None:
    model = _ok("What does the agreement say generally?")
    result = await _rewriter(model).arewrite(original, HISTORY)
    assert result.source == "rejected", f"{original} lost its qualifier unnoticed"


# --------------------------------------------------------------- failures


async def test_a_provider_failure_retrieves_on_the_original_question() -> None:
    """The original question is a valid query — it is what the system used
    before this stage existed. A rewrite failure must not fail the turn."""
    model = _FakeModel(error=RuntimeError("503 upstream"))
    result = await _rewriter(model).arewrite("And the indemnity?", HISTORY)

    assert result.query == "And the indemnity?"
    assert result.source == "failed"
    assert result.error is not None and "503" in result.error


async def test_an_unparseable_response_degrades_the_same_way() -> None:
    result = await _rewriter(_FakeModel(result=None)).arewrite("And the cap?", HISTORY)

    assert result.query == "And the cap?"
    assert result.source == "failed"
    assert "unparseable" in (result.error or "")


async def test_the_raise_failure_mode_propagates() -> None:
    model = _FakeModel(error=RuntimeError("boom"))
    with pytest.raises(RouterUnavailable):
        await _rewriter(model, on_failure="raise").arewrite("And the cap?", HISTORY)


# ------------------------------------------------------------ the prompt


async def test_the_chain_uses_native_structured_outputs() -> None:
    model = _ok("x")
    await _rewriter(model).arewrite("And the cap?", HISTORY)
    assert model.kwargs["method"] == "json_schema"
    assert model.kwargs["include_raw"] is True


async def test_history_is_sent_oldest_first_and_truncated() -> None:
    model = _ok("x")
    history = tuple(("user", f"turn {i}") for i in range(10))
    await _rewriter(model, history_turns=3).arewrite("And?", history)

    human = str(model.calls[0][-1].content)
    assert "turn 7" in human and "turn 9" in human
    assert "turn 6" not in human
    assert human.index("turn 7") < human.index("turn 9")


async def test_a_long_history_turn_is_truncated() -> None:
    model = _ok("x")
    await _rewriter(model, history_chars=40).arewrite("And?", (("user", "x" * 4000),))
    assert "…" in str(model.calls[0][-1].content)


async def test_the_question_is_delimited() -> None:
    model = _ok("x")
    await _rewriter(model).arewrite("And the indemnity?", HISTORY)
    human = str(model.calls[0][-1].content)
    assert "<question>" in human and "And the indemnity?" in human


def test_the_prompt_states_both_guardrails() -> None:
    body = PROMPT.system.lower()
    assert "history is empty" in body
    assert "independent" in body
    assert "mutually exclusive" in body
    # And the rules that stop a rewrite from inventing content.
    assert "never add a fact" in body
    assert "never answer the question" in body
    assert "never change the scope" in body


def test_the_prompt_gold_set_covers_both_outcomes() -> None:
    outcomes = {e.expected for e in PROMPT.examples}
    assert outcomes == {"yes", "no"}
    assert len(PROMPT.examples) >= 8


def test_usage_is_carried_out() -> None:
    model = _FakeModel(
        QueryRewrite(reasoning="r", rewrite_needed=True, rewritten_question="Does the MSA renew?"),
        usage={"input_tokens": 900, "output_tokens": 30, "total_tokens": 930},
    )
    result = _rewriter(model).rewrite("Does it renew?", HISTORY)
    assert result.usage.input_tokens == 900
    assert result.usage.output_tokens == 30
    assert result.usage.latency_ms > 0
