"""Scripted answers over the fixture corpus, ported from
`frontend/src/mocks/answers.ts`.

Citation markers are `[[n]]` and map 1:1 onto `span_ids` order, which is how a
citation arrives mid-stream exactly when the claim needing it is written.

The fallback is a refusal, not a guess. That is the whole point: the fixture
engine declines for the same reason the real system will — nothing retrieved
clears the floor (FR-14, TS-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScriptedAnswer:
    #: Any of these phrases in the question selects this answer.
    match: list[str]
    #: Retrieval queries shown in the trace and in a refusal.
    queries: list[str]
    span_ids: list[str]
    answer_md: str
    relevances: list[float] = field(default_factory=list)


SCRIPTED: list[ScriptedAnswer] = [
    ScriptedAnswer(
        match=["assign", "assignment", "change of control", "coc", "successor", "merger"],
        queries=[
            "assignment change of control successor in interest",
            "anti-assignment consent transfer of agreement",
            "merger consolidation sale of substantially all assets",
        ],
        span_ids=["s_msa_1223", "s_msa_1226", "s_delmar_coc"],
        relevances=[0.94, 0.88, 0.63],
        answer_md="""**Yes — assignment is restricted, but there is a broad merger carve-out with one exception.**

The agreement bars assignment or transfer by either party, including by operation of law, without the other party's prior written consent. [[1]] Two qualifications matter:

1. **Successor carve-out.** Either party may assign *without* consent to a successor in interest in a merger, consolidation, reorganisation, or sale of all or substantially all assets or voting securities. [[1]]
2. **Restricted Competitor exception.** The carve-out does not apply to Customer where the proposed assignee appears on Schedule 12.3 — in that case Customer's consent is still required. [[1]]

Separately, a transfer of **more than 50% of voting securities** is a Change of Control, and the non-transferring party then has a **30-day termination right**, exercisable only within 60 days of receiving notice of the change. [[2]]

One thing worth flagging: Schedule 12.3 is referenced but is not among the documents in this workspace, so the identity of the Restricted Competitors cannot be confirmed here.

> **Related authority in your documents.** *Delmar Holdings* declined to read "all or substantially all of its assets" as reaching a transaction where the transferor kept its operating business and sold only a minority equity interest — and refused to imply a lower threshold where sophisticated parties had specified a voting-securities test. [[3]] That reading supports a narrow construction of the § 12.3 carve-out.""",
    ),
    ScriptedAnswer(
        match=[
            "liability",
            "liability cap",
            "damages cap",
            "limitation of liability",
            "exposure",
            "cap on damages",
        ],
        queries=[
            "limitation of liability aggregate cap excluded claims",
            "cap on damages consequential indirect",
            "amendment to limitation of liability",
        ],
        span_ids=["s_msa_0902", "s_amend_0302"],
        relevances=[0.96, 0.91],
        answer_md="""**The cap is the greater of 200% of trailing-12-month fees or $12,000,000 — not the $5,000,000 printed in the base agreement.**

Section 9.2 of the master agreement caps each party's aggregate liability at the greater of (a) 200% of fees paid or payable by Customer in the preceding 12 months, or (b) $5,000,000, in both cases except for Excluded Claims. [[1]]

That figure has been superseded. Amendment No. 3 amends and restates § 9.2 to replace "$5,000,000" with **"$12,000,000"**, effective 1 January 2025. [[2]]

So the operative floor is **$12,000,000**, and the effective cap is whichever is greater of that figure and 200% of trailing fees.

Two caveats on this answer:

- **Excluded Claims are not defined in the retrieved text.** The carve-out is referenced in § 9.2 but its definition sits elsewhere in the agreement and was not retrieved for this question — worth confirming before you rely on the cap.
- **Amendment No. 3 is a scanned document.** It was read by OCR at 0.79 mean confidence. The amended figure appears on page 2, which was not among the flagged low-confidence pages, but the dollar figure is worth confirming against the executed original.""",
    ),
    ScriptedAnswer(
        match=["terminate", "termination", "notice period", "exit", "get out of", "convenience"],
        queries=[
            "termination for convenience notice period",
            "term renewal non-renewal notice",
            "change of control termination right",
        ],
        span_ids=["s_msa_1102", "s_msa_0201", "s_msa_1226"],
        relevances=[0.95, 0.84, 0.72],
        answer_md="""**There are three distinct exits, each with a different notice period.**

