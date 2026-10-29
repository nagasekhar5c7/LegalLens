"""The summary workflow end to end, offline.

Two things are under test and they matter in this order.

**FR-16.** A field the passages do not address must come back `value_md=None`,
emitted rather than skipped, and never filled from the model's expectation of
what an agreement of this kind contains. That is the product's central promise
and it is the first thing this file checks.

**The event contract.** The frontend and `turn_service.TurnAccumulator` were
built against the M3 summary stream, so this layer replacing `FixtureEngine`
must emit the same sequence in the same order.
"""

from __future__ import annotations

import pytest

from app.ai.orchestration import (
    STAGE_LABEL,
    Hit,
    Retriever,
    RouterSettings,
    SummaryOutcome,
    SummaryWorkflow,
    load_named_prompt,
)
from app.ai.orchestration.workflows.summary import FieldExtraction, _renumber
from app.schemas.events import StreamEvent
from tests.test_retrieval import FakeEmbedder, FakeReranker, FakeStore, chunk

PROMPT = load_named_prompt("summary_extraction")

CAP_TEXT = "Liability under this Agreement is capped at twelve (12) months of fees."
NOTICE_TEXT = "Either party may terminate on sixty (60) days' written notice."


class FakeStructuredChat:
    """Stands in for `ChatAnthropic.with_structured_output(...)`.

    Returns a canned `FieldExtraction` per field, keyed by the field label it
    finds in the rendered prompt. That keying is deliberate: a test that
    returned the same extraction for every call could not catch the workflow
    sending the wrong field's label or the wrong field's sources.
    """

    def __init__(
        self,
        by_label: dict[str, FieldExtraction] | None = None,
        *,
        default: FieldExtraction | None = None,
        error: Exception | None = None,
    ) -> None:
        self.by_label = by_label or {}
        self.default = default or FieldExtraction(
            reasoning="nothing in the passages addresses it",
            present=False,
            value_md=None,
        )
        self.error = error
        self.prompts: list[str] = []

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ANN201, ARG002
        return self

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201
        if self.error is not None:
            raise self.error
        rendered = "\n".join(getattr(m, "content", "") or "" for m in messages)
        self.prompts.append(rendered)
        for label, extraction in self.by_label.items():
            if f"<field>\n{label}\n</field>" in rendered:
                return {"parsed": extraction, "raw": _FakeMessage(), "parsing_error": None}
        return {"parsed": self.default, "raw": _FakeMessage(), "parsing_error": None}


class _FakeMessage:
    usage_metadata = {"input_tokens": 4200, "output_tokens": 80, "total_tokens": 4280}


def _settings(**over: object) -> RouterSettings:
    base: dict[str, object] = {"api_key": "sk-ant-test"}
    base.update(over)
    return RouterSettings(**base)  # type: ignore[arg-type]


def _workflow(
    chat: FakeStructuredChat | None = None,
    *,
    hits: list[Hit] | None = None,
    parents: list | None = None,
    floor: float = 0.25,
    scores: dict[str, float] | None = None,
    **over: object,
) -> SummaryWorkflow:
    store = FakeStore(
        hits if hits is not None else [Hit(chunk(1, text=CAP_TEXT, parent="p1", doc=1), 0.9)],
        parents
        if parents is not None
        else [chunk(1, text=CAP_TEXT, parent="p1", is_parent=True, doc=1)],
    )
    retriever = Retriever(
        store=store,
        embedder=FakeEmbedder(),
        reranker=FakeReranker(scores),
        relevance_floor=floor,
    )
    return SummaryWorkflow(
        retriever=retriever,
        settings=_settings(**over),
        chat_model=chat or FakeStructuredChat(),
        prompt=PROMPT,
    )


async def _collect(workflow: SummaryWorkflow, **kw):  # noqa: ANN201
    outcome = SummaryOutcome()
    events: list[StreamEvent] = []
    kw.setdefault("document_id", "doc1")
    kw.setdefault("document_name", "Master Services Agreement")
    kw.setdefault("document_type", "contract")
    kw.setdefault("document_pages", 312)
    async for event in workflow.run(outcome=outcome, **kw):
        events.append(event)
    return events, outcome


