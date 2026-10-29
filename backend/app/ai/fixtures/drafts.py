"""Draft fixtures, ported from `frontend/src/mocks/drafts.ts`.

FR-18: each draft type in its conventional structure. FR-20: every factual
assertion carries a citation. FR-22 is the one that matters most — these drafts
cite only *Delmar*, *Vance* and the agreement itself, all of which are in the
corpus. No case, statute or docket number appears that is not in a source
document, including ones a model would "know" to be real. That is the rule that
keeps this out of *Mata v. Avianca*.
"""

from __future__ import annotations

from typing import Any

DRAFT_SECTIONS: dict[str, list[dict[str, str]]] = {
    "memo": [
        {"key": "qp", "heading": "Question Presented"},
        {"key": "short", "heading": "Short Answer"},
        {"key": "facts", "heading": "Statement of Facts"},
        {"key": "discussion", "heading": "Discussion"},
        {"key": "conclusion", "heading": "Conclusion"},
    ],
    "case_brief": [
        {"key": "posture", "heading": "Procedural Posture"},
        {"key": "facts", "heading": "Facts"},
        {"key": "issue", "heading": "Issue"},
        {"key": "holding", "heading": "Holding"},
        {"key": "reasoning", "heading": "Reasoning"},
        {"key": "disposition", "heading": "Disposition"},
    ],
}

DRAFT_TYPE_LABEL: dict[str, str] = {
    "memo": "Legal memorandum",
    "case_brief": "Case brief",
}

MEMO_SPANS = ["s_msa_1223", "s_msa_1226", "s_delmar_coc", "s_msa_1401", "s_msa_1102"]
BRIEF_SPANS = [
    "s_vance_post",
    "s_vance_facts",
    "s_vance_issue",
    "s_vance_hold",
    "s_vance_reason",
    "s_vance_disp",
    "s_vance_dissent",
]

_MEMO_DEFAULT_QP = (
    "Whether Nexora may withhold consent to assignment of the Master Services Agreement "
    "where Arclight transfers more than fifty percent of its voting securities to a "
    "strategic acquirer, and what termination exposure follows."
)


def _memo(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "draft_type": "memo",
        "title": "Assignment of the Nexora–Arclight MSA on a Change of Control",
        "params": params,
        "sections": [
            {
                "key": "qp",
                "heading": "Question Presented",
                "citations": [],
                "body_md": params.get("question_presented") or _MEMO_DEFAULT_QP,
            },
            {
                "key": "short",
                "heading": "Short Answer",
                "citations": [1, 2],
                "body_md": "**Probably not as to consent, but the transaction does expose the agreement to termination.** The successor carve-out in § 12.3 permits assignment without consent in connection with a sale of all or substantially all voting securities, so consent is not required unless the acquirer is a Restricted Competitor on Schedule 12.3. [[1]] However, a transfer of more than fifty percent of voting securities is a Change of Control, and § 12.6 gives Nexora an independent right to terminate on thirty days’ notice if exercised within sixty days of receiving notice. [[2]] The consent question and the termination question are therefore separate, and winning the first does not dispose of the second.",
            },
            {
                "key": "facts",
                "heading": "Statement of Facts",
                "citations": [1, 2],
                "body_md": "Arclight Industrial Holdings, LLC is the Customer under a Master Services Agreement with Nexora Systems, Inc. The agreement restricts assignment or transfer by either party, including by operation of law, without the other party’s prior written consent, subject to a carve-out for a successor in interest in a merger, consolidation, reorganisation, or sale of all or substantially all assets or voting securities. [[1]] Customer’s consent right is further qualified: the carve-out does not apply where the proposed assignee is a Restricted Competitor identified on Schedule 12.3. [[1]]\n\nThe agreement separately defines a transfer of more than fifty percent of a party’s voting securities as a Change of Control, and affords the non-transferring party a thirty-day termination right exercisable within sixty days after receiving notice. [[2]]\n\n*Schedule 12.3 is incorporated by reference but was not among the documents supplied for this draft, so the identity of the Restricted Competitors could not be verified.*",
            },
            {
                "key": "discussion",
                "heading": "Discussion",
                "citations": [1, 2, 3, 4, 5],
                "body_md": '### I. The successor carve-out most likely reaches the proposed transaction\n\nThe carve-out is drafted broadly: it covers a "merger, consolidation, reorganisation, or sale of all or substantially all of its assets or voting securities," and it operates without consent. [[1]] A transfer of a controlling voting-securities interest to a strategic acquirer falls within the plain terms of the final clause.\n\nThe authority supplied for this draft supports reading the carve-out according to its specified threshold rather than an implied one. In *Delmar Holdings v. Castellan*, the court declined to treat "sale of all or substantially all of its assets" as reaching a transaction in which the transferor retained its operating business and disposed only of a minority equity interest, and refused to supply a lower threshold by implication where sophisticated parties had defined a Change of Control by reference to a specified voting-securities test. [[3]]\n\nTwo observations follow, and the second cuts against our position. *Delmar* confirms that a specified threshold governs — helpful, because the proposed transaction exceeds the fifty-percent test on its face. But *Delmar*’s refusal to imply a lower threshold is a reading tool that runs in both directions: it equally forecloses an argument that the carve-out should be read *more* generously than its terms.\n\n### II. The Restricted Competitor exception is the live risk, and it is presently unresolvable\n\nThe carve-out withdraws from Customer alone where the assignee appears on Schedule 12.3. [[1]] Because that schedule was not supplied, this draft cannot state whether the exception applies. **This is the first item to resolve**, and it should be resolved before any notice is given, since it determines whether the transaction proceeds as a no-consent assignment or requires a negotiated consent.\n\n### III. Termination exposure survives a successful consent analysis\n\nEven on the assumption that no consent is required, § 12.6 operates independently. A transfer of more than fifty percent of voting securities is a Change of Control, and Nexora may terminate on thirty days’ notice. [[2]] The constraint on that right is temporal, not substantive: it must be exercised within sixty days of Nexora receiving notice of the change. [[2]] Nothing in the retrieved text conditions the right on reasonableness or on the identity of the acquirer.\n\nThe practical consequence is that the sixty-day window is the principal lever. Notice timing is within Arclight’s control, and the window closes.\n\n### IV. Forum\n\nAny dispute would proceed under New York law in the state or federal courts sitting in Manhattan, with venue objections and jury trial waived. [[4]] Should Arclight prefer to exit rather than litigate the Change of Control right, § 11.2 permits Customer to terminate for convenience on sixty days’ notice, subject to accrued fees and documented non-cancellable commitments. [[5]]',
            },
            {
                "key": "conclusion",
                "heading": "Conclusion",
                "citations": [1, 2],
                "body_md": "Consent to the assignment is most likely not required, because the successor carve-out in § 12.3 covers a sale of substantially all voting securities. [[1]] That conclusion is contingent on the acquirer not appearing on Schedule 12.3, which cannot be confirmed from the documents supplied. The separate Change of Control termination right under § 12.6 is unaffected by the consent analysis and represents the real exposure; it is time-limited to sixty days from notice, which makes notice sequencing the operative tactical question. [[2]]\n\n**Next steps.** (1) Obtain Schedule 12.3. (2) Confirm the acquirer against it. (3) Model the notice timeline against the sixty-day termination window before any notice is delivered.",
            },
        ],
    }