| Route | Who may use it | Notice | Cost on exit |
|---|---|---|---|
| Termination for convenience | Customer only | 60 days' written notice [[1]] | Accrued fees plus documented non-cancellable commitments [[1]] |
| Non-renewal | Either party | 90 days before end of term [[2]] | None stated |
| Change of Control | The non-transferring party | 30 days, exercisable within 60 days of notice [[3]] | None stated |

**On convenience termination**, note the asymmetry: the right runs to Customer only, and the clause covers any Statement of Work as well as the agreement as a whole. [[1]] The payment tail is the part to diligence — "documented, non-cancellable commitments reasonably incurred" is undefined in the retrieved text and is where the cost of an exit actually sits.

**On non-renewal**, the initial term is 36 months from the 14 March 2024 Effective Date, rolling into automatic 12-month renewals. [[2]] On that timeline the first non-renewal notice would be due by **approximately 14 December 2026** — worth confirming against the executed signature page, which was not retrieved here.""",
    ),
    ScriptedAnswer(
        match=[
            "payment",
            "invoice",
            "pay",
            "late fee",
            "interest",
            "fees",
            "price increase",
            "cpi",
        ],
        queries=[
            "payment terms invoice due date net days",
            "late payment interest rate",
            "fee increase renewal CPI cap",
        ],
        span_ids=["s_msa_0301"],
        relevances=[0.95],
        answer_md="""**Net 45 on undisputed invoices, 1.5% monthly interest on late amounts, and renewal increases capped at 5% or CPI-U.**

- **Due date.** Undisputed invoices are payable within 45 days of receipt. [[1]] Note the qualifier — the clause creates an implicit dispute mechanism but the retrieved text does not say how a dispute is raised or within what period.
- **Late payment.** Interest accrues at the lesser of 1.5% per month (an 18% nominal annual rate) or the maximum rate permitted by applicable law. [[1]]
- **Fee increases.** For any Renewal Term, Provider may increase fees by no more than the lesser of 5% or the trailing twelve-month increase in CPI-U. [[1]]

The CPI-U reference is to an external index whose values are not in your documents, so the actual permitted increase for a given renewal cannot be computed here.""",
    ),
    ScriptedAnswer(
        match=[
            "governing law",
            "jurisdiction",
            "venue",
            "jury",
            "forum",
            "dispute resolution",
            "which law",
        ],
        queries=[
            "governing law choice of law",
            "exclusive jurisdiction venue forum selection",
            "waiver of jury trial arbitration",
        ],
        span_ids=["s_msa_1401"],
        relevances=[0.97],
        answer_md="""**New York law, exclusive jurisdiction in Manhattan, and a mutual jury waiver.**

- **Governing law.** New York, expressly without regard to its conflict-of-laws principles. [[1]]
- **Forum.** Exclusive jurisdiction in the state and federal courts sitting in the Borough of Manhattan, with any venue objection waived. [[1]]
- **Jury.** Each party irrevocably waives trial by jury. [[1]]

**There is no arbitration clause in the retrieved text** — this is a litigation-forum provision, not an arbitration provision. If you are looking for arbitration in this agreement, nothing in what was retrieved supports one, though I can only speak to the 312 pages indexed.""",
    ),
    ScriptedAnswer(
        match=[
            "arbitration",
            "illusory",
            "consideration",
            "handbook",
            "compel arbitration",
            "unenforceable",
            "vance",
        ],
        queries=[
            "arbitration provision unilateral amendment illusory consideration",
            "motion to compel arbitration handbook",
            "holding disposition appeal",
        ],
        span_ids=[
            "s_vance_hold",
            "s_vance_reason",
            "s_vance_facts",
            "s_vance_disp",
            "s_vance_dissent",
        ],
        relevances=[0.97, 0.93, 0.81, 0.74, 0.58],
        answer_md="""**Held: an arbitration clause the employer may amend or revoke unilaterally, at any time and without notice, is illusory and unenforceable for want of consideration.** [[1]]

**Reasoning.** A promise is illusory where the promisor retains an unlimited right to determine the nature and extent of its own performance. Ridgeline's reserved right to amend the handbook "at any time and without notice" was such a right — it could have withdrawn the promise to arbitrate on the eve of a claim, which the court treats as having promised nothing at all. [[2]]

