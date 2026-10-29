"""Summary templates and authored fixtures, ported from
`frontend/src/mocks/summaries.ts`.

BRD §9: Summary is three features, not one. A contract summary and an opinion
summary share almost no structure, so each type has its own schema and its own
field set. A field with `value_md: None` is genuinely absent — never inferred,
never filled from model knowledge (FR-16). That is also what makes Summary
measurable: field-level precision, rather than a judgment about prose.
"""

from __future__ import annotations

from typing import Any

TEMPLATE_NAME: dict[str, str] = {
    "contract": "Contract / agreement template",
    "opinion": "Judicial opinion template",
    "pleading": "Pleading / filing template",
    "other": "General document template",
}

TEMPLATE_FIELDS: dict[str, list[dict[str, str]]] = {
    "contract": [
        {"key": "parties", "label": "Parties and roles"},
        {"key": "term", "label": "Effective date, term, renewal"},
        {"key": "obligations", "label": "Core obligations per party"},
        {"key": "payment", "label": "Payment / consideration"},
        {"key": "termination", "label": "Termination rights and notice periods"},
        {"key": "liability", "label": "Limitation of liability and indemnity"},
        {"key": "assignment", "label": "Assignment and change of control"},
        {"key": "law", "label": "Governing law and dispute resolution"},
        {"key": "unusual", "label": "Unusual or off-market terms"},
    ],
    "opinion": [
        {"key": "posture", "label": "Procedural posture"},
        {"key": "facts", "label": "Facts"},
        {"key": "issues", "label": "Issues presented"},
        {"key": "holding", "label": "Holding"},
        {"key": "reasoning", "label": "Reasoning"},
        {"key": "disposition", "label": "Disposition"},
        {"key": "separate", "label": "Notable concurrences / dissents"},
    ],
    "pleading": [
        {"key": "court", "label": "Court, parties, cause of action"},
        {"key": "relief", "label": "Relief sought"},
        {"key": "allegations", "label": "Key factual allegations"},
        {"key": "theories", "label": "Legal theories asserted"},
        {"key": "dates", "label": "Deadlines and dates in the document"},
    ],
    "other": [
        {"key": "what", "label": "What this document is"},
        {"key": "parties", "label": "Parties or persons named"},
        {"key": "substance", "label": "Substantive content"},
        {"key": "dates", "label": "Dates appearing in the document"},
    ],
}

#: Span sets backing each summary, in citation order.
SUMMARY_SPANS: dict[str, list[str]] = {
    "doc_msa": [
        "s_msa_0101",
        "s_msa_0201",
        "s_msa_0301",
        "s_msa_0905",
        "s_msa_1512",
        "s_msa_1102",
        "s_msa_1226",
        "s_msa_0902",
        "s_amend_0302",
        "s_msa_1223",
        "s_msa_1401",
    ],
    "doc_vance": [
        "s_vance_post",
        "s_vance_facts",
        "s_vance_issue",
        "s_vance_hold",
        "s_vance_reason",
        "s_vance_disp",
        "s_vance_dissent",
    ],
    "doc_tessara": [
        "s_tess_court",
        "s_tess_relief",
        "s_tess_alleg",
        "s_tess_theory",
        "s_tess_dates",
    ],
    "doc_delmar": ["s_delmar_coc", "s_delmar_hold"],
    "doc_nda": ["s_nda_0401"],
    "doc_amendment": ["s_amend_0302"],
}