def _types(events: list[StreamEvent]) -> list[str]:
    return [e.type for e in events]


# ------------------------------------------------------------------- FR-16


async def test_an_absent_field_is_reported_absent_not_inferred() -> None:
    """The central promise. Nothing in the passages addresses liability, so the
    field comes back null — not the market-standard cap the model knows."""
    chat = FakeStructuredChat(
        default=FieldExtraction(
            reasoning="the passages do not mention a limitation of liability",
            present=False,
            value_md=None,
        )
    )
    events, outcome = await _collect(_workflow(chat))

    liability = next(f for f in outcome.fields if f.key == "liability")
    assert liability.value_md is None
    assert liability.citations == []
    assert "liability" in outcome.absent_fields


async def test_an_absent_field_is_still_emitted() -> None:
    """Skipping it would be indistinguishable in the UI from a field still
    streaming, and "no liability cap" is a finding."""
    events, outcome = await _collect(_workflow())

    emitted = [e for e in events if e.type == "summary_field"]
    assert len(emitted) == len(outcome.fields) == 9, "every contract field must be emitted"
    assert all(e.field.value_md is None for e in emitted)
    assert {e.index for e in emitted} == set(range(9))
    assert all(e.total == 9 for e in emitted)


async def test_a_present_field_carries_its_value_and_citations() -> None:
    chat = FakeStructuredChat(
        {
            "Limitation of liability and indemnity": FieldExtraction(
                reasoning="section 12.3 caps liability",
                present=True,
                value_md=f"{CAP_TEXT}[[1]]",
                citations=[1],
            )
        }
    )
    _, outcome = await _collect(_workflow(chat))

    liability = next(f for f in outcome.fields if f.key == "liability")
    assert liability.value_md is not None
    assert "twelve (12) months" in liability.value_md
    assert liability.citations == [1]
    assert outcome.fields_present == 1


async def test_an_extraction_failure_is_not_an_absent_field() -> None:
    """A provider timeout reported as "not addressed" would be a false negative
    about the document — the one class of error this product refuses to make."""
    chat = FakeStructuredChat(error=RuntimeError("provider timeout"))
    events, outcome = await _collect(_workflow(chat))

    assert _types(events)[-1] == "error"
    error = events[-1].error
    assert error.kind == "llm_unavailable"
    assert "Parties and roles" in error.message, "the field it stopped at must be named"
    assert error.excerpts_available is True
    assert outcome.absent_fields == [], "nothing may be marked absent on a failure"


# ---------------------------------------------------------- event contract


async def test_the_event_sequence_matches_the_m3_contract() -> None:
    chat = FakeStructuredChat(
        default=FieldExtraction(
            reasoning="present", present=True, value_md=f"{CAP_TEXT}[[1]]", citations=[1]
        )
    )
    events, _ = await _collect(_workflow(chat))
    types = _types(events)

    assert types[0] == "stage"  # classifying
    assert types.index("summary_start") < types.index("summary_field")
    assert types.index("citation") < types.index("summary_start")
    assert types.index("excerpt") < types.index("summary_start")
    assert types[-1] == "done"
    assert types[-2] == "usage"
    assert "coverage" in types


async def test_the_stage_labels_agree_with_the_fixture_engine() -> None:
    """The duplication in `workflows/stages.py` cannot drift while both exist."""
    from app.ai.fixture_engine import STAGE_LABEL as FIXTURE_LABELS

    assert STAGE_LABEL == FIXTURE_LABELS


async def test_the_stages_are_the_ones_the_summary_ui_renders() -> None:
    events, _ = await _collect(_workflow())
    keys = [e.stage.key for e in events if e.type == "stage" and e.stage.status == "done"]
    assert keys == ["classifying", "field_retrieval", "mapping", "verifying"]


