"""Synthetic document text, ported from `frontend/src/mocks/pages.ts`.

Citations must resolve to a real character range on a real page, or the evidence
panel and the viewer highlight are theatre. So each cited span is *planted* into
generated page text, and its offsets are computed from the text that actually
renders — the same approach the frontend used, which is why its expectations
still hold against this backend.

The seed writes these pages into the encrypted blob store, so a fixture document
is indistinguishable from an uploaded one as far as the API is concerned.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PlantedSpan:
    document_id: str
    page: int
    text: str
    section: str | None = None


PLANTED: dict[str, PlantedSpan] = {
    # ---- Nexora–Arclight Master Services Agreement ----------------------
    "s_msa_0101": PlantedSpan(
        "doc_msa",
        3,
        'This Master Services Agreement (this "Agreement") is entered into as of 14 March 2024 (the "Effective Date") by and between Nexora Systems, Inc., a Delaware corporation with its principal place of business at 1400 Harrow Street, Wilmington, Delaware ("Provider"), and Arclight Industrial Holdings, LLC, a New York limited liability company ("Customer").',
        "§ 1.1",
    ),
    "s_msa_0201": PlantedSpan(
        "doc_msa",
        14,
        'The initial term of this Agreement shall commence on the Effective Date and continue for a period of thirty-six (36) months (the "Initial Term"). Thereafter, this Agreement shall renew automatically for successive twelve (12) month periods (each, a "Renewal Term") unless either party delivers written notice of non-renewal not less than ninety (90) days prior to the end of the then-current term.',
        "§ 2.1",
    ),
    "s_msa_0301": PlantedSpan(
        "doc_msa",
        22,
        "Customer shall pay each undisputed invoice within forty-five (45) days of receipt. Amounts not paid when due shall accrue interest at the lesser of one and one-half percent (1.5%) per month or the maximum rate permitted by applicable law. Provider may increase fees for any Renewal Term by no more than the lesser of five percent (5%) or the increase in the CPI-U over the preceding twelve months.",
        "§ 3.1",
    ),
    "s_msa_0902": PlantedSpan(
        "doc_msa",
        61,
        "EXCEPT FOR THE EXCLUDED CLAIMS, EACH PARTY’S AGGREGATE LIABILITY ARISING OUT OF OR RELATING TO THIS AGREEMENT SHALL NOT EXCEED THE GREATER OF (A) TWO HUNDRED PERCENT (200%) OF THE FEES PAID OR PAYABLE BY CUSTOMER IN THE TWELVE (12) MONTHS PRECEDING THE EVENT GIVING RISE TO LIABILITY, OR (B) FIVE MILLION DOLLARS ($5,000,000).",
        "§ 9.2",
    ),
    "s_msa_0905": PlantedSpan(
        "doc_msa",
        63,
        "Provider shall defend, indemnify and hold harmless Customer from and against any third-party claim alleging that the Services, as furnished by Provider and used in accordance with this Agreement, infringe any United States patent, copyright or trade secret, and shall pay all damages finally awarded or amounts agreed in settlement.",
        "§ 9.5",
    ),
    "s_msa_1102": PlantedSpan(
        "doc_msa",
        78,
        "Customer may terminate this Agreement, or any Statement of Work, for convenience upon sixty (60) days’ prior written notice to Provider, provided that Customer shall pay all fees accrued through the effective date of termination together with any documented, non-cancellable commitments reasonably incurred by Provider.",
        "§ 11.2",
    ),
    "s_msa_1223": PlantedSpan(
        "doc_msa",
        84,
        "Neither party may assign or otherwise transfer this Agreement, in whole or in part, whether by operation of law or otherwise, without the prior written consent of the other party; provided, however, that either party may assign this Agreement without consent to a successor in interest in connection with a merger, consolidation, reorganisation, or sale of all or substantially all of its assets or voting securities, except that Customer’s consent shall be required where the proposed assignee is a Restricted Competitor identified on Schedule 12.3.",
        "§ 12.3",
    ),
    "s_msa_1226": PlantedSpan(
        "doc_msa",
        85,
        "For the avoidance of doubt, a transaction or series of related transactions resulting in the transfer of more than fifty percent (50%) of the voting securities of a party shall constitute a Change of Control, and the non-transferring party may terminate this Agreement upon thirty (30) days’ written notice delivered within sixty (60) days after receiving notice of such Change of Control.",
        "§ 12.6",
    ),
    "s_msa_1401": PlantedSpan(
        "doc_msa",
        97,
        "This Agreement shall be governed by and construed in accordance with the laws of the State of New York, without regard to its conflict-of-laws principles. The parties irrevocably submit to the exclusive jurisdiction of the state and federal courts sitting in the Borough of Manhattan, City of New York, and waive any objection to venue therein. Each party irrevocably waives any right to trial by jury.",
        "§ 14.1",
    ),
    "s_msa_1512": PlantedSpan(
        "doc_msa",
        103,
        "Customer shall maintain, and shall cause each of its Affiliates to maintain, commercial general liability insurance with limits of not less than ten million dollars ($10,000,000) per occurrence, naming Provider as an additional insured, and shall furnish certificates of insurance upon request. This obligation shall survive termination for a period of six (6) years.",
        "§ 15.12",
    ),
    # ---- Mutual NDA ------------------------------------------------------
    "s_nda_0401": PlantedSpan(
        "doc_nda",
        4,
        "The obligations of confidentiality set forth herein shall continue for a period of five (5) years from the date of disclosure, except with respect to any Confidential Information constituting a trade secret, as to which the obligations shall continue for so long as such information remains a trade secret under applicable law.",
        "§ 4.1",
    ),
    # ---- Vance v. Ridgeline Logistics -----------------------------------
    "s_vance_post": PlantedSpan(
        "doc_vance",
        3,
        "Ridgeline Logistics, Inc. appeals from the district court’s order granting partial summary judgment to plaintiff Dana Vance on her claim for unpaid overtime, and from its denial of Ridgeline’s motion to compel arbitration. We have jurisdiction under 28 U.S.C. § 1291, and we review the district court’s legal conclusions de novo.",
    ),
    "s_vance_facts": PlantedSpan(
        "doc_vance",
        5,
        'Vance worked as a route coordinator at Ridgeline’s Sacramento terminal from 2019 to 2023. Her offer letter incorporated by reference an employee handbook containing an arbitration provision, which the handbook elsewhere described as subject to unilateral amendment by Ridgeline "at any time and without notice."',
    ),
    "s_vance_issue": PlantedSpan(
        "doc_vance",
        11,
        "The question presented is whether an arbitration clause contained in a handbook that the employer reserves the unilateral right to amend without notice is supported by consideration sufficient to form an enforceable agreement under California law.",
    ),
    "s_vance_hold": PlantedSpan(
        "doc_vance",
        18,
        "We hold that an arbitration provision which the employer may amend or revoke unilaterally, at any time and without notice, is illusory and therefore unenforceable for want of consideration. Because the handbook provision at issue reserved precisely that power to Ridgeline, the district court correctly denied the motion to compel arbitration.",
    ),
    "s_vance_reason": PlantedSpan(
        "doc_vance",
        14,
        'A promise is illusory where the promisor retains an unlimited right to determine the nature and extent of her performance. Ridgeline’s reservation of the right to amend the handbook "at any time and without notice" is such a right: it could withdraw the promise to arbitrate on the eve of a claim being filed, which is to say it promised nothing at all.',
    ),
    "s_vance_disp": PlantedSpan(
        "doc_vance",
        27,
        "The order denying the motion to compel arbitration is AFFIRMED. The grant of partial summary judgment on the overtime claim is VACATED and the matter is REMANDED for further proceedings consistent with this opinion. Each party shall bear its own costs on appeal.",
    ),
    "s_vance_dissent": PlantedSpan(
        "doc_vance",
        29,
        "JUDGE ALMEIDA, dissenting in part. I agree that the overtime judgment cannot stand, but I would enforce the arbitration provision. The majority’s illusoriness analysis sweeps far more broadly than our precedent permits, and it does so by reading a general amendment clause as though it were a reservation of the right to amend retroactively.",
    ),
    # ---- Delmar Holdings v. Castellan -----------------------------------
    "s_delmar_coc": PlantedSpan(
        "doc_delmar",
        12,
        'The Court declines to read the phrase "sale of all or substantially all of its assets" as reaching a transaction in which the transferor retains its operating business and disposes only of a minority equity interest. Where sophisticated parties have defined a Change of Control by reference to a specified voting-securities threshold, the Court will not supply a lower threshold by implication.',
    ),
    "s_delmar_hold": PlantedSpan(
        "doc_delmar",
        19,
        "For the foregoing reasons, plaintiff’s motion for summary judgment is GRANTED as to Count I and DENIED as to Count II. The anti-assignment provision was not triggered by the 2023 recapitalisation.",
    ),
    # ---- Complaint — Tessara Fabrication v. Juneau Mills ----------------
    "s_tess_court": PlantedSpan(
        "doc_tessara",
        1,
        "IN THE UNITED STATES DISTRICT COURT FOR THE NORTHERN DISTRICT OF ILLINOIS, EASTERN DIVISION. Tessara Fabrication LLC, Plaintiff, v. Juneau Mills Corporation, Defendant. Case No. 1:25-cv-04417. COMPLAINT FOR BREACH OF CONTRACT AND MISAPPROPRIATION OF TRADE SECRETS. JURY TRIAL DEMANDED.",
    ),
    "s_tess_alleg": PlantedSpan(
        "doc_tessara",
        9,
        "Beginning no later than February 2025, Juneau Mills used Tessara’s proprietary tolerance specifications for the HX-series housing to solicit Tessara’s customers, including at least four customers identified in Exhibit D, and did so after executing the Mutual Non-Disclosure Agreement attached as Exhibit A.",
    ),
    "s_tess_theory": PlantedSpan(
        "doc_tessara",
        17,
        "COUNT III — MISAPPROPRIATION OF TRADE SECRETS (Defend Trade Secrets Act, 18 U.S.C. § 1836). Tessara re-alleges and incorporates by reference the allegations of paragraphs 1 through 84 as though fully set forth herein.",
    ),
    "s_tess_relief": PlantedSpan(
        "doc_tessara",
        24,
        "WHEREFORE, Plaintiff Tessara Fabrication LLC respectfully requests that this Court: (a) enter judgment in its favour on all counts; (b) award compensatory damages in an amount not less than $4,200,000; (c) award exemplary damages as permitted by 18 U.S.C. § 1836(b)(3)(C); (d) enter a permanent injunction restraining further use or disclosure of the Trade Secrets; and (e) award attorneys’ fees and costs.",
    ),
    "s_tess_dates": PlantedSpan(
        "doc_tessara",
        26,
        "The Mutual Non-Disclosure Agreement was executed on 3 November 2023. The supply agreement was terminated by Juneau Mills by letter dated 19 April 2025. This action was commenced on 22 July 2025, within the three-year limitations period prescribed by 18 U.S.C. § 1836(d).",
    ),
    # ---- Executed Amendment No. 3 (scanned, low OCR confidence) ---------
    "s_amend_0302": PlantedSpan(
        "doc_amendment",
        2,
        'Section 9.2 of the Agreement is hereby amended and restated in its entirety to replace the figure "FIVE MILLION DOLLARS ($5,000,000)" with the figure "TWELVE MILLION DOLLARS ($12,000,000)", effective as of 1 January 2025.',
        "§ 2",
    ),
}

#: DR-1: pages the (simulated) OCR flagged as below the confidence floor.
LOW_CONFIDENCE: dict[str, list[int]] = {"doc_amendment": [3, 4]}

FILLER_CONTRACT = [
    "Each party shall comply with all applicable laws, rules and regulations in the performance of its obligations hereunder, and shall obtain and maintain all licences, permits and authorisations necessary for such performance.",
    "No failure or delay by either party in exercising any right, power or privilege under this Agreement shall operate as a waiver thereof, nor shall any single or partial exercise preclude any other or further exercise of the same.",
    "The parties acknowledge that the Services are provided on a non-exclusive basis and that nothing herein shall be construed to prohibit either party from entering into similar arrangements with third parties.",
    "Any notice required or permitted to be given under this Agreement shall be in writing and shall be deemed effectively given upon personal delivery, upon confirmed electronic transmission, or three business days after deposit with a nationally recognised overnight courier.",
    "If any provision of this Agreement is held to be invalid or unenforceable, such provision shall be struck and the remaining provisions shall be enforced to the fullest extent permitted by law.",
    "The headings used in this Agreement are for convenience of reference only and shall not affect the interpretation of any provision hereof.",
    "Each party represents that it has the full corporate right, power and authority to enter into this Agreement and to perform the acts required of it hereunder.",
    "The relationship of the parties is that of independent contractors, and nothing in this Agreement shall be construed to create a partnership, joint venture, agency or employment relationship.",
]

FILLER_OPINION = [
    "We begin, as we must, with the text. The parties agree that the provision is unambiguous; they disagree only about what it unambiguously means.",
    "The district court concluded otherwise, reasoning that the parties’ course of dealing supplied the missing term. We need not decide whether that conclusion was available on this record.",
    "Appellant’s remaining contentions are addressed in turn, and none requires extended discussion.",
    "The cases on which appellant relies are not to the contrary. Each involved a provision materially different in both scope and effect from the one before us.",
    "That the result may be commercially inconvenient for one party is not a reason to depart from the language to which both parties agreed.",
    "We acknowledge the force of the dissent’s position, but we think it proves too much.",
    "Because the judgment must be vacated on this ground, we do not reach the alternative arguments pressed below.",
]

FILLER_PLEADING = [
    "Plaintiff re-alleges and incorporates by reference each of the foregoing paragraphs as though fully set forth herein.",
    "At all times relevant to this Complaint, Defendant was and is a corporation organised under the laws of the State of Delaware with its principal place of business in Cook County, Illinois.",
    "Venue is proper in this District pursuant to 28 U.S.C. § 1391(b) because a substantial part of the events giving rise to the claims occurred in this District.",
    "Plaintiff has performed all conditions, covenants and promises required of it in accordance with the terms of the agreement.",
    "As a direct and proximate result of Defendant’s conduct, Plaintiff has suffered and continues to suffer substantial damages in an amount to be proven at trial.",
    "Defendant’s conduct was wilful, wanton and in conscious disregard of Plaintiff’s rights, entitling Plaintiff to exemplary damages.",
]

MASK32 = 0xFFFFFFFF


def _seeded(document_id: str, page: int) -> Callable[[], float]:
    """FNV-1a + xorshift32, matching the TS implementation bit for bit so a
    fixture page renders identically on both sides."""
    h = 2166136261
    key = f"{document_id}#{page}"
    for ch in key:
        h ^= ord(ch)
        h = (h * 16777619) & MASK32

    state = h

    def rand() -> float:
        nonlocal state
        state ^= (state << 13) & MASK32
        state &= MASK32
        state ^= state >> 17
        state ^= (state << 5) & MASK32
        state &= MASK32
        # JS `Math.abs(h) / 2**31` on a value Math.imul keeps as int32.
        signed = state - 0x100000000 if state & 0x80000000 else state
        return abs(signed) / 2**31

    return rand


def _filler_for(document_id: str) -> list[str]:
    if document_id in ("doc_vance", "doc_delmar"):
        return FILLER_OPINION
    if document_id == "doc_tessara":
        return FILLER_PLEADING
    return FILLER_CONTRACT


def _clause_number(page: int, i: int) -> str:
    return f"{max(1, page // 7 + 1)}.{i + 1}"


def page_text(document_id: str, page: int) -> str:
    """Compose one page. Planted spans are inserted verbatim, which is what
    makes the offsets real."""
    rand = _seeded(document_id, page)
    filler = _filler_for(document_id)
    is_contract = filler is FILLER_CONTRACT
    planted = [p for p in PLANTED.values() if p.document_id == document_id and p.page == page]

    paragraphs: list[str] = []
    lead = 2 + int(rand() * 2)
    for i in range(lead):
        body = filler[int(rand() * len(filler))]
        paragraphs.append(f"{_clause_number(page, i)}  {body}" if is_contract else body)

    for p in planted:
        paragraphs.append(f"{p.section.replace('§ ', '')}  {p.text}" if p.section else p.text)

    tail = 1 + int(rand() * 3)
    for i in range(tail):
        body = filler[int(rand() * len(filler))]
        paragraphs.append(
            f"{_clause_number(page, lead + len(planted) + i)}  {body}" if is_contract else body
        )

    return "\n\n".join(paragraphs)


def page_ocr_confidence(document_id: str, page: int) -> float | None:
    if page not in LOW_CONFIDENCE.get(document_id, []):
        return None
    rand = _seeded(document_id, page)
    for _ in range(6):  # advance past the paragraph draws, as the TS does
        rand()
    return round(0.58 + rand() * 0.08, 2)


@dataclass(frozen=True)
class ResolvedSpan:
    id: str
    document_id: str
    page: int
    char_start: int
    char_end: int
    section: str | None


def resolve_span(span_id: str) -> ResolvedSpan | None:
    """Char offsets computed against the text that actually renders (FR-11)."""
    planted = PLANTED.get(span_id)
    if planted is None:
        return None
    text = page_text(planted.document_id, planted.page)
    index = text.find(planted.text)
    if index < 0:
        return None
    return ResolvedSpan(
        id=span_id,
        document_id=planted.document_id,
        page=planted.page,
        char_start=index,
        char_end=index + len(planted.text),
        section=planted.section,
    )


def span_context(span_id: str, window: int = 170) -> dict[str, str] | None:
    """Surrounding context for an excerpt card (FR-12), clipped to word bounds."""
    span = resolve_span(span_id)
    planted = PLANTED.get(span_id)
    if span is None or planted is None:
        return None
    text = page_text(planted.document_id, planted.page)
    before_raw = text[max(0, span.char_start - window) : span.char_start]
    after_raw = text[span.char_end : span.char_end + window]
    # Drop the partial leading word, as the TS `replace(/^\S*\s/, '')` does.
    before = before_raw.split(" ", 1)[1] if " " in before_raw else before_raw
    after = after_raw.rsplit(" ", 1)[0] if " " in after_raw else after_raw
    return {
        "before": ("…" if span.char_start > window else "") + before,
        "text": planted.text,
        "after": after + ("…" if span.char_end + window < len(text) else ""),
    }


def spans_for_document(document_id: str) -> list[str]:
    return [sid for sid, p in PLANTED.items() if p.document_id == document_id]


def planted_pages(document_id: str) -> list[int]:
    return sorted({p.page for p in PLANTED.values() if p.document_id == document_id})
