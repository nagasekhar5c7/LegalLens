"""The orchestration layer's intent router, tested without a network call.

Every test here runs offline against a fake chat model. The live behaviour —
whether Claude Haiku 4.5 actually agrees with the prompt's gold set — is a
separate, paid question answered by `scripts/try_intent_router.py`. Mixing the
two would make the suite slow, flaky and billable, and would stop it running in
CI without a key.

What is worth testing offline turns out to be most of it: the taxonomy matching
the API contract, the mode chip never reaching the model, what the prompt
contains and what it must not, the fallback ladder, and the usage accounting.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.ai.orchestration import (
    INTENTS,
    SAFE_DEFAULT,
    IntentClassification,
    IntentRouter,
    PromptFileError,
    RouterInput,
    RouterSettings,
    RouterUnavailable,
    ScopeFacts,
    Usage,
    classify_by_keyword,
    load_prompt,
)
from app.ai.orchestration.prompt import PROMPT_PATH, _examples, _system_prompt
from app.ai.orchestration.router import _HAIKU_CACHE_FLOOR_TOKENS
from app.schemas.common import Intent as ContractIntent

PROMPT = load_prompt()


# --------------------------------------------------------------- the doubles


class _FakeStructuredModel:
    """Stands in for `ChatAnthropic`, capturing what the chain sends it.

    It implements only `with_structured_output`, which is the whole surface
    `IntentRouter` uses — a narrower double than a mocked SDK client, and it
    fails loudly if the router starts calling something else.
    """

    def __init__(
        self,
        result: IntentClassification | None = None,
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
        self.schema = schema

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


def _settings(**overrides: object) -> RouterSettings:
    base: dict[str, object] = {
        "api_key": "sk-ant-test-not-a-real-key",
        "model": "claude-haiku-4-5",
    }
    base.update(overrides)
    return RouterSettings(**base)  # type: ignore[arg-type]


def _router(model: _FakeStructuredModel, **overrides: object) -> IntentRouter:
    return IntentRouter(_settings(**overrides), chat_model=model)


def _ok(intent: str = "ask", confidence: float = 0.9) -> _FakeStructuredModel:
    return _FakeStructuredModel(
        IntentClassification(rationale="because", intent=intent, confidence=confidence)  # type: ignore[arg-type]
    )


# --------------------------------------------------- the taxonomy is shared


def test_the_taxonomy_matches_the_api_contract() -> None:
    """NFR-14 keeps the AI layer behind three callables, but the *labels* cross
    the boundary: the turn route dispatches on them and `types.ts` renders
    them. A router that invented a fifth label would route into nothing."""
    from typing import get_args

    assert set(INTENTS) == set(get_args(ContractIntent))


def test_the_safe_default_is_the_read_only_workflow() -> None:
    """A router outage must not be able to manufacture work-product: `draft`
    writes an unreviewed document (FR-23), `ask` can refuse cleanly (FR-14)."""
    assert SAFE_DEFAULT == "ask"


# ------------------------------------------------------------- the prompt


def test_the_prompt_file_parses_and_is_versioned() -> None:
    assert PROMPT.system
    assert PROMPT.declared_version.startswith("intent-router/")
    # NFR-12: the stamp is derived from the bytes sent, so an edit without a
    # version bump is still distinguishable in the audit log.
    assert PROMPT.version.startswith(PROMPT.declared_version + "+")
    assert len(PROMPT.version.split("+")[-1]) == 8


def test_the_prompt_version_changes_when_the_prompt_changes() -> None:
    import hashlib

    digest = hashlib.sha256(PROMPT.system.encode()).hexdigest()[:8]
    assert PROMPT.version.endswith(digest)
    other = hashlib.sha256((PROMPT.system + " ").encode()).hexdigest()[:8]
    assert other != digest


def test_the_prompt_names_every_label_and_the_precedence_rules() -> None:
    for label in INTENTS:
        assert f"`{label}`" in PROMPT.system, f"{label} is undocumented in the prompt"
    # The ordered tie-breaks are the part a reviewer most needs to find.
    assert "Precedence" in PROMPT.system
    assert "last resort" in PROMPT.system


def test_the_prompt_demands_the_three_output_fields_in_order() -> None:
    body = PROMPT.system
    assert body.index("`rationale`") < body.index("`intent`") < body.index("`confidence`")


def test_malformed_prompt_files_fail_loudly() -> None:
    """A truncated prompt would classify badly and look healthy, so the loader
    refuses it rather than defaulting."""
    with pytest.raises(PromptFileError):
        _system_prompt("no markers here")
    with pytest.raises(PromptFileError):
        _system_prompt("<!-- SYSTEM PROMPT BEGIN --><!-- SYSTEM PROMPT END -->")


def test_the_gold_set_covers_every_label() -> None:
    labels = {e.expected for e in PROMPT.examples}
    assert labels == set(INTENTS)
    assert len(PROMPT.examples) >= 20


def test_the_gold_set_table_lives_outside_the_prompt() -> None:
    """Keeping the machine-read table after the END marker means a failing case
    can be added without re-baselining the prompt hash. Individual questions
    may still appear in the prompt's own illustrative tables — that is what
    `taught` records."""
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    assert raw.index("<!-- SYSTEM PROMPT END -->") < raw.index("## Labelled examples")
    assert "## Labelled examples" not in PROMPT.system
    # The held-out cases are genuinely absent from what the model is shown.
    for example in PROMPT.held_out_examples:
        cell = f"| {example.question} |"
        assert cell not in PROMPT.system


def test_most_of_the_gold_set_is_held_out_of_the_prompt() -> None:
    """A case the prompt states verbatim tests instruction-following, not
    generalisation. Scoring the two together would flatter the router, so the
    loader separates them and the live harness reports both."""
    assert PROMPT.taught_examples, "no case checks the prompt's own stated rules"
    assert len(PROMPT.held_out_examples) >= len(PROMPT.examples) // 2
    assert all(not e.taught for e in PROMPT.held_out_examples)


def test_only_the_table_after_the_end_marker_is_read_as_the_gold_set() -> None:
    """The prompt body has illustrative tables of its own; they are not cases."""
    raw = (
        "<!-- SYSTEM PROMPT BEGIN -->\n"
        "| inside | auto | ask | illustrative |\n"
        "<!-- SYSTEM PROMPT END -->\n"
        "| question | mode | expected | note |\n"
        "| --- | --- | --- | --- |\n"
        "| outside | auto | draft | real |\n"
    )
    found = _examples(raw)
    assert [e.question for e in found] == ["outside"]


# ------------------------------------------------- the mode chip short-circuit


@pytest.mark.parametrize("mode", ["ask", "summary", "draft"])
async def test_an_explicit_mode_never_reaches_the_model(mode: str) -> None:
    """BRD §7 and frontend nuance #4: an attorney who pressed a chip is not
    second-guessed. It is also the largest cost saving in the module."""
    model = _ok("summary")
    decision = await _router(model).aclassify(RouterInput(text="anything", mode=mode))  # type: ignore[arg-type]

    assert decision.intent == mode
    assert decision.source == "explicit_mode"
    assert decision.confidence == 1.0
    assert decision.usage.input_tokens == 0
    assert decision.usage.usd == 0.0
    assert model.calls == [], "the model was called for an explicit mode"


async def test_an_empty_request_never_reaches_the_model() -> None:
    model = _ok()
    decision = await _router(model).aclassify(RouterInput(text="   \n  "))

    assert decision.intent == SAFE_DEFAULT
    assert decision.source == "default_fallback"
    assert model.calls == []


# ------------------------------------------------------- the happy path


@pytest.mark.parametrize("label", INTENTS)
async def test_every_label_the_model_returns_is_passed_through(label: str) -> None:
    decision = await _router(_ok(label, 0.84)).aclassify(RouterInput(text="what is the cap?"))

    assert decision.intent == label
    assert decision.source == "model"
    assert decision.used_model
    assert decision.confidence == 0.84
    assert decision.rationale == "because"
    assert decision.error is None


async def test_the_decision_is_stamped_for_reproducibility() -> None:
    """NFR-12: prompt, model and index versions on every generation."""
    decision = await _router(_ok()).aclassify(RouterInput(text="hello", trace_id="trace-1"))

    assert decision.model == "claude-haiku-4-5"
    assert decision.prompt_version == PROMPT.version
    assert decision.trace_id == "trace-1"


async def test_the_chain_uses_claudes_native_structured_outputs() -> None:
    """Not forced tool calling: a malformed label should be impossible to
    generate rather than something to handle."""
    model = _ok()
    await _router(model).aclassify(RouterInput(text="hi there"))

    assert model.kwargs["method"] == "json_schema"
    # include_raw carries token usage out, and turns a parse failure into a
    # returned error instead of an exception mid-chain.
    assert model.kwargs["include_raw"] is True
    assert model.schema is IntentClassification


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.95, True), (0.70, True), (0.69, False), (0.0, False)],
)
async def test_low_confidence_is_reported_so_the_ui_can_offer_a_chip(
    confidence: float, expected: bool
) -> None:
    decision = await _router(_ok("summary", confidence)).aclassify(RouterInput(text="what's in it"))
    assert decision.is_confident is expected


async def test_an_out_of_range_confidence_clamps_rather_than_failing() -> None:
    """A sloppy float should not demote a usable label to a keyword fallback."""
    model = _FakeStructuredModel(
        IntentClassification(rationale="r", intent="draft", confidence=4.2)
    )
    decision = await _router(model).aclassify(RouterInput(text="draft a memo"))

    assert decision.intent == "draft"
    assert decision.confidence == 1.0
    assert decision.source == "model"


# ----------------------------------------------------- what the prompt carries


async def test_the_request_is_sent_and_delimited() -> None:
    model = _ok()
    await _router(model).aclassify(RouterInput(text="What is the indemnity cap?"))

    human = model.calls[0][-1].content
    assert "<request>" in human
    assert "What is the indemnity cap?" in human


async def test_scope_reaches_the_model_as_counts_and_types_only() -> None:
    model = _ok()
    await _router(model).aclassify(
        RouterInput(
            text="summarise it",
            scope=ScopeFacts(
                document_count=2,
                document_types=("contract", "opinion"),
                single_document_in_focus=False,
            ),
        )
    )

    human = model.calls[0][-1].content
    assert "2 documents in scope" in human
    assert "contract, opinion" in human
    assert "no single document is in focus" in human


async def test_no_document_name_or_content_can_reach_the_classifier() -> None:
    """DR-5 treats every upload as privileged. Deciding between "answer this"
    and "summarise this" does not need the matter name, so `ScopeFacts` has no
    field that could carry one — this test guards that shape."""
    assert not hasattr(ScopeFacts(), "document_names")
    assert not hasattr(ScopeFacts(), "text")

    model = _ok()
    await _router(model).aclassify(
        RouterInput(
            text="summarise it",
            scope=ScopeFacts(document_count=1, document_types=("contract",)),
        )
    )
    sent = " ".join(str(m.content) for m in model.calls[0])
    assert "Smith v. Acme" not in sent  # nothing anywhere plumbs a name through
    assert "1 document in scope" in sent


async def test_history_is_sent_oldest_first_and_capped() -> None:
    model = _ok()
    history = tuple(("user", f"turn {i}") for i in range(10))
    await _router(model, history_turns=3).aclassify(
        RouterInput(text="now as a memo", history=history)
    )

    human = model.calls[0][-1].content
    assert "turn 7" in human and "turn 8" in human and "turn 9" in human
    assert "turn 6" not in human
    assert human.index("turn 7") < human.index("turn 9")


async def test_a_long_history_turn_is_truncated() -> None:
    """A pasted contract in a previous turn would otherwise dominate the prompt
    and the bill."""
    model = _ok()
    await _router(model, history_chars=40).aclassify(
        RouterInput(text="and the indemnity?", history=(("user", "x" * 5000),))
    )

    human = model.calls[0][-1].content
    assert "…" in human
    assert "x" * 60 not in human


async def test_no_history_block_when_there_is_no_history() -> None:
    model = _ok()
    await _router(model).aclassify(RouterInput(text="what is the cap?"))
    assert "Recent conversation" not in model.calls[0][-1].content


# ------------------------------------------------------------ the fallbacks


async def test_a_provider_failure_falls_back_to_the_keyword_rules() -> None:
    """NFR-6: the turn survives a provider outage. "draft me a memo" routed to
    `ask` during an outage is a visibly wrong answer the heuristic avoids."""
    model = _FakeStructuredModel(error=RuntimeError("503 upstream"))
    decision = await _router(model).aclassify(RouterInput(text="Draft a memo on the lease"))

    assert decision.intent == "draft"
    assert decision.source == "heuristic_fallback"
    assert decision.confidence == 0.0
    assert decision.error is not None and "503" in decision.error


async def test_a_provider_failure_on_an_unrecognised_request_falls_back_to_ask() -> None:
    model = _FakeStructuredModel(error=RuntimeError("timeout"))
    decision = await _router(model).aclassify(RouterInput(text="what is the indemnity cap?"))

    assert decision.intent == SAFE_DEFAULT
    assert decision.source == "default_fallback"
    assert decision.error is not None


async def test_the_ask_failure_mode_skips_the_heuristic() -> None:
    model = _FakeStructuredModel(error=RuntimeError("boom"))
    decision = await _router(model, on_failure="ask").aclassify(RouterInput(text="draft a memo"))

    assert decision.intent == SAFE_DEFAULT
    assert decision.source == "default_fallback"


async def test_the_raise_failure_mode_propagates_for_eval_harnesses() -> None:
    """An eval must not score a keyword fallback as a model prediction."""
    model = _FakeStructuredModel(error=RuntimeError("boom"))
    with pytest.raises(RouterUnavailable):
        await _router(model, on_failure="raise").aclassify(RouterInput(text="draft a memo"))


async def test_an_unparseable_response_degrades_like_an_outage() -> None:
    """Structured outputs make this near-unreachable, but a refusal or a
    max_tokens cut-off both land here."""
    model = _FakeStructuredModel(result=None)
    decision = await _router(model).aclassify(RouterInput(text="summarise this"))

    assert decision.intent == "summary"
    assert decision.source == "heuristic_fallback"
    assert decision.error is not None and "unparseable" in decision.error


def test_missing_credentials_are_reported_before_any_request() -> None:
    with pytest.raises(RouterUnavailable, match="ANTHROPIC_API_KEY"):
        IntentRouter(_settings(api_key=""))


# ------------------------------------------------------ the keyword fallback


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Draft a memo", "draft"),
        ("prepare a case brief", "draft"),
        ("Summarise this contract", "summary"),
        ("summary please", "summary"),
        ("brief me on this", "summary"),
        ("walk me through the term", "summary"),
        ("what's the weather", "out_of_scope"),
        ("translate this", "out_of_scope"),
    ],
)
def test_the_keyword_rules_recognise_what_they_claim_to(text: str, expected: str) -> None:
    match = classify_by_keyword(text)
    assert match is not None and match[0] == expected


def test_the_keyword_rules_say_nothing_rather_than_guessing_ask() -> None:
    """None, not `ask`, so a trace can tell "recognised" from "nothing to
    say, default applied"."""
    assert classify_by_keyword("what is the indemnity cap?") is None