async def test_the_header_names_the_template_and_the_document() -> None:
    events, outcome = await _collect(_workflow())
    start = next(e for e in events if e.type == "summary_start")

    assert start.summary.document_id == "doc1"
    assert start.summary.document_name == "Master Services Agreement"
    assert start.summary.document_type == "contract"
    assert start.summary.template_name == "Contract / agreement template"
    assert start.field_count == 9
    assert outcome.template == "Contract / agreement template"


# ------------------------------------------------------------- the templates


@pytest.mark.parametrize(
    ("doc_type", "expected"),
    [
        ("contract", 9),
        ("opinion", 7),
        ("pleading", 5),
        ("other", 4),
    ],
)
async def test_each_document_type_gets_its_own_schema(doc_type: str, expected: int) -> None:
    _, outcome = await _collect(_workflow(), document_type=doc_type)
    assert len(outcome.fields) == expected


async def test_an_unknown_document_type_falls_back_to_the_general_template() -> None:
    """A classification gap is not a reason to refuse a summary."""
    _, outcome = await _collect(_workflow(), document_type="affidavit")
    assert len(outcome.fields) == 4
    assert outcome.document_type == "other"
    assert outcome.template == "General document template"


def test_the_templates_match_the_fixture_field_sets() -> None:
    """The duplication from `app/ai/fixtures/summaries.py` cannot drift.

    Keys and labels only — the fixture has no per-field query, which is the one
    thing this layer adds. See `templates.py`.
    """
    from app.ai.fixtures.summaries import TEMPLATE_FIELDS, TEMPLATE_NAME
    from app.ai.orchestration.templates import TEMPLATE_NAME as OURS
    from app.ai.orchestration.templates import TEMPLATES

    assert OURS == TEMPLATE_NAME
    for doc_type, fields in TEMPLATE_FIELDS.items():
        ours = TEMPLATES[doc_type]
        assert [f["key"] for f in fields] == [f.key for f in ours], doc_type
        assert [f["label"] for f in fields] == [f.label for f in ours], doc_type


def test_every_field_has_a_query_that_is_not_just_its_label() -> None:
    """The label is a poor query: no contract says "unusual or off-market terms"
    about itself. A field whose query is its label would retrieve nothing."""
    from app.ai.orchestration.templates import TEMPLATES

    for doc_type, fields in TEMPLATES.items():
        for field in fields:
            assert field.query, f"{doc_type}.{field.key} has no query"
            assert field.query.casefold() != field.label.casefold(), (
                f"{doc_type}.{field.key} queries on its own label"
            )


# -------------------------------------------------------------- retrieval


async def test_retrieval_runs_once_per_field_with_the_fields_own_query() -> None:
    workflow = _workflow()
    _, outcome = await _collect(workflow)

    from app.ai.orchestration.templates import TEMPLATES

    assert outcome.queries == [f.query for f in TEMPLATES["contract"]]
    assert len(outcome.retrievals) == 9
    assert [key for key, _ in outcome.retrievals] == [f.key for f in TEMPLATES["contract"]]


async def test_retrieval_is_scoped_to_the_one_document_being_summarised() -> None:
    """FR-7 from the summary side: a nine-field sweep must not reach another
    document in the workspace."""
    workflow = _workflow()
    await _collect(workflow, document_id="doc1")

    store = workflow.retriever.store
    assert store.search_calls, "nothing was searched"
    assert all(c["document_ids"] == ["doc1"] for c in store.search_calls)


async def test_a_clause_retrieved_by_several_fields_is_one_citation() -> None:
    """The ledger dedupes on chunk id: the liability cap turns up under
    liability, termination and unusual terms, and the attorney should see one
    chip, not three identical ones."""
    _, outcome = await _collect(_workflow())
    assert len(outcome.citations) == 1
    assert [c.n for c in outcome.citations] == [1]


async def test_nothing_retrievable_refuses_before_spending_a_token() -> None:
    chat = FakeStructuredChat()
    workflow = _workflow(chat, hits=[], parents=[])
    events, outcome = await _collect(workflow)

    assert outcome.refused is True
    assert outcome.refusal is not None
    assert outcome.refusal.kind == "no_support"
    assert "Master Services Agreement" in outcome.refusal.message
    assert _types(events) == ["stage", "stage", "stage", "stage", "refusal", "usage", "done"]
    assert chat.prompts == [], "no extraction may be attempted with no sources"


