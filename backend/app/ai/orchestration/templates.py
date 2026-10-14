"""The schemas Summary extracts against and Draft composes into (BRD §9, FR-18).

Duplicated from `app/ai/fixtures/{summaries,drafts}.py` rather than imported,
for the reason `workflows/stages.py` gives about `STAGE_LABEL`: the fixture
layer is scheduled for deletion once this one is wired in, and the replacement
must not depend on the thing it replaces. `tests/test_summary_workflow.py` and
`tests/test_draft_workflow.py` assert the field keys, labels and section
headings still match the fixtures, so the duplication cannot drift while both
exist.

**What is new here, and is not in the fixtures: the per-field query.** The
fixture summaries are authored prose, so they needed only a label per field. A
real summary has to *retrieve* per field, and the field label is a poor query —
"Unusual or off-market terms" matches nothing in a contract, because no contract
says that about itself. So each field carries a query written in the language
documents actually use. This is the whole reason Summary is not just Ask run
nine times with the label as the question.

**Why a fixed schema per document type at all.** FR-16 requires an absent term
to be reported as absent rather than inferred, and that is only checkable
against a schema decided *before* the document is read. A model asked to
"summarise this contract" writes about what the contract contains and is
silent about what it omits — the omission is invisible, which is the failure
FR-16 names. Asking per field forces an answer for every field, including
`None`, which is what makes Summary measurable as field-level precision rather
than a judgement about prose.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import DocType, DraftType

#: What the summary card names as its template. Mirrors `TEMPLATE_NAME`.
TEMPLATE_NAME: dict[str, str] = {
    "contract": "Contract / agreement template",
    "opinion": "Judicial opinion template",
    "pleading": "Pleading / filing template",
    "other": "General document template",
}


@dataclass(frozen=True)
class TemplateField:
    """One row of a summary template.

    `query` is what retrieval searches for; `label` is what the attorney reads;
    `key` is what the field is persisted and evaluated under. Keeping them
    separate is the point — see the module docstring.
    """

    key: str
    label: str
    query: str
    #: Fields where an unusual value is worth flagging in the UI (FR-16's
    #: `flag`). Only set where "off-market" is a meaningful judgement about the
    #: clause rather than about the deal.
    flaggable: bool = False


#: The four schemas. Field order is presentation order and is load-bearing: the
#: card renders in this order, and the fields stream in it, so the most
#: important one must not arrive last on a document that takes three minutes.
TEMPLATES: dict[str, tuple[TemplateField, ...]] = {
    "contract": (
        TemplateField(
            "parties",
            "Parties and roles",
            "parties to this agreement, defined terms for each party, recitals",
        ),
        TemplateField(
            "term",
            "Effective date, term, renewal",
            "effective date, initial term, renewal term, automatic renewal, expiration",
        ),
        TemplateField(
            "obligations",
            "Core obligations per party",
            "obligations of the parties, services to be provided, deliverables, covenants",
        ),
        TemplateField(
            "payment",
            "Payment / consideration",
            "fees, payment terms, invoicing, net days, late payment interest, consideration",
        ),
        TemplateField(
            "termination",
            "Termination rights and notice periods",
            "termination for convenience, termination for cause, written notice "
            "period, cure period",
        ),
        TemplateField(
            "liability",
            "Limitation of liability and indemnity",
            "limitation of liability, liability cap, consequential damages, "
            "indemnification, hold harmless",
            flaggable=True,
        ),
        TemplateField(
            "assignment",
            "Assignment and change of control",
            "assignment, transfer, change of control, merger, consent to assign",
            flaggable=True,
        ),
        TemplateField(
            "law",
            "Governing law and dispute resolution",
            "governing law, jurisdiction, venue, arbitration, dispute resolution, "
            "waiver of jury trial",
        ),
        TemplateField(
            "unusual",
            "Unusual or off-market terms",
            "exclusivity, most favoured nation, non-compete, liquidated damages, "
            "unilateral amendment, automatic price increase, perpetual licence",
            flaggable=True,
        ),
    ),
    "opinion": (
        TemplateField(
            "posture",
            "Procedural posture",
            "procedural posture, appeal from, motion for summary judgment, on review",
        ),
        TemplateField(
            "facts", "Facts", "factual background, the parties, what happened, undisputed facts"
        ),
        TemplateField(
            "issues",
            "Issues presented",
            "issue presented, question before the court, whether the court should",
        ),
        TemplateField("holding", "Holding", "we hold, the court holds, holding, accordingly we"),
        TemplateField(
            "reasoning",
            "Reasoning",
            "reasoning, analysis, because, the test requires, we reason that",
        ),
        TemplateField(
            "disposition",
            "Disposition",
            "affirmed, reversed, remanded, vacated, judgment entered, disposition",
        ),
        TemplateField(
            "separate",
            "Notable concurrences / dissents",
            "concurring, dissenting, I respectfully dissent, concurrence",
        ),
    ),
    "pleading": (
        TemplateField(
            "court",
            "Court, parties, cause of action",
            "in the court of, plaintiff, defendant, caption, cause of action, count",
        ),
        TemplateField(
            "relief",
            "Relief sought",
            "prayer for relief, wherefore plaintiff requests, damages sought, injunctive relief",
        ),
        TemplateField(
            "allegations",
            "Key factual allegations",
            "plaintiff alleges, on information and belief, factual allegations",
        ),
        TemplateField(
            "theories",
            "Legal theories asserted",
            "breach of contract, negligence, count one, legal theory, claim for relief",
        ),
        TemplateField(
            "dates",
            "Deadlines and dates in the document",
            "dated, filed on, deadline, within days of, service date, hearing date",
        ),
    ),
    "other": (
        TemplateField(
            "what", "What this document is", "title, subject, purpose of this document, re:"
        ),
        TemplateField(
            "parties", "Parties or persons named", "names, signatories, from, to, attendees"
        ),
        TemplateField(
            "substance", "Substantive content", "summary, discussion, findings, conclusions"
        ),
        TemplateField("dates", "Dates appearing in the document", "dated, on, deadline, as of"),
    ),
}


def template_for(document_type: str) -> tuple[TemplateField, ...]:
    """The schema for a document type, falling back to `other`.

    Falls back rather than raising: a document whose type the classifier could
    not resolve still deserves a summary, and the general template produces a
    useful one. An unknown type is a classification gap, not a reason to refuse.
    """
    return TEMPLATES.get(document_type, TEMPLATES["other"])


def template_name(document_type: str) -> str:
    return TEMPLATE_NAME.get(document_type, TEMPLATE_NAME["other"])


def normalise_doc_type(value: str | None) -> DocType:
    """Narrow an ingestion-assigned type string to the `DocType` union."""
    if value in ("contract", "opinion", "pleading", "other"):
        return value  # type: ignore[return-value]  # narrowed by the membership test
    return "other"


# ---------------------------------------------------------------------- draft


@dataclass(frozen=True)
class DraftSectionPlan:
    """One section of a draft, and the instruction that writes it.

    `instruction` is sent to the model per section rather than one instruction
    for the whole document. FR-18 requires each draft type in its *conventional*
    structure, and a single call asked for "a memo" produces five headings of
    undifferentiated prose — a Question Presented that argues, a Short Answer as
    long as the Discussion. Section-at-a-time is also what makes FR-20
    enforceable, because a section that cites nothing is identifiable as the
    unit that failed.
    """

    key: str
    heading: str
    instruction: str
    #: A section that may legitimately carry no citation. Only the two that
    #: restate the attorney's own framing — everything else is an assertion
    #: about the documents and FR-20 requires support for it.
    citation_optional: bool = False


DRAFT_TYPE_LABEL: dict[str, str] = {
    "memo": "Legal memorandum",
    "case_brief": "Case brief",
}

DRAFT_PLANS: dict[str, tuple[DraftSectionPlan, ...]] = {
    "memo": (
        DraftSectionPlan(
            "qp",
            "Question Presented",
            "State the legal question in one sentence, in the conventional "
            '"Whether ... where ..." form: the question, then the legally '
            "material facts that make it concrete. Use the attorney's framing "
            "where they supplied one. Do not answer it here.",
            citation_optional=True,
        ),
        DraftSectionPlan(
            "short",
            "Short Answer",
            "Answer the question in two to four sentences. Lead with the "
            "answer, then the one or two provisions that drive it. If the "
            "sources do not resolve it, say so and name what is missing.",
        ),
        DraftSectionPlan(
            "facts",
            "Statement of Facts",
            "State only the facts that appear in the sources and bear on the "
            "question, in the order a reader needs them. No argument, no "
            "characterisation, no fact the sources do not contain.",
        ),
        DraftSectionPlan(
            "discussion",
            "Discussion",
            "Work through the operative language provision by provision. Quote "
            "the words that carry the weight. Where provisions interact, say "
            "how. Where they conflict or are silent, say that rather than "
            "resolving it. This is the longest section.",
        ),
        DraftSectionPlan(
            "conclusion",
            "Conclusion",
            "Restate the answer and the exposure it implies in two or three "
            "sentences. Add nothing not already supported above.",
        ),
    ),
    "case_brief": (
        DraftSectionPlan(
            "posture",
            "Procedural Posture",
            "How the case arrived at this court: the court below, the ruling "
            "appealed from, the standard of review if stated.",
        ),
        DraftSectionPlan(
            "facts",
            "Facts",
            "The facts the court treated as material, as the opinion states "
            "them. Not every fact in the opinion — the ones the holding turns on.",
        ),
        DraftSectionPlan(
            "issue",
            "Issue",
            "The question the court decided, in one sentence, as the court framed it.",
        ),
        DraftSectionPlan(
            "holding",
            "Holding",
            "What the court held, in one or two sentences. The rule, not the outcome.",
        ),
        DraftSectionPlan(
            "reasoning",
            "Reasoning",
            "The reasoning that produced the holding, in the court's own terms. "
            "Quote the operative language where the wording is the rule.",
        ),
        DraftSectionPlan(
            "disposition",
            "Disposition",
            "What the court ordered, and any separate opinions worth noting.",
        ),
    ),
}


def plan_for(draft_type: str) -> tuple[DraftSectionPlan, ...]:
    return DRAFT_PLANS.get(draft_type, DRAFT_PLANS["memo"])


def draft_type_label(draft_type: str) -> str:
    return DRAFT_TYPE_LABEL.get(draft_type, DRAFT_TYPE_LABEL["memo"])


def normalise_draft_type(value: str | None) -> DraftType:
    if value in ("memo", "case_brief"):
        return value  # type: ignore[return-value]  # narrowed by the membership test
    return "memo"