def test_draft_beats_summary_in_the_keyword_rules() -> None:
    """Precedence rule 1, in the degraded path too."""
    match = classify_by_keyword("draft a memo summarising the lease")
    assert match is not None and match[0] == "draft"


# ------------------------------------------------------------- the accounting


async def test_token_usage_is_carried_out_of_the_call() -> None:
    """NFR-8 attributes spend per request; NFR-11 wants it per stage, and
    routing is a stage."""
    model = _FakeStructuredModel(
        IntentClassification(rationale="r", intent="ask", confidence=0.9),
        usage={
            "input_tokens": 2000,
            "output_tokens": 40,
            "total_tokens": 2040,
            "input_token_details": {"cache_read": 1500, "cache_creation": 0},
        },
    )
    decision = await _router(model).aclassify(RouterInput(text="what is the cap?"))

    assert decision.usage.input_tokens == 2000
    assert decision.usage.output_tokens == 40
    assert decision.usage.cache_read_input_tokens == 1500
    assert decision.usage.latency_ms > 0
    # Haiku 4.5: $1/MTok in, $5/MTok out, cache reads ~0.1x input.
    expected = (2000 * 1.00 + 1500 * 0.10 + 40 * 5.00) / 1_000_000
    assert decision.usage.usd == pytest.approx(expected)