_MSA: dict[str, Any] = {
    "document_id": "doc_msa",
    "document_name": "Nexora–Arclight Master Services Agreement.pdf",
    "document_type": "contract",
    "template_name": TEMPLATE_NAME["contract"],
    # FR-17: a 312-page agreement is composed, not truncated, and the ledger
    # says what was covered.
    "coverage": {
        "pages_covered": 312,
        "total_pages": 312,
        "passes": 7,
        "note": (
            "Composed from all 312 pages in 7 passes. Schedules 4.2 and 12.3 are referenced "
            "in the body but are not present in the file, and are reported as absent rather "
            "than inferred."
        ),
    },
    "fields": [
        {
            "key": "parties",
            "label": "Parties and roles",
            "value_md": "**Provider** — Nexora Systems, Inc., a Delaware corporation, principal place of business 1400 Harrow Street, Wilmington, Delaware.\n\n**Customer** — Arclight Industrial Holdings, LLC, a New York limited liability company. Customer’s obligations extend to its Affiliates in the insurance covenant.",
            "citations": [1],
        },
        {
            "key": "term",
            "label": "Effective date, term, renewal",
            "value_md": "Effective 14 March 2024. Initial term of 36 months, then automatic successive 12-month Renewal Terms. Either party may stop renewal with 90 days’ written notice before the end of the then-current term.",
            "citations": [1, 2],
        },
        {
            "key": "obligations",
            "label": "Core obligations per party",
            "value_md": "**Provider** — furnish the Services per each Statement of Work; defend and indemnify Customer against third-party US patent, copyright and trade-secret infringement claims arising from the Services.\n\n**Customer** — pay undisputed invoices within 45 days; maintain CGL insurance of at least $10m per occurrence naming Provider as additional insured, surviving termination for six years.",
            "citations": [3, 4, 5],
        },
        {
            "key": "payment",
            "label": "Payment / consideration",
            "value_md": "Net 45 from receipt on undisputed invoices. Late amounts accrue interest at the lesser of 1.5% per month or the maximum lawful rate. Renewal-term fee increases capped at the lesser of 5% or trailing-12-month CPI-U.\n\nThe fee schedule itself is in Schedule 4.2, which is referenced but not present in this file.",
            "citations": [3],
        },
        {
            "key": "termination",
            "label": "Termination rights and notice periods",
            "value_md": "| Route | Who | Notice |\n|---|---|---|\n| For convenience | **Customer only** | 60 days |\n| Non-renewal | Either party | 90 days before term end |\n| Change of Control | Non-transferring party | 30 days, within 60 days of notice |\n\nOn convenience termination Customer pays accrued fees plus documented, non-cancellable commitments reasonably incurred by Provider.",
            "citations": [6, 2, 7],
        },
        {
            "key": "liability",
            "label": "Limitation of liability and indemnity",
            "value_md": "Aggregate liability capped at the greater of 200% of trailing-12-month fees or **$12,000,000** — the base agreement states $5,000,000, amended upward by Amendment No. 3 effective 1 January 2025. Excluded Claims sit outside the cap, but their definition was not located in this document.\n\nProvider’s indemnity covers third-party claims of US patent, copyright or trade-secret infringement, conditioned on the Services being as furnished and used in accordance with the agreement. Trademark is not covered.",
            "citations": [8, 9, 4],
            "flag": "unusual",
            "flag_note": "The operative cap figure comes from a separate scanned amendment, not from the agreement body. Reading § 9.2 alone understates the cap by $7m.",
        },
        {
            "key": "assignment",
            "label": "Assignment and change of control",
            "value_md": "No assignment or transfer, including by operation of law, without prior written consent — subject to a carve-out permitting assignment without consent to a successor in a merger, consolidation, reorganisation, or sale of all or substantially all assets or voting securities.\n\nThe carve-out does **not** help Customer where the assignee is a Restricted Competitor listed on Schedule 12.3 (schedule not present in this file). Transfer of more than 50% of voting securities is a Change of Control triggering the 30-day termination right.",
            "citations": [10, 7],
        },
        {
            "key": "law",
            "label": "Governing law and dispute resolution",
            "value_md": "New York law, expressly excluding its conflict-of-laws principles. Exclusive jurisdiction in state and federal courts in Manhattan, venue objections waived, mutual irrevocable waiver of jury trial.\n\nThere is no arbitration provision in this agreement.",
            "citations": [11],
        },
        {
            "key": "unusual",
            "label": "Unusual or off-market terms",
            "value_md": "1. **Convenience termination is one-sided.** Customer may exit on 60 days’ notice; Provider has no equivalent right.\n2. **Insurance obligation survives six years** and binds Customer’s Affiliates — longer than the typical two-to-three-year tail.\n3. **Liability cap floor raised 140% by amendment** rather than by restating the agreement, so the body text is misleading on its face.\n4. **Restricted Competitor list is incorporated but missing**, which makes the assignment analysis unresolvable from this file alone.",
            "citations": [6, 5, 9, 10],
            "flag": "off_market",
            "flag_note": "Items 1 and 2 are asymmetric in Customer’s and Provider’s favour respectively.",
        },
    ],
}