def _brief(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "draft_type": "case_brief",
        "title": "Vance v. Ridgeline Logistics, 118 F.4th 221 (9th Cir. 2025)",
        "params": params,
        "sections": [
            {
                "key": "posture",
                "heading": "Procedural Posture",
                "citations": [1],
                "body_md": "Defendant-appellant Ridgeline Logistics appealed from the district court’s order granting plaintiff Vance partial summary judgment on her claim for unpaid overtime and from the denial of Ridgeline’s motion to compel arbitration. The Ninth Circuit took jurisdiction under 28 U.S.C. § 1291 and reviewed the district court’s legal conclusions de novo. [[1]]",
            },
            {
                "key": "facts",
                "heading": "Facts",
                "citations": [2],
                "body_md": 'Vance worked as a route coordinator at Ridgeline’s Sacramento terminal from 2019 to 2023. Her offer letter incorporated by reference an employee handbook that contained an arbitration provision. The handbook elsewhere described itself as subject to unilateral amendment by Ridgeline "at any time and without notice." [[2]]',
            },
            {
                "key": "issue",
                "heading": "Issue",
                "citations": [3],
                "body_md": "Whether an arbitration clause contained in a handbook that the employer reserves the unilateral right to amend without notice is supported by consideration sufficient to form an enforceable agreement under California law. [[3]]",
            },
            {
                "key": "holding",
                "heading": "Holding",
                "citations": [4],
                "body_md": "No. An arbitration provision that the employer may amend or revoke unilaterally, at any time and without notice, is illusory and therefore unenforceable for want of consideration; the district court correctly denied the motion to compel. [[4]]",
            },
            {
                "key": "reasoning",
                "heading": "Reasoning",
                "citations": [5, 7],
                "body_md": 'A promise is illusory where the promisor retains an unlimited right to determine the nature and extent of its own performance. Ridgeline’s reservation of the right to amend the handbook "at any time and without notice" was such a right: it could have withdrawn the promise to arbitrate on the eve of a claim being filed, which the court characterised as having promised nothing at all. [[5]]\n\n**Contrary view.** Judge Almeida, dissenting in part, would have enforced the provision, contending that the majority’s illusoriness analysis sweeps more broadly than precedent permits because it reads a general amendment clause as though it reserved a right to amend retroactively. [[7]]',
            },
            {
                "key": "disposition",
                "heading": "Disposition",
                "citations": [6],
                "body_md": "Denial of the motion to compel arbitration affirmed. Grant of partial summary judgment on the overtime claim vacated and remanded for further proceedings. Each party to bear its own costs on appeal. [[6]]",
            },
        ],
    }


def draft_for(params: dict[str, Any]) -> dict[str, Any]:
    return _brief(params) if params.get("draft_type") == "case_brief" else _memo(params)


def draft_spans(draft_type: str) -> list[str]:
    return BRIEF_SPANS if draft_type == "case_brief" else MEMO_SPANS


AUDIENCE_OPTIONS = [
    "supervising partner",
    "client (sophisticated)",
    "client (lay)",
    "file memo",
    "opposing counsel",
]

TONE_OPTIONS = ["neutral predictive", "advocacy", "cautious", "plain english"]