def test_a_routed_turn_costs_a_small_fraction_of_the_nfr8_ask_budget() -> None:
    """NFR-8 allows $0.05 per Ask. Routing is one call on top of retrieval and
    generation, so it has to stay a small slice of that — this pins the actual
    arithmetic rather than trusting that Haiku is "cheap".

    The input side dominates: the system prompt is resent on every routed turn
    and Claude Haiku 4.5 will not cache a prefix below 4096 tokens, so the
    prompt's own size is the cost. Raising the budget check is the wrong fix if
    this fails — shrinking the prompt, or pushing it over the cache floor, is.
    """
    ask_budget_usd = 0.05
    typical_output_tokens = 60
    worst_case = Usage(
        input_tokens=PROMPT.approx_tokens + 400,  # prompt + request, history, scope
        output_tokens=typical_output_tokens,
    )
    assert worst_case.usd < ask_budget_usd * 0.10, (
        f"routing costs ${worst_case.usd:.5f}, over 10% of the ${ask_budget_usd} Ask budget"
    )


async def test_a_missing_usage_block_is_not_an_error() -> None:
    decision = await _router(_ok()).aclassify(RouterInput(text="what is the cap?"))
    assert decision.usage.input_tokens == 0
    assert decision.usage.usd == 0.0


# ------------------------------------------------------------- the caching