_VANCE: dict[str, Any] = {
    "document_id": "doc_vance",
    "document_name": "Vance v. Ridgeline Logistics, 118 F.4th 221 (9th Cir. 2025).pdf",
    "document_type": "opinion",
    "template_name": TEMPLATE_NAME["opinion"],
    "fields": [
        {
            "key": "posture",
            "label": "Procedural posture",
            "value_md": "Appeal by defendant Ridgeline Logistics from (i) an order granting plaintiff partial summary judgment on an unpaid-overtime claim and (ii) denial of its motion to compel arbitration. Ninth Circuit jurisdiction under 28 U.S.C. § 1291; legal conclusions reviewed de novo.",
            "citations": [1],
        },
        {
            "key": "facts",
            "label": "Facts",
            "value_md": 'Vance was a route coordinator at Ridgeline’s Sacramento terminal, 2019–2023. Her offer letter incorporated an employee handbook by reference. The handbook contained an arbitration provision and elsewhere described itself as subject to unilateral amendment by Ridgeline "at any time and without notice."',
            "citations": [2],
        },
        {
            "key": "issues",
            "label": "Issues presented",
            "value_md": "Whether an arbitration clause in a handbook that the employer reserves the unilateral right to amend without notice is supported by consideration sufficient to form an enforceable agreement under California law.",
            "citations": [3],
        },
        {
            "key": "holding",
            "label": "Holding",
            "value_md": "An arbitration provision the employer may amend or revoke unilaterally, at any time and without notice, is **illusory and unenforceable for want of consideration**. Denial of the motion to compel was therefore correct.",
            "citations": [4],
        },
        {
            "key": "reasoning",
            "label": "Reasoning",
            "value_md": 'A promise is illusory where the promisor retains an unlimited right to determine the nature and extent of its own performance. The reserved right to amend "at any time and without notice" would have allowed Ridgeline to withdraw the promise to arbitrate on the eve of a claim — which the court treats as promising nothing at all.',
            "citations": [5],
        },
        {
            "key": "disposition",
            "label": "Disposition",
            "value_md": "Denial of the motion to compel arbitration **affirmed**. Partial summary judgment on the overtime claim **vacated and remanded**. Each party bears its own costs on appeal.",
            "citations": [6],
        },
        {
            "key": "separate",
            "label": "Notable concurrences / dissents",
            "value_md": "**Almeida, J., dissenting in part.** Agrees the overtime judgment cannot stand but would enforce the arbitration provision, arguing the majority’s illusoriness analysis sweeps more broadly than precedent permits by reading a general amendment clause as a reservation of the right to amend *retroactively*.",
            "citations": [7],
            "flag": "unusual",
            "flag_note": "The dissent identifies the distinction opposing counsel is most likely to press.",
        },
    ],
}

_TESSARA: dict[str, Any] = {
    "document_id": "doc_tessara",
    "document_name": "Complaint — Tessara Fabrication v. Juneau Mills.pdf",
    "document_type": "pleading",
    "template_name": TEMPLATE_NAME["pleading"],
    "fields": [
        {
            "key": "court",
            "label": "Court, parties, cause of action",
            "value_md": "N.D. Ill., Eastern Division. Case No. 1:25-cv-04417. **Tessara Fabrication LLC** v. **Juneau Mills Corporation**. Breach of contract and misappropriation of trade secrets. Jury trial demanded.",
            "citations": [1],
        },
        {
            "key": "relief",
            "label": "Relief sought",
            "value_md": "(a) judgment on all counts; (b) compensatory damages of not less than **$4,200,000**; (c) exemplary damages under 18 U.S.C. § 1836(b)(3)(C); (d) a permanent injunction against further use or disclosure of the Trade Secrets; (e) attorneys’ fees and costs.",
            "citations": [2],
        },
        {
            "key": "allegations",
            "label": "Key factual allegations",
            "value_md": "From no later than February 2025, Juneau Mills used Tessara’s proprietary tolerance specifications for the HX-series housing to solicit Tessara’s customers — at least four identified in Exhibit D — after executing the Mutual NDA attached as Exhibit A.\n\nExhibits A and D are referenced but are not in this workspace.",
            "citations": [3],
        },
        {
            "key": "theories",
            "label": "Legal theories asserted",
            "value_md": "Count III pleads misappropriation of trade secrets under the Defend Trade Secrets Act, 18 U.S.C. § 1836, incorporating paragraphs 1–84.\n\nCounts I and II are referenced by the prayer for relief but their text was not located in the indexed pages of this filing.",
            "citations": [4, 2],
        },
        {
            "key": "dates",
            "label": "Deadlines and dates in the document",
            "value_md": "- Mutual NDA executed **3 November 2023**\n- Supply agreement terminated by Juneau Mills **19 April 2025**\n- Action commenced **22 July 2025**, asserted to be within the three-year DTSA limitations period under § 1836(d)\n\nNo response deadline, scheduling order or court-imposed date appears in this document.",
            "citations": [5],
        },
    ],
}

SUMMARIES: dict[str, dict[str, Any]] = {
    "doc_msa": _MSA,
    "doc_vance": _VANCE,
    "doc_tessara": _TESSARA,
}


def summary_for(document_id: str, document_name: str, doc_type: str) -> dict[str, Any]:
    """Documents without an authored fixture still produce a structurally valid
    summary: the right template, every field marked absent. That is the honest
    degradation, and it is what FR-16 requires — a field with no supporting span
    is returned empty, never filled by inference."""
    authored = SUMMARIES.get(document_id)
    if authored is not None:
        return authored
    template = doc_type if doc_type in TEMPLATE_FIELDS else "other"
    return {
        "document_id": document_id,
        "document_name": document_name,
        "document_type": doc_type,
        "template_name": TEMPLATE_NAME[template],
        "fields": [
            {"key": f["key"], "label": f["label"], "value_md": None, "citations": []}
            for f in TEMPLATE_FIELDS[template]
        ],
    }