**The operative facts.** Vance's offer letter incorporated the handbook by reference, and the handbook itself described the arbitration provision as subject to unilateral amendment by Ridgeline "at any time and without notice." [[3]] It is the combination — incorporation by reference plus an unrestricted amendment power — that drives the result.

**Disposition.** Denial of the motion to compel arbitration **affirmed**; partial summary judgment on the overtime claim **vacated and remanded**; costs borne by each party. [[4]]

**Limiting signal.** Judge Almeida, dissenting in part, would have enforced the provision, arguing the majority read a general amendment clause as though it reserved a right to amend *retroactively*. [[5]] If you are relying on *Vance*, expect that distinction to be pressed: a clause with notice requirements or prospective-only amendment may fall outside the holding.""",
    ),
    ScriptedAnswer(
        match=["indemn", "infringement", "defend", "hold harmless", "ip claim"],
        queries=[
            "indemnification defend hold harmless third-party claim",
            "intellectual property infringement indemnity",
        ],
        span_ids=["s_msa_0905"],
        relevances=[0.94],
        answer_md="""**Provider indemnifies Customer for third-party IP infringement claims arising from the Services.**

The obligation covers defence, indemnity and hold-harmless against any third-party claim that the Services — *as furnished by Provider and used in accordance with the agreement* — infringe a United States patent, copyright or trade secret, and extends to damages finally awarded or amounts agreed in settlement. [[1]]

Three limits are visible on the face of the clause:

1. **United States rights only.** Foreign patents and copyrights are outside it. [[1]]
2. **Two conditions precedent.** The Services must be as furnished by Provider *and* used in accordance with the agreement — modification or out-of-scope use breaks the indemnity. [[1]]
3. **No trademark.** Patent, copyright and trade secret are named; trademark is not. [[1]]

The retrieved text does not contain the procedural conditions usually paired with an indemnity — notice, control of defence, cooperation, settlement consent. Those may exist elsewhere in the agreement but were not retrieved for this question.""",
    ),
    ScriptedAnswer(
        match=[
            "trade secret",
            "relief sought",
            "damages sought",
            "tessara",
            "juneau",
            "complaint",
            "what is claimed",
        ],
        queries=[
            "relief requested prayer for relief damages",
            "counts causes of action legal theories",
            "factual allegations misappropriation",
        ],
        span_ids=["s_tess_relief", "s_tess_theory", "s_tess_alleg", "s_tess_dates"],
        relevances=[0.95, 0.9, 0.86, 0.69],
        answer_md="""**Tessara seeks at least $4.2m compensatory damages, exemplary damages, a permanent injunction, and fees.**

**Relief requested.** Judgment on all counts; compensatory damages of not less than $4,200,000; exemplary damages under 18 U.S.C. § 1836(b)(3)(C); a permanent injunction against further use or disclosure of the Trade Secrets; and attorneys' fees and costs. [[1]]

**Theory pleaded.** Count III is misappropriation of trade secrets under the Defend Trade Secrets Act, 18 U.S.C. § 1836, incorporating paragraphs 1–84. [[2]]

**Core allegation.** From no later than February 2025, Juneau Mills is alleged to have used Tessara's proprietary tolerance specifications for the HX-series housing to solicit Tessara's customers — at least four identified in Exhibit D — after executing the Mutual NDA attached as Exhibit A. [[3]]

**Timeline as pleaded.** NDA executed 3 November 2023; supply agreement terminated by Juneau Mills 19 April 2025; action commenced 22 July 2025, which the complaint asserts is within the three-year DTSA limitations period. [[4]]

Note that Exhibits A and D are referenced but are not in this workspace, so the NDA terms and the customer identities cannot be verified from what is indexed.""",
    ),
    ScriptedAnswer(
        match=["insurance", "certificate of insurance", "additional insured", "coverage limits"],
        queries=[
            "insurance requirements limits additional insured",
            "survival of obligations after termination",
        ],
        span_ids=["s_msa_1512"],
        relevances=[0.93],
        answer_md="""**Customer carries CGL of at least $10m per occurrence, names Provider as additional insured, and the obligation survives for six years.**

The requirement runs to Customer *and its Affiliates*, with commercial general liability limits of not less than $10,000,000 per occurrence, Provider named as an additional insured, and certificates furnished on request. [[1]] The obligation survives termination for six years. [[1]]