async def test_everything_below_the_floor_refuses_and_reports_the_best_score() -> None:
    # `summary_relevance_floor`, not the retriever's: Summary overrides the
    # floor per call, so the retriever's value is not what governs here.
    workflow = _workflow(
        hits=[Hit(chunk(1, text=CAP_TEXT, parent="p1", doc=1), 0.9)],
        scores={"c1": 0.11},
        floor=0.6,
        summary_relevance_floor=0.6,
    )
    _, outcome = await _collect(workflow)

    assert outcome.refused is True
    assert outcome.refusal is not None
    assert outcome.refusal.kind == "below_threshold"
    assert outcome.refusal.best_relevance == pytest.approx(0.11)
    assert outcome.refusal.threshold == 0.6


async def test_serialising_retrieval_changes_nothing_but_the_order_of_work() -> None:
    """`summary_retrieval_concurrency=1` is the escape hatch for a memory-bound
    deployment, so it has to be a pure scheduling change — same fields, same
    citation numbers, same coverage as the concurrent default."""
    _, concurrent = await _collect(_workflow())
    _, serial = await _collect(_workflow(summary_retrieval_concurrency=1))

    assert [f.key for f in serial.fields] == [f.key for f in concurrent.fields]
    assert [c.n for c in serial.citations] == [c.n for c in concurrent.citations]
    assert serial.refused is concurrent.refused is False


async def test_a_weak_match_is_summarised_rather_than_refused_by_default() -> None:
    """Regression: the shipped default must not gate fields on the floor.

    The cross-encoder scores a template field ("parties", "dates") near zero
    even on a document that plainly contains them — it is trained to score
    "does this passage answer this question", and a field is not a question.
    Inheriting Ask's 0.25 admission test therefore refused every summary of
    every document. Summary ranks; it does not gate.
    """
    workflow = _workflow(
        hits=[Hit(chunk(1, text=CAP_TEXT, parent="p1", doc=1), 0.9)],
        scores={"c1": 0.013},
        floor=0.25,
    )
    _, outcome = await _collect(workflow)

    assert outcome.refused is False
    assert outcome.fields, "a weakly-scored passage must still be summarised from"


# ----------------------------------------------------------------- FR-17


async def test_coverage_counts_pages_actually_retrieved() -> None:
    _, outcome = await _collect(_workflow(), document_pages=312)

    assert outcome.coverage is not None
    assert outcome.coverage.total_pages == 312
    assert outcome.coverage.pages_covered == 1
    assert outcome.coverage.passes == 9
    assert "would be reported as absent" in outcome.coverage.note


async def test_coverage_is_omitted_when_the_page_count_is_unknown() -> None:
    """Better absent than invented: a coverage claim is only useful if its
    denominator is real."""
    _, outcome = await _collect(_workflow(), document_pages=0)
    assert outcome.coverage is None


# ----------------------------------------------------------- FR-21 / FR-22


async def test_an_unresolvable_citation_is_stripped_from_the_field() -> None:
    chat = FakeStructuredChat(
        default=FieldExtraction(
            reasoning="present",
            present=True,
            value_md=f"{CAP_TEXT}[[9]]",
            citations=[9],
        )
    )
    _, outcome = await _collect(_workflow(chat))

    assert 9 in outcome.stripped_citations
    first = next(f for f in outcome.fields if f.value_md)
    assert "[[9]]" not in first.value_md