def test_no_cache_breakpoint_is_requested_below_the_model_floor() -> None:
    """Claude Haiku 4.5 silently declines a prefix under 4096 tokens. Asking
    anyway would leave a dead marker looking like a working optimisation, so
    the router checks the estimate and logs instead."""
    router = IntentRouter(_settings(), chat_model=_ok())
    if PROMPT.approx_tokens < _HAIKU_CACHE_FLOOR_TOKENS:
        assert router._cache_system is False
        assert isinstance(router._system_content(), str)
    else:
        assert router._cache_system is True
        blocks = router._system_content()
        assert isinstance(blocks, list)
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_caching_can_be_switched_off() -> None:
    router = IntentRouter(_settings(cache_system_prompt=False), chat_model=_ok())
    assert router._cache_system is False


# ------------------------------------------------------------- the defaults


def test_the_configured_model_is_haiku_for_this_task() -> None:
    """Intent classification is one short-output call on the NFR-1 critical
    path, which is the whole argument for the cheapest current model."""
    assert RouterSettings(api_key="x").model == "claude-haiku-4-5"


def test_the_classifier_is_deterministic_by_default() -> None:
    """A classifier that returns different labels for the same question cannot
    be evaluated, and NFR-12's reproducibility promise would be false."""
    assert RouterSettings(api_key="x").temperature == 0.0