Note the direction of this clause: it obliges **Customer**, not Provider. The retrieved text contains no reciprocal insurance requirement on Provider — if you expected one, it is either elsewhere in the agreement or absent from it.""",
    ),
    ScriptedAnswer(
        match=[
            "confidentiality period",
            "nda term",
            "how long is confidentiality",
            "harbour point",
        ],
        queries=[
            "confidentiality obligations duration survival",
            "trade secret carve-out from time limit",
        ],
        span_ids=["s_nda_0401"],
        relevances=[0.95],
        answer_md="""**Five years from disclosure, with an indefinite tail for trade secrets.**

Confidentiality obligations run for five years from the date of disclosure — measured per disclosure, not from the agreement date. [[1]] Information that constitutes a trade secret is carved out of the five-year limit and protected for as long as it remains a trade secret under applicable law. [[1]]

The practical consequence of a per-disclosure clock is that the obligation expires on a rolling basis, so the last protected disclosure governs the real end date.""",
    ),
    ScriptedAnswer(
        match=["term", "renewal", "how long", "expiry", "effective date", "parties"],
        queries=["effective date term initial term renewal", "parties and roles"],
        span_ids=["s_msa_0201", "s_msa_0101"],
        relevances=[0.92, 0.8],
        answer_md="""**36-month initial term from 14 March 2024, auto-renewing in 12-month increments, 90 days' notice to stop it.**

The initial term runs 36 months from the Effective Date, then renews automatically for successive 12-month Renewal Terms unless either party gives written notice of non-renewal at least 90 days before the end of the then-current term. [[1]]

The Effective Date is 14 March 2024, between **Nexora Systems, Inc.** (Delaware corporation, Wilmington — "Provider") and **Arclight Industrial Holdings, LLC** (New York LLC — "Customer"). [[2]]

On that basis the initial term ends **14 March 2027**, and notice to prevent the first renewal would be due by **approximately 14 December 2026**. Both dates are computed from the Effective Date in the retrieved text rather than stated in the document.""",
    ),
]


def find_scripted(question: str) -> ScriptedAnswer | None:
    """Longest-match wins, as in the TS implementation."""
    q = question.lower()
    best: tuple[ScriptedAnswer, int] | None = None
    for answer in SCRIPTED:
        score = sum(len(m) for m in answer.match if m in q)
        if score > 0 and (best is None or score > best[1]):
            best = (answer, score)
    return best[0] if best else None


def refusal_for(question: str, scope_description: str) -> dict[str, object]:
    """FR-14: the refusal names the scope searched and what was looked for, and
    offers next actions. A refusal with no account of what was tried is
    indistinguishable from a failure."""
    topic = question.rstrip("?.!")[:90]
    lower = topic.lower()
    return {
        "kind": "below_threshold",
        "message": (
            f"Nothing in {scope_description} addresses this. The closest passages scored "
            "below the relevance floor, so rather than assemble an answer from "
            "weakly-related text, Legal Lens is declining."
        ),
        "looked_for": [
            lower,
            f"{' '.join(lower.split()[:4])} clause provision",
            f"definition of {' '.join(lower.split()[-3:])}",
        ],
        "best_relevance": 0.18,
        "threshold": 0.25,
        "suggestions": [
            {
                "kind": "widen_scope",
                "label": "Search the whole workspace",
                "detail": "Currently scoped narrower than your full document set.",
            },
            {
                "kind": "upload",
                "label": "Upload the document that covers this",
                "detail": (
                    "If the provision lives in a schedule or exhibit you have not added "
                    "yet, it cannot be found."
                ),
            },
            {
                "kind": "rephrase",
                "label": "Rephrase using the document’s own vocabulary",
                "detail": "Contracts often address a concept without using its common name.",
            },
        ],
    }


OUT_OF_SCOPE_REFUSAL: dict[str, object] = {
    "kind": "out_of_scope",
    "message": (
        "This is outside what Legal Lens does. It answers only from the documents in your "
        "workspace — it has no general knowledge of statutes, case law, or anything else, "
        "by design."
    ),
    "looked_for": [],
    "suggestions": [
        {"kind": "rephrase", "label": "Ask about something in your documents"},
        {"kind": "switch_mode", "label": "Summarise a document instead"},
    ],
}