async def test_fabricated_authority_withholds_that_field_and_keeps_the_rest() -> None:
    """A summary is nine independent findings. Voiding all nine over one bad
    sentence would throw away eight good ones — see the draft workflow for the
    opposite call, and why."""
    chat = FakeStructuredChat(
        {
            "Governing law and dispute resolution": FieldExtraction(
                reasoning="venue",
                present=True,
                value_md="Venue lies in Delaware, see Pennoyer v. Neff, 95 U.S. 714.[[1]]",
                citations=[1],
            )
        },
        default=FieldExtraction(
            reasoning="present", present=True, value_md=f"{CAP_TEXT}[[1]]", citations=[1]
        ),
    )
    _, outcome = await _collect(_workflow(chat))

    law = next(f for f in outcome.fields if f.key == "law")
    assert law.value_md is not None
    assert "Withheld" in law.value_md
    assert "Pennoyer" not in law.value_md
    assert law.citations == []
    assert outcome.fabricated, "the finding must be recorded for the trace"

    # The other eight survive.
    assert outcome.fields_present == 9  # the withheld one still carries text
    assert sum(1 for f in outcome.fields if f.citations) == 8


async def test_a_flag_is_only_honoured_on_a_flaggable_field() -> None:
    """A flag on every field is a flag on nothing."""
    chat = FakeStructuredChat(
        default=FieldExtraction(
            reasoning="present",
            present=True,
            value_md=f"{CAP_TEXT}[[1]]",
            citations=[1],
            flag="unusual",
            flag_note="notable",
        )
    )
    _, outcome = await _collect(_workflow(chat))

    flagged = {f.key for f in outcome.fields if f.flag}
    assert flagged == {"liability", "assignment", "unusual"}
    assert all(f.flag_note for f in outcome.fields if f.flag)


# ------------------------------------------------------------- numbering


def test_renumber_rewrites_the_slice_to_its_ledger_numbers() -> None:
    """`assemble` always numbers from 1 and the gate resolves against the whole
    ledger, so a field's slice has to be rewritten before the model sees it.
    Without this, every citation after the first field points at the wrong
    clause."""
    sources = '<source n="1" document="A" page="1">x</source>\n\n<source n="2" document="A" page="2">y</source>'
    assert '<source n="4"' in _renumber(sources, [4, 7])
    assert '<source n="7"' in _renumber(sources, [4, 7])
    assert '<source n="1"' not in _renumber(sources, [4, 7])


async def test_a_field_sees_only_its_own_sources() -> None:
    """Nine fields' passages in one prompt invites answering the liability field
    from the termination clause."""
    chat = FakeStructuredChat()
    await _collect(_workflow(chat))

    assert len(chat.prompts) == 9
    for rendered in chat.prompts:
        # The system prompt carries one illustrative `<source>` of its own, so
        # the real passages are everything after the human message begins.
        human = rendered.split("The field to extract:")[0].split("</source>")
        assert rendered.count("<source n=") == 2, "one illustrative + one real"
        assert any("Liability under this Agreement" in part for part in human)


# ----------------------------------------------------------------- usage


async def test_usage_is_summed_across_every_field_call() -> None:
    chat = FakeStructuredChat()
    events, outcome = await _collect(_workflow(chat))
    usage = next(e for e in events if e.type == "usage")

    assert outcome.usage.input_tokens == 4200 * 9
    assert usage.usage.input_tokens == 4200 * 9
    assert usage.usage.usd > 0
    assert usage.versions.model == "claude-sonnet-5"
    assert usage.versions.prompt == PROMPT.version
    assert len(outcome.calls) == 9


async def test_the_field_budget_caps_what_a_long_template_can_cost() -> None:
    _, outcome = await _collect(_workflow(summary_max_fields=3))
    assert len(outcome.fields) == 3
    assert len(outcome.queries) == 3


# ------------------------------------------------------------- the prompt


def test_the_prompt_states_the_absent_means_absent_rule() -> None:
    """FR-16 lives in the prompt as well as in the code; a prompt that stopped
    saying so would change behaviour with no code change to review."""
    system = PROMPT.system.casefold()
    assert "absent means absent" in system
    assert "do not infer" in system
    assert "never write a case name" in system


def test_the_prompt_carries_its_labelled_examples() -> None:
    expected = {"present_cited", "absent", "partial", "flagged"}
    assert {e.expected for e in PROMPT.examples} == expected