def test_routing_gets_a_slice_of_the_generation_budget_not_all_of_it() -> None:
    from app.config import get_settings

    assert RouterSettings(api_key="x").timeout_s < get_settings().generation_budget_s


def test_the_api_key_is_read_from_the_conventional_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert RouterSettings().api_key == "sk-ant-from-env"
    assert RouterSettings().has_credentials


def test_the_prefixed_api_key_env_var_also_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a single `validation_alias` *replaces* the `LL_AI_` prefix
    rather than adding to it, so LL_AI_API_KEY was silently ignored while the
    code and the error message both advertised it. `AliasChoices` accepts
    both."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LL_AI_API_KEY", "sk-ant-prefixed")
    assert RouterSettings().api_key == "sk-ant-prefixed"


def test_the_api_key_can_be_passed_by_field_name() -> None:
    """Regression: the alias also stopped `RouterSettings(api_key=...)` from
    working. Every test here does that, and they all passed anyway because
    they inject a fake model and never build a real client — so the bug was
    invisible until something constructed one."""
    assert RouterSettings(api_key="sk-ant-explicit").api_key == "sk-ant-explicit"
    assert RouterSettings(api_key="sk-ant-explicit").has_credentials


def test_anthropic_api_key_wins_over_the_prefixed_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-conventional")
    monkeypatch.setenv("LL_AI_API_KEY", "sk-ant-prefixed")
    assert RouterSettings().api_key == "sk-ant-conventional"


def test_the_env_file_is_anchored_to_the_backend_directory() -> None:
    """`env_file=".env"` resolves against the working directory, so running a
    script from the repo root silently loaded nothing and a missing key looked
    like a code problem rather than a path problem."""
    from app.ai.orchestration.settings import BACKEND_ROOT

    configured = RouterSettings.model_config["env_file"]
    assert Path(configured).is_absolute()  # type: ignore[arg-type]
    assert Path(configured) == BACKEND_ROOT / ".env"  # type: ignore[arg-type]
    assert (BACKEND_ROOT / "pyproject.toml").exists(), "BACKEND_ROOT is not the backend package"


def test_the_fake_chat_model_import_is_available() -> None:
    """Guards the test-only dependency the doubles above rely on."""
    assert GenericFakeChatModel is not None
